from pathlib import Path

import pytest
from pydantic import ValidationError

from adforge.creative_quality import (
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

    executor = AndroidActionExecutor(Fake())
    with pytest.raises(AndroidActionError) as error:
        executor.execute(AndroidAction(action=AndroidActionType.TAP, x=1081, y=2))
    assert error.value.code.value == "INVALID_COORDINATE"


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
