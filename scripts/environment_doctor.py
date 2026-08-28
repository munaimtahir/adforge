#!/usr/bin/env python3
"""Emit a credential-safe inventory of AdForge runtime capabilities."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capability:
    available: bool
    executable: str | None = None
    version: str | None = None
    detail: str | None = None


def command_capability(name: str, *version_args: str) -> Capability:
    executable = shutil.which(name)
    if executable is None:
        return Capability(available=False)
    version = None
    if version_args:
        try:
            result = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
                [executable, *version_args],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
            output = (result.stdout or result.stderr).splitlines()
            version = output[0].strip() if output else None
        except (OSError, subprocess.TimeoutExpired):
            version = "installed; version probe failed"
    return Capability(available=True, executable=executable, version=version)


def adb_capability() -> Capability:
    base = command_capability("adb", "version")
    if not base.available:
        return base
    try:
        result = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
            [base.executable or "adb", "devices"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        devices = [line for line in result.stdout.splitlines()[1:] if line.strip()]
        return Capability(True, base.executable, base.version, f"devices={len(devices)}")
    except (OSError, subprocess.TimeoutExpired):
        return Capability(True, base.executable, base.version, "device probe failed")


def emulator_capability() -> Capability:
    base = command_capability("emulator", "-version")
    if not base.available:
        return base
    try:
        result = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
            [base.executable or "emulator", "-list-avds"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        avds = [line for line in result.stdout.splitlines() if line.strip()]
        return Capability(True, base.executable, base.version, f"avds={len(avds)}")
    except (OSError, subprocess.TimeoutExpired):
        return Capability(True, base.executable, base.version, "AVD probe failed")


def build_report() -> dict[str, object]:
    chromium = command_capability("chromium", "--version")
    if not chromium.available:
        chromium = command_capability("google-chrome", "--version")
    capabilities = {
        "python": command_capability("python3", "--version"),
        "git": command_capability("git", "--version"),
        "node": command_capability("node", "--version"),
        "npm": command_capability("npm", "--version"),
        "ffmpeg": command_capability("ffmpeg", "-version"),
        "ffprobe": command_capability("ffprobe", "-version"),
        "chromium": chromium,
        "adb": adb_capability(),
        "emulator": emulator_capability(),
        "claude": command_capability("claude", "--version"),
        "codex": command_capability("codex", "--version"),
        "caddy": command_capability("caddy", "version"),
    }
    required = ("python", "git")
    return {
        "platform": platform.platform(),
        "python_runtime": sys.version.split()[0],
        "capabilities": {name: asdict(value) for name, value in capabilities.items()},
        "required_ready": all(capabilities[name].available for name in required),
        "runtime_root": os.environ.get("ADFORGE_DATA_ROOT", ".adforge-runtime"),
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["required_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
