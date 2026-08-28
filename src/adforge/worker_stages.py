"""Campaign-stage handlers that dispatch worker-capable stages through WorkerJob.

`APP_CAPTURE` and `ASSET_GENERATION` (Flow/video generation) are worker-capable
stages: instead of always producing a `HandoffPackage` for manual completion, they
create a durable `WorkerJob` and wait for a distributed worker to claim and complete
it. Precedence is deterministic and matches `docs/02-architecture/WORKER_PROTOCOL.md`:

1. An explicit manual-handoff fallback for the stage (`ADFORGE_MANUAL_HANDOFF_STAGES`)
   is configured -- always used when set, regardless of worker availability. This is
   an operator opt-in for a specific stage, not an automatic degrade.
2. Otherwise, always dispatch through `WorkerJob`. A `WorkerJob` is durable and
   PENDING until a compatible worker claims it, so campaigns never fail merely
   because no worker happens to be online right now -- they wait in
   `WAITING_FOR_WORKER` and resume automatically once a job completes
   (`WorkerJobService.complete` -> `_resume_campaign_if_waiting`).

A campaign-stage handler runs exactly once per stage-version: `Orchestrator.execute_task`
marks the task COMPLETE the moment the handler returns (even when it returns a
`waiting_state`), so there is no second invocation to "check back" on worker progress.
Importing the resulting `WorkerArtifact`s into `Asset` records therefore happens
out-of-band, in `on_worker_job_complete`, which `WorkerJobService` invokes for a
completed job's capability just before it resumes a `WAITING_FOR_WORKER` campaign.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from adforge.android import (
    APKValidationError,
    EmulatorCaptureRequest,
    EmulatorHandoffService,
)
from adforge.models import (
    Asset,
    Campaign,
    CampaignState,
    CampaignTask,
    WorkerJob,
)
from adforge.services import Services
from adforge.storage import sha256_file
from adforge.video_generation import GenerationHandoffService, VideoGenerationRequest
from adforge.worker import StageHandler
from adforge.worker_api import WorkerJobService

APP_CAPTURE_CAPABILITY = "android_capture"
FLOW_GENERATION_CAPABILITY = "flow_generation"

GENERATION_REQUEST_RELATIVE_PATH = ("asset-plan", "GENERATION_REQUEST.json")
MANUAL_APP_CAPTURE_REQUEST_RELATIVE_PATH = ("app-capture", "manual-request.json")


class StageDispatchError(RuntimeError):
    """Raised when a worker-capable stage cannot even build/dispatch its job."""


def _manual_fallback_enabled(stage: str) -> bool:
    configured = os.getenv("ADFORGE_MANUAL_HANDOFF_STAGES", "")
    return stage in {item.strip() for item in configured.split(",") if item.strip() if item}


def _existing_job(services: Services, campaign_id: str, idempotency_key: str) -> WorkerJob | None:
    matches = [
        job
        for job in services.worker_jobs.find_by("idempotency_key", idempotency_key)
        if job.campaign_id == campaign_id
    ]
    return matches[0] if matches else None


def _append_asset_to_manifest(services: Services, campaign_id: str, asset: Asset) -> None:
    manifest = services.storage.read_manifest(campaign_id)
    manifest["assets"].append(
        {
            "asset_id": asset.id,
            "asset_type": asset.asset_type,
            "source": asset.source,
            "provider": asset.provider,
            "version": asset.version,
            "status": asset.status,
            "qc_score": asset.qc_score,
            "filepath": asset.filepath,
            "checksum": asset.checksum,
            "used_in_final": asset.used_in_final,
        }
    )
    services.storage.write_manifest(campaign_id, manifest)


def _asset_already_imported(services: Services, campaign_id: str, filepath: str) -> bool:
    manifest = services.storage.read_manifest(campaign_id)
    return any(entry.get("filepath") == filepath for entry in manifest["assets"])


# --------------------------------------------------------------------------
# APP_CAPTURE (android_capture)
# --------------------------------------------------------------------------


def _app_capture_payload(services: Services, campaign: Campaign) -> dict[str, Any]:
    workspace = services.storage.campaign_workspace(campaign.id)
    apk_path = workspace / "app-capture" / "source.apk"
    metadata_path = workspace / "app-capture" / "apk-metadata.json"
    if not apk_path.is_file() or not metadata_path.is_file():
        raise StageDispatchError(
            "no ingested APK found for this campaign; ingest one with APKIngestor "
            "before the campaign reaches APP_CAPTURE"
        )
    metadata = json.loads(metadata_path.read_text())
    package_id = metadata.get("package_id")
    if not package_id:
        raise StageDispatchError("ingested APK has no discoverable package_id")
    return {
        "apk_relative_path": "app-capture/source.apk",
        "apk_filename": "source.apk",
        "apk_sha256": sha256_file(apk_path),
        "package_id": package_id,
    }


def _export_app_capture_manual(services: Services, campaign: Campaign) -> dict[str, Any]:
    workspace = services.storage.campaign_workspace(campaign.id)
    request_path = workspace.joinpath(*MANUAL_APP_CAPTURE_REQUEST_RELATIVE_PATH)
    if not request_path.is_file():
        raise StageDispatchError(
            "manual APP_CAPTURE fallback is enabled but "
            f"{'/'.join(MANUAL_APP_CAPTURE_REQUEST_RELATIVE_PATH)} does not exist; "
            "prepare an EmulatorCaptureRequest there before this stage runs"
        )
    request = EmulatorCaptureRequest.model_validate_json(request_path.read_text())
    if request.campaign_id != campaign.id:
        raise StageDispatchError("manual APP_CAPTURE request campaign_id mismatch")
    try:
        package = EmulatorHandoffService(services).export(request)
    except APKValidationError as exc:
        raise StageDispatchError(str(exc)) from exc
    return {
        "waiting_state": CampaignState.WAITING_FOR_EXTERNAL_ASSET,
        "reason": f"manual emulator handoff exported ({package.id})",
    }


def build_app_capture_handler(services: Services, worker_jobs: WorkerJobService) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        if _manual_fallback_enabled(CampaignState.APP_CAPTURE.value):
            return _export_app_capture_manual(services, campaign)
        idempotency_key = f"worker:{task.id}"
        job = _existing_job(services, campaign.id, idempotency_key)
        if job is None:
            payload = _app_capture_payload(services, campaign)
            job = worker_jobs.create_job(
                campaign.id,
                APP_CAPTURE_CAPABILITY,
                payload,
                idempotency_key,
                task_id=task.id,
            )
        return {
            "waiting_state": CampaignState.WAITING_FOR_WORKER,
            "reason": f"{APP_CAPTURE_CAPABILITY} WorkerJob {job.id} dispatched",
        }

    return handler


def _import_app_capture_artifacts(services: Services, campaign: Campaign, job: WorkerJob) -> None:
    artifacts = {a.filename: a for a in services.worker_artifacts.find_by("job_id", job.id)}
    required = ("screenshot.png", "recording.mp4", "device.json", "capture.json", "checksums.json")
    missing = [name for name in required if name not in artifacts]
    if missing:
        raise StageDispatchError(
            f"android_capture WorkerJob {job.id} is missing required artifacts: {missing}"
        )
    for filename, asset_type in (
        ("screenshot.png", "app_capture_image"),
        ("recording.mp4", "app_capture_video"),
    ):
        artifact = artifacts[filename]
        if _asset_already_imported(services, campaign.id, artifact.filepath):
            continue
        asset = services.assets.save(
            Asset(
                campaign_id=campaign.id,
                asset_type=asset_type,
                status="READY",
                filepath=artifact.filepath,
                source="worker:android_capture",
                checksum=artifact.checksum,
                provenance={
                    "worker_job_id": job.id,
                    "worker_id": job.worker_id,
                    "fictional_demo_data": True,
                },
            )
        )
        _append_asset_to_manifest(services, campaign.id, asset)


# --------------------------------------------------------------------------
# ASSET_GENERATION (flow_generation)
# --------------------------------------------------------------------------


def _load_generation_request(services: Services, campaign: Campaign) -> VideoGenerationRequest:
    workspace = services.storage.campaign_workspace(campaign.id)
    request_path = workspace.joinpath(*GENERATION_REQUEST_RELATIVE_PATH)
    if not request_path.is_file():
        raise StageDispatchError(
            f"no {'/'.join(GENERATION_REQUEST_RELATIVE_PATH)} found for this campaign; "
            "write a VideoGenerationRequest there before the campaign reaches "
            "ASSET_GENERATION"
        )
    request = VideoGenerationRequest.model_validate_json(request_path.read_text())
    if request.campaign_id != campaign.id:
        raise StageDispatchError("generation request campaign_id mismatch")
    return request


def build_flow_generation_handler(
    services: Services, worker_jobs: WorkerJobService
) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        request = _load_generation_request(services, campaign)
        if _manual_fallback_enabled(CampaignState.ASSET_GENERATION.value):
            package = GenerationHandoffService(services).export(request)
            return {
                "waiting_state": CampaignState.WAITING_FOR_EXTERNAL_ASSET,
                "reason": f"manual generation handoff exported ({package.id})",
            }
        dispatched: list[str] = []
        for scene in request.scenes:
            idempotency_key = f"worker:{task.id}:{scene.scene_id}"
            job = _existing_job(services, campaign.id, idempotency_key)
            if job is None:
                job = worker_jobs.create_job(
                    campaign.id,
                    FLOW_GENERATION_CAPABILITY,
                    {
                        "prompt": scene.prompt,
                        "output_filename": scene.expected_filename,
                        "scene_id": scene.scene_id,
                    },
                    idempotency_key,
                    task_id=task.id,
                )
            dispatched.append(job.id)
        joined = ", ".join(dispatched)
        return {
            "waiting_state": CampaignState.WAITING_FOR_WORKER,
            "reason": f"{FLOW_GENERATION_CAPABILITY} WorkerJob(s) dispatched: {joined}",
        }

    return handler


def _import_flow_generation_artifacts(
    services: Services, campaign: Campaign, job: WorkerJob
) -> None:
    scene_id = job.payload.get("scene_id")
    output_filename = job.payload.get("output_filename")
    if not output_filename:
        raise StageDispatchError(
            f"flow_generation WorkerJob {job.id} payload has no output_filename"
        )
    artifacts = {a.filename: a for a in services.worker_artifacts.find_by("job_id", job.id)}
    if output_filename not in artifacts:
        raise StageDispatchError(
            f"flow_generation WorkerJob {job.id} is missing artifact {output_filename}"
        )
    artifact = artifacts[output_filename]
    if _asset_already_imported(services, campaign.id, artifact.filepath):
        return
    provenance_artifact = artifacts.get("provenance.json")
    asset = services.assets.save(
        Asset(
            campaign_id=campaign.id,
            asset_type="generated_video",
            status="READY",
            filepath=artifact.filepath,
            source="worker:flow_generation",
            provider="flow",
            checksum=artifact.checksum,
            provenance={
                "worker_job_id": job.id,
                "worker_id": job.worker_id,
                "scene_id": scene_id,
                "provenance_artifact": provenance_artifact.filepath
                if provenance_artifact
                else None,
            },
        )
    )
    _append_asset_to_manifest(services, campaign.id, asset)


# --------------------------------------------------------------------------
# Wiring: capability -> importer, used by WorkerJobService on job completion
# --------------------------------------------------------------------------

ArtifactImporter = Callable[[Services, Campaign, WorkerJob], None]

WORKER_ARTIFACT_IMPORTERS: dict[str, ArtifactImporter] = {
    APP_CAPTURE_CAPABILITY: _import_app_capture_artifacts,
    FLOW_GENERATION_CAPABILITY: _import_flow_generation_artifacts,
}
