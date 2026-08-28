"""Campaign -> WorkerJob dispatch integration for APP_CAPTURE and ASSET_GENERATION.

Mirrors the exact wiring `WebContext` uses in `web.py` (a `CampaignWorker` built with
the real `worker_stages` handlers, and a `WorkerJobService` configured with the real
artifact importers and an `on_campaign_resumed` callback that continues the worker) so
these tests exercise the same code path production runs, not a reimplementation of it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from adforge.models import (
    Campaign,
    CampaignState,
    CampaignTask,
    WorkerErrorClass,
    WorkerJobStatus,
    WorkerNode,
)
from adforge.orchestrator import Orchestrator
from adforge.services import Services
from adforge.video_generation import GenerationScene, VideoGenerationRequest
from adforge.worker import CampaignWorker
from adforge.worker_api import WorkerJobService
from adforge.worker_auth import issue_token
from adforge.worker_stages import (
    APP_CAPTURE_CAPABILITY,
    FLOW_GENERATION_CAPABILITY,
    WORKER_ARTIFACT_IMPORTERS,
    build_app_capture_handler,
    build_flow_generation_handler,
)

ORDERED_STAGES = [
    CampaignState.PRODUCT_TRUTH_VALIDATION,
    CampaignState.STRATEGY,
    CampaignState.SCRIPT,
    CampaignState.STORYBOARD,
    CampaignState.ASSET_PLAN,
    CampaignState.ASSET_GENERATION,
    CampaignState.APP_CAPTURE,
]


@pytest.fixture
def services(tmp_path: Path) -> Services:
    value = Services(tmp_path / "runtime", Path("schemas"))
    value.initialize()
    return value


def build_worker(services: Services) -> tuple[CampaignWorker, WorkerJobService]:
    job_service = WorkerJobService(services)
    worker = CampaignWorker(
        services,
        {
            CampaignState.APP_CAPTURE: build_app_capture_handler(services, job_service),
            CampaignState.ASSET_GENERATION: build_flow_generation_handler(services, job_service),
        },
    )
    job_service.artifact_importers = WORKER_ARTIFACT_IMPORTERS
    job_service.on_campaign_resumed = worker.run
    return worker, job_service


def campaign_at(services: Services, target: CampaignState) -> Campaign:
    campaign = services.campaigns.save(
        Campaign(product_id="product-1", name="Demo", brief="a fictional demo brief")
    )
    orchestrator = Orchestrator(services)
    for stage in ORDERED_STAGES:
        campaign = orchestrator.transition(campaign.id, stage)
        if stage == target:
            return campaign
    raise AssertionError(f"{target} is not reachable via ORDERED_STAGES")


def seed_apk(services: Services, campaign_id: str) -> str:
    workspace = services.storage.campaign_workspace(campaign_id)
    apk_path = workspace / "app-capture" / "source.apk"
    apk_path.write_bytes(b"fictional-demo-apk-bytes")
    checksum = hashlib.sha256(apk_path.read_bytes()).hexdigest()
    (workspace / "app-capture" / "apk-metadata.json").write_text(
        json.dumps({"package_id": "pk.fictional.demotask", "sha256": checksum})
    )
    return checksum


def seed_generation_request(services: Services, campaign_id: str) -> VideoGenerationRequest:
    request = VideoGenerationRequest(
        campaign_id=campaign_id,
        credit_budget=10,
        scenes=[
            GenerationScene(
                scene_id="scene-1",
                prompt="A fictional productivity app demo, cinematic vertical clip.",
                aspect_ratio="9:16",
                duration_seconds=5,
                expected_filename="scene-1.mp4",
            )
        ],
    )
    workspace = services.storage.campaign_workspace(campaign_id)
    (workspace / "asset-plan" / "GENERATION_REQUEST.json").write_text(request.model_dump_json())
    return request


def register_worker(services: Services, capability: str) -> tuple[WorkerNode, str]:
    worker = services.worker_nodes.save(
        WorkerNode(
            name="adforge-linux-01",
            agent_version="0.1.0",
            os="Linux",
            architecture="x86_64",
            capabilities=[capability],
        )
    )
    token = issue_token(services, worker)
    return worker, token


def only_task(services: Services, campaign_id: str) -> CampaignTask:
    tasks = services.tasks.find_by("campaign_id", campaign_id)
    assert len(tasks) == 1
    return tasks[0]


def complete_app_capture_job(job_service: WorkerJobService, node: WorkerNode, job_id: str) -> None:
    for filename, content, content_type in (
        ("screenshot.png", b"fake-png-bytes", "image/png"),
        ("recording.mp4", b"fake-mp4-bytes", "video/mp4"),
        ("device.json", b"{}", "application/json"),
        ("capture.json", b"{}", "application/json"),
        ("checksums.json", b"{}", "application/json"),
    ):
        job_service.store_artifact(
            node,
            job_id,
            filename=filename,
            content=content,
            content_type=content_type,
            declared_checksum=hashlib.sha256(content).hexdigest(),
        )
    job_service.complete(node, job_id)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def test_app_capture_creates_android_capture_worker_job_with_correct_payload(
    services: Services,
) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    checksum = seed_apk(services, campaign.id)
    worker, _job_service = build_worker(services)

    result = worker.run(campaign.id, max_stages=1)

    assert result.state == CampaignState.WAITING_FOR_WORKER
    jobs = services.worker_jobs.find_by("campaign_id", campaign.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.capability == APP_CAPTURE_CAPABILITY
    assert job.status == WorkerJobStatus.PENDING
    assert job.payload == {
        "apk_relative_path": "app-capture/source.apk",
        "apk_filename": "source.apk",
        "apk_sha256": checksum,
        "package_id": "pk.fictional.demotask",
    }
    assert job.task_id == only_task(services, campaign.id).id


def test_flow_stage_creates_flow_generation_worker_job_per_scene(services: Services) -> None:
    campaign = campaign_at(services, CampaignState.ASSET_GENERATION)
    seed_generation_request(services, campaign.id)
    worker, _job_service = build_worker(services)

    result = worker.run(campaign.id, max_stages=1)

    assert result.state == CampaignState.WAITING_FOR_WORKER
    jobs = services.worker_jobs.find_by("campaign_id", campaign.id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.capability == FLOW_GENERATION_CAPABILITY
    assert job.payload == {
        "prompt": "A fictional productivity app demo, cinematic vertical clip.",
        "output_filename": "scene-1.mp4",
        "scene_id": "scene-1",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
    }


def test_no_compatible_worker_online_waits_without_failing_campaign(services: Services) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, _job_service = build_worker(services)

    result = worker.run(campaign.id, max_stages=1)

    assert result.state == CampaignState.WAITING_FOR_WORKER
    refreshed = services.campaigns.get(campaign.id)
    assert refreshed is not None
    assert refreshed.state == CampaignState.WAITING_FOR_WORKER
    assert refreshed.active is False
    assert refreshed.resume_state == CampaignState.APP_CAPTURE


def test_compatible_worker_can_claim_dispatched_job(services: Services) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, job_service = build_worker(services)
    worker.run(campaign.id, max_stages=1)
    node, _ = register_worker(services, APP_CAPTURE_CAPABILITY)

    claimed = job_service.claim(node)

    assert claimed is not None
    assert claimed.capability == APP_CAPTURE_CAPABILITY
    assert claimed.status == WorkerJobStatus.CLAIMED
    assert claimed.worker_id == node.id


# --------------------------------------------------------------------------
# Completion / auto-resume / duplicate-completion / failure
# --------------------------------------------------------------------------


def test_completion_imports_artifacts_and_auto_resumes_campaign(services: Services) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, job_service = build_worker(services)
    worker.run(campaign.id, max_stages=1)
    node, _ = register_worker(services, APP_CAPTURE_CAPABILITY)
    claimed = job_service.claim(node)
    assert claimed is not None

    complete_app_capture_job(job_service, node, claimed.id)

    refreshed = services.campaigns.get(campaign.id)
    assert refreshed is not None
    # the auto-resume callback re-ran CampaignWorker, which advanced past APP_CAPTURE
    # to AUDIO_PRODUCTION (no handler registered there in this test) and blocked --
    # proving real advancement happened rather than only flipping back to APP_CAPTURE.
    assert refreshed.state == CampaignState.BLOCKED
    assets = services.assets.find_by("campaign_id", campaign.id)
    asset_types = {asset.asset_type for asset in assets}
    assert asset_types == {"app_capture_image", "app_capture_video"}
    manifest = services.storage.read_manifest(campaign.id)
    assert len(manifest["assets"]) == 2


def test_duplicate_completion_does_not_double_import_or_double_advance(
    services: Services,
) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, job_service = build_worker(services)
    worker.run(campaign.id, max_stages=1)
    node, _ = register_worker(services, APP_CAPTURE_CAPABILITY)
    claimed = job_service.claim(node)
    assert claimed is not None
    complete_app_capture_job(job_service, node, claimed.id)
    before = services.campaigns.get(campaign.id)
    assets_before = services.assets.find_by("campaign_id", campaign.id)

    second = job_service.complete(node, claimed.id)

    assert second.status == WorkerJobStatus.COMPLETE
    after = services.campaigns.get(campaign.id)
    assert after == before
    assert services.assets.find_by("campaign_id", campaign.id) == assets_before


def test_non_retryable_failure_blocks_campaign(services: Services) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, job_service = build_worker(services)
    worker.run(campaign.id, max_stages=1)
    node, _ = register_worker(services, APP_CAPTURE_CAPABILITY)
    claimed = job_service.claim(node)
    assert claimed is not None

    job_service.fail(
        node, claimed.id, error_class=WorkerErrorClass.NON_RETRYABLE, detail="bad apk"
    )

    refreshed = services.campaigns.get(campaign.id)
    assert refreshed is not None
    assert refreshed.state == CampaignState.BLOCKED


def test_retryable_failure_requeues_job_without_blocking_campaign(services: Services) -> None:
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, job_service = build_worker(services)
    worker.run(campaign.id, max_stages=1)
    node, _ = register_worker(services, APP_CAPTURE_CAPABILITY)
    claimed = job_service.claim(node)
    assert claimed is not None

    job_service.fail(
        node, claimed.id, error_class=WorkerErrorClass.RETRYABLE, detail="emulator boot timeout"
    )

    requeued = services.worker_jobs.get(claimed.id)
    assert requeued is not None
    assert requeued.status == WorkerJobStatus.PENDING
    refreshed = services.campaigns.get(campaign.id)
    assert refreshed is not None
    assert refreshed.state == CampaignState.WAITING_FOR_WORKER


# --------------------------------------------------------------------------
# Restart / manual fallback
# --------------------------------------------------------------------------


def test_restart_preserves_waiting_for_worker_state(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    services = Services(runtime_root, Path("schemas"))
    services.initialize()
    campaign = campaign_at(services, CampaignState.APP_CAPTURE)
    seed_apk(services, campaign.id)
    worker, _job_service = build_worker(services)
    worker.run(campaign.id, max_stages=1)

    restarted = Services(runtime_root, Path("schemas"))
    restarted.initialize()

    refreshed = restarted.campaigns.get(campaign.id)
    assert refreshed is not None
    assert refreshed.state == CampaignState.WAITING_FOR_WORKER
    jobs = restarted.worker_jobs.find_by("campaign_id", campaign.id)
    assert len(jobs) == 1
    assert jobs[0].status == WorkerJobStatus.PENDING


def test_manual_handoff_fallback_bypasses_worker_dispatch_when_configured(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADFORGE_MANUAL_HANDOFF_STAGES", "ASSET_GENERATION")
    campaign = campaign_at(services, CampaignState.ASSET_GENERATION)
    seed_generation_request(services, campaign.id)
    worker, _job_service = build_worker(services)

    result = worker.run(campaign.id, max_stages=1)

    assert result.state == CampaignState.WAITING_FOR_EXTERNAL_ASSET
    assert services.worker_jobs.find_by("campaign_id", campaign.id) == []
    packages = services.handoffs.find_by("campaign_id", campaign.id)
    assert len(packages) == 1
    assert packages[0].handoff_type == "generation"


def test_product_truth_and_qc_gates_unaffected(services: Services) -> None:
    """CampaignWorker.run() still refuses CREATED campaigns without READY Product Truth."""
    campaign = services.campaigns.save(
        Campaign(product_id="missing-product", name="Demo", brief="brief")
    )
    worker, _job_service = build_worker(services)

    result = worker.run(campaign.id)

    assert result.reason == "Product Truth is not READY"
    refreshed = services.campaigns.get(campaign.id)
    assert refreshed is not None
    assert refreshed.state == CampaignState.CREATED
