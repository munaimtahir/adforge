"""Tests for the standalone worker agent's pure/discovery logic.

These never create, launch, or install anything (no AVD creation, no emulator
launch, no sdkmanager install) — only read-only discovery against whatever
Android SDK / browser happens to be present in this environment, mirroring how
`scripts/environment_doctor.py` is tested.
"""

from __future__ import annotations

from pathlib import Path

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
