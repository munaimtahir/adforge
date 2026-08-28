"""Real capability health checks for the Runtime/Provider Health surface.

Existence of a binary (`shutil.which`) or a successful `--version` call is never
sufficient evidence of READY on its own. Fast, safe checks (database, storage,
ffmpeg, chromium, local ADB) run on every request. Checks that are slow or touch a
real external subscription (Claude/Codex CLI invocation, Flow login detection) are
cached with a visible timestamp and can be force-refreshed from Settings.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from adforge.android import ADBAdapter
from adforge.models import Configuration, WorkerStatus
from adforge.providers import (
    ClaudeCodeProvider,
    CLIProvider,
    CodexCLIProvider,
    ProviderError,
    ProviderRequest,
)
from adforge.services import Services

SLOW_CHECK_TTL = timedelta(seconds=300)
CLAUDE_AUTHENTICATION_REQUIRED = "CLAUDE_AUTHENTICATION_REQUIRED"

SMOKE_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["ok"]}},
    "required": ["status"],
    "additionalProperties": False,
}
SMOKE_PROMPT = 'Return only the JSON object {"status": "ok"}. Do not explain.'


class CapabilityStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    NOT_READY = "NOT_READY"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


class CapabilityHealth(BaseModel):
    name: str
    status: CapabilityStatus
    detail: str | None = None
    checked_at: datetime
    cached: bool = False


class PlatformVerdict(StrEnum):
    PLATFORM_READY = "PLATFORM_READY"
    PLATFORM_DEGRADED = "PLATFORM_DEGRADED"
    PLATFORM_NOT_READY = "PLATFORM_NOT_READY"


PLATFORM_OWNED_CAPABILITIES = ("database", "storage", "ffmpeg", "chromium", "claude", "codex")


def check_database(services: Services) -> CapabilityHealth:
    now = datetime.now(UTC)
    try:
        with services.database.connect() as connection:
            connection.execute("SELECT 1")
        return CapabilityHealth(name="database", status=CapabilityStatus.READY, checked_at=now)
    except Exception as exc:  # noqa: BLE001 - health probe must never raise
        return CapabilityHealth(
            name="database",
            status=CapabilityStatus.NOT_READY,
            detail=str(exc)[:300],
            checked_at=now,
        )


def check_storage(services: Services) -> CapabilityHealth:
    now = datetime.now(UTC)
    try:
        probe = services.storage.root / ".health-probe"
        probe.write_text("ok")
        probe.unlink()
        return CapabilityHealth(name="storage", status=CapabilityStatus.READY, checked_at=now)
    except OSError as exc:
        return CapabilityHealth(
            name="storage",
            status=CapabilityStatus.NOT_READY,
            detail=str(exc)[:300],
            checked_at=now,
        )


def check_ffmpeg() -> CapabilityHealth:
    now = datetime.now(UTC)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return CapabilityHealth(
            name="ffmpeg",
            status=CapabilityStatus.NOT_READY,
            detail="ffmpeg/ffprobe not found",
            checked_at=now,
        )
    with tempfile.TemporaryDirectory() as workdir:
        output = Path(workdir) / "probe.png"
        try:
            subprocess.run(  # noqa: S603 - resolved executables, fixed argv
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1",
                    "-frames:v", "1", str(output),
                ],
                capture_output=True, check=True, timeout=15, text=True,
            )
            probe = subprocess.run(  # noqa: S603
                [
                    ffprobe, "-v", "error", "-show_entries", "stream=width,height",
                    "-of", "json", str(output),
                ],
                capture_output=True, check=True, timeout=15, text=True,
            )
            parsed = json.loads(probe.stdout)
            width = parsed["streams"][0]["width"]
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
            KeyError,
            ValueError,
        ) as exc:
            return CapabilityHealth(
                name="ffmpeg",
                status=CapabilityStatus.NOT_READY,
                detail=str(exc)[:300],
                checked_at=now,
            )
    return CapabilityHealth(
        name="ffmpeg",
        status=CapabilityStatus.READY,
        detail=f"probe frame width={width}",
        checked_at=now,
    )


def check_chromium() -> CapabilityHealth:
    now = datetime.now(UTC)
    executable = (
        shutil.which("chromium")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
    )
    if executable is None:
        return CapabilityHealth(
            name="chromium",
            status=CapabilityStatus.NOT_READY,
            detail="no Chromium/Chrome binary found",
            checked_at=now,
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return CapabilityHealth(
            name="chromium",
            status=CapabilityStatus.NOT_READY,
            detail="Playwright is not installed",
            checked_at=now,
        )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page()
            page.goto("about:blank", timeout=10_000)
            page.set_content("<title>adforge-probe</title>")
            title = page.title()
            browser.close()
    except Exception as exc:  # noqa: BLE001 - health probe must never raise
        return CapabilityHealth(
            name="chromium",
            status=CapabilityStatus.NOT_READY,
            detail=str(exc)[:300],
            checked_at=now,
        )
    if title != "adforge-probe":
        return CapabilityHealth(
            name="chromium",
            status=CapabilityStatus.DEGRADED,
            detail="page load did not round-trip",
            checked_at=now,
        )
    return CapabilityHealth(name="chromium", status=CapabilityStatus.READY, checked_at=now)


def _cache_key(name: str) -> str:
    return f"health:{name}"


def _read_cache(services: Services, name: str) -> CapabilityHealth | None:
    record = services.configurations.find_by("key", _cache_key(name))
    if not record:
        return None
    payload = record[0].value
    health = CapabilityHealth.model_validate(payload)
    if datetime.now(UTC) - health.checked_at > SLOW_CHECK_TTL:
        return None
    return health.model_copy(update={"cached": True})


def _write_cache(
    services: Services, existing_id: str | None, name: str, health: CapabilityHealth
) -> None:
    services.configurations.save(
        Configuration(
            id=existing_id or f"health-{name}",
            key=_cache_key(name),
            value=json.loads(health.model_dump_json()),
        )
    )


def _cached_or_live(
    services: Services, name: str, *, force: bool, live: Callable[[], CapabilityHealth]
) -> CapabilityHealth:
    """Slow/external checks never block a normal page load with a live probe.

    A cached real result (TTL-bounded) is served on GET; only an explicit
    "run diagnostics" call (`force=True`) performs the real invocation. This keeps
    Settings responsive while never claiming READY without a genuine execution.
    """
    if not force:
        cached = _read_cache(services, name)
        if cached is not None:
            return cached
        return CapabilityHealth(
            name=name,
            status=CapabilityStatus.BLOCKED,
            detail="not yet verified with a real invocation; run full diagnostics",
            checked_at=datetime.now(UTC),
        )
    existing = services.configurations.find_by("key", _cache_key(name))
    health: CapabilityHealth = live()
    _write_cache(services, existing[0].id if existing else None, name, health)
    return health


def _cli_smoke(provider_cls: type[CLIProvider], workspace: Path, name: str) -> CapabilityHealth:
    now = datetime.now(UTC)
    provider = provider_cls(workspace)
    health = provider.health()
    if not health.available:
        detail = health.detail or f"{name} executable not found"
        status_value = (
            CapabilityStatus.BLOCKED
            if "auth" in (detail or "").lower()
            else CapabilityStatus.NOT_READY
        )
        return CapabilityHealth(name=name, status=status_value, detail=detail, checked_at=now)
    request = ProviderRequest(
        request_id=f"health-{name}",
        task_type="health_check",
        capability="reasoning",
        prompt=SMOKE_PROMPT,
        output_schema=SMOKE_SCHEMA,
        timeout_seconds=60,
    )
    try:
        response = provider.execute(request)
    except ProviderError as exc:
        detail = str(exc)
        if "auth" in detail.lower() or "login" in detail.lower():
            return CapabilityHealth(
                name=name,
                status=CapabilityStatus.BLOCKED,
                detail=CLAUDE_AUTHENTICATION_REQUIRED if name == "claude" else detail[:300],
                checked_at=now,
            )
        return CapabilityHealth(
            name=name, status=CapabilityStatus.NOT_READY, detail=detail[:300], checked_at=now
        )
    if response.output.get("status") != "ok":
        return CapabilityHealth(
            name=name,
            status=CapabilityStatus.DEGRADED,
            detail="unexpected structured output",
            checked_at=now,
        )
    return CapabilityHealth(
        name=name,
        status=CapabilityStatus.READY,
        detail=f"real invocation {response.duration_ms}ms",
        checked_at=now,
    )


def check_claude(services: Services, *, force: bool = False) -> CapabilityHealth:
    workspace = services.storage.root / "temp" / "provider-smoke"
    return _cached_or_live(
        services, "claude", force=force,
        live=lambda: _cli_smoke(ClaudeCodeProvider, workspace, "claude"),
    )


def check_codex(services: Services, *, force: bool = False) -> CapabilityHealth:
    workspace = services.storage.root / "temp" / "provider-smoke"
    return _cached_or_live(
        services, "codex", force=force,
        live=lambda: _cli_smoke(CodexCLIProvider, workspace, "codex"),
    )


def check_android_capture(services: Services) -> CapabilityHealth:
    now = datetime.now(UTC)
    local = ADBAdapter().health()
    if local.available:
        return CapabilityHealth(
            name="android_capture",
            status=CapabilityStatus.READY,
            detail=f"local devices={local.devices}",
            checked_at=now,
        )
    online_worker = next(
        (
            node
            for node in services.worker_nodes.list()
            if node.status == WorkerStatus.ONLINE and "android_capture" in node.capabilities
        ),
        None,
    )
    if online_worker is not None:
        return CapabilityHealth(
            name="android_capture",
            status=CapabilityStatus.READY,
            detail=f"worker {online_worker.name} reports android_capture",
            checked_at=now,
        )
    return CapabilityHealth(
        name="android_capture",
        status=CapabilityStatus.TEMPORARILY_UNAVAILABLE,
        detail="no local device and no compatible worker online",
        checked_at=now,
    )


def check_flow_generation(services: Services, *, force: bool = False) -> CapabilityHealth:
    def live() -> CapabilityHealth:
        now = datetime.now(UTC)
        online_worker = next(
            (
                node
                for node in services.worker_nodes.list()
                if node.status == WorkerStatus.ONLINE
                and "flow_generation" in node.capabilities
            ),
            None,
        )
        if online_worker is not None:
            return CapabilityHealth(
                name="flow_generation",
                status=CapabilityStatus.READY,
                detail=f"worker {online_worker.name} reports flow_generation",
                checked_at=now,
            )
        try:
            from adforge.video_generation import PlaywrightFlowDriver
        except ImportError as exc:
            return CapabilityHealth(
                name="flow_generation",
                status=CapabilityStatus.NOT_READY,
                detail=str(exc)[:300],
                checked_at=now,
            )
        profile = services.storage.root / "browser-profiles" / "flow"
        driver = PlaywrightFlowDriver(profile)
        result = driver.health()
        if result.available:
            return CapabilityHealth(
                name="flow_generation", status=CapabilityStatus.READY, checked_at=now
            )
        status_value = (
            CapabilityStatus.BLOCKED
            if result.login_state == "LOGIN_REQUIRED"
            else CapabilityStatus.NOT_READY
        )
        return CapabilityHealth(
            name="flow_generation", status=status_value, detail=result.detail, checked_at=now
        )

    return _cached_or_live(services, "flow_generation", force=force, live=live)


def platform_status(capabilities: dict[str, CapabilityHealth]) -> PlatformVerdict:
    owned = {
        name: capabilities[name] for name in PLATFORM_OWNED_CAPABILITIES if name in capabilities
    }
    if any(health.status == CapabilityStatus.NOT_READY for health in owned.values()):
        return PlatformVerdict.PLATFORM_NOT_READY
    degraded = (CapabilityStatus.BLOCKED, CapabilityStatus.DEGRADED)
    if any(health.status in degraded for health in owned.values()):
        return PlatformVerdict.PLATFORM_DEGRADED
    return PlatformVerdict.PLATFORM_READY


def collect_capabilities(
    services: Services, *, force_slow: bool = False
) -> dict[str, CapabilityHealth]:
    return {
        "database": check_database(services),
        "storage": check_storage(services),
        "ffmpeg": check_ffmpeg(),
        "chromium": check_chromium(),
        "claude": check_claude(services, force=force_slow),
        "codex": check_codex(services, force=force_slow),
        "android_capture": check_android_capture(services),
        "flow_generation": check_flow_generation(services, force=force_slow),
    }
