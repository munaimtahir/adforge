"""Campaign-stage handlers for the AI creative pipeline, audio, edit, render, QC,
repair, and export stages.

These are the counterpart to `worker_stages.py` (which dispatches worker-capable
stages through `WorkerJob`): every stage here executes synchronously against local
capabilities (structured AI provider calls via `CreativePipeline`, the local audio
providers, FFmpeg, and QC) using the exact same `StageHandler` contract
(`Callable[[Campaign, CampaignTask, int], dict[str, Any]]`) that `CampaignWorker`
drives. A handler returns `{}` to take the default linear transition
(`worker.DEFAULT_NEXT`), `{"next_state": ...}` to branch explicitly (QC's
pass/repair/block decision), or `{"waiting_state": ...}` to pause the campaign.

Single-role stages (STRATEGY/SCRIPT/STORYBOARD) call `CreativePipeline.execute`
unconditionally on every invocation: `persist()` always writes a fresh version, so a
genuine retry (the provider got it wrong, or failed) correctly asks the AI again.
ASSET_PLAN is the one multi-call stage (a plan role, then one generation-prompt role
per generative asset need) and is made idempotent by checking for an already-persisted
plan before recomputing it -- otherwise a retry after a later generation-prompt
failure would silently mint a spurious new plan version.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from adforge.audio import (
    AudioService,
    LocalProceduralMusicProvider,
    LocalProceduralVoiceProvider,
    MusicRequest,
    VoiceRequest,
)
from adforge.creative import (
    ROLE_DIRECTORIES,
    ROLE_MODELS,
    AssetClassification,
    AssetNeed,
    AssetPlanOutput,
    CreativePipeline,
    GenerationPromptOutput,
    ScriptOutput,
    StoryboardOutput,
    StructuredOutput,
)
from adforge.models import (
    Asset,
    Campaign,
    CampaignState,
    CampaignTask,
    LedgerEvent,
    ProductTruthSnapshot,
    Render,
    TruthReadiness,
)
from adforge.orchestrator import Orchestrator
from adforge.product_truth import ProductTruthService
from adforge.providers import ProviderRouter
from adforge.qc import QCFinding, QCPolicy, QCService, Severity
from adforge.renderer import (
    SUPPORTED_DURATIONS,
    AudioTrackSpec,
    ClipSpec,
    EditSpec,
    FFmpegRenderer,
    OutputProfile,
    TextOverlay,
)
from adforge.services import Services
from adforge.storage import sha256_file
from adforge.video_generation import GenerationScene, VideoGenerationRequest
from adforge.worker import StageHandler
from adforge.worker_stages import GENERATION_REQUEST_RELATIVE_PATH, StageDispatchError

FINAL_RENDER_RELATIVE_PATH = "renders/final/final.mp4"


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _require_snapshot(services: Services, campaign_id: str) -> ProductTruthSnapshot:
    snapshots = services.truth_snapshots.find_by("campaign_id", campaign_id)
    if not snapshots:
        raise StageDispatchError("no Product Truth snapshot exists for this campaign")
    return snapshots[0]


def _latest_output(services: Services, campaign_id: str, role: str) -> StructuredOutput | None:
    workspace = services.storage.campaign_workspace(campaign_id)
    directory = workspace / ROLE_DIRECTORIES[role]
    versions = sorted(directory.glob(f"{role}.v*.json"))
    if not versions:
        return None
    payload = json.loads(versions[-1].read_text())
    return ROLE_MODELS[role].model_validate(payload["output"])


def _require_output(services: Services, campaign_id: str, role: str) -> StructuredOutput:
    output = _latest_output(services, campaign_id, role)
    if output is None:
        raise StageDispatchError(f"no persisted {role} output exists for this campaign")
    return output


def _select_and_execute(
    services: Services,
    router: ProviderRouter,
    pipeline: CreativePipeline,
    role: str,
    campaign: Campaign,
    snapshot: ProductTruthSnapshot,
    task_id: str,
    target_duration_seconds: float,
    additional_context: dict[str, Any] | None = None,
) -> StructuredOutput:
    request = pipeline.build_request(
        role,
        campaign,
        snapshot,
        target_duration_seconds=target_duration_seconds,
        additional_context=additional_context,
    )
    provider = router.select(request)
    return pipeline.execute(
        role,
        campaign,
        snapshot,
        task_id,
        provider,
        target_duration_seconds=target_duration_seconds,
        additional_context=additional_context,
    )


def _render_and_persist(
    services: Services,
    renderer: FFmpegRenderer,
    workspace: Path,
    spec: EditSpec,
    stage: str,
    event_type: str,
) -> Render:
    result = renderer.render(spec, workspace)
    render = services.renders.save(
        Render(
            campaign_id=spec.campaign_id,
            status="COMPLETE",
            spec_path="edit/spec.json",
            output_path=spec.output_path,
            aspect_ratio=spec.output_profile.aspect_ratio,
            duration_seconds=result.duration_seconds,
            checksum=result.checksum,
        )
    )
    services.ledger.append(
        LedgerEvent(
            campaign_id=spec.campaign_id,
            stage=stage,
            event_type=event_type,
            status="COMPLETE",
            details={"checksum": result.checksum, "output_path": spec.output_path},
        )
    )
    return render


# --------------------------------------------------------------------------
# PRODUCT_TRUTH_VALIDATION
# --------------------------------------------------------------------------


def build_product_truth_validation_handler(services: Services) -> StageHandler:
    truth_service = ProductTruthService(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        product = services.products.get(campaign.product_id)
        if (
            product is None
            or product.truth_readiness != TruthReadiness.READY
            or not product.truth_source_path
        ):
            raise StageDispatchError("product truth is not READY for this campaign's product")
        truth = truth_service.parse(Path(product.truth_source_path))
        truth_service.snapshot_for_campaign(product, campaign, truth)
        return {}

    return handler


# --------------------------------------------------------------------------
# STRATEGY / SCRIPT / STORYBOARD (single AI role each)
# --------------------------------------------------------------------------


def build_strategy_handler(services: Services, router: ProviderRouter) -> StageHandler:
    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        _select_and_execute(
            services, router, pipeline, "creative-strategy", campaign, snapshot,
            task.id, campaign.target_duration_seconds,
        )
        return {}

    return handler


def build_script_handler(services: Services, router: ProviderRouter) -> StageHandler:
    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        _select_and_execute(
            services, router, pipeline, "script", campaign, snapshot,
            task.id, campaign.target_duration_seconds,
        )
        return {}

    return handler


def build_storyboard_handler(services: Services, router: ProviderRouter) -> StageHandler:
    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        _select_and_execute(
            services, router, pipeline, "storyboard", campaign, snapshot,
            task.id, campaign.target_duration_seconds,
        )
        return {}

    return handler


# --------------------------------------------------------------------------
# ASSET_PLAN (plan role, plus one generation-prompt role per generative need)
# --------------------------------------------------------------------------

SUPPORTED_ASSET_CLASSIFICATIONS = (
    AssetClassification.CAPTURE_APP,
    AssetClassification.GENERATE_VIDEO,
)


def build_asset_plan_handler(services: Services, router: ProviderRouter) -> StageHandler:
    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        storyboard = _require_output(services, campaign.id, "storyboard")
        assert isinstance(storyboard, StoryboardOutput)
        plan = _latest_output(services, campaign.id, "asset-plan")
        if plan is None:
            plan = _select_and_execute(
                services, router, pipeline, "asset-plan", campaign, snapshot,
                task.id, campaign.target_duration_seconds,
                additional_context={
                    "supported_classifications": [c.value for c in SUPPORTED_ASSET_CLASSIFICATIONS],
                    "instruction": (
                        "Classify every asset need using only CAPTURE_APP or "
                        "GENERATE_VIDEO; other classifications are not yet "
                        "production-supported."
                    ),
                },
            )
        assert isinstance(plan, AssetPlanOutput)
        unsupported = [
            need
            for need in plan.assets
            if need.classification not in SUPPORTED_ASSET_CLASSIFICATIONS
        ]
        if unsupported:
            codes = sorted({need.classification.value for need in unsupported})
            raise StageDispatchError(
                "asset plan requested unsupported classifications: " + ", ".join(codes)
            )
        capture_needs = [
            need for need in plan.assets if need.classification == AssetClassification.CAPTURE_APP
        ]
        video_needs = [
            need
            for need in plan.assets
            if need.classification == AssetClassification.GENERATE_VIDEO
        ]
        if capture_needs:
            workspace = services.storage.campaign_workspace(campaign.id)
            if not (workspace / "app-capture" / "source.apk").is_file():
                raise StageDispatchError(
                    "asset plan requires APP_CAPTURE but no APK has been ingested "
                    "for this campaign"
                )
        if video_needs:
            _dispatch_generation_request(
                services, router, pipeline, campaign, snapshot, task.id, storyboard, video_needs
            )
        return {}

    return handler


def _dispatch_generation_request(
    services: Services,
    router: ProviderRouter,
    pipeline: CreativePipeline,
    campaign: Campaign,
    snapshot: ProductTruthSnapshot,
    task_id: str,
    storyboard: StoryboardOutput,
    video_needs: list[AssetNeed],
) -> None:
    scene_durations = {
        scene.scene_id: scene.end_seconds - scene.start_seconds for scene in storyboard.scenes
    }
    scenes: list[GenerationScene] = []
    for need in video_needs:
        required_duration = max(
            (
                scene_durations[scene_id]
                for scene_id in need.scene_ids
                if scene_id in scene_durations
            ),
            default=campaign.target_duration_seconds,
        )
        if required_duration > 30:
            raise StageDispatchError(
                f"asset {need.asset_id} requires a {required_duration:g}s clip, "
                "which exceeds the 30s single-generation limit"
            )
        prompt_output = _select_and_execute(
            services, router, pipeline, "generation-prompt", campaign, snapshot,
            f"{task_id}:{need.asset_id}", campaign.target_duration_seconds,
            additional_context={
                "asset_id": need.asset_id,
                "target_scene_ids": need.scene_ids,
                "description": need.description,
                "minimum_duration_seconds": required_duration,
            },
        )
        assert isinstance(prompt_output, GenerationPromptOutput)
        duration = min(max(prompt_output.duration_seconds, required_duration), 30.0)
        scenes.append(
            GenerationScene(
                scene_id=need.asset_id,
                prompt=prompt_output.prompt,
                negative_constraints=prompt_output.negative_constraints,
                aspect_ratio=prompt_output.aspect_ratio,
                duration_seconds=duration,
                expected_filename=f"{need.asset_id}.mp4",
            )
        )
    credit_budget = sum(
        scene.max_attempts * scene.generation_count * scene.estimated_credits_per_attempt
        for scene in scenes
    )
    request = VideoGenerationRequest(
        campaign_id=campaign.id, credit_budget=credit_budget, scenes=scenes
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    request_path = workspace.joinpath(*GENERATION_REQUEST_RELATIVE_PATH)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(request.model_dump_json(indent=2) + "\n")


# --------------------------------------------------------------------------
# AUDIO_PRODUCTION
# --------------------------------------------------------------------------


def build_audio_production_handler(services: Services) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        script = _require_output(services, campaign.id, "script")
        assert isinstance(script, ScriptOutput)
        narration_text = " ".join(
            line.text for line in script.lines if line.mode in {"NARRATION", "CTA"}
        )
        if not narration_text.strip():
            raise StageDispatchError("script has no narration or CTA lines to voice")
        workspace = services.storage.campaign_workspace(campaign.id)
        audio_service = AudioService(services)
        narration_metadata = LocalProceduralVoiceProvider().synthesize(
            VoiceRequest(
                text=narration_text, target_duration_seconds=campaign.target_duration_seconds
            ),
            workspace / "audio" / "voice" / "narration.wav",
        )
        audio_service.register(campaign.id, "voice_narration", narration_metadata)
        music_metadata = LocalProceduralMusicProvider().generate(
            MusicRequest(duration_seconds=campaign.target_duration_seconds),
            workspace / "audio" / "music" / "bed.wav",
        )
        audio_service.register(campaign.id, "music_bed", music_metadata)
        return {}

    return handler


# --------------------------------------------------------------------------
# EDIT_PLAN
# --------------------------------------------------------------------------


def build_edit_plan_handler(services: Services, renderer: FFmpegRenderer) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        workspace = services.storage.campaign_workspace(campaign.id)
        script = _require_output(services, campaign.id, "script")
        storyboard = _require_output(services, campaign.id, "storyboard")
        plan = _require_output(services, campaign.id, "asset-plan")
        assert isinstance(script, ScriptOutput)
        assert isinstance(storyboard, StoryboardOutput)
        assert isinstance(plan, AssetPlanOutput)

        duration = int(campaign.target_duration_seconds)
        if duration not in SUPPORTED_DURATIONS:
            raise StageDispatchError(
                f"campaign target_duration_seconds={duration} is not a supported "
                f"render duration {sorted(SUPPORTED_DURATIONS)}"
            )

        assets = services.assets.find_by("campaign_id", campaign.id)
        capture_assets = [a for a in assets if a.asset_type == "app_capture_video"]
        capture_asset = max(capture_assets, key=lambda a: a.created_at) if capture_assets else None
        video_assets_by_scene_id = {
            a.provenance.get("scene_id"): a
            for a in assets
            if a.source == "worker:flow_generation" and a.provenance.get("scene_id")
        }
        need_by_scene_id = {
            scene_id: need for need in plan.assets for scene_id in need.scene_ids
        }

        used_assets = []
        clips = []
        for scene in sorted(storyboard.scenes, key=lambda item: item.start_seconds):
            need = need_by_scene_id.get(scene.scene_id)
            if need is None:
                raise StageDispatchError(
                    f"storyboard scene {scene.scene_id} has no planned asset in ASSET_PLAN"
                )
            asset: Asset | None
            if need.classification == AssetClassification.CAPTURE_APP:
                asset = capture_asset
                if asset is None:
                    raise StageDispatchError(
                        f"scene {scene.scene_id} needs an app capture, but none was imported"
                    )
            else:
                asset = video_assets_by_scene_id.get(need.asset_id)
                if asset is None:
                    raise StageDispatchError(
                        f"scene {scene.scene_id} needs generated asset {need.asset_id}, "
                        "but no matching flow_generation asset was imported"
                    )
            scene_duration = scene.end_seconds - scene.start_seconds
            source_path = workspace / asset.filepath
            probed = renderer.probe(source_path, expect_audio=False)
            if probed.duration_seconds + 0.05 < scene_duration:
                raise StageDispatchError(
                    f"source clip for scene {scene.scene_id} is {probed.duration_seconds:.2f}s, "
                    f"shorter than the required {scene_duration:.2f}s"
                )
            clips.append(
                ClipSpec(
                    source=asset.filepath,
                    timeline_start_seconds=scene.start_seconds,
                    source_in_seconds=0,
                    source_out_seconds=scene_duration,
                )
            )
            used_assets.append(asset)

        overlays = [
            TextOverlay(
                text=line.text,
                start_seconds=line.start_seconds,
                end_seconds=line.end_seconds,
                position="BOTTOM",
                background=True,
            )
            for line in script.lines
            if line.mode == "ON_SCREEN"
        ]
        cta_line = next((line for line in script.lines if line.mode == "CTA"), None)
        if cta_line is None:
            raise StageDispatchError("script produced no CTA line; a CTA-mode line is required")
        cta = TextOverlay(
            text=cta_line.text,
            start_seconds=cta_line.start_seconds,
            end_seconds=cta_line.end_seconds,
            position="CENTER",
            background=True,
        )

        audio_types = {"voice_narration", "music_bed"}
        audio_by_type = {a.asset_type: a for a in assets if a.asset_type in audio_types}
        if "voice_narration" not in audio_by_type or "music_bed" not in audio_by_type:
            raise StageDispatchError(
                "audio production assets are missing; AUDIO_PRODUCTION must complete first"
            )
        narration_asset = audio_by_type["voice_narration"]
        music_asset = audio_by_type["music_bed"]
        used_assets.extend([narration_asset, music_asset])
        audio_tracks = [
            AudioTrackSpec(source=narration_asset.filepath, kind="NARRATION"),
            AudioTrackSpec(source=music_asset.filepath, kind="MUSIC", duck_under_narration=True),
        ]

        edit_spec = EditSpec(
            campaign_id=campaign.id,
            clips=clips,
            overlays=overlays,
            cta=cta,
            audio_tracks=audio_tracks,
            output_profile=OutputProfile(aspect_ratio="9:16", duration_seconds=duration),
            output_path="renders/drafts/draft.mp4",
        )
        (workspace / "edit" / "spec.json").write_text(edit_spec.model_dump_json(indent=2) + "\n")
        for asset in used_assets:
            if not asset.used_in_final:
                services.assets.save(asset.model_copy(update={"used_in_final": True}))
        services.ledger.append(
            LedgerEvent(
                campaign_id=campaign.id,
                stage="EDIT_PLAN",
                event_type="edit_plan_created",
                status="COMPLETE",
                output_asset_ids=[asset.id for asset in used_assets],
            )
        )
        return {}

    return handler


# --------------------------------------------------------------------------
# DRAFT_RENDER / REPAIR / FINAL_RENDER
# --------------------------------------------------------------------------


def _load_edit_spec(services: Services, campaign_id: str) -> EditSpec:
    workspace = services.storage.campaign_workspace(campaign_id)
    return EditSpec.model_validate_json((workspace / "edit" / "spec.json").read_text())


def build_draft_render_handler(services: Services, renderer: FFmpegRenderer) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        workspace = services.storage.campaign_workspace(campaign.id)
        spec = _load_edit_spec(services, campaign.id)
        _render_and_persist(services, renderer, workspace, spec, "DRAFT_RENDER", "draft_rendered")
        return {}

    return handler


def build_repair_handler(services: Services, renderer: FFmpegRenderer) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        workspace = services.storage.campaign_workspace(campaign.id)
        spec = _load_edit_spec(services, campaign.id)
        _render_and_persist(services, renderer, workspace, spec, "REPAIR", "repair_render_retried")
        return {}

    return handler


def build_final_render_handler(services: Services, renderer: FFmpegRenderer) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        workspace = services.storage.campaign_workspace(campaign.id)
        spec = _load_edit_spec(services, campaign.id)
        final_spec = spec.model_copy(update={"output_path": FINAL_RENDER_RELATIVE_PATH})
        _render_and_persist(
            services, renderer, workspace, final_spec, "FINAL_RENDER", "final_rendered"
        )
        return {}

    return handler


# --------------------------------------------------------------------------
# QC
# --------------------------------------------------------------------------


def build_qc_handler(
    services: Services, renderer: FFmpegRenderer, policy: QCPolicy | None = None
) -> StageHandler:
    qc_service = QCService(services, renderer, policy=policy)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        spec = _load_edit_spec(services, campaign.id)
        renders = sorted(
            services.renders.find_by("campaign_id", campaign.id), key=lambda item: item.created_at
        )
        if not renders:
            raise StageDispatchError("no render exists for QC")
        render = renders[-1]
        snapshot = _require_snapshot(services, campaign.id)
        script = _require_output(services, campaign.id, "script")
        storyboard = _require_output(services, campaign.id, "storyboard")
        assert isinstance(script, ScriptOutput)
        assert isinstance(storyboard, StoryboardOutput)
        claims = [line.claim for line in script.lines if line.claim]
        claims.extend(claim for scene in storyboard.scenes for claim in scene.claims)
        required_asset_ids = [
            asset.id
            for asset in services.assets.find_by("campaign_id", campaign.id)
            if asset.used_in_final
        ]
        result = qc_service.run(
            campaign, render, spec, snapshot, claims=claims, required_asset_ids=required_asset_ids
        )
        if result.passed:
            return {"next_state": CampaignState.FINAL_RENDER}
        previous_repairs = [
            item
            for item in services.tasks.find_by("campaign_id", campaign.id)
            if item.task_type == f"repair:{task.task_type}"
        ]
        reason = "; ".join(result.blockers)
        if len(previous_repairs) >= qc_service.policy.max_targeted_repairs_per_task:
            return {"next_state": CampaignState.BLOCKED, "reason": reason}
        findings = [
            QCFinding.model_validate(item) for item in result.metrics.get("findings", [])
        ]
        targets = sorted(
            {
                item.asset_id
                for item in findings
                if item.severity == Severity.BLOCKER and item.asset_id
            }
        )
        Orchestrator(services).create_repair_task(campaign.id, task, targets)
        return {"next_state": CampaignState.REPAIR, "reason": reason}

    return handler


# --------------------------------------------------------------------------
# EXPORT
# --------------------------------------------------------------------------


def build_export_handler(services: Services) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        workspace = services.storage.campaign_workspace(campaign.id)
        finals = [
            item
            for item in services.renders.find_by("campaign_id", campaign.id)
            if item.output_path == FINAL_RENDER_RELATIVE_PATH
        ]
        if not finals:
            raise StageDispatchError("no FINAL_RENDER output exists to export")
        render = sorted(finals, key=lambda item: item.created_at)[-1]
        source = workspace / render.output_path
        export_dir = services.storage.root / "exports" / campaign.id
        export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, export_dir / "final.mp4")
        checksum = sha256_file(export_dir / "final.mp4")
        if checksum != render.checksum:
            raise StageDispatchError("exported MP4 checksum does not match the render record")
        manifest = services.storage.read_manifest(campaign.id)
        (export_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        events = services.ledger.read(campaign.id)
        (export_dir / "ledger.jsonl").write_text(
            "\n".join(event.model_dump_json() for event in events) + "\n"
        )
        services.ledger.append(
            LedgerEvent(
                campaign_id=campaign.id,
                stage="EXPORT",
                event_type="campaign_exported",
                status="COMPLETE",
                details={"export_path": str(export_dir), "checksum": checksum},
            )
        )
        return {}

    return handler
