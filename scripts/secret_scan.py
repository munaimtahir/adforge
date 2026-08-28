#!/usr/bin/env python3
"""Fail when tracked source resembles common high-confidence credential formats."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "Google API key": re.compile(r"AIza[A-Za-z0-9_-]{30,}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
}


def tracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is unavailable")
    result = subprocess.run(  # noqa: S603 - resolved git, fixed read-only argv
        [git, "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return [Path(value.decode()) for value in result.stdout.split(b"\0") if value]


def scan() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        try:
            content = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f"{path}: possible {name}")
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print("\n".join(findings))
        return 1
    print("Tracked-file secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
