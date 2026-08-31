"""Tests for the standalone worker agent's pure/discovery logic.

These never create, launch, or install anything (no AVD creation, no emulator
launch, no sdkmanager install) — only read-only discovery against whatever
Android SDK / browser happens to be present in this environment, mirroring how
`scripts/environment_doctor.py` is tested.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts import worker_agent


def test_find_android_sdk_discovers_a_real_sdk_when_present() -> None:
    sdk = worker_agent.find_android_sdk()
    if sdk["adb"] is None:
        return  # no SDK in this environment; discovery correctly reports absence
    assert Path(sdk["adb"]).is_file()
    assert Path(sdk["emulator"]).is_file()


def test_list_avds_is_read_only_and_returns_a_list() -> None:
    sdk = worker_agent.find_android_sdk()
    if sdk["emulator"] is None:
        return
    avds = worker_agent.list_avds(sdk["emulator"])
    assert isinstance(avds, list)


def test_detect_capabilities_always_includes_synthetic_echo() -> None:
    detected = worker_agent.detect_capabilities()
    assert "synthetic_echo" in detected["capabilities"]
    assert isinstance(detected["metadata"], dict)


def test_android_capture_only_advertised_when_canonical_avd_exists() -> None:
    detected = worker_agent.detect_capabilities()
    sdk = worker_agent.find_android_sdk()
    if sdk["emulator"] is None:
        assert "android_capture" not in detected["capabilities"]
        return
    avds = worker_agent.list_avds(sdk["emulator"])
    has_canonical = worker_agent.CANONICAL_AVD_NAME in avds
    assert ("android_capture" in detected["capabilities"]) == has_canonical


def test_flow_health_reports_login_required_against_the_real_flow_site(tmp_path: Path) -> None:
    executable = worker_agent._chromium_executable()
    if executable is None or not worker_agent._playwright_available():
        return
    profile = tmp_path / "flow-profile"
    result = worker_agent.flow_health(profile, executable)
    assert result["status"] in {"LOGIN_REQUIRED", "READY", "BROWSER_UNAVAILABLE"}


def test_config_round_trips_with_restrictive_permissions(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(worker_agent, "CONFIG_PATH", tmp_path / "config.json")
    worker_agent.save_config({"base_url": "https://example.test", "token": "x", "name": "n"})
    loaded = worker_agent.load_config()
    assert loaded["base_url"] == "https://example.test"
    mode = (tmp_path / "config.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_apply_canonical_display_config_forces_documented_resolution(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "hw.device.name=pixel_6\n"
        "hw.lcd.width=1080\n"
        "hw.lcd.height=2400\n"
        "hw.initialOrientation=portrait\n"
    )

    worker_agent.apply_canonical_display_config(config_path)

    written = dict(
        line.split("=", 1) for line in config_path.read_text().splitlines() if "=" in line
    )
    assert written["hw.lcd.width"] == "1080"
    assert written["hw.lcd.height"] == "1920"
    assert written["hw.initialOrientation"] == "portrait"


def test_apply_canonical_display_config_adds_missing_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.ini"
    config_path.write_text("hw.device.name=pixel_6\n")

    worker_agent.apply_canonical_display_config(config_path)

    written = dict(
        line.split("=", 1) for line in config_path.read_text().splitlines() if "=" in line
    )
    assert written["hw.lcd.width"] == "1080"
    assert written["hw.lcd.height"] == "1920"


def _xml_for(*texts: str) -> str:
    nodes = "".join(
        f'<node text="{text}" bounds="[10,20][200,60]"/>' for text in texts
    )
    return f"<hierarchy>{nodes}</hierarchy>"


class _FakeAdb:
    """Feeds `_scroll_until_visible` a scripted sequence of UI dumps (one per
    scroll position) and records the swipe commands it issues, without ever
    touching a real device."""

    def __init__(self, screens: list[str]) -> None:
        self.screens = screens
        self.index = 0
        self.swipes: list[tuple[str, ...]] = []

    def ui_dump(self, adb_path: str, serial: str, job_dir) -> str:  # type: ignore[no-untyped-def]
        return self.screens[self.index]

    def adb(self, adb_path: str, serial: str, *args: str, timeout: float = 30, text: bool = True):  # type: ignore[no-untyped-def]
        if args[:3] == ("shell", "input", "swipe"):
            self.swipes.append(args)
            if self.index < len(self.screens) - 1:
                self.index += 1

        class _Result:
            stdout = ""

        return _Result()


def _patch_scroll_env(  # type: ignore[no-untyped-def]
    monkeypatch, fake: _FakeAdb, *, width: int = 1080, height: int = 1920
) -> None:
    monkeypatch.setattr(worker_agent, "_ui_dump", fake.ui_dump)
    monkeypatch.setattr(worker_agent, "_adb", fake.adb)
    monkeypatch.setattr(worker_agent, "_screen_size", lambda *a, **k: (width, height))
    monkeypatch.setattr(worker_agent.time, "sleep", lambda *_: None)


def test_scroll_until_visible_does_nothing_when_already_visible(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    fake = _FakeAdb([_xml_for("Warranty Duration")])
    _patch_scroll_env(monkeypatch, fake)
    worker_agent._scroll_until_visible(
        "adb", "emulator-5554", "Warranty Duration", "DOWN", 8, 15, 0, 0.4, tmp_path
    )
    assert fake.swipes == []


def test_scroll_until_visible_succeeds_after_one_swipe(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeAdb([_xml_for("Provider"), _xml_for("Warranty Duration")])
    _patch_scroll_env(monkeypatch, fake)
    worker_agent._scroll_until_visible(
        "adb", "emulator-5554", "Warranty Duration", "DOWN", 8, 15, 0, 0.4, tmp_path
    )
    assert len(fake.swipes) == 1


def test_scroll_until_visible_succeeds_after_multiple_bounded_swipes(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeAdb([_xml_for("A"), _xml_for("B"), _xml_for("Save Product")])
    _patch_scroll_env(monkeypatch, fake)
    worker_agent._scroll_until_visible(
        "adb", "emulator-5554", "Save Product", "DOWN", 8, 15, 0, 0.4, tmp_path
    )
    assert len(fake.swipes) == 2


def test_scroll_until_visible_reports_target_not_found_when_never_appears(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    fake = _FakeAdb([_xml_for("A"), _xml_for("B"), _xml_for("C")])
    _patch_scroll_env(monkeypatch, fake)
    with pytest.raises(worker_agent.AndroidDSLError) as error:
        worker_agent._scroll_until_visible(
            "adb", "emulator-5554", "Nonexistent Field", "DOWN", 2, 15, 0, 0.4, tmp_path
        )
    assert error.value.code == "SCROLL_TARGET_NOT_FOUND"


def test_scroll_until_visible_detects_no_progress_at_bottom_of_form(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    # Only one distinct screen: every swipe is a no-op because the form
    # already reached the bottom, so the viewport never actually changes.
    fake = _FakeAdb([_xml_for("Save Product does not appear here")])
    _patch_scroll_env(monkeypatch, fake)
    with pytest.raises(worker_agent.AndroidDSLError) as error:
        worker_agent._scroll_until_visible(
            "adb", "emulator-5554", "Warranty Duration", "DOWN", 8, 15, 0, 0.4, tmp_path
        )
    assert error.value.code == "SCROLL_NO_PROGRESS"
    assert len(fake.swipes) == 2


def test_scroll_until_visible_times_out_instead_of_scrolling_forever(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeAdb([_xml_for("A")])
    _patch_scroll_env(monkeypatch, fake)
    clock = iter([0.0, 1000.0])
    monkeypatch.setattr(worker_agent.time, "monotonic", lambda: next(clock))
    with pytest.raises(worker_agent.AndroidDSLError) as error:
        worker_agent._scroll_until_visible(
            "adb", "emulator-5554", "Warranty Duration", "DOWN", 8, 15, 0, 0.4, tmp_path
        )
    assert error.value.code == "SCROLL_TIMEOUT"
    assert fake.swipes == []


def test_scroll_until_visible_rejects_invalid_direction_without_touching_device(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    fake = _FakeAdb([_xml_for("A")])
    _patch_scroll_env(monkeypatch, fake)
    with pytest.raises(worker_agent.AndroidDSLError) as error:
        worker_agent._scroll_until_visible(
            "adb", "emulator-5554", "Warranty Duration", "SIDEWAYS", 8, 15, 0, 0.4, tmp_path
        )
    assert error.value.code == "INVALID_SCROLL_DIRECTION"
    assert fake.swipes == []


def test_scroll_until_visible_rejects_invalid_max_scrolls(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeAdb([_xml_for("A")])
    _patch_scroll_env(monkeypatch, fake)
    with pytest.raises(worker_agent.AndroidDSLError) as error:
        worker_agent._scroll_until_visible(
            "adb", "emulator-5554", "Warranty Duration", "DOWN", 0, 15, 0, 0.4, tmp_path
        )
    assert error.value.code == "INVALID_SCROLL_LIMIT"


def test_scroll_endpoints_are_viewport_relative_not_hard_coded() -> None:
    x, y1, y2 = worker_agent._scroll_endpoints(1080, 1920, "DOWN", 0.4)
    assert x == 540
    assert 0 <= y2 < y1 <= 1920
    x_up, y1_up, y2_up = worker_agent._scroll_endpoints(1080, 1920, "UP", 0.4)
    assert y1_up < y2_up

    # A differently sized device gets proportionally different coordinates,
    # not the same fixed pixel distance -- this is the whole point of the fix.
    x2, y1_2, y2_2 = worker_agent._scroll_endpoints(1440, 2560, "DOWN", 0.4)
    assert x2 != x
    assert (y1_2, y2_2) != (y1, y2)


def test_execute_capture_actions_dispatches_scroll_with_defaults(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    calls: list[tuple] = []

    def fake_scroll(  # type: ignore[no-untyped-def]
        adb_path, serial, target_text, direction, max_scrolls,
        timeout_seconds, settle_ms, scroll_step_fraction, job_dir,
    ):
        calls.append((target_text, direction, max_scrolls, settle_ms, scroll_step_fraction))

    monkeypatch.setattr(worker_agent, "_scroll_until_visible", fake_scroll)
    monkeypatch.setattr(worker_agent, "_keyboard_visible", lambda *a, **k: False)
    worker_agent.execute_capture_actions(
        "adb",
        "emulator-5554",
        [{"action": "SCROLL_UNTIL_VISIBLE", "target_text": "Warranty Duration"}],
        tmp_path,
    )
    assert calls == [("Warranty Duration", "DOWN", 8, 400, 0.4)]


def test_scroll_until_visible_round_trips_through_json_payload(tmp_path) -> None:  # type: ignore[no-untyped-def]
    action = {
        "action": "SCROLL_UNTIL_VISIBLE",
        "target_text": "Save Product",
        "direction": "DOWN",
        "max_scrolls": 6,
        "timeout_seconds": 20,
        "settle_ms": 250,
        "scroll_step_fraction": 0.3,
    }
    payload = json.loads(json.dumps(action))
    assert payload == action


def test_execute_capture_actions_timestamps_each_shot_boundary(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression: multiple storyboard shots chained into one android_capture
    session share a single recording.mp4. Without a real per-shot timestamp,
    EDIT_PLAN had no way to know where in that recording any shot but the
    first actually starts, and every capture-derived shot in a real render
    ended up showing the same opening seconds (the app's onboarding screen).
    """
    fake = _FakeAdb([_xml_for("irrelevant")])
    _patch_scroll_env(monkeypatch, fake)
    monkeypatch.setattr(worker_agent, "_keyboard_visible", lambda *a, **k: False)
    clock = iter([100.0, 101.5, 103.25])
    monkeypatch.setattr(worker_agent.time, "monotonic", lambda: next(clock))

    result = worker_agent.execute_capture_actions(
        "adb",
        "emulator-5554",
        [
            {"action": "WAIT", "duration_ms": 100},
            {"action": "TAP_COORDINATE", "x": 10, "y": 10},
            {"action": "WAIT", "duration_ms": 100},
        ],
        tmp_path,
        shot_boundaries=[
            {"shot_id": "shot-early", "action_start_index": 0},
            {"shot_id": "shot-later", "action_start_index": 2},
        ],
        recording_started_at=100.0,
    )

    assert result["shot_boundaries"] == [
        {"shot_id": "shot-early", "start_seconds": 0.0, "end_seconds": 1.5},
        {"shot_id": "shot-later", "start_seconds": 1.5, "end_seconds": None},
    ]


def test_execute_capture_actions_without_boundaries_reports_empty_list(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    fake = _FakeAdb([_xml_for("irrelevant")])
    _patch_scroll_env(monkeypatch, fake)
    monkeypatch.setattr(worker_agent, "_keyboard_visible", lambda *a, **k: False)

    result = worker_agent.execute_capture_actions(
        "adb", "emulator-5554", [{"action": "WAIT", "duration_ms": 0}], tmp_path
    )

    assert result["shot_boundaries"] == []


def test_warranty_vault_coverage_form_scroll_and_tap_regression(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression test for the real Warranty Vault failure this primitive
    fixes: the Coverage tab is a long scrollable form where a single blind
    fixed-distance SWIPE overshot 'Warranty Duration' and landed in Return
    Tracking. Each target now gets its own bounded SCROLL_UNTIL_VISIBLE
    immediately before its TAP_TEXT."""
    screens = [
        _xml_for("Provider", "Provider Contact", "Coverage Notes"),
        _xml_for("Warranty Duration", "Component warranty"),
        _xml_for("Return Period (days)", "Return Deadline"),
        _xml_for("Insurance", "Save Product"),
    ]
    fake = _FakeAdb(screens)
    _patch_scroll_env(monkeypatch, fake)
    monkeypatch.setattr(worker_agent, "_keyboard_visible", lambda *a, **k: False)

    result = worker_agent.execute_capture_actions(
        "adb",
        "emulator-5554",
        [
            {
                "action": "SCROLL_UNTIL_VISIBLE",
                "target_text": "Warranty Duration",
                "direction": "DOWN",
            },
            {"action": "TAP_TEXT", "target_text": "Warranty Duration"},
            {"action": "SCROLL_UNTIL_VISIBLE", "target_text": "Save Product", "direction": "DOWN"},
            {"action": "TAP_TEXT", "target_text": "Save Product"},
        ],
        tmp_path,
    )

    assert len(fake.swipes) == 3
    assert result["keyboard_visible"] is False


def test_estimate_recording_seconds_covers_real_measured_session_length() -> None:
    """Regression: a real 38-action directed capture took ~102s of actual
    wall-clock execution time, but the old duration_ms-based estimate came
    out to ~27s, so screenrecord stopped a third of the way through the
    session and later shots had no real footage at all."""
    actions = [{"action": "TAP_TEXT", "target_text": "x", "duration_ms": 300}] * 38
    assert worker_agent._estimate_recording_seconds(actions) >= 102


def test_estimate_recording_seconds_has_sane_bounds() -> None:
    assert worker_agent._estimate_recording_seconds([]) == 10
    assert worker_agent._estimate_recording_seconds([{"action": "WAIT", "duration_ms": 100}]) >= 10
    huge = [{"action": "TAP_TEXT", "target_text": "x"}] * 200
    assert worker_agent._estimate_recording_seconds(huge) == worker_agent.SCREENRECORD_MAX_SECONDS


def test_record_progress_swallows_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = worker_agent.AgentClient("https://example.test", "token")  # noqa: S106
    client.client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client.record_progress("job-1", "doing something")  # must not raise


def test_record_progress_swallows_http_status_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    client = worker_agent.AgentClient("https://example.test", "token")  # noqa: S106
    client.client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client.record_progress("job-1", "doing something")  # must not raise


def test_record_progress_posts_detail_on_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "job-1"})

    client = worker_agent.AgentClient("https://example.test", "token")  # noqa: S106
    client.client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client.record_progress("job-1", "action 3/38: TAP_TEXT Save Product")

    assert len(seen) == 1
    assert seen[0].url.path == "/api/worker/jobs/job-1/progress"
    assert json.loads(seen[0].content) == {"detail": "action 3/38: TAP_TEXT Save Product"}


def test_execute_capture_actions_calls_on_progress_for_every_action(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = _FakeAdb([_xml_for("irrelevant")])
    _patch_scroll_env(monkeypatch, fake)
    monkeypatch.setattr(worker_agent, "_keyboard_visible", lambda *a, **k: False)
    seen: list[str] = []

    worker_agent.execute_capture_actions(
        "adb",
        "emulator-5554",
        [
            {"action": "WAIT", "duration_ms": 0},
            {"action": "TAP_TEXT", "target_text": "irrelevant"},
        ],
        tmp_path,
        on_progress=seen.append,
    )

    assert seen == ["action 1/2: WAIT", "action 2/2: TAP_TEXT irrelevant"]


def test_package_and_serial_validation_reject_unsafe_values() -> None:
    worker_agent._validate_package("com.fixture.demo")
    try:
        worker_agent._validate_package("com.fixture; rm -rf /")
        raise AssertionError("expected AndroidError")
    except worker_agent.AndroidError:
        pass
    worker_agent._validate_serial("emulator-5554")
    try:
        worker_agent._validate_serial("emulator-5554; echo pwned")
        raise AssertionError("expected AndroidError")
    except worker_agent.AndroidError:
        pass
