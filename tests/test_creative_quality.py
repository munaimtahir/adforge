from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.creative_quality import (
    ActionFailureCode,
    AndroidAction,
    AndroidActionError,
    AndroidActionExecutor,
    AndroidActionType,
    CaptureInstruction,
    CompositionInstruction,
    CompositionMode,
    CreativeQCSignal,
    CreativeStrategy2,
    ScriptBeat2,
    ScriptChannel,
    ScriptPlan2,
    ScrollDirection,
    Shot,
    Storyboard2,
    VisualSource,
    analyze_creative_qc,
    duplicate_text_pairs,
    repair_instruction,
)


def shot(source: VisualSource, duration: float, *, start: float, shot_id: str) -> Shot:
    return Shot(
        shot_id=shot_id,
        scene_id=f"scene-{shot_id}",
        order=int(start),
        start=start,
        duration=duration,
        purpose="proof",
        visual_source=source,
        creative_description="directed product moment",
    )


def test_storyboard_v2_is_contiguous_and_rejects_unknown_capture_ids() -> None:
    capture = CaptureInstruction(
        capture_id="capture-1",
        package_id="pk.example.app",
        actions=[AndroidAction(action=AndroidActionType.WAIT, duration_ms=100)],
        expected_filenames=["shot.mp4"],
    )
    board = Storyboard2(
        target_duration=3,
        shots=[
            shot(VisualSource.ANDROID_DIRECT_CAPTURE, 1, start=0, shot_id="s1").model_copy(
                update={"capture_instruction": capture}
            ),
            shot(VisualSource.CTA_END_CARD, 2, start=1, shot_id="s2"),
        ],
    )
    with pytest.raises(ValueError, match="unknown canonical"):
        from adforge.creative_quality import validate_storyboard_asset_ids

        validate_storyboard_asset_ids(board, {"different-id"})


def test_storyboard_rejects_gaps_and_device_frame_requires_frame() -> None:
    with pytest.raises(ValidationError, match="gap or overlap"):
        Storyboard2(
            target_duration=3,
            shots=[
                shot(VisualSource.STATIC_PRODUCT, 1, start=0, shot_id="s1"),
                shot(VisualSource.CTA_END_CARD, 2, start=1.2, shot_id="s2"),
            ],
        )
    with pytest.raises(ValidationError, match="frame asset"):
        CompositionInstruction(mode=CompositionMode.DEVICE_FRAME)


def test_script_redundancy_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needlessly repeat"):
        ScriptPlan2(
            target_duration=3,
            message_hierarchy=["benefit"],
            beats=[
                ScriptBeat2(
                    beat_id="b1",
                    start=0,
                    end=1,
                    channel=ScriptChannel.NARRATION,
                    text="Organize receipts",
                ),
                ScriptBeat2(
                    beat_id="b2",
                    start=1,
                    end=2,
                    channel=ScriptChannel.OVERLAY,
                    text="Organize receipts",
                ),
            ],
        )


def test_android_dsl_rejects_injection_and_executor_has_no_shell_escape_hatch(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        AndroidAction(action=AndroidActionType.TYPE_TEXT, text="x; rm\n-rf /")

    class Fake:
        def tap(self, x: int, y: int) -> None:
            pass

        def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
            pass

        def type_text(self, value: str) -> None:
            pass

        def screenshot(self, destination: Path) -> Path:
            return destination

        def back(self) -> None:
            pass

        def home(self) -> None:
            pass

        def hide_keyboard(self) -> None:
            pass

        def show_keyboard(self) -> None:
            pass

        def clear_text(self) -> None:
            pass

        def tap_text(self, target_text: str) -> None:
            pass

        def assert_visible(self, target_text: str) -> bool:
            return True

        def assert_package(self, package_id: str) -> bool:
            return True

    executor = AndroidActionExecutor(Fake())
    with pytest.raises(AndroidActionError) as error:
        executor.execute(AndroidAction(action=AndroidActionType.TAP, x=1081, y=2))
    assert error.value.code.value == "INVALID_COORDINATE"


def test_scroll_until_visible_requires_target_text_and_direction() -> None:
    with pytest.raises(ValidationError, match="requires target_text"):
        AndroidAction(action=AndroidActionType.SCROLL_UNTIL_VISIBLE, direction=ScrollDirection.DOWN)
    with pytest.raises(ValidationError, match="requires direction"):
        AndroidAction(action=AndroidActionType.SCROLL_UNTIL_VISIBLE, target_text="Save Product")
    action = AndroidAction(
        action=AndroidActionType.SCROLL_UNTIL_VISIBLE,
        target_text="Save Product",
        direction=ScrollDirection.DOWN,
    )
    assert action.max_scrolls == 8
    assert action.scroll_step_fraction == 0.4


def test_scroll_until_visible_rejects_invalid_direction_and_limit() -> None:
    with pytest.raises(ValidationError):
        AndroidAction(
            action=AndroidActionType.SCROLL_UNTIL_VISIBLE,
            target_text="Save Product",
            direction="SIDEWAYS",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        AndroidAction(
            action=AndroidActionType.SCROLL_UNTIL_VISIBLE,
            target_text="Save Product",
            direction=ScrollDirection.DOWN,
            max_scrolls=0,
        )
    with pytest.raises(ValidationError):
        AndroidAction(
            action=AndroidActionType.SCROLL_UNTIL_VISIBLE,
            target_text="Save Product",
            direction=ScrollDirection.DOWN,
            max_scrolls=21,
        )


def test_scroll_until_visible_survives_json_round_trip() -> None:
    action = AndroidAction(
        action=AndroidActionType.SCROLL_UNTIL_VISIBLE,
        target_text="Warranty Duration",
        direction=ScrollDirection.DOWN,
        max_scrolls=6,
    )
    restored = AndroidAction.model_validate_json(action.model_dump_json())
    assert restored == action


class _ScrollFakeAdapter:
    """Fake adapter that reports a scripted sequence of screens (advancing one
    per swipe) so the executor's scroll-and-check loop can be exercised
    without any real device."""

    def __init__(self, screens: list[set[str]]) -> None:
        self.screens = screens
        self.index = 0
        self.swipes: list[tuple[int, int, int, int]] = []

    def tap(self, x: int, y: int) -> None:
        pass

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.swipes.append((x1, y1, x2, y2))
        if self.index < len(self.screens) - 1:
            self.index += 1

    def type_text(self, value: str) -> None:
        pass

    def screenshot(self, destination: Path) -> Path:
        return destination

    def back(self) -> None:
        pass

    def home(self) -> None:
        pass

    def hide_keyboard(self) -> None:
        pass

    def show_keyboard(self) -> None:
        pass

    def clear_text(self) -> None:
        pass

    def tap_text(self, target_text: str) -> None:
        pass

    def assert_visible(self, target_text: str) -> bool:
        return target_text in self.screens[self.index]

    def assert_package(self, package_id: str) -> bool:
        return True

    def visible_text_digest(self) -> str:
        return ",".join(sorted(self.screens[self.index]))


def _scroll_action(target: str, **overrides: object) -> AndroidAction:
    fields = {
        "action": AndroidActionType.SCROLL_UNTIL_VISIBLE,
        "target_text": target,
        "direction": ScrollDirection.DOWN,
        "settle_ms": 0,
        **overrides,
    }
    return AndroidAction(**fields)  # type: ignore[arg-type]


def test_executor_scroll_until_visible_zero_swipes_when_already_visible() -> None:
    adapter = _ScrollFakeAdapter([{"Warranty Duration"}])
    executor = AndroidActionExecutor(adapter)
    executor.execute(_scroll_action("Warranty Duration"))
    assert adapter.swipes == []


def test_executor_scroll_until_visible_succeeds_after_one_swipe() -> None:
    adapter = _ScrollFakeAdapter([{"Provider"}, {"Warranty Duration"}])
    executor = AndroidActionExecutor(adapter)
    executor.execute(_scroll_action("Warranty Duration"))
    assert len(adapter.swipes) == 1


def test_executor_scroll_until_visible_succeeds_after_multiple_swipes() -> None:
    adapter = _ScrollFakeAdapter([{"A"}, {"B"}, {"Save Product"}])
    executor = AndroidActionExecutor(adapter)
    executor.execute(_scroll_action("Save Product", max_scrolls=8))
    assert len(adapter.swipes) == 2


def test_executor_scroll_until_visible_reports_target_not_found() -> None:
    adapter = _ScrollFakeAdapter([{"A"}, {"B"}, {"C"}])
    executor = AndroidActionExecutor(adapter)
    with pytest.raises(AndroidActionError) as error:
        executor.execute(_scroll_action("Never Appears", max_scrolls=2))
    assert error.value.code == ActionFailureCode.SCROLL_TARGET_NOT_FOUND


def test_executor_scroll_until_visible_detects_no_progress() -> None:
    adapter = _ScrollFakeAdapter([{"Static screen"}])
    executor = AndroidActionExecutor(adapter)
    with pytest.raises(AndroidActionError) as error:
        executor.execute(_scroll_action("Warranty Duration", max_scrolls=8))
    assert error.value.code == ActionFailureCode.SCROLL_NO_PROGRESS
    assert len(adapter.swipes) == 2


def test_executor_scroll_until_visible_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import adforge.creative_quality as cq

    adapter = _ScrollFakeAdapter([{"A"}])
    executor = AndroidActionExecutor(adapter)
    clock = iter([0.0, 1000.0])
    monkeypatch.setattr(cq.time, "monotonic", lambda: next(clock))
    with pytest.raises(AndroidActionError) as error:
        executor.execute(_scroll_action("Warranty Duration"))
    assert error.value.code == ActionFailureCode.SCROLL_TIMEOUT
    assert adapter.swipes == []


def test_qc_and_repair_mapping_are_conservative() -> None:
    board = Storyboard2(
        target_duration=4,
        shots=[
            shot(VisualSource.ANDROID_DIRECT_CAPTURE, 3, start=0, shot_id="s1"),
            shot(VisualSource.CTA_END_CARD, 1, start=3, shot_id="s2"),
        ],
    )
    strategy = CreativeStrategy2(
        audience_insight="busy people",
        audience_tension="lost time",
        campaign_objective="trial",
        single_minded_proposition="stay organized",
        core_benefit="clarity",
        reason_to_believe="demo",
        hook="stop searching",
        visual_thesis="clean motion",
        demonstration_objective="show proof",
        proof_moments=["proof"],
        cta="Try it",
        viewer_action="tap",
        brand_personality=["clear"],
        pace="quick",
        energy="bright",
        shot_count_recommendation=2,
        generated_real_balance=0.5,
        raw_ui_tolerance=0.3,
        audio_direction="upbeat",
        typography_direction="bold",
        visual_continuity_direction="clean",
    )
    result = analyze_creative_qc(board, strategy=strategy, max_raw_ui_fraction=0.4)
    assert any(signal.rule_id == CreativeQCSignal.RAW_UI_TOO_LONG for signal in result.signals)
    assert repair_instruction(CreativeQCSignal.KEYBOARD_EXPOSURE).stages == ["APP_CAPTURE"]
    assert duplicate_text_pairs([("voice", "Hello!"), ("overlay", "hello")]) == [
        ("voice", "overlay")
    ]
