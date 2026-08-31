"""Creative Quality 2.0 contracts and deterministic planning helpers.

This module is intentionally independent from the V1 campaign handlers.  It gives
new callers a typed, versioned contract while old campaign records can continue to
deserialize through :mod:`adforge.creative`.
"""

from __future__ import annotations

import re
import time
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class CQModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: ClassVar[str] = "2.0"


class VisualSource(StrEnum):
    GENERATED_CINEMATIC = "GENERATED_CINEMATIC"
    ANDROID_DIRECT_CAPTURE = "ANDROID_DIRECT_CAPTURE"
    ANDROID_DEVICE_COMPOSITE = "ANDROID_DEVICE_COMPOSITE"
    STATIC_PRODUCT = "STATIC_PRODUCT"
    MOTION_GRAPHIC = "MOTION_GRAPHIC"
    CTA_END_CARD = "CTA_END_CARD"


class CompositionMode(StrEnum):
    RAW_FULL_SCREEN = "RAW_FULL_SCREEN"
    DEVICE_FRAME = "DEVICE_FRAME"


class KeyboardPolicy(StrEnum):
    REQUIRED = "REQUIRED"
    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"


class TransitionKind(StrEnum):
    HARD_CUT = "HARD_CUT"
    CROSS_DISSOLVE = "CROSS_DISSOLVE"
    PUSH_IN = "PUSH_IN"
    PULL_OUT = "PULL_OUT"
    DEVICE_REVEAL = "DEVICE_REVEAL"
    DEVICE_EXIT = "DEVICE_EXIT"
    MASKED_REVEAL = "MASKED_REVEAL"
    BLUR_MATCH = "BLUR_MATCH"
    ZOOM_MATCH = "ZOOM_MATCH"
    BACKGROUND_CONTINUATION = "BACKGROUND_CONTINUATION"


class EditingPattern(StrEnum):
    HOOK_CUT = "HOOK_CUT"
    PRODUCT_REVEAL = "PRODUCT_REVEAL"
    FEATURE_PROOF = "FEATURE_PROOF"
    BENEFIT_CUTAWAY = "BENEFIT_CUTAWAY"
    PRODUCT_CONFIRMATION = "PRODUCT_CONFIRMATION"
    CTA_HOLD = "CTA_HOLD"


class AndroidActionType(StrEnum):
    WAIT = "WAIT"
    TAP = "TAP"
    TAP_TEXT = "TAP_TEXT"
    TAP_TEXT_IF_VISIBLE = "TAP_TEXT_IF_VISIBLE"
    TAP_COORDINATE = "TAP_COORDINATE"
    TYPE_TEXT = "TYPE_TEXT"
    CLEAR_TEXT = "CLEAR_TEXT"
    SWIPE = "SWIPE"
    SCROLL_UNTIL_VISIBLE = "SCROLL_UNTIL_VISIBLE"
    BACK = "BACK"
    HOME = "HOME"
    HIDE_KEYBOARD = "HIDE_KEYBOARD"
    SHOW_KEYBOARD = "SHOW_KEYBOARD"
    SCREENSHOT = "SCREENSHOT"
    ASSERT_VISIBLE = "ASSERT_VISIBLE"
    ASSERT_NOT_VISIBLE = "ASSERT_NOT_VISIBLE"
    ASSERT_PACKAGE = "ASSERT_PACKAGE"
    HOLD = "HOLD"


class ScrollDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"


class CaptureScenarioKind(StrEnum):
    FRESH_INSTALL = "FRESH_INSTALL"
    CLEAN_STATE = "CLEAN_STATE"
    PREPOPULATED_STATE = "PREPOPULATED_STATE"
    EXISTING_TASK = "EXISTING_TASK"
    COMPLETED_TASK = "COMPLETED_TASK"
    FEATURE_SCREEN_READY = "FEATURE_SCREEN_READY"


class TypographyRole(StrEnum):
    HEADLINE = "HEADLINE"
    BENEFIT = "BENEFIT"
    CAPTION = "CAPTION"
    CTA = "CTA"
    BRAND_LOCKUP = "BRAND_LOCKUP"
    DISCLAIMER = "DISCLAIMER"


class ExportKind(StrEnum):
    MASTER = "MASTER"
    DELIVERY = "DELIVERY"


class CreativeStrategy2(CQModel):
    audience_insight: str
    audience_tension: str
    campaign_objective: str
    single_minded_proposition: str
    core_benefit: str
    reason_to_believe: str
    hook: str
    emotional_angle: str | None = None
    functional_angle: str | None = None
    visual_thesis: str
    demonstration_objective: str
    proof_moments: list[str] = Field(min_length=1)
    cta: str
    viewer_action: str
    brand_personality: list[str] = Field(min_length=1)
    pace: str
    energy: str
    shot_count_recommendation: int = Field(ge=1, le=30)
    generated_real_balance: float = Field(ge=0, le=1)
    raw_ui_tolerance: float = Field(ge=0, le=1)
    avoidances: list[str] = Field(default_factory=list)
    claim_boundaries: list[str] = Field(default_factory=list)
    audio_direction: str
    typography_direction: str
    visual_continuity_direction: str


class ScriptChannel(StrEnum):
    NARRATION = "NARRATION"
    OVERLAY = "OVERLAY"
    PRODUCT_UI = "PRODUCT_UI"
    SOUND_DESIGN = "SOUND_DESIGN"
    SILENCE = "SILENCE"
    CTA = "CTA"


class ScriptBeat2(CQModel):
    beat_id: str = Field(pattern=ID_PATTERN)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    channel: ScriptChannel
    text: str = Field(min_length=1, max_length=300)
    claim: str | None = None

    @model_validator(mode="after")
    def interval(self) -> ScriptBeat2:
        if self.end <= self.start:
            raise ValueError("script beat end must be after start")
        return self


class ScriptPlan2(CQModel):
    target_duration: float = Field(gt=0, le=60)
    beats: list[ScriptBeat2] = Field(min_length=1)
    message_hierarchy: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def no_needless_channel_echo(self) -> ScriptPlan2:
        spoken = {
            b.text.casefold().strip()
            for b in self.beats
            if b.channel in {ScriptChannel.NARRATION, ScriptChannel.CTA}
        }
        overlays = {
            b.text.casefold().strip() for b in self.beats if b.channel == ScriptChannel.OVERLAY
        }
        if spoken & overlays:
            raise ValueError("narration/CTA and overlay text needlessly repeat")
        return self


class AndroidAction(CQModel):
    action: AndroidActionType
    target_text: str | None = Field(default=None, max_length=200)
    x: int | None = Field(default=None, ge=0, le=10000)
    y: int | None = Field(default=None, ge=0, le=10000)
    x2: int | None = Field(default=None, ge=0, le=10000)
    y2: int | None = Field(default=None, ge=0, le=10000)
    text: str | None = Field(default=None, max_length=200)
    duration_ms: int = Field(default=300, ge=0, le=180000)
    timeout_seconds: float = Field(default=15, gt=0, le=300)
    retry_count: int = Field(default=0, ge=0, le=3)
    expected_state: str | None = Field(default=None, max_length=200)
    direction: ScrollDirection | None = None
    max_scrolls: int = Field(default=8, ge=1, le=20)
    settle_ms: int = Field(default=400, ge=0, le=5000)
    scroll_step_fraction: float = Field(default=0.4, gt=0, le=1)
    exact_match: bool = False
    case_sensitive: bool = False

    @model_validator(mode="after")
    def shape_matches_action(self) -> AndroidAction:
        coordinate_actions = {
            AndroidActionType.TAP_COORDINATE,
            AndroidActionType.TAP,
            AndroidActionType.HOLD,
        }
        if self.action in coordinate_actions and (self.x is None or self.y is None):
            raise ValueError("coordinate action requires x and y")
        if self.action == AndroidActionType.SWIPE and None in (self.x, self.y, self.x2, self.y2):
            raise ValueError("SWIPE requires x, y, x2, and y2")
        if (
            self.action
            in {
                AndroidActionType.TAP_TEXT,
                AndroidActionType.TAP_TEXT_IF_VISIBLE,
                AndroidActionType.ASSERT_VISIBLE,
                AndroidActionType.ASSERT_NOT_VISIBLE,
            }
            and not self.target_text
        ):
            raise ValueError("text action requires target_text")
        if self.action == AndroidActionType.SCROLL_UNTIL_VISIBLE:
            if not self.target_text:
                raise ValueError("SCROLL_UNTIL_VISIBLE requires target_text")
            if self.direction is None:
                raise ValueError("SCROLL_UNTIL_VISIBLE requires direction")
        if self.action == AndroidActionType.TYPE_TEXT and self.text is None:
            raise ValueError("TYPE_TEXT requires text")
        if (
            self.action in {AndroidActionType.WAIT, AndroidActionType.HOLD}
            and self.duration_ms == 0
        ):
            raise ValueError("timed action requires positive duration")
        for value in (self.target_text, self.text, self.expected_state):
            if value is not None and ("\x00" in value or "\n" in value or "\r" in value):
                raise ValueError("Android action text contains a control character")
        return self


class CaptureInstruction(CQModel):
    capture_id: str = Field(pattern=ID_PATTERN)
    package_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
    actions: list[AndroidAction] = Field(min_length=1)
    keyboard_policy: KeyboardPolicy = KeyboardPolicy.FORBIDDEN
    expected_filenames: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def safe_filenames(self) -> CaptureInstruction:
        for filename in self.expected_filenames:
            if not re.fullmatch(r"[A-Za-z0-9._-]+\.(?:png|mp4)", filename, re.IGNORECASE):
                raise ValueError("capture filenames must be simple PNG/MP4 names")
        if len(set(self.expected_filenames)) != len(self.expected_filenames):
            raise ValueError("capture filenames must be unique")
        return self


class CompositionInstruction(CQModel):
    mode: CompositionMode = CompositionMode.RAW_FULL_SCREEN
    scale: float = Field(default=1, gt=0, le=4)
    crop: str | None = Field(default=None, max_length=100)
    screen_mask: str | None = None
    frame_asset: str | None = None
    background: str = "#000000"
    shadow: bool = True
    safe_margin: float = Field(default=0.05, ge=0, le=0.25)

    @model_validator(mode="after")
    def frame_requirements(self) -> CompositionInstruction:
        if self.mode == CompositionMode.DEVICE_FRAME and not self.frame_asset:
            raise ValueError("DEVICE_FRAME requires a frame asset")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.background):
            raise ValueError("background must be a hex color")
        return self


class TypographyInstruction(CQModel):
    role: TypographyRole
    text: str = Field(min_length=1, max_length=300)
    font: str = "DejaVu Sans"
    weight: str = "bold"
    size: int = Field(default=64, ge=12, le=180)
    line_spacing: float = Field(default=1.0, ge=0.5, le=2)
    max_characters: int = Field(default=80, ge=1, le=300)
    alignment: str = Field(default="center", pattern=r"^(left|center|right)$")
    safe_zone: str = Field(default="center", pattern=r"^(top|center|bottom)$")
    contrast_background: bool = True
    entrance: TransitionKind = TransitionKind.HARD_CUT
    hold_seconds: float = Field(default=1, ge=0)
    exit: TransitionKind = TransitionKind.HARD_CUT


class TransitionInstruction(CQModel):
    kind: TransitionKind = TransitionKind.HARD_CUT
    duration: float = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def cut_has_no_duration(self) -> TransitionInstruction:
        if self.kind == TransitionKind.HARD_CUT and self.duration != 0:
            raise ValueError("HARD_CUT cannot have a duration")
        return self


class Shot(CQModel):
    shot_id: str = Field(pattern=ID_PATTERN)
    scene_id: str = Field(pattern=ID_PATTERN)
    order: int = Field(ge=0)
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    purpose: str
    visual_source: VisualSource
    creative_description: str
    product_feature: str | None = None
    required_product_state: str | None = None
    composition_intent: CompositionInstruction = Field(default_factory=CompositionInstruction)
    camera_intent: str = "locked"
    motion_intent: str = "none"
    text_intent: list[TypographyInstruction] = Field(default_factory=list)
    transition_in: TransitionInstruction = Field(default_factory=TransitionInstruction)
    transition_out: TransitionInstruction = Field(default_factory=TransitionInstruction)
    audio_intent: str = "silence"
    capture_instruction: CaptureInstruction | None = None
    keyboard_policy: KeyboardPolicy = KeyboardPolicy.FORBIDDEN
    qc_requirements: list[str] = Field(default_factory=list)


class Storyboard2(CQModel):
    target_duration: float = Field(gt=0, le=60)
    shots: list[Shot] = Field(min_length=1)
    override_reason: str | None = None

    @model_validator(mode="after")
    def contiguous_and_unique(self) -> Storyboard2:
        ordered = sorted(self.shots, key=lambda s: (s.start, s.order))
        if len({shot.shot_id for shot in ordered}) != len(ordered):
            raise ValueError("shot IDs must be unique")
        if len({shot.scene_id for shot in ordered}) != len(ordered):
            raise ValueError("scene IDs must be unique in the shot grammar")
        cursor = 0.0
        for shot in ordered:
            if abs(shot.start - cursor) > 0.01:
                raise ValueError("storyboard shots contain a gap or overlap")
            cursor += shot.duration
        if abs(cursor - self.target_duration) > 0.01:
            raise ValueError("storyboard shots do not fill target duration")
        return self


class EditClip2(CQModel):
    shot_id: str = Field(pattern=ID_PATTERN)
    source_asset_id: str = Field(pattern=ID_PATTERN)
    trim_start: float = Field(ge=0)
    trim_end: float = Field(gt=0)
    target_duration: float = Field(gt=0)
    composition: CompositionInstruction = Field(default_factory=CompositionInstruction)
    transition: TransitionInstruction = Field(default_factory=TransitionInstruction)
    text_layers: list[TypographyInstruction] = Field(default_factory=list)
    audio_behavior: str = "normal"
    z_index: int = 0

    @model_validator(mode="after")
    def trim_matches_duration(self) -> EditClip2:
        if self.trim_end <= self.trim_start:
            raise ValueError("clip trim end must be after trim start")
        if self.trim_end - self.trim_start + 0.01 < self.target_duration:
            raise ValueError("source trim is shorter than target duration")
        return self


class EditPlan2(CQModel):
    target_duration: float = Field(gt=0, le=60)
    pattern: EditingPattern
    clips: list[EditClip2] = Field(min_length=1)
    background_treatment: str = "solid"
    safe_area: float = Field(default=0.05, ge=0, le=0.25)

    @model_validator(mode="after")
    def clip_ids_unique(self) -> EditPlan2:
        if len({clip.shot_id for clip in self.clips}) != len(self.clips):
            raise ValueError("edit clip shot IDs must be unique")
        if abs(sum(c.target_duration for c in self.clips) - self.target_duration) > 0.01:
            raise ValueError("edit clips do not fill target duration")
        return self


class CreativeQCSignal(StrEnum):
    HOOK_TOO_WEAK = "HOOK_TOO_WEAK"
    RAW_UI_TOO_LONG = "RAW_UI_TOO_LONG"
    LOW_SHOT_VARIETY = "LOW_SHOT_VARIETY"
    KEYBOARD_EXPOSURE = "KEYBOARD_EXPOSURE"
    DUPLICATE_TEXT = "DUPLICATE_TEXT"
    EXCESSIVE_BRAND_REPETITION = "EXCESSIVE_BRAND_REPETITION"
    OVERLAY_COLLISION = "OVERLAY_COLLISION"
    CTA_TOO_SHORT = "CTA_TOO_SHORT"
    CTA_MISSING = "CTA_MISSING"
    TEXT_SAFE_ZONE = "TEXT_SAFE_ZONE"
    TEXT_UNREADABLE = "TEXT_UNREADABLE"
    VISUAL_STYLE_DISCONTINUITY = "VISUAL_STYLE_DISCONTINUITY"
    EXCESSIVE_STATIC_FRAMES = "EXCESSIVE_STATIC_FRAMES"
    INSUFFICIENT_PRODUCT_PROOF = "INSUFFICIENT_PRODUCT_PROOF"
    BRAND_INCONSISTENCY = "BRAND_INCONSISTENCY"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    AUDIO_LEVEL = "AUDIO_LEVEL"
    SHOT_DURATION_OUTLIER = "SHOT_DURATION_OUTLIER"
    VISUAL_SOURCE_IMBALANCE = "VISUAL_SOURCE_IMBALANCE"


class CreativeQCSignalResult(CQModel):
    rule_id: CreativeQCSignal
    severity: str = Field(pattern=r"^(BLOCKER|ADVISORY)$")
    measurement: float | str | None = None
    threshold: float | str | None = None
    affected_shot_ids: list[str] = Field(default_factory=list)
    evidence: str
    suggested_repair_stage: str


class CreativeQCResult2(CQModel):
    passed: bool
    signals: list[CreativeQCSignalResult] = Field(default_factory=list)
    metrics: dict[str, float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def pass_matches_blockers(self) -> CreativeQCResult2:
        blockers = [s for s in self.signals if s.severity == "BLOCKER"]
        if self.passed and blockers:
            raise ValueError("QC cannot pass with blocker signals")
        return self


class RepairInstruction(CQModel):
    signal: CreativeQCSignal
    stages: list[str] = Field(min_length=1)
    preserve_ids: bool = True
    reason: str


REPAIR_MAP: dict[CreativeQCSignal, tuple[str, ...]] = {
    CreativeQCSignal.RAW_UI_TOO_LONG: ("STORYBOARD", "EDIT_PLAN"),
    CreativeQCSignal.KEYBOARD_EXPOSURE: ("APP_CAPTURE",),
    CreativeQCSignal.DUPLICATE_TEXT: ("SCRIPT", "TYPOGRAPHY", "EDIT_PLAN"),
    CreativeQCSignal.CTA_TOO_SHORT: ("EDIT_PLAN",),
    CreativeQCSignal.CTA_MISSING: ("STORYBOARD", "EDIT_PLAN"),
    CreativeQCSignal.VISUAL_STYLE_DISCONTINUITY: ("COMPOSITION", "EDIT_PLAN"),
    CreativeQCSignal.AUDIO_LEVEL: ("AUDIO_PRODUCTION",),
    CreativeQCSignal.INSUFFICIENT_PRODUCT_PROOF: ("STORYBOARD", "APP_CAPTURE"),
    CreativeQCSignal.UNSUPPORTED_CLAIM: ("STRATEGY", "SCRIPT"),
    CreativeQCSignal.OVERLAY_COLLISION: ("TYPOGRAPHY", "EDIT_PLAN"),
}


def repair_instruction(signal: CreativeQCSignal) -> RepairInstruction:
    stages = REPAIR_MAP.get(signal, ("STORYBOARD", "EDIT_PLAN"))
    return RepairInstruction(
        signal=signal, stages=list(stages), reason=f"targeted repair for {signal.value}"
    )


class CaptureScenario(CQModel):
    kind: CaptureScenarioKind
    preparation_method: str
    initial_expectation: str
    preparation_actions: list[AndroidAction] = Field(default_factory=list)
    verification: list[AndroidAction] = Field(min_length=1)
    final_expected_state: str


class ExportProfile(CQModel):
    kind: ExportKind
    name: str = Field(pattern=ID_PATTERN)
    width: int = Field(default=1080, ge=240, le=3840)
    height: int = Field(default=1920, ge=240, le=3840)
    fps: int = Field(default=30, ge=24, le=60)
    video_codec: str = Field(default="libx264", pattern=r"^libx264$")
    audio_codec: str = Field(default="aac", pattern=r"^aac$")
    crf: int = Field(default=18, ge=0, le=51)
    preset: str = Field(default="medium", pattern=r"^[a-z0-9-]+$")
    audio_bitrate: str = Field(default="192k", pattern=r"^[0-9]+k$")
    faststart: bool = True


MASTER_EXPORT = ExportProfile(kind=ExportKind.MASTER, name="vertical-master")
DELIVERY_EXPORT = ExportProfile(
    kind=ExportKind.DELIVERY,
    name="vertical-delivery",
    crf=23,
    preset="veryfast",
    audio_bitrate="128k",
)


def validate_canonical_ids(*, canonical: set[str], referenced: set[str], label: str) -> None:
    unknown = referenced - canonical
    if unknown:
        raise ValueError(f"{label} contains unknown canonical IDs: {', '.join(sorted(unknown))}")


def validate_storyboard_asset_ids(storyboard: Storyboard2, asset_ids: set[str]) -> None:
    """Reject downstream ID mutation instead of silently remapping it."""
    referenced = {
        shot.capture_instruction.capture_id
        for shot in storyboard.shots
        if shot.capture_instruction is not None
    }
    validate_canonical_ids(canonical=asset_ids, referenced=referenced, label="storyboard")


def analyze_creative_qc(
    storyboard: Storyboard2,
    *,
    strategy: CreativeStrategy2 | None = None,
    script: ScriptPlan2 | None = None,
    max_raw_ui_fraction: float = 0.45,
    min_shot_variety: int = 2,
    min_cta_seconds: float = 1.5,
) -> CreativeQCResult2:
    """Run conservative planning-level checks; technical media QC remains V1's job."""
    signals: list[CreativeQCSignalResult] = []
    ordered = sorted(storyboard.shots, key=lambda shot: shot.start)
    raw = [shot for shot in ordered if shot.visual_source == VisualSource.ANDROID_DIRECT_CAPTURE]
    raw_fraction = sum(shot.duration for shot in raw) / storyboard.target_duration
    if raw_fraction > max_raw_ui_fraction:
        signals.append(
            CreativeQCSignalResult(
                rule_id=CreativeQCSignal.RAW_UI_TOO_LONG,
                severity="ADVISORY",
                measurement=raw_fraction,
                threshold=max_raw_ui_fraction,
                affected_shot_ids=[shot.shot_id for shot in raw],
                evidence="direct Android footage dominates the timeline",
                suggested_repair_stage="STORYBOARD",
            )
        )
    variety = len({shot.visual_source for shot in ordered})
    if variety < min_shot_variety:
        signals.append(
            CreativeQCSignalResult(
                rule_id=CreativeQCSignal.LOW_SHOT_VARIETY,
                severity="ADVISORY",
                measurement=variety,
                threshold=min_shot_variety,
                evidence="few visual source types are used",
                suggested_repair_stage="STORYBOARD",
            )
        )
    if (
        strategy is not None
        and len(strategy.proof_moments) > 0
        and not any(shot.product_feature for shot in ordered)
    ):
        signals.append(
            CreativeQCSignalResult(
                rule_id=CreativeQCSignal.INSUFFICIENT_PRODUCT_PROOF,
                severity="ADVISORY",
                measurement=0,
                threshold=1,
                evidence="strategy requires proof but no shot declares a product feature",
                suggested_repair_stage="STORYBOARD",
            )
        )
    cta_shots = [shot for shot in ordered if shot.visual_source == VisualSource.CTA_END_CARD]
    cta_duration = sum(shot.duration for shot in cta_shots)
    if (
        script is not None
        and any(beat.channel == ScriptChannel.CTA for beat in script.beats)
        and cta_duration < min_cta_seconds
    ):
        signals.append(
            CreativeQCSignalResult(
                rule_id=CreativeQCSignal.CTA_TOO_SHORT,
                severity="ADVISORY",
                measurement=cta_duration,
                threshold=min_cta_seconds,
                affected_shot_ids=[shot.shot_id for shot in cta_shots],
                evidence="CTA end card hold is shorter than the configured minimum",
                suggested_repair_stage="EDIT_PLAN",
            )
        )
    return CreativeQCResult2(
        passed=not any(signal.severity == "BLOCKER" for signal in signals),
        signals=signals,
        metrics={
            "raw_ui_fraction": raw_fraction,
            "source_variety": float(variety),
            "cta_duration": cta_duration,
        },
    )


# Public CQ2 names, kept short for schema consumers.  The suffixed definitions
# make coexistence with the established V1 ``creative`` module explicit.
CreativeStrategy = CreativeStrategy2
ScriptPlan = ScriptPlan2
Storyboard = Storyboard2
EditPlan = EditPlan2
CreativeQCResult = CreativeQCResult2


def normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def duplicate_text_pairs(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return exact normalized duplicates, retaining source labels for QC evidence."""
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []
    for label, text in values:
        normalized = normalized_text(text)
        if normalized and normalized in seen:
            duplicates.append((seen[normalized], label))
        elif normalized:
            seen[normalized] = label
    return duplicates


class ActionFailureCode(StrEnum):
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    KEYBOARD_STATE_FAILURE = "KEYBOARD_STATE_FAILURE"
    APP_NOT_FOREGROUND = "APP_NOT_FOREGROUND"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    ACTION_REJECTED = "ACTION_REJECTED"
    INVALID_COORDINATE = "INVALID_COORDINATE"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    SCROLL_TARGET_NOT_FOUND = "SCROLL_TARGET_NOT_FOUND"
    SCROLL_TIMEOUT = "SCROLL_TIMEOUT"
    SCROLL_NO_PROGRESS = "SCROLL_NO_PROGRESS"
    INVALID_SCROLL_DIRECTION = "INVALID_SCROLL_DIRECTION"
    INVALID_SCROLL_LIMIT = "INVALID_SCROLL_LIMIT"


class AndroidActionError(RuntimeError):
    def __init__(self, code: ActionFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class AndroidAdapter(Protocol):
    def tap(self, x: int, y: int) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None: ...
    def type_text(self, value: str) -> None: ...
    def screenshot(self, destination: Path) -> Path: ...
    def back(self) -> None: ...
    def home(self) -> None: ...
    def hide_keyboard(self) -> None: ...
    def show_keyboard(self) -> None: ...
    def clear_text(self) -> None: ...
    def tap_text(self, target_text: str) -> None: ...
    def assert_visible(self, target_text: str) -> bool: ...
    def assert_package(self, package_id: str) -> bool: ...
    def visible_text_digest(self) -> str: ...


class AndroidActionExecutor:
    """Execute only the finite DSL; it deliberately has no shell escape hatch."""

    def __init__(self, adapter: AndroidAdapter, *, width: int = 1080, height: int = 1920) -> None:
        self.adapter = adapter
        self.width = width
        self.height = height

    def execute(
        self, action: AndroidAction, *, screenshot_dir: Path | None = None
    ) -> dict[str, Any]:
        try:
            if action.action in {
                AndroidActionType.TAP,
                AndroidActionType.TAP_COORDINATE,
                AndroidActionType.HOLD,
            }:
                self._coordinate(action.x, action.y)
                if action.action == AndroidActionType.HOLD:
                    self.adapter.swipe(
                        action.x or 0,
                        action.y or 0,
                        action.x or 0,
                        action.y or 0,
                        action.duration_ms,
                    )
                else:
                    self.adapter.tap(action.x or 0, action.y or 0)
            elif action.action == AndroidActionType.SWIPE:
                self._coordinate(action.x, action.y)
                self._coordinate(action.x2, action.y2)
                self.adapter.swipe(
                    action.x or 0, action.y or 0, action.x2 or 0, action.y2 or 0, action.duration_ms
                )
            elif action.action == AndroidActionType.SCROLL_UNTIL_VISIBLE:
                assert action.target_text is not None
                assert action.direction is not None
                self._scroll_until_visible(action)
            elif action.action == AndroidActionType.TYPE_TEXT:
                assert action.text is not None
                self.adapter.type_text(action.text)
            elif action.action == AndroidActionType.SCREENSHOT:
                if screenshot_dir is None:
                    raise AndroidActionError(
                        ActionFailureCode.ACTION_REJECTED, "screenshot directory is required"
                    )
                filename = action.expected_state or "action-screenshot.png"
                if not re.fullmatch(r"[A-Za-z0-9._-]+\.png", filename):
                    raise AndroidActionError(
                        ActionFailureCode.ACTION_REJECTED, "unsafe screenshot filename"
                    )
                self.adapter.screenshot(screenshot_dir / filename)
            elif action.action == AndroidActionType.WAIT:
                time.sleep(action.duration_ms / 1000)
            elif action.action == AndroidActionType.BACK:
                self.adapter.back()
            elif action.action == AndroidActionType.HOME:
                self.adapter.home()
            elif action.action == AndroidActionType.HIDE_KEYBOARD:
                self.adapter.hide_keyboard()
            elif action.action == AndroidActionType.SHOW_KEYBOARD:
                self.adapter.show_keyboard()
            elif action.action == AndroidActionType.CLEAR_TEXT:
                self.adapter.clear_text()
            elif action.action == AndroidActionType.TAP_TEXT:
                assert action.target_text is not None
                self.adapter.tap_text(action.target_text)
            elif action.action == AndroidActionType.ASSERT_VISIBLE:
                assert action.target_text is not None
                if not self.adapter.assert_visible(action.target_text):
                    raise AndroidActionError(
                        ActionFailureCode.ASSERTION_FAILED,
                        f"{action.target_text!r} is not visible",
                    )
            elif action.action == AndroidActionType.ASSERT_NOT_VISIBLE:
                assert action.target_text is not None
                if self.adapter.assert_visible(action.target_text):
                    raise AndroidActionError(
                        ActionFailureCode.ASSERTION_FAILED, f"{action.target_text!r} is visible"
                    )
            elif action.action == AndroidActionType.ASSERT_PACKAGE:
                if action.expected_state and not self.adapter.assert_package(
                    action.expected_state
                ):
                    raise AndroidActionError(
                        ActionFailureCode.ASSERTION_FAILED,
                        f"package {action.expected_state!r} is not focused",
                    )
            else:
                raise AndroidActionError(
                    ActionFailureCode.UNSUPPORTED_ACTION,
                    f"unsupported action {action.action.value}",
                )
        except AndroidActionError:
            raise
        except TimeoutError as exc:
            raise AndroidActionError(ActionFailureCode.TIMEOUT, str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise AndroidActionError(ActionFailureCode.DEVICE_DISCONNECTED, str(exc)) from exc
        return {"action": action.action.value, "status": "COMPLETE"}

    def _coordinate(self, x: int | None, y: int | None) -> None:
        if x is None or y is None or x > self.width or y > self.height:
            raise AndroidActionError(
                ActionFailureCode.INVALID_COORDINATE, "coordinate is outside capture bounds"
            )

    def _scroll_endpoints(
        self, direction: AndroidActionType | str, scroll_step_fraction: float
    ) -> tuple[int, int, int]:
        """Derive a viewport-relative swipe from the reported device dimensions
        rather than a hard-coded 1080x1920 magic distance -- DOWN moves content
        up (revealing lower fields), UP moves content down."""
        x = self.width // 2
        span = int(self.height * scroll_step_fraction)
        mid = self.height // 2
        half = span // 2
        if direction == ScrollDirection.DOWN:
            y1, y2 = min(self.height - 1, mid + half), max(0, mid - half)
        else:
            y1, y2 = max(0, mid - half), min(self.height - 1, mid + half)
        return x, y1, y2

    def _scroll_until_visible(self, action: AndroidAction) -> None:
        target_text = action.target_text or ""
        deadline = time.monotonic() + action.timeout_seconds
        last_digest: str | None = None
        stall_count = 0
        scrolls_done = 0
        while True:
            if self.adapter.assert_visible(target_text):
                return
            if scrolls_done >= action.max_scrolls:
                raise AndroidActionError(
                    ActionFailureCode.SCROLL_TARGET_NOT_FOUND,
                    f"{target_text!r} not visible after {scrolls_done} scroll(s) "
                    f"{action.direction}",
                )
            if time.monotonic() >= deadline:
                raise AndroidActionError(
                    ActionFailureCode.SCROLL_TIMEOUT,
                    f"{target_text!r} not found within {action.timeout_seconds}s",
                )
            digest = self.adapter.visible_text_digest()
            if last_digest is not None and digest == last_digest:
                stall_count += 1
                if stall_count >= 2:
                    raise AndroidActionError(
                        ActionFailureCode.SCROLL_NO_PROGRESS,
                        f"viewport unchanged after {scrolls_done} scroll(s) seeking "
                        f"{target_text!r}",
                    )
            else:
                stall_count = 0
            last_digest = digest
            x, y1, y2 = self._scroll_endpoints(
                action.direction or ScrollDirection.DOWN, action.scroll_step_fraction
            )
            self.adapter.swipe(x, y1, x, y2, action.duration_ms)
            scrolls_done += 1
            time.sleep(action.settle_ms / 1000)
