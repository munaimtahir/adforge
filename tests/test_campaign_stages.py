"""Real handler coverage for the previously-unwired campaign stages.

`test_worker_stages.py` mirrors the worker-dispatch handlers `WebContext` wires;
this module mirrors the rest: PRODUCT_TRUTH_VALIDATION, STRATEGY, SCRIPT,
STORYBOARD, ASSET_PLAN, AUDIO_PRODUCTION, EDIT_PLAN, DRAFT_RENDER, QC, REPAIR,
FINAL_RENDER, EXPORT. The AI provider boundary is a scripted fake (matching the
project's existing pattern of stubbing `ReasoningProvider`, e.g. `test_providers.py`);
everything else -- FFmpeg rendering, audio synthesis, QC/ffprobe validation, the
worker-job protocol -- is real, matching `test_qc.py`/`test_renderer.py`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from adforge.campaign_stages import (
    build_asset_plan_handler,
    build_audio_production_handler,
    build_draft_render_handler,
    build_edit_plan_handler,
    build_export_handler,
    build_final_render_handler,
    build_product_truth_validation_handler,
    build_qc_handler,
    build_repair_handler,
    build_script_handler,
    build_storyboard_handler,
    build_strategy_handler,
)
from adforge.models import (
    Campaign,
    CampaignState,
    CampaignTask,
    Product,
    ProductTruthSnapshot,
    TruthReadiness,
    WorkerNode,
)
from adforge.providers import (
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    ProviderRouter,
    ReasoningProvider,
)
from adforge.renderer import FFmpegRenderer
from adforge.services import Services
from adforge.worker import CampaignWorker
from adforge.worker_api import WorkerJobService
from adforge.worker_auth import issue_token
from adforge.worker_stages import (
    APP_CAPTURE_CAPABILITY,
    FLOW_GENERATION_CAPABILITY,
    WORKER_ARTIFACT_IMPORTERS,
    StageDispatchError,
    build_app_capture_handler,
    build_flow_generation_handler,
)


class ScriptedProvider(ReasoningProvider):
    """A deterministic, offline `ReasoningProvider` returning canned per-role output."""

    name = "scripted"
    capabilities = {
        "creative",
        "reasoning",
        "script",
        "storyboard",
        "product_truth_qc",
        "creative-strategy-v2",
        "script-v2",
        "storyboard-v2",
    }

    def __init__(self, outputs: dict[str, Any]) -> None:
        self._outputs = outputs
        self.calls: list[ProviderRequest] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, available=True, capabilities=self.capabilities)

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if request.task_type == "generation-prompt":
            asset_id = request.context.get("task_inputs", {}).get("asset_id")
            output = self._outputs["generation-prompt"][asset_id]
        else:
            output = self._outputs[request.task_type]
        return ProviderResponse(provider=self.name, output=output, duration_ms=1)


TRUTH = {
    "product_id": "demotask",
    "product_name": "DemoTask",
    "approved_features": ["Organizes tasks into fictional lists"],
    "prohibited_claims": ["Guarantees you will never miss a deadline"],
    "known_limitations": [],
    "privacy_claims": [],
    "audiences": ["Busy professionals"],
    "apk_locations": [],
    "demo_workflows": [{"name": "Add a fictional task"}],
    "evidence": [
        {"claim": "Organizes tasks into fictional lists", "status": "CURRENT", "source": "APK"}
    ],
    "last_verified_at": "2026-08-28T00:00:00Z",
}

STRATEGY_V2: dict[str, Any] = {
    "audience_insight": "Busy professionals lose track of small tasks",
    "audience_tension": "Too many scattered to-dos, not enough time",
    "campaign_objective": "Drive trial installs",
    "single_minded_proposition": "DemoTask keeps your day organized",
    "core_benefit": "Never lose track of your day again",
    "reason_to_believe": "Organizes tasks into fictional lists",
    "hook": "Never lose track of your day again.",
    "visual_thesis": "Clean, confident motion from chaos to clarity",
    "demonstration_objective": "Show DemoTask organizing a cluttered day",
    "proof_moments": ["Adding a task in seconds"],
    "cta": "Try DemoTask today.",
    "viewer_action": "Install the app",
    "brand_personality": ["clear", "calm"],
    "pace": "quick",
    "energy": "bright",
    "shot_count_recommendation": 2,
    "generated_real_balance": 0.5,
    "raw_ui_tolerance": 0.5,
    "audio_direction": "upbeat, minimal",
    "typography_direction": "bold, high-contrast",
    "visual_continuity_direction": "clean cuts between real UI and lifestyle b-roll",
}

SCRIPT_V2: dict[str, Any] = {
    "target_duration": 6,
    "message_hierarchy": ["organization", "speed", "CTA"],
    "beats": [
        {
            "beat_id": "b1",
            "start": 0,
            "end": 3,
            "channel": "NARRATION",
            "text": "DemoTask keeps your day organized.",
        },
        {
            "beat_id": "b2",
            "start": 3,
            "end": 5,
            "channel": "NARRATION",
            "text": "Add a task in seconds.",
        },
        {"beat_id": "b3", "start": 5, "end": 6, "channel": "CTA", "text": "Try DemoTask today."},
    ],
}

STORYBOARD_V2: dict[str, Any] = {
    "target_duration": 6,
    "shots": [
        {
            "shot_id": "shot-1",
            "scene_id": "capture-1",
            "order": 0,
            "start": 0,
            "duration": 3,
            "purpose": "proof",
            "visual_source": "ANDROID_DIRECT_CAPTURE",
            "creative_description": "App UI walkthrough",
            "capture_instruction": {
                "capture_id": "capture-1",
                "package_id": "pk.fictional.demotask",
                "actions": [{"action": "WAIT", "duration_ms": 300}],
                "keyboard_policy": "FORBIDDEN",
                "expected_filenames": ["shot.mp4"],
            },
        },
        {
            "shot_id": "shot-2",
            "scene_id": "gen-1",
            "order": 1,
            "start": 3,
            "duration": 3,
            "purpose": "benefit",
            "visual_source": "GENERATED_CINEMATIC",
            "creative_description": "Lifestyle b-roll",
        },
    ],
}

SCRIPTED_OUTPUTS: dict[str, Any] = {
    "creative-strategy-v2": STRATEGY_V2,
    "script-v2": SCRIPT_V2,
    "storyboard-v2": STORYBOARD_V2,
    "asset-plan": {
        "assets": [
            {
                "asset_id": "capture-1",
                "scene_ids": ["capture-1"],
                "classification": "CAPTURE_APP",
                "description": "Real app capture",
            },
            {
                "asset_id": "gen-1",
                "scene_ids": ["gen-1"],
                "classification": "GENERATE_VIDEO",
                "description": "Generated lifestyle clip",
            },
        ]
    },
    "generation-prompt": {
        "gen-1": {
            "scene_id": "gen-1",
            "prompt": "A person smiling while using a phone productivity app, vertical shot.",
            "negative_constraints": ["no text overlays", "no logos"],
            "reference_asset_ids": [],
            "duration_seconds": 3,
            "aspect_ratio": "9:16",
        }
    },
}


@pytest.fixture
def services(tmp_path: Path) -> Services:
    value = Services(tmp_path / "runtime", Path("schemas"))
    value.initialize()
    return value


def make_product_and_campaign(services: Services, tmp_path: Path, *, duration: int = 6) -> Campaign:
    truth_path = tmp_path / "product-truth.json"
    truth_path.write_text(json.dumps(TRUTH))
    product = services.products.save(
        Product(
            id="demotask",
            name="DemoTask",
            slug="demotask",
            truth_readiness=TruthReadiness.READY,
            truth_source_path=str(truth_path),
        )
    )
    return services.campaigns.save(
        Campaign(
            product_id=product.id,
            name="DemoTask launch",
            brief="A fictional productivity app ad",
            target_duration_seconds=duration,
        )
    )


def seed_apk(services: Services, campaign_id: str) -> None:
    workspace = services.storage.campaign_workspace(campaign_id)
    apk_path = workspace / "app-capture" / "source.apk"
    apk_path.write_bytes(b"fictional-demo-apk-bytes")
    (workspace / "app-capture" / "apk-metadata.json").write_text(
        json.dumps({"package_id": "pk.fictional.demotask"})
    )


def make_clip(path: Path, *, duration: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603, S607 - test fixture, fixed argv
        [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=640x360:d={duration}:r=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def all_real_handlers(
    services: Services,
    router: ProviderRouter,
    worker_jobs: WorkerJobService,
    renderer: FFmpegRenderer,
) -> dict[CampaignState, Any]:
    return {
        CampaignState.PRODUCT_TRUTH_VALIDATION: build_product_truth_validation_handler(services),
        CampaignState.STRATEGY: build_strategy_handler(services, router),
        CampaignState.SCRIPT: build_script_handler(services, router),
        CampaignState.STORYBOARD: build_storyboard_handler(services, router),
        CampaignState.ASSET_PLAN: build_asset_plan_handler(services, router),
        CampaignState.ASSET_GENERATION: build_flow_generation_handler(services, worker_jobs),
        CampaignState.APP_CAPTURE: build_app_capture_handler(services, worker_jobs),
        CampaignState.AUDIO_PRODUCTION: build_audio_production_handler(services),
        CampaignState.EDIT_PLAN: build_edit_plan_handler(services, renderer),
        CampaignState.DRAFT_RENDER: build_draft_render_handler(services, renderer),
        CampaignState.QC: build_qc_handler(services, renderer),
        CampaignState.REPAIR: build_repair_handler(services, router, renderer, worker_jobs),
        CampaignState.FINAL_RENDER: build_final_render_handler(services, renderer),
        CampaignState.EXPORT: build_export_handler(services),
    }


def register_worker(services: Services) -> Any:
    worker = services.worker_nodes.save(
        WorkerNode(
            name="test-worker",
            agent_version="0.1.0",
            os="Linux",
            architecture="x86_64",
            capabilities=[APP_CAPTURE_CAPABILITY, FLOW_GENERATION_CAPABILITY],
        )
    )
    issue_token(services, worker)
    return worker


def complete_job(
    job_service: WorkerJobService, node: Any, job_id: str, files: dict[str, bytes]
) -> None:
    for filename, content in files.items():
        content_type = "video/mp4" if filename.endswith(".mp4") else "application/octet-stream"
        job_service.store_artifact(
            node,
            job_id,
            filename=filename,
            content=content,
            content_type=content_type,
            declared_checksum=hashlib.sha256(content).hexdigest(),
        )
    job_service.complete(node, job_id)


def test_full_pipeline_produces_a_real_playable_final_mp4(
    tmp_path: Path, services: Services
) -> None:
    campaign = make_product_and_campaign(services, tmp_path)
    seed_apk(services, campaign.id)
    router = ProviderRouter([ScriptedProvider(SCRIPTED_OUTPUTS)])
    worker_jobs = WorkerJobService(services)
    renderer = FFmpegRenderer()
    handlers = all_real_handlers(services, router, worker_jobs, renderer)
    worker_jobs.artifact_importers = WORKER_ARTIFACT_IMPORTERS
    campaign_worker = CampaignWorker(services, handlers)
    worker_jobs.on_campaign_resumed = campaign_worker.run

    result = campaign_worker.run(campaign.id)
    assert result.state == CampaignState.WAITING_FOR_WORKER

    node = register_worker(services)
    flow_job = worker_jobs.claim(node)
    assert flow_job is not None and flow_job.capability == FLOW_GENERATION_CAPABILITY
    clip_path = tmp_path / "gen-1.mp4"
    make_clip(clip_path, duration=4)
    complete_job(worker_jobs, node, flow_job.id, {"gen-1.mp4": clip_path.read_bytes()})

    refreshed = services.campaigns.get(campaign.id)
    assert refreshed is not None
    assert refreshed.state == CampaignState.WAITING_FOR_WORKER

    capture_job = worker_jobs.claim(node)
    assert capture_job is not None and capture_job.capability == APP_CAPTURE_CAPABILITY
    recording_path = tmp_path / "recording.mp4"
    make_clip(recording_path, duration=4)
    complete_job(
        worker_jobs,
        node,
        capture_job.id,
        {
            "screenshot.png": b"fake-png-bytes",
            "recording.mp4": recording_path.read_bytes(),
            "device.json": b"{}",
            "capture.json": b"{}",
            "checksums.json": b"{}",
        },
    )

    final = services.campaigns.get(campaign.id)
    assert final is not None
    assert final.state == CampaignState.COMPLETE, services.ledger.read(campaign.id)[-5:]

    export_dir = services.storage.root / "exports" / campaign.id
    final_mp4 = export_dir / "final.mp4"
    assert final_mp4.is_file()
    probe = renderer.probe(final_mp4, expect_audio=True)
    assert probe.width == 1080
    assert probe.height == 1920
    assert abs(probe.duration_seconds - 6) < 0.5
    assert probe.has_audio
    assert (export_dir / "manifest.json").is_file()
    assert (export_dir / "ledger.jsonl").is_file()

    render_records = [r for r in services.renders.list() if r.campaign_id == campaign.id]
    assert any(r.output_path == "renders/final/final.mp4" for r in render_records)
    qc_results = services.qc_results.find_by("campaign_id", campaign.id)
    assert qc_results and qc_results[0].passed


def test_qc_failure_schedules_repair_and_repair_fixes_the_render(
    tmp_path: Path, services: Services
) -> None:
    # `on_campaign_resumed` is deliberately left unset here (unlike the full-pipeline
    # test) so job completion resumes the campaign to its waiting stage without
    # auto-continuing -- giving this test precise, manual control over when to stop
    # and corrupt the draft, right after DRAFT_RENDER and before QC ever inspects it.
    campaign = make_product_and_campaign(services, tmp_path)
    seed_apk(services, campaign.id)
    router = ProviderRouter([ScriptedProvider(SCRIPTED_OUTPUTS)])
    worker_jobs = WorkerJobService(services)
    renderer = FFmpegRenderer()
    handlers = all_real_handlers(services, router, worker_jobs, renderer)
    worker_jobs.artifact_importers = WORKER_ARTIFACT_IMPORTERS
    campaign_worker = CampaignWorker(services, handlers)

    campaign_worker.run(campaign.id)
    node = register_worker(services)
    flow_job = worker_jobs.claim(node)
    assert flow_job is not None
    clip_path = tmp_path / "gen-1.mp4"
    make_clip(clip_path, duration=4)
    complete_job(worker_jobs, node, flow_job.id, {"gen-1.mp4": clip_path.read_bytes()})
    campaign_worker.run(campaign.id)

    capture_job = worker_jobs.claim(node)
    assert capture_job is not None
    recording_path = tmp_path / "recording.mp4"
    make_clip(recording_path, duration=4)
    complete_job(
        worker_jobs,
        node,
        capture_job.id,
        {
            "screenshot.png": b"fake-png-bytes",
            "recording.mp4": recording_path.read_bytes(),
            "device.json": b"{}",
            "capture.json": b"{}",
            "checksums.json": b"{}",
        },
    )
    # APP_CAPTURE(1) -> AUDIO_PRODUCTION(2) -> EDIT_PLAN(3) -> DRAFT_RENDER(4), then stop
    # before QC ever runs.
    stopped = campaign_worker.run(campaign.id, max_stages=4)
    assert stopped.state == CampaignState.QC

    workspace = services.storage.campaign_workspace(campaign.id)
    draft_path = workspace / "renders" / "drafts" / "draft.mp4"
    assert draft_path.is_file()
    draft_path.write_bytes(b"corrupted-not-a-real-video")

    result = campaign_worker.run(campaign.id)

    assert result.state == CampaignState.COMPLETE
    qc_results = sorted(
        services.qc_results.find_by("campaign_id", campaign.id), key=lambda item: item.created_at
    )
    assert len(qc_results) >= 2
    assert qc_results[0].passed is False
    assert qc_results[-1].passed is True
    repair_tasks = [
        task
        for task in services.tasks.find_by("campaign_id", campaign.id)
        if task.task_type.startswith("repair:")
    ]
    assert repair_tasks


def test_product_truth_validation_rejects_non_ready_product(services: Services) -> None:
    product = services.products.save(
        Product(name="NotReady", slug="not-ready", truth_readiness=TruthReadiness.UNKNOWN)
    )
    campaign = services.campaigns.save(
        Campaign(
            product_id=product.id,
            name="x",
            brief="brief",
            state=CampaignState.PRODUCT_TRUTH_VALIDATION,
            active=True,
        )
    )
    handler = build_product_truth_validation_handler(services)
    task = services.tasks.save(
        CampaignTask(
            campaign_id=campaign.id, task_type="product_truth_validation", idempotency_key="k"
        )
    )
    with pytest.raises(StageDispatchError, match="not READY"):
        handler(campaign, task, 1)


def test_asset_plan_rejects_unsupported_classifications(tmp_path: Path, services: Services) -> None:
    campaign = make_product_and_campaign(services, tmp_path)
    workspace = services.storage.campaign_workspace(campaign.id)
    (workspace / "storyboard" / "storyboard-v2.v1.json").write_text(
        json.dumps(
            {
                "role": "storyboard-v2",
                "version": 1,
                "product_truth_snapshot_id": "x",
                "product_truth_checksum": "a" * 64,
                "output": SCRIPTED_OUTPUTS["storyboard-v2"],
            }
        )
    )
    services.truth_snapshots.save(
        ProductTruthSnapshot(
            product_id=campaign.product_id,
            campaign_id=campaign.id,
            checksum="a" * 64,
            truth=TRUTH,
            provenance=TRUTH["evidence"],
        )
    )
    outputs = dict(SCRIPTED_OUTPUTS)
    outputs["asset-plan"] = {
        "assets": [
            {
                "asset_id": "img-1",
                "scene_ids": ["capture-1"],
                "classification": "GENERATE_IMAGE",
                "description": "unsupported for now",
            }
        ]
    }
    router = ProviderRouter([ScriptedProvider(outputs)])
    handler = build_asset_plan_handler(services, router)
    task = services.tasks.save(
        CampaignTask(campaign_id=campaign.id, task_type="asset_plan", idempotency_key="k")
    )
    with pytest.raises(StageDispatchError, match="unsupported classifications"):
        handler(campaign, task, 1)


def test_asset_plan_request_gives_the_ai_the_real_storyboard_scene_ids(
    tmp_path: Path, services: Services
) -> None:
    """Regression test: found live on a real campaign run.

    The asset-plan role's context never included the storyboard's own scene_id
    strings, so the AI free-formed its own scene id convention (e.g. "scene_01"
    instead of the storyboard's "scene_01_hook"). EDIT_PLAN then failed for every
    scene with "has no planned asset in ASSET_PLAN" because the two AI-authored
    outputs referred to the same scenes by different ids. The context must carry
    the real scene_id values so the AI can (and is told to) copy them verbatim.
    """
    campaign = make_product_and_campaign(services, tmp_path)
    workspace = services.storage.campaign_workspace(campaign.id)
    seed_apk(services, campaign.id)
    (workspace / "storyboard" / "storyboard-v2.v1.json").write_text(
        json.dumps(
            {
                "role": "storyboard-v2",
                "version": 1,
                "product_truth_snapshot_id": "x",
                "product_truth_checksum": "a" * 64,
                "output": SCRIPTED_OUTPUTS["storyboard-v2"],
            }
        )
    )
    services.truth_snapshots.save(
        ProductTruthSnapshot(
            product_id=campaign.product_id,
            campaign_id=campaign.id,
            checksum="a" * 64,
            truth=TRUTH,
            provenance=TRUTH["evidence"],
        )
    )
    provider = ScriptedProvider(dict(SCRIPTED_OUTPUTS))
    router = ProviderRouter([provider])
    handler = build_asset_plan_handler(services, router)
    task = services.tasks.save(
        CampaignTask(campaign_id=campaign.id, task_type="asset_plan", idempotency_key="k")
    )
    handler(campaign, task, 1)

    request = next(call for call in provider.calls if call.task_type == "asset-plan")
    storyboard_scenes = request.context["task_inputs"]["storyboard_scenes"]
    assert [scene["scene_id"] for scene in storyboard_scenes] == ["capture-1", "gen-1"]
    assert "verbatim" in request.context["task_inputs"]["instruction"]
