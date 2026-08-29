#!/usr/bin/env python3
"""Cross-platform AdForge worker agent: outbound-only HTTPS, no inbound port.

Usage:
    worker_agent.py configure --url https://adforge.example --token <bootstrap-token>
    worker_agent.py doctor
    worker_agent.py flow-login
    worker_agent.py start [--once]

Only outbound requests are made to the configured AdForge base URL. No inbound
port is ever opened by this agent, and ADB is never exposed off the machine that
runs it. Dependencies are deliberately minimal (stdlib + httpx, Playwright
optional) so this single file can run on a bare worker machine without the full
`adforge` package installed.

Capabilities:
- `synthetic_echo`      always available; proves the protocol end to end.
- `android_capture`     available when a local Android SDK (adb + emulator) is
                         discoverable. Requires the canonical AVD to exist; see
                         `doctor` for exact status and `ensure-avd` to create it.
- `flow_generation`     available when a Chromium/Chrome binary and Playwright
                         are present. Requires `flow-login` to have been run once
                         to authenticate the persistent profile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - dependency documented in README
    print("httpx is required: pip install httpx", file=sys.stderr)
    raise SystemExit(1) from None

AGENT_VERSION = "0.2.0"
WORKER_HOME = Path.home() / ".adforge-worker"
CONFIG_PATH = WORKER_HOME / "config.json"
FLOW_PROFILE_PATH = WORKER_HOME / "flow-profile"
HEARTBEAT_INTERVAL_SECONDS = 30
POLL_INTERVAL_SECONDS = 5

CANONICAL_AVD_NAME = "AdForge_API_36"
CANONICAL_API_LEVEL = 36
CANONICAL_SYSTEM_IMAGE = f"system-images;android-{CANONICAL_API_LEVEL};google_apis;x86_64"
CANONICAL_DEVICE_PROFILE = "pixel_6"
CANONICAL_RESOLUTION = "1080x1920"

FLOW_URL = "https://labs.google/fx/tools/flow"

# Gemini API (Veo) direct video generation -- a proper first-party API, preferred
# over browser-automated Flow whenever GEMINI_API_KEY is configured. Browser
# automation of Flow's sign-in is blocked by Google's own anti-automation
# detection (verified live: "This browser or app may not be secure"), which is a
# deliberate security control, not a bug to route around -- the API is the
# correct path for automated use, not a workaround for it.
VEO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
VEO_MODEL = "veo-3.1-generate-preview"
VEO_ALLOWED_DURATIONS = (4, 6, 8)
VEO_ALLOWED_ASPECT_RATIOS = {"9:16", "16:9"}
VEO_POLL_SECONDS = 10
VEO_POLL_TIMEOUT_SECONDS = 600

PACKAGE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise SystemExit(f"not configured; run `configure` first ({CONFIG_PATH})")
    return json.loads(CONFIG_PATH.read_text())


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def cmd_configure(args: argparse.Namespace) -> int:
    save_config(
        {
            "base_url": args.url.rstrip("/"),
            "token": args.token,
            "name": args.name or platform.node(),
        }
    )
    print(f"configured {CONFIG_PATH} (mode 0600)")
    return 0


# --------------------------------------------------------------------------
# Android SDK discovery (read-only; never creates or launches anything here)
# --------------------------------------------------------------------------


def _exe(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _candidate_sdk_roots() -> list[Path]:
    candidates: list[Path] = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Android" / "Sdk")
    elif system == "Darwin":
        candidates.append(Path.home() / "Library" / "Android" / "sdk")
    else:
        candidates.append(Path.home() / "Android" / "Sdk")
    return candidates


def _find_under(sdk_root: Path, *relative_candidates: str) -> str | None:
    for relative in relative_candidates:
        candidate = sdk_root / relative
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def find_android_sdk() -> dict[str, Any]:
    """Locate the Android SDK and its key tools without requiring PATH setup."""
    for sdk_root in _candidate_sdk_roots():
        if not sdk_root.is_dir():
            continue
        adb = _find_under(sdk_root, f"platform-tools/{_exe('adb')}")
        emulator = _find_under(sdk_root, f"emulator/{_exe('emulator')}")
        avdmanager = _find_under(
            sdk_root,
            f"cmdline-tools/latest/bin/{_exe('avdmanager')}",
            f"tools/bin/{_exe('avdmanager')}",
        )
        sdkmanager = _find_under(
            sdk_root,
            f"cmdline-tools/latest/bin/{_exe('sdkmanager')}",
            f"tools/bin/{_exe('sdkmanager')}",
        )
        if adb and emulator:
            return {
                "sdk_root": str(sdk_root.resolve()),
                "adb": adb,
                "emulator": emulator,
                "avdmanager": avdmanager,
                "sdkmanager": sdkmanager,
            }
    # Fall back to PATH if a full SDK layout wasn't found under a known root.
    adb = shutil.which("adb")
    emulator = shutil.which("emulator")
    if adb and emulator:
        return {
            "sdk_root": None,
            "adb": adb,
            "emulator": emulator,
            "avdmanager": shutil.which("avdmanager"),
            "sdkmanager": shutil.which("sdkmanager"),
        }
    return {"sdk_root": None, "adb": None, "emulator": None, "avdmanager": None, "sdkmanager": None}


def list_avds(emulator_path: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
        [emulator_path, "-list-avds"], capture_output=True, check=False, text=True, timeout=15
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def ensure_canonical_avd(sdk: dict[str, Any]) -> dict[str, Any]:
    """Best-effort, non-interactive creation of the canonical AVD.

    Never launches an emulator. If a system image or cmdline-tools are missing
    and cannot be installed non-interactively, returns a status the caller
    should treat as EXTERNAL_ACTION_REQUIRED rather than retrying blindly.
    """
    if not sdk.get("emulator"):
        return {"status": "NOT_READY", "detail": "Android SDK emulator not found"}
    existing = list_avds(sdk["emulator"])
    if CANONICAL_AVD_NAME in existing:
        return {"status": "READY", "detail": "canonical AVD already exists"}
    if not sdk.get("sdkmanager") or not sdk.get("avdmanager"):
        return {
            "status": "EXTERNAL_ACTION_REQUIRED",
            "detail": "avdmanager/sdkmanager not found; install Android cmdline-tools",
        }
    try:
        subprocess.run(  # noqa: S603 - resolved executable, fixed argv
            [sdk["sdkmanager"], "--licenses"],
            input="y\n" * 100,
            capture_output=True,
            text=True,
            timeout=120,
        )
        install = subprocess.run(  # noqa: S603
            [sdk["sdkmanager"], "--install", CANONICAL_SYSTEM_IMAGE],
            capture_output=True,
            check=False,
            text=True,
            timeout=1800,
        )
        if install.returncode != 0:
            return {
                "status": "EXTERNAL_ACTION_REQUIRED",
                "detail": f"system image install failed: {install.stderr[-500:]}",
            }
        create = subprocess.run(  # noqa: S603
            [
                sdk["avdmanager"], "create", "avd",
                "--name", CANONICAL_AVD_NAME,
                "--package", CANONICAL_SYSTEM_IMAGE,
                "--device", CANONICAL_DEVICE_PROFILE,
            ],
            input="no\n",
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
        if create.returncode != 0:
            return {
                "status": "EXTERNAL_ACTION_REQUIRED",
                "detail": f"avdmanager create avd failed: {create.stderr[-500:]}",
            }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"status": "EXTERNAL_ACTION_REQUIRED", "detail": str(exc)[:500]}
    apply_canonical_display_config(avd_config_path(CANONICAL_AVD_NAME))
    return {"status": "READY", "detail": "canonical AVD created"}


def avd_config_path(avd_name: str) -> Path:
    return Path.home() / ".android" / "avd" / f"{avd_name}.avd" / "config.ini"


def apply_canonical_display_config(config_path: Path) -> None:
    """Force the canonical AVD's resolution/orientation to match `CANONICAL_RESOLUTION`.

    `avdmanager create avd --device pixel_6` pulls the device profile's native skin
    resolution (1080x2400), not the documented canonical baseline -- this patches
    `hw.lcd.width`/`hw.lcd.height` afterward so the emulator actually boots at the
    resolution `device.json` (and every downstream capture) claims it did.
    """
    width, height = (int(part) for part in CANONICAL_RESOLUTION.split("x"))
    lines = config_path.read_text().splitlines()
    updates = {"hw.lcd.width": str(width), "hw.lcd.height": str(height)}
    seen = set()
    patched = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else None
        if key in updates:
            patched.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            patched.append(line)
    for key, value in updates.items():
        if key not in seen:
            patched.append(f"{key}={value}")
    config_path.write_text("\n".join(patched) + "\n")


class AndroidError(RuntimeError):
    pass


def _validate_package(package_id: str) -> None:
    if not PACKAGE_ID_PATTERN.fullmatch(package_id):
        raise AndroidError("invalid Android package ID")


def _validate_serial(serial: str) -> None:
    if not SERIAL_PATTERN.fullmatch(serial):
        raise AndroidError("invalid ADB serial")


def _adb(adb_path: str, serial: str, *args: str, timeout: float = 30, text: bool = True) -> Any:
    _validate_serial(serial)
    command = [adb_path, "-s", serial, *args]
    result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
        command, capture_output=True, check=False, text=text, timeout=timeout
    )
    return result


def _recording_has_real_duration(path: Path, expected_seconds: int) -> bool:
    """Best-effort validation that `adb shell screenrecord` actually captured video.

    Found live: on this emulator, `screenrecord` sometimes returns success and
    writes a well-formed MP4 container with a single keyframe and no duration
    atom -- ffprobe parses it fine, but it holds under a second of real content.
    Downstream EDIT_PLAN rendering needs a real ~10s clip, so catch this here
    instead of uploading a broken artifact as though the capture succeeded.
    Skips validation (returns True) if ffprobe isn't installed on this worker
    host -- this agent's dependencies are deliberately minimal.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True
    if not path.is_file() or path.stat().st_size == 0:
        return False
    result = subprocess.run(  # noqa: S603 - resolved executable, fixed argv
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, check=False, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return False
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return False
    return duration >= expected_seconds * 0.5


def start_emulator(sdk: dict[str, Any], avd_name: str) -> subprocess.Popen[bytes]:
    # `start_new_session=True` puts the emulator (and whatever qemu child it
    # forks into) in its own process group, so `stop_emulator` below can kill
    # the whole group instead of just the launcher PID -- the launcher often
    # exits before the real qemu process does, which otherwise leaves qemu
    # running and orphaned after we think we've stopped it.
    return subprocess.Popen(  # noqa: S603 - resolved executable, fixed argv
        [sdk["emulator"], "-avd", avd_name, "-no-audio", "-no-boot-anim"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_emulator(process: subprocess.Popen[bytes], *, timeout: float = 20.0) -> None:
    """Terminate an emulator started by `start_emulator` and block until it's
    actually gone. A bare `process.terminate()` without waiting is what caused
    retries to pile up competing emulator instances in production: the launcher
    process can take several seconds (or leave an orphaned qemu child) to exit,
    and the next retry's `start_emulator` call would race ahead of that.
    """
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def wait_for_boot(adb_path: str, serial: str, *, timeout_seconds: int = 180) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _adb(adb_path, serial, "shell", "getprop", "sys.boot_completed", timeout=10)
        if result.returncode == 0 and result.stdout.strip() == "1":
            return True
        time.sleep(3)
    return False


class AndroidDSLError(RuntimeError):
    pass


def _ui_dump(adb_path: str, serial: str, workdir: Path) -> str:
    _adb(adb_path, serial, "shell", "uiautomator", "dump", "/sdcard/window_dump.xml", timeout=20)
    local = workdir / "window_dump.xml"
    _adb(adb_path, serial, "pull", "/sdcard/window_dump.xml", str(local), timeout=20)
    try:
        return local.read_text(errors="replace")
    except OSError:
        return ""


def _ui_bounds_for_text(xml_text: str, target_text: str) -> tuple[int, int, int, int] | None:
    needle = target_text.strip().casefold()
    if not needle or not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 - our own uiautomator dump, not remote input
    except ET.ParseError:
        return None
    for node in root.iter("node"):
        text = node.get("text") or ""
        desc = node.get("content-desc") or ""
        if needle in text.casefold() or needle in desc.casefold():
            match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds") or "")
            if match:
                x1, y1, x2, y2 = (int(value) for value in match.groups())
                return (x1, y1, x2, y2)
    return None


def _keyboard_visible(adb_path: str, serial: str) -> bool:
    result = _adb(adb_path, serial, "shell", "dumpsys", "input_method", timeout=15)
    return bool(re.search(r"mInputShown=true", result.stdout or ""))


def _dsl_type_text(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9 ._@+-]{1,200}", value):
        raise AndroidDSLError("capture DSL text contains unsupported characters")
    return value.replace(" ", "%s")


def execute_capture_actions(
    adb_path: str, serial: str, actions: list[dict[str, Any]], job_dir: Path
) -> dict[str, Any]:
    """Real, directed execution of the CQ2 Android DSL (`AndroidActionType` in
    `adforge.creative_quality`) against a live device/emulator -- deliberate
    taps, typed text, waits, and asserted UI state, driven by the storyboard's
    `CaptureInstruction`. This replaces blind `monkey --pct-touch` event
    injection, which cannot produce directed, on-brief cinematography.
    """
    screenshots: list[str] = []
    for index, raw_action in enumerate(actions):
        action = raw_action.get("action")
        x, y = raw_action.get("x"), raw_action.get("y")
        x2, y2 = raw_action.get("x2"), raw_action.get("y2")
        text = raw_action.get("text")
        target_text = raw_action.get("target_text")
        duration_ms = int(raw_action.get("duration_ms") or 300)
        expected_state = raw_action.get("expected_state")
        if action == "WAIT":
            time.sleep(duration_ms / 1000)
        elif action in ("TAP", "TAP_COORDINATE"):
            _adb(adb_path, serial, "shell", "input", "tap", str(x), str(y), timeout=15)
        elif action == "HOLD":
            _adb(
                adb_path, serial, "shell", "input", "swipe",
                str(x), str(y), str(x), str(y), str(duration_ms), timeout=15,
            )
        elif action == "SWIPE":
            _adb(
                adb_path, serial, "shell", "input", "swipe",
                str(x), str(y), str(x2), str(y2), str(duration_ms), timeout=15,
            )
        elif action == "TYPE_TEXT":
            _adb(
                adb_path, serial, "shell", "input", "text", _dsl_type_text(text or ""), timeout=15
            )
        elif action == "CLEAR_TEXT":
            _adb(adb_path, serial, "shell", "input", "keyevent", "KEYCODE_MOVE_END", timeout=15)
            _adb(
                adb_path, serial, "shell", "input", "keyevent",
                *(["KEYCODE_DEL"] * 50), timeout=15,
            )
        elif action == "BACK":
            _adb(adb_path, serial, "shell", "input", "keyevent", "KEYCODE_BACK", timeout=15)
        elif action == "HOME":
            _adb(adb_path, serial, "shell", "input", "keyevent", "KEYCODE_HOME", timeout=15)
        elif action == "HIDE_KEYBOARD":
            if _keyboard_visible(adb_path, serial):
                _adb(adb_path, serial, "shell", "input", "keyevent", "KEYCODE_BACK", timeout=15)
                time.sleep(0.5)
        elif action == "SHOW_KEYBOARD":
            if x is not None and y is not None:
                _adb(adb_path, serial, "shell", "input", "tap", str(x), str(y), timeout=15)
        elif action == "TAP_TEXT":
            bounds = _ui_bounds_for_text(_ui_dump(adb_path, serial, job_dir), target_text or "")
            if bounds is None:
                raise AndroidDSLError(f"TAP_TEXT could not find element with text {target_text!r}")
            cx, cy = (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
            _adb(adb_path, serial, "shell", "input", "tap", str(cx), str(cy), timeout=15)
        elif action == "SCREENSHOT":
            filename = expected_state or f"action-{index}.png"
            if not re.fullmatch(r"[A-Za-z0-9._-]+\.png", filename):
                raise AndroidDSLError("unsafe screenshot filename in capture DSL")
            destination = job_dir / filename
            shot = _adb(adb_path, serial, "exec-out", "screencap", "-p", text=False, timeout=30)
            destination.write_bytes(shot.stdout)
            screenshots.append(destination.name)
        elif action == "ASSERT_VISIBLE":
            xml_text = _ui_dump(adb_path, serial, job_dir)
            if _ui_bounds_for_text(xml_text, target_text or "") is None:
                raise AndroidDSLError(f"ASSERT_VISIBLE failed: {target_text!r} not found")
        elif action == "ASSERT_NOT_VISIBLE":
            xml_text = _ui_dump(adb_path, serial, job_dir)
            if _ui_bounds_for_text(xml_text, target_text or "") is not None:
                raise AndroidDSLError(f"ASSERT_NOT_VISIBLE failed: {target_text!r} is visible")
        elif action == "ASSERT_PACKAGE":
            if expected_state:
                focus = _adb(adb_path, serial, "shell", "dumpsys", "window", "windows", timeout=15)
                if expected_state not in (focus.stdout or ""):
                    raise AndroidDSLError(f"ASSERT_PACKAGE failed: {expected_state!r} not focused")
        else:
            raise AndroidDSLError(f"unsupported capture DSL action: {action}")
    return {
        "keyboard_visible": _keyboard_visible(adb_path, serial),
        "screenshot_files": screenshots,
    }


def _kill_stray_emulators(avd_name: str) -> None:
    """Best-effort cleanup of leftover `emulator`/`qemu-system-*` processes for
    our AVD from a previous, non-graceful worker exit. Matches on the AVD name
    in the command line so this only ever touches AdForge's own emulator, not
    unrelated processes.
    """
    pkill_path = shutil.which("pkill")
    if not pkill_path:
        return
    try:
        subprocess.run(  # noqa: S603 - resolved executable, fixed argv
            [pkill_path, "-9", "-f", f"(emulator|qemu-system).*-avd[ =]{re.escape(avd_name)}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    time.sleep(1)


def run_android_capture(client: AgentClient, job: dict[str, Any], workdir: Path) -> None:
    job_id = job["id"]
    payload = job.get("payload", {})
    package_id = payload.get("package_id")
    apk_filename = payload.get("apk_filename", "source.apk")
    apk_sha256 = payload.get("apk_sha256")
    if not package_id or not apk_sha256:
        client.fail(job_id, "NON_RETRYABLE", "job payload missing package_id or apk_sha256")
        return
    try:
        _validate_package(package_id)
        FILENAME_PATTERN.fullmatch(apk_filename) or (_ for _ in ()).throw(
            AndroidError("unsafe apk filename")
        )
    except AndroidError as exc:
        client.fail(job_id, "NON_RETRYABLE", str(exc))
        return

    sdk = find_android_sdk()
    if not sdk.get("adb") or not sdk.get("emulator"):
        client.fail(job_id, "EXTERNAL_ACTION_REQUIRED", "Android SDK adb/emulator not found")
        return
    avd_status = ensure_canonical_avd(sdk)
    if avd_status["status"] != "READY":
        client.fail(job_id, "EXTERNAL_ACTION_REQUIRED", avd_status["detail"])
        return

    job_dir = workdir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    apk_path = job_dir / apk_filename
    try:
        client.download_input(job_id, apk_filename, apk_path)
    except httpx.HTTPError as exc:
        client.fail(job_id, "RETRYABLE", f"could not download APK input: {exc}")
        return
    actual_checksum = hashlib.sha256(apk_path.read_bytes()).hexdigest()
    if actual_checksum != apk_sha256:
        client.fail(job_id, "NON_RETRYABLE", "downloaded APK checksum does not match job payload")
        return

    # Belt-and-suspenders: a prior run of this worker (crashed, killed, or one
    # whose `finally` block didn't get to run) can leave a stray emulator for
    # the canonical AVD behind. Clear it before launching a fresh one so
    # retries never end up with multiple emulators competing for CPU/RAM --
    # that starvation is what caused every attempt of job dcfd906c to fail to
    # boot in time.
    _kill_stray_emulators(CANONICAL_AVD_NAME)
    process = start_emulator(sdk, CANONICAL_AVD_NAME)
    try:
        serial = "emulator-5554"
        boot_wait = subprocess.run(  # noqa: S603
            [sdk["adb"], "wait-for-device"], capture_output=True, check=False, timeout=180
        )
        if boot_wait.returncode != 0 or not wait_for_boot(sdk["adb"], serial):
            client.fail(job_id, "RETRYABLE", "emulator did not report boot completion in time")
            return
        install = _adb(sdk["adb"], serial, "install", "-r", str(apk_path), timeout=180)
        stale_install = any(
            marker in (install.stderr or "")
            for marker in ("INSTALL_FAILED_UPDATE_INCOMPATIBLE", "INSTALL_FAILED_VERSION_DOWNGRADE")
        )
        if install.returncode != 0 and stale_install:
            # A build of this package_id signed with a different key, or with a
            # higher version code, is already on the device (e.g. left over from
            # an earlier session on this persistent AVD) -- `-r` cannot replace
            # across a signature change or downgrade, that's an Android OS
            # security/versioning restriction no install flag can override.
            # Remove it and retry once.
            _adb(sdk["adb"], serial, "uninstall", package_id, timeout=60)
            install = _adb(sdk["adb"], serial, "install", "-r", str(apk_path), timeout=180)
        if install.returncode != 0:
            client.fail(job_id, "RETRYABLE", f"adb install failed: {install.stderr[-500:]}")
            return
        _adb(sdk["adb"], serial, "shell", "pm", "clear", package_id, timeout=30)
        _adb(
            sdk["adb"], serial, "shell", "monkey", "-p", package_id,
            "-c", "android.intent.category.LAUNCHER", "1", timeout=30,
        )
        time.sleep(5)

        screenshot_path = job_dir / "screenshot.png"
        screenshot = _adb(sdk["adb"], serial, "exec-out", "screencap", "-p", text=False, timeout=30)
        screenshot_path.write_bytes(screenshot.stdout)

        dsl_actions = payload.get("actions") or []
        remote_video = "/sdcard/adforge-capture.mp4"
        recording_path = job_dir / "recording.mp4"
        if dsl_actions:
            wait_seconds = sum((a.get("duration_ms") or 300) / 1000 for a in dsl_actions)
            recording_seconds = max(10, min(60, int(wait_seconds + 0.3 * len(dsl_actions)) + 4))
        else:
            recording_seconds = 10
        recording_ok = False
        dsl_result: dict[str, Any] = {}
        dsl_error: str | None = None
        for _capture_attempt in range(1, 4):
            _adb(sdk["adb"], serial, "shell", "rm", "-f", remote_video, timeout=15)
            started_at = time.monotonic()
            # `screenrecord` must be backgrounded with `nohup` -- a synchronous
            # `adb shell screenrecord ...` call *can* return a well-formed but
            # near-empty MP4 (found live: 1 frame, no duration) because Android's
            # encoder only emits a frame when SurfaceFlinger composites a change,
            # and this app's screen has nothing animating on it while idle. Real
            # on-screen action during the recording (below) is what actually
            # produces a valid, playable clip.
            _adb(
                sdk["adb"], serial, "shell", "nohup", "screenrecord", "--time-limit",
                str(recording_seconds), remote_video, ">", "/dev/null", "2>&1", "&",
                timeout=15,
            )
            time.sleep(1)
            if dsl_actions:
                # Real directed cinematography: execute the storyboard's typed
                # Android DSL action sequence instead of injecting random touches.
                try:
                    dsl_result = execute_capture_actions(
                        sdk["adb"], serial, dsl_actions, job_dir
                    )
                    dsl_error = None
                except AndroidDSLError as exc:
                    dsl_error = str(exc)
            else:
                _adb(
                    sdk["adb"], serial, "shell", "monkey", "-p", package_id,
                    "--pct-touch", "100", "--pct-motion", "0", "--pct-trackball", "0",
                    "--pct-nav", "0", "--pct-majornav", "0", "--pct-syskeys", "0",
                    "--pct-appswitch", "0", "--pct-anyevent", "0",
                    "-v", "40", "--throttle", "150", timeout=30,
                )
            remaining = recording_seconds + 2 - (time.monotonic() - started_at)
            if remaining > 0:
                time.sleep(remaining)
            _adb(sdk["adb"], serial, "pull", remote_video, str(recording_path), timeout=60)
            recording_ok = _recording_has_real_duration(recording_path, recording_seconds)
            if recording_ok and dsl_error is None:
                break
        _adb(sdk["adb"], serial, "shell", "rm", "-f", remote_video, timeout=15)
        if dsl_error is not None:
            client.fail(job_id, "RETRYABLE", f"directed capture DSL failed: {dsl_error}")
            return
        if not recording_ok:
            client.fail(
                job_id, "RETRYABLE",
                "screenrecord produced a truncated/empty recording.mp4 on every "
                f"attempt (checked with ffprobe, wanted ~{recording_seconds}s)",
            )
            return

        diagnostics = _adb(
            sdk["adb"], serial, "shell", "dumpsys", "package", package_id, timeout=30
        )
        (job_dir / "adb.log").write_text(diagnostics.stdout)

        model = _adb(sdk["adb"], serial, "shell", "getprop", "ro.product.model", timeout=15)
        sdk_version = _adb(
            sdk["adb"], serial, "shell", "getprop", "ro.build.version.sdk", timeout=15
        )
        device_info = {
            "model": model.stdout.strip(),
            "sdk_version": sdk_version.stdout.strip(),
            "avd_name": CANONICAL_AVD_NAME,
            "resolution": CANONICAL_RESOLUTION,
        }
        (job_dir / "device.json").write_text(json.dumps(device_info, indent=2))
        capture_info = {
            "job_id": job_id,
            "package_id": package_id,
            "apk_sha256": apk_sha256,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "directed": bool(dsl_actions),
            "keyboard_visible": dsl_result.get("keyboard_visible"),
        }
        (job_dir / "capture.json").write_text(json.dumps(capture_info, indent=2))

        artifacts = [screenshot_path, recording_path, job_dir / "device.json",
                     job_dir / "capture.json", job_dir / "adb.log"]
        checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
        checksums_path = job_dir / "checksums.json"
        checksums_path.write_text(json.dumps(checksums, indent=2))
        for artifact_path in [*artifacts, checksums_path]:
            client.upload_artifact(job_id, artifact_path)
        client.complete(job_id)
    finally:
        stop_emulator(process)


# --------------------------------------------------------------------------
# Flow (Playwright) capability
# --------------------------------------------------------------------------


def _chromium_executable() -> str | None:
    return (
        shutil.which("chromium")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chrome")
    )


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


FLOW_CTA_TEXT = "Create with Google Flow"


def _navigate_to_flow_app(page: Any) -> None:
    """From the labs.google marketing page, click through to the real tool.

    `FLOW_URL` (https://labs.google/fx/tools/flow) is Flow's public marketing/
    landing page, not the generation tool itself -- it never shows "Sign in" text
    or an accounts.google URL even when this profile has no real Flow access,
    which previously made both `flow_health()` and `cmd_flow_login()` falsely
    report READY/signed-in off that page alone. The primary CTA on that page is
    what actually triggers Google's OAuth flow for the tool (landing on
    accounts.google.com if unauthenticated, or the real authenticated app if
    not) -- that resulting page, not the marketing page, is what reflects real
    login status.
    """
    page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(1500)
    cta = page.get_by_text(FLOW_CTA_TEXT, exact=False)
    if cta.count() > 0:
        cta.first.click(timeout=10_000)
        page.wait_for_timeout(3000)


def flow_health(profile_path: Path, executable: str) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), executable_path=executable, headless=True
            )
            page = context.pages[0] if context.pages else context.new_page()
            _navigate_to_flow_app(page)
            login_required = "accounts.google" in page.url
            context.close()
    except Exception as exc:  # noqa: BLE001 - health probe must never raise
        return {"status": "BROWSER_UNAVAILABLE", "detail": str(exc)[:500]}
    if login_required:
        return {"status": "LOGIN_REQUIRED", "detail": "authenticate via `flow-login`"}
    return {"status": "READY", "detail": None}


FLOW_LOGIN_POLL_SECONDS = 3
FLOW_LOGIN_TIMEOUT_SECONDS = 1200


def cmd_flow_login(_: argparse.Namespace) -> int:
    """Open a real, visible browser window for one-time interactive Flow sign-in.

    Waits by polling the page for a signed-in state rather than blocking on
    `input()`: some invocation contexts (e.g. driven through another tool, or a
    non-interactive shell) close stdin immediately, which previously raised
    EOFError and tore the browser down before the user had any chance to sign in.
    """
    if not _playwright_available():
        print("Playwright is not installed: pip install playwright && playwright install chromium")
        return 1
    executable = _chromium_executable()
    if not executable:
        print("No Chrome/Chromium binary found")
        return 1
    from playwright.sync_api import sync_playwright

    FLOW_PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    print("Opening a visible browser window. Sign in to Flow in it.")
    print(
        f"Waiting up to {FLOW_LOGIN_TIMEOUT_SECONDS // 60} minutes for sign-in to "
        "complete; this closes automatically once detected."
    )
    signed_in = False
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(FLOW_PROFILE_PATH), executable_path=executable, headless=False
        )
        page = context.pages[0] if context.pages else context.new_page()
        _navigate_to_flow_app(page)
        deadline = time.monotonic() + FLOW_LOGIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(FLOW_LOGIN_POLL_SECONDS)
            try:
                login_required = "accounts.google" in page.url
            except Exception:  # noqa: BLE001, S112 - a transient navigation must not abort the poll
                continue
            if not login_required:
                signed_in = True
                break
        context.close()
    if not signed_in:
        print("Timed out waiting for sign-in. Run this command again once you're ready.")
        return 1
    print(f"Signed in. Profile saved to {FLOW_PROFILE_PATH} (mode restricted to the current user).")
    return 0


def _veo_duration(requested: float) -> int:
    """Round up to the nearest Veo-supported duration (4, 6, or 8 seconds)."""
    for duration in VEO_ALLOWED_DURATIONS:
        if requested <= duration:
            return duration
    return VEO_ALLOWED_DURATIONS[-1]


def run_veo_generation(
    client: AgentClient, job: dict[str, Any], workdir: Path, api_key: str
) -> None:
    job_id = job["id"]
    payload = job.get("payload", {})
    prompt = payload.get("prompt")
    if not prompt:
        client.fail(job_id, "NON_RETRYABLE", "job payload missing prompt")
        return
    aspect_ratio = payload.get("aspect_ratio", "9:16")
    if aspect_ratio not in VEO_ALLOWED_ASPECT_RATIOS:
        client.fail(job_id, "NON_RETRYABLE", f"Veo does not support aspect ratio {aspect_ratio!r}")
        return
    duration = _veo_duration(float(payload.get("duration_seconds", VEO_ALLOWED_DURATIONS[-1])))

    job_dir = workdir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output_path = job_dir / payload.get("output_filename", "generated.mp4")
    headers = {"x-goog-api-key": api_key}
    try:
        with httpx.Client(timeout=60) as http:
            start = http.post(
                f"{VEO_BASE_URL}/models/{VEO_MODEL}:predictLongRunning",
                headers=headers,
                json={
                    "instances": [{"prompt": prompt}],
                    "parameters": {
                        "aspectRatio": aspect_ratio,
                        "durationSeconds": str(duration),
                        "resolution": "720p",
                        "numberOfVideos": 1,
                        "personGeneration": "allow_adult",
                    },
                },
            )
            start.raise_for_status()
            operation_name = start.json()["name"]
            deadline = time.monotonic() + VEO_POLL_TIMEOUT_SECONDS
            operation: dict[str, Any] = {}
            completed = False
            while time.monotonic() < deadline:
                time.sleep(VEO_POLL_SECONDS)
                poll = http.get(f"{VEO_BASE_URL}/{operation_name}", headers=headers)
                poll.raise_for_status()
                operation = poll.json()
                if operation.get("done"):
                    completed = True
                    break
            if not completed:
                client.fail(job_id, "RETRYABLE", "Veo generation timed out waiting for completion")
                return
            if "error" in operation:
                client.fail(job_id, "RETRYABLE", f"Veo generation failed: {operation['error']}")
                return
            samples = (
                operation.get("response", {})
                .get("generateVideoResponse", {})
                .get("generatedSamples", [])
            )
            if not samples:
                client.fail(job_id, "RETRYABLE", "Veo operation completed with no video samples")
                return
            video_uri = samples[0]["video"]["uri"]
            download = http.get(video_uri, headers=headers, follow_redirects=True)
            download.raise_for_status()
            output_path.write_bytes(download.content)
    except httpx.HTTPStatusError as exc:
        detail = f"Veo API error: {exc.response.status_code} {exc.response.text[:300]}"
        client.fail(job_id, "RETRYABLE", detail)
        return
    except httpx.HTTPError as exc:
        client.fail(job_id, "RETRYABLE", f"Veo request failed: {str(exc)[:300]}")
        return

    if not output_path.is_file() or output_path.stat().st_size == 0:
        client.fail(job_id, "RETRYABLE", "Veo download did not produce a non-empty file")
        return
    checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
    provenance = {
        "job_id": job_id,
        "prompt": prompt,
        "provider": "gemini-veo-api",
        "model": VEO_MODEL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checksum": checksum,
    }
    provenance_path = job_dir / "provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2))
    client.upload_artifact(job_id, output_path)
    client.upload_artifact(job_id, provenance_path)
    client.complete(job_id)


# --------------------------------------------------------------------------
# Capability detection / doctor
# --------------------------------------------------------------------------


def detect_capabilities() -> dict[str, Any]:
    """`flow_generation` is only ever offered via the real Gemini (Veo) API.

    Browser-automated Flow generation (filling its prompt box, clicking Generate)
    used to be attempted here as a fallback whenever a Chromium/Playwright profile
    was configured. Removed entirely -- verified live that Google blocks
    Playwright-controlled sign-in ("This browser or app may not be secure") and,
    separately, that a stale-CTA navigation issue made the generation attempt fill
    a hidden reCAPTCHA field and time out -- three independent, deliberate
    anti-automation layers is a firm signal to stop, not route around. Without a
    configured key, flow_generation jobs are simply never claimed by this worker
    and sit `PENDING` for manual completion via the AdForge web UI instead of
    burning through 3 automated attempts that cannot succeed.
    """
    capabilities = ["synthetic_echo"]
    metadata: dict[str, Any] = {}

    sdk = find_android_sdk()
    if sdk.get("adb") and sdk.get("emulator"):
        avds = list_avds(sdk["emulator"]) if sdk.get("emulator") else []
        metadata["android_sdk_root"] = sdk.get("sdk_root")
        metadata["android_avds"] = avds
        if CANONICAL_AVD_NAME in avds:
            capabilities.append("android_capture")

    has_veo_key = bool(os.environ.get("GEMINI_API_KEY"))
    metadata["gemini_api_key_configured"] = has_veo_key
    if has_veo_key:
        capabilities.append("flow_generation")

    return {"capabilities": capabilities, "metadata": metadata}


def cmd_doctor(_: argparse.Namespace) -> int:
    sdk = find_android_sdk()
    avds = list_avds(sdk["emulator"]) if sdk.get("emulator") else []
    report: dict[str, Any] = {
        "os": platform.system(),
        "architecture": platform.machine(),
        "android_sdk": sdk,
        "android_avds": avds,
        "canonical_avd_ready": CANONICAL_AVD_NAME in avds,
        "chromium": _chromium_executable(),
        "playwright_installed": _playwright_available(),
        "flow_profile_configured": FLOW_PROFILE_PATH.is_dir(),
        **detect_capabilities(),
    }
    print(json.dumps(report, indent=2))
    return 0


def cmd_ensure_avd(_: argparse.Namespace) -> int:
    sdk = find_android_sdk()
    result = ensure_canonical_avd(sdk)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "READY" else 1


class AgentClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.client = httpx.Client(
            base_url=base_url, headers={"authorization": f"Bearer {token}"}, timeout=30
        )

    def heartbeat(self, name: str) -> dict[str, Any]:
        detected = detect_capabilities()
        response = self.client.post(
            "/api/worker/heartbeat",
            json={
                "agent_version": AGENT_VERSION,
                "os": platform.system(),
                "architecture": platform.machine(),
                "capabilities": detected["capabilities"],
                "metadata": {"name": name, **detected["metadata"]},
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def claim(self) -> dict[str, Any] | None:
        response = self.client.post("/api/worker/jobs/claim")
        response.raise_for_status()
        return response.json().get("job")

    def lease(self, job_id: str) -> None:
        self.client.post(f"/api/worker/jobs/{job_id}/lease").raise_for_status()

    def download_input(self, job_id: str, filename: str, destination: Path) -> None:
        response = self.client.get(f"/api/worker/jobs/{job_id}/inputs/{filename}")
        response.raise_for_status()
        destination.write_bytes(response.content)

    def upload_artifact(self, job_id: str, path: Path) -> None:
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        files = {"file": (path.name, content, "application/octet-stream")}
        response = self.client.post(
            f"/api/worker/jobs/{job_id}/artifacts", data={"checksum": checksum}, files=files
        )
        response.raise_for_status()

    def complete(self, job_id: str) -> None:
        self.client.post(f"/api/worker/jobs/{job_id}/complete").raise_for_status()

    def fail(self, job_id: str, error_class: str, detail: str) -> None:
        self.client.post(
            f"/api/worker/jobs/{job_id}/fail",
            json={"error_class": error_class, "detail": detail},
        ).raise_for_status()


def run_synthetic_echo(client: AgentClient, job: dict[str, Any], workdir: Path) -> None:
    payload = job.get("payload", {})
    output = workdir / f"{job['id']}-echo.json"
    output.write_text(json.dumps({"echo": payload}, sort_keys=True))
    client.upload_artifact(job["id"], output)
    client.complete(job["id"])


def run_job(client: AgentClient, job: dict[str, Any], workdir: Path) -> None:
    client.lease(job["id"])
    capability = job["capability"]
    if capability == "synthetic_echo":
        run_synthetic_echo(client, job, workdir)
    elif capability == "android_capture":
        run_android_capture(client, job, workdir)
    elif capability == "flow_generation":
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            run_veo_generation(client, job, workdir, api_key)
        else:
            # Not reached in practice -- detect_capabilities() never advertises
            # flow_generation without a key, so this worker never claims one.
            # Guarded here only so a job can't silently hang if that ever changes.
            client.fail(
                job["id"],
                "EXTERNAL_ACTION_REQUIRED",
                "no GEMINI_API_KEY configured; complete this job manually via "
                "the AdForge web UI instead",
            )
    else:
        client.fail(
            job["id"], "EXTERNAL_ACTION_REQUIRED", f"{capability} has no handler on this agent"
        )


def cmd_start(args: argparse.Namespace) -> int:
    config = load_config()
    client = AgentClient(config["base_url"], config["token"])
    workdir = WORKER_HOME / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    last_heartbeat = 0.0
    while True:
        now = time.monotonic()
        try:
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS or last_heartbeat == 0.0:
                client.heartbeat(config["name"])
                last_heartbeat = now
            job = client.claim()
        except httpx.HTTPError as exc:
            # A transient network blip (read/connect timeout, DNS hiccup) here used
            # to crash the whole poll loop and silently stop the worker -- found
            # live: this left dispatched WorkerJobs sitting PENDING indefinitely
            # with nothing to notice or restart the process. Log and keep polling
            # instead; the next iteration retries on its own.
            print(f"poll cycle transport error: {exc}", file=sys.stderr)
            if args.once:
                return 0
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if job is not None:
            try:
                run_job(client, job, workdir)
            except httpx.HTTPError as exc:
                print(f"job {job['id']} transport error: {exc}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(POLL_INTERVAL_SECONDS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--url", required=True)
    configure.add_argument("--token", required=True)
    configure.add_argument("--name")
    configure.set_defaults(func=cmd_configure)

    doctor = subparsers.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    ensure_avd = subparsers.add_parser("ensure-avd")
    ensure_avd.set_defaults(func=cmd_ensure_avd)

    flow_login = subparsers.add_parser("flow-login")
    flow_login.set_defaults(func=cmd_flow_login)

    start = subparsers.add_parser("start")
    start.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    start.set_defaults(func=cmd_start)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
