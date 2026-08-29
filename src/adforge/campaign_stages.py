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
from typing import Any, Literal

from pydantic import BaseModel

from adforge.audio import (
    AudioService,
    LocalProceduralMusicProvider,
    LocalProceduralVoiceProvider,
    MusicRequest,
    VoiceRequest,
)
from adforge.creative import (
    AssetClassification,
    AssetNeed,
    AssetPlanOutput,
    CreativePipeline,
    GenerationPromptOutput,
    latest_role_output,
)
from adforge.creative_quality import (
    CreativeQCSignal,
    CreativeStrategy2,
    EditClip2,
    EditingPattern,
    ScriptBeat2,
    ScriptChannel,
    Storyboard2,
    TransitionInstruction,
    TransitionKind,
    TypographyInstruction,
    TypographyRole,
    duplicate_text_pairs,
    repair_instruction,
    validate_canonical_ids,
)
from adforge.creative_quality import (
    EditPlan2 as EditPlan,
)
from adforge.creative_quality import (
    ScriptPlan2 as ScriptPlan,
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
from adforge.qc import CQ2_CODE_PREFIX, CreativeQCHook, QCFinding, QCPolicy, QCService, Severity
from adforge.renderer import (
    SUPPORTED_DURATIONS,
    AudioTrackSpec,
    ClipSpec,
    EditSpec,
    FFmpegRenderer,
    OutputProfile,
    TextOverlay,
    TransitionSpec,
)
from adforge.services import Services
from adforge.storage import sha256_file
from adforge.video_generation import GenerationScene, VideoGenerationRequest
from adforge.worker import StageHandler
from adforge.worker_api import WorkerJobService
from adforge.worker_stages import (
    APP_CAPTURE_CAPABILITY,
    GENERATION_REQUEST_RELATIVE_PATH,
    StageDispatchError,
    app_capture_payload,
    find_existing_job,
)

FINAL_RENDER_RELATIVE_PATH = "renders/final/final.mp4"


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _require_snapshot(services: Services, campaign_id: str) -> ProductTruthSnapshot:
    snapshots = services.truth_snapshots.find_by("campaign_id", campaign_id)
    if not snapshots:
        raise StageDispatchError("no Product Truth snapshot exists for this campaign")
    return snapshots[0]


def _latest_output(services: Services, campaign_id: str, role: str) -> BaseModel | None:
    return latest_role_output(services, campaign_id, role)


def _require_output(services: Services, campaign_id: str, role: str) -> BaseModel:
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
) -> BaseModel:
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

# `ProductTruthService.validate_claim` (by design -- this is the safety-critical
# claim gate) requires a `claims` entry to exactly equal an approved feature's
# evidence text after casefold/strip, not a paraphrase or substring. Providers
# reliably paraphrase ("Lets you add a task to a list" for the approved "Lets you
# add a task to a fictional list") unless told not to, which burns through the
# task's retry budget on an avoidable rejection. Telling them explicitly to copy
# verbatim or omit is a prompting fix; weakening the validator itself is not an
# option here, that's the actual safeguard.
CLAIM_DISCIPLINE_INSTRUCTION = (
    "If this role's output includes a `claims` field, every entry must be copied "
    "character-for-character from the supplied approved_features list -- do not "
    "paraphrase, shorten, or reword them even slightly. If no approved feature "
    "text fits naturally, leave `claims` empty rather than inventing or "
    "paraphrasing one."
)

# Found live: providers reliably emit `{"action": "TAP", "target_text": "..."}`
# with no x/y when they mean "tap this label" -- the schema correctly rejects
# TAP without coordinates (a real safety property, not to be loosened), so the
# fix is telling the model which action name to use, not weakening validation.
ANDROID_DSL_INSTRUCTION = (
    "Android capture actions: use TAP or TAP_COORDINATE only when you specify "
    "exact numeric x/y pixel coordinates within the 1080x1920 capture canvas. "
    "If you only know an on-screen text label to tap (a button, a list item, a "
    "field), use TAP_TEXT with `target_text` set and no x/y -- do not use TAP "
    "with only `target_text`, that combination is invalid. Likewise "
    "ASSERT_VISIBLE/ASSERT_NOT_VISIBLE take `target_text`, not coordinates. "
    "TYPE_TEXT requires `text`. WAIT/HOLD require a positive `duration_ms`."
)


def build_strategy_handler(services: Services, router: ProviderRouter) -> StageHandler:
    """The real STRATEGY stage: produces and persists Creative Quality 2.0's
    `CreativeStrategy2` (audience insight/tension, proposition, hook, proof
    moments, CTA, pacing/energy, generated/real balance, raw-UI tolerance,
    avoidances, claim boundaries, audio/typography/continuity direction).
    SCRIPT/STORYBOARD downstream consume this directly, not a V1 parallel object.
    """

    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        _select_and_execute(
            services, router, pipeline, "creative-strategy-v2", campaign, snapshot,
            task.id, campaign.target_duration_seconds,
            additional_context={"claim_discipline": CLAIM_DISCIPLINE_INSTRUCTION},
        )
        return {}

    return handler


def build_script_handler(services: Services, router: ProviderRouter) -> StageHandler:
    """The real SCRIPT stage: consumes Strategy 2.0 and produces `ScriptPlan2`
    (typed NARRATION/OVERLAY/PRODUCT_UI/SOUND_DESIGN/SILENCE/CTA beats). Redundant
    narration/overlay wording is rejected by the model itself.
    """

    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        strategy = _require_output(services, campaign.id, "creative-strategy-v2")
        assert isinstance(strategy, CreativeStrategy2)
        _select_and_execute(
            services, router, pipeline, "script-v2", campaign, snapshot,
            task.id, campaign.target_duration_seconds,
            additional_context={
                "claim_discipline": CLAIM_DISCIPLINE_INSTRUCTION,
                "strategy": strategy.model_dump(mode="json"),
            },
        )
        return {}

    return handler


def build_storyboard_handler(services: Services, router: ProviderRouter) -> StageHandler:
    """The real STORYBOARD stage: consumes Strategy 2.0 + Script 2.0 and produces
    `Storyboard2` -- contiguous shots with canonical shot/scene IDs, explicit
    visual source, composition/typography intent, and Android capture
    instructions, driving every downstream stage (ASSET_PLAN, APP_CAPTURE,
    EDIT_PLAN, QC).
    """

    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        snapshot = _require_snapshot(services, campaign.id)
        strategy = _require_output(services, campaign.id, "creative-strategy-v2")
        script = _require_output(services, campaign.id, "script-v2")
        assert isinstance(strategy, CreativeStrategy2)
        assert isinstance(script, ScriptPlan)
        _select_and_execute(
            services, router, pipeline, "storyboard-v2", campaign, snapshot,
            task.id, campaign.target_duration_seconds,
            additional_context={
                "claim_discipline": CLAIM_DISCIPLINE_INSTRUCTION,
                "android_dsl": ANDROID_DSL_INSTRUCTION,
                "strategy": strategy.model_dump(mode="json"),
                "script": script.model_dump(mode="json"),
            },
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
        storyboard = _require_output(services, campaign.id, "storyboard-v2")
        assert isinstance(storyboard, Storyboard2)
        plan = _latest_output(services, campaign.id, "asset-plan")
        if plan is None:
            plan = _select_and_execute(
                services, router, pipeline, "asset-plan", campaign, snapshot,
                task.id, campaign.target_duration_seconds,
                additional_context={
                    "supported_classifications": [c.value for c in SUPPORTED_ASSET_CLASSIFICATIONS],
                    "storyboard_scenes": [
                        {"scene_id": shot.scene_id, "description": shot.creative_description}
                        for shot in storyboard.shots
                    ],
                    "instruction": (
                        "Classify every asset need using only CAPTURE_APP or "
                        "GENERATE_VIDEO; other classifications are not yet "
                        "production-supported. Every scene_id you output in "
                        "scene_ids MUST be copied verbatim, character-for-character, "
                        "from storyboard_scenes[].scene_id above -- do not "
                        "paraphrase, shorten, or invent your own scene ids. Every "
                        "storyboard scene must be covered by exactly one asset need."
                    ),
                },
            )
        assert isinstance(plan, AssetPlanOutput)
        # Canonical-ID protection: an asset plan may only reference scene IDs the
        # storyboard actually minted. A prior real production bug (8f2f4b6) shipped
        # because nothing enforced this -- reject rather than silently remapping.
        validate_canonical_ids(
            canonical={shot.scene_id for shot in storyboard.shots},
            referenced={scene_id for need in plan.assets for scene_id in need.scene_ids},
            label="asset plan",
        )
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
    storyboard: Storyboard2,
    video_needs: list[AssetNeed],
) -> None:
    scene_durations = {shot.scene_id: shot.duration for shot in storyboard.shots}
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
        script = _require_output(services, campaign.id, "script-v2")
        assert isinstance(script, ScriptPlan)
        narration_text = " ".join(
            beat.text
            for beat in sorted(script.beats, key=lambda item: item.start)
            if beat.channel in {ScriptChannel.NARRATION, ScriptChannel.CTA}
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
# EDIT_PLAN (Editing Grammar 2.0: deterministic CQ2 EditPlan2, translated to the
# renderer's EditSpec -- the real renderer input is no longer arbitrary AI prose)
# --------------------------------------------------------------------------

_TRANSITION_KEYWORD_PATTERNS = {
    "hook": EditingPattern.HOOK_CUT,
    "reveal": EditingPattern.PRODUCT_REVEAL,
    "intro": EditingPattern.PRODUCT_REVEAL,
    "proof": EditingPattern.FEATURE_PROOF,
    "feature": EditingPattern.FEATURE_PROOF,
    "demo": EditingPattern.FEATURE_PROOF,
    "benefit": EditingPattern.BENEFIT_CUTAWAY,
    "confirm": EditingPattern.PRODUCT_CONFIRMATION,
    "result": EditingPattern.PRODUCT_CONFIRMATION,
    "cta": EditingPattern.CTA_HOLD,
}


def _infer_editing_pattern(storyboard: Storyboard2) -> EditingPattern:
    purposes = " ".join(shot.purpose.lower() for shot in storyboard.shots)
    for keyword, pattern in _TRANSITION_KEYWORD_PATTERNS.items():
        if keyword in purposes:
            return pattern
    return EditingPattern.FEATURE_PROOF


def _render_transition(transition: TransitionInstruction) -> TransitionSpec:
    if transition.kind == TransitionKind.HARD_CUT:
        return TransitionSpec(type="CUT", duration_seconds=0)
    # The renderer's real FFmpeg pipeline only implements CUT/FADE. Every richer
    # CQ2 transition kind (DEVICE_REVEAL, PUSH_IN, BLUR_MATCH, ...) collapses to a
    # FADE of the requested duration rather than pretending an unimplemented
    # transition ran -- this is an honest simplification, not a silent no-op.
    duration = min(max(transition.duration, 0.15), 1.0)
    return TransitionSpec(type="FADE", duration_seconds=duration)


_TYPOGRAPHY_POSITION: dict[str, Literal["TOP", "CENTER", "BOTTOM"]] = {
    "top": "TOP",
    "center": "CENTER",
    "bottom": "BOTTOM",
}


def _render_text_overlay(
    instruction: TypographyInstruction, *, start: float, end: float
) -> TextOverlay:
    alignment: Literal["left", "center", "right"] = instruction.alignment  # type: ignore[assignment]
    return TextOverlay(
        text=instruction.text,
        start_seconds=start,
        end_seconds=end,
        position=_TYPOGRAPHY_POSITION[instruction.safe_zone],
        alignment=alignment,
        font_size=instruction.size,
        background=instruction.contrast_background,
        role=instruction.role.value,
    )


def _script_beat_typography(
    beat: ScriptBeat2, role: TypographyRole, *, safe_zone: str
) -> TypographyInstruction:
    return TypographyInstruction(
        role=role, text=beat.text, safe_zone=safe_zone, alignment="center", contrast_background=True
    )


def _execute_edit_plan(services: Services, renderer: FFmpegRenderer, campaign: Campaign) -> None:
    """The real EDIT_PLAN build logic, factored out so `REPAIR` can re-run it
    (e.g. after a typography/CTA-timing fix) without duplicating it.
    """
    workspace = services.storage.campaign_workspace(campaign.id)
    script = _require_output(services, campaign.id, "script-v2")
    storyboard = _require_output(services, campaign.id, "storyboard-v2")
    plan = _require_output(services, campaign.id, "asset-plan")
    assert isinstance(script, ScriptPlan)
    assert isinstance(storyboard, Storyboard2)
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
    need_by_scene_id = {scene_id: need for need in plan.assets for scene_id in need.scene_ids}

    used_assets = []
    clips = []
    edit_clips: list[EditClip2] = []
    ordered_shots = sorted(storyboard.shots, key=lambda item: item.start)
    for shot in ordered_shots:
        need = need_by_scene_id.get(shot.scene_id)
        if need is None:
            raise StageDispatchError(
                f"storyboard shot {shot.shot_id} (scene {shot.scene_id}) has no "
                "planned asset in ASSET_PLAN"
            )
        asset: Asset | None
        if need.classification == AssetClassification.CAPTURE_APP:
            asset = capture_asset
            if asset is None:
                raise StageDispatchError(
                    f"shot {shot.shot_id} needs an app capture, but none was imported"
                )
        else:
            asset = video_assets_by_scene_id.get(need.asset_id)
            if asset is None:
                raise StageDispatchError(
                    f"shot {shot.shot_id} needs generated asset {need.asset_id}, "
                    "but no matching flow_generation asset was imported"
                )
        source_path = workspace / asset.filepath
        probed = renderer.probe(source_path, expect_audio=False)
        if probed.duration_seconds + 0.05 < shot.duration:
            raise StageDispatchError(
                f"source clip for shot {shot.shot_id} is {probed.duration_seconds:.2f}s, "
                f"shorter than the required {shot.duration:.2f}s"
            )
        clips.append(
            ClipSpec(
                source=asset.filepath,
                timeline_start_seconds=shot.start,
                source_in_seconds=0,
                source_out_seconds=shot.duration,
                composition_mode=shot.composition_intent.mode.value,  # type: ignore[arg-type]
                device_frame_scale=1 - shot.composition_intent.safe_margin * 2,
                device_frame_background=shot.composition_intent.background,
                device_frame_shadow=shot.composition_intent.shadow,
                transition_in=_render_transition(shot.transition_in),
                transition_out=_render_transition(shot.transition_out),
            )
        )
        edit_clips.append(
            EditClip2(
                shot_id=shot.shot_id,
                source_asset_id=asset.id,
                trim_start=0,
                trim_end=shot.duration,
                target_duration=shot.duration,
                composition=shot.composition_intent,
                transition=shot.transition_in,
                text_layers=shot.text_intent,
                z_index=0,
            )
        )
        used_assets.append(asset)

    overlays = [
        _render_text_overlay(
            _script_beat_typography(beat, TypographyRole.CAPTION, safe_zone="bottom"),
            start=beat.start,
            end=beat.end,
        )
        for beat in sorted(script.beats, key=lambda item: item.start)
        if beat.channel == ScriptChannel.OVERLAY
    ]
    cta_beat = next((beat for beat in script.beats if beat.channel == ScriptChannel.CTA), None)
    if cta_beat is None:
        raise StageDispatchError("script produced no CTA beat; a CTA-channel beat is required")
    cta = _render_text_overlay(
        _script_beat_typography(cta_beat, TypographyRole.CTA, safe_zone="center"),
        start=cta_beat.start,
        end=cta_beat.end,
    )

    # Duplicate-text protection (CQ2, wired into planning): the same wording
    # must not needlessly repeat across on-screen overlay text and the CTA --
    # `ScriptPlan2` already rejects narration/overlay echoes at the script
    # stage; this catches the remaining overlay-vs-CTA case at edit time.
    duplicate_overlay_cta = duplicate_text_pairs(
        [("overlay", overlay.text) for overlay in overlays] + [("cta", cta.text)]
    )
    if duplicate_overlay_cta:
        raise StageDispatchError(
            f"overlay text needlessly repeats the CTA: {duplicate_overlay_cta}"
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

    edit_plan = EditPlan(
        target_duration=duration,
        pattern=_infer_editing_pattern(storyboard),
        clips=edit_clips,
        safe_area=0.05,
    )
    (workspace / "edit" / "edit_plan.v2.json").write_text(
        edit_plan.model_dump_json(indent=2) + "\n"
    )

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


def build_edit_plan_handler(services: Services, renderer: FFmpegRenderer) -> StageHandler:
    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        _execute_edit_plan(services, renderer, campaign)
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


def _latest_qc_findings(services: Services, campaign_id: str) -> list[QCFinding]:
    results = sorted(
        services.qc_results.find_by("campaign_id", campaign_id), key=lambda item: item.created_at
    )
    if not results:
        return []
    return [QCFinding.model_validate(item) for item in results[-1].metrics.get("findings", [])]


def _cq2_repair_stages(findings: list[QCFinding]) -> set[str]:
    """Recover the CQ2 repair-target mapping (`REPAIR_MAP`) from this QC run's
    BLOCKER findings, so REPAIR invalidates/reruns the right upstream stage
    instead of always blindly re-rendering. `FALSE_OR_UNSUPPORTED_CLAIM` (V1's
    Product Truth gate) is treated the same as CQ2's `UNSUPPORTED_CLAIM`.
    """
    blockers = [item for item in findings if item.severity == Severity.BLOCKER]
    stages: set[str] = set()
    for item in blockers:
        if item.code.startswith(CQ2_CODE_PREFIX):
            signal = CreativeQCSignal(item.code[len(CQ2_CODE_PREFIX) :])
            stages.update(repair_instruction(signal).stages)
        elif item.code == "FALSE_OR_UNSUPPORTED_CLAIM":
            stages.update(repair_instruction(CreativeQCSignal.UNSUPPORTED_CLAIM).stages)
    # TYPOGRAPHY/COMPOSITION aren't real pipeline stages; both are decided in EDIT_PLAN.
    return {"EDIT_PLAN" if stage in {"TYPOGRAPHY", "COMPOSITION"} else stage for stage in stages}


def build_repair_handler(
    services: Services,
    router: ProviderRouter,
    renderer: FFmpegRenderer,
    worker_jobs: WorkerJobService,
) -> StageHandler:
    """Bounded, stage-targeted repair. Which upstream stage(s) get regenerated
    is decided by `REPAIR_MAP`, recovered from the last QC run's CQ2 signals:

    - `APP_CAPTURE` (e.g. KEYBOARD_EXPOSURE): dispatch a fresh `android_capture`
      WorkerJob and wait -- storyboard/asset-plan/script are untouched, only the
      capture is redone.
    - `STRATEGY`/`SCRIPT` (e.g. UNSUPPORTED_CLAIM): regenerate strategy and
      script only. Storyboard/asset-plan/capture stay valid (their canonical IDs
      don't depend on strategy/script wording).
    - Everything else (RAW_UI_TOO_LONG, DUPLICATE_TEXT, CTA_TOO_SHORT,
      OVERLAY_COLLISION, VISUAL_STYLE_DISCONTINUITY, plain technical QC
      blockers, ...): rebuild EDIT_PLAN from the existing storyboard/script and
      re-render. Regenerating STORYBOARD itself is intentionally out of scope
      for this repair loop -- it would invalidate already-captured/generated
      assets and require re-dispatching worker jobs mid-repair; campaigns whose
      storyboard is the real defect fall through to `BLOCKED` once the repair
      budget is exhausted, same as before this stage-targeting existed.
    """

    pipeline = CreativePipeline(services)

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        workspace = services.storage.campaign_workspace(campaign.id)
        stages = _cq2_repair_stages(_latest_qc_findings(services, campaign.id))

        if "APP_CAPTURE" in stages:
            idempotency_key = f"worker:repair:{task.id}"
            job = find_existing_job(services, campaign.id, idempotency_key)
            if job is None:
                payload = app_capture_payload(services, campaign)
                job = worker_jobs.create_job(
                    campaign.id, APP_CAPTURE_CAPABILITY, payload, idempotency_key, task_id=task.id
                )
            return {
                "waiting_state": CampaignState.WAITING_FOR_WORKER,
                "reason": f"repair recapture dispatched ({job.id})",
            }

        snapshot = _require_snapshot(services, campaign.id)
        if "STRATEGY" in stages or "SCRIPT" in stages:
            _select_and_execute(
                services, router, pipeline, "creative-strategy-v2", campaign, snapshot,
                f"{task.id}:strategy", campaign.target_duration_seconds,
                additional_context={"claim_discipline": CLAIM_DISCIPLINE_INSTRUCTION},
            )
            strategy = _require_output(services, campaign.id, "creative-strategy-v2")
            assert isinstance(strategy, CreativeStrategy2)
            _select_and_execute(
                services, router, pipeline, "script-v2", campaign, snapshot,
                f"{task.id}:script", campaign.target_duration_seconds,
                additional_context={
                    "claim_discipline": CLAIM_DISCIPLINE_INSTRUCTION,
                    "strategy": strategy.model_dump(mode="json"),
                },
            )

        _execute_edit_plan(services, renderer, campaign)
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
    qc_service = QCService(
        services, renderer, policy=policy, hooks=[CreativeQCHook(services, renderer)]
    )

    def handler(campaign: Campaign, task: CampaignTask, attempt: int) -> dict[str, Any]:
        spec = _load_edit_spec(services, campaign.id)
        renders = sorted(
            services.renders.find_by("campaign_id", campaign.id), key=lambda item: item.created_at
        )
        if not renders:
            raise StageDispatchError("no render exists for QC")
        render = renders[-1]
        snapshot = _require_snapshot(services, campaign.id)
        script = _require_output(services, campaign.id, "script-v2")
        assert isinstance(script, ScriptPlan)
        claims = [beat.claim for beat in script.beats if beat.claim]
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
