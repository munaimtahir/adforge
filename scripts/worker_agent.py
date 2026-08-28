#!/usr/bin/env python3
"""Cross-platform AdForge worker agent: outbound-only HTTPS, no inbound port.

Usage:
    worker_agent.py configure --url https://adforge.example --token <bootstrap-token>
    worker_agent.py doctor
    worker_agent.py start [--once]

Only outbound requests are made to the configured AdForge base URL. No inbound
port is ever opened by this agent. `synthetic_echo` is the only capability with a
real handler in this phase; `android_capture` and `flow_generation` are detected
but fail any claimed job with EXTERNAL_ACTION_REQUIRED until implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - dependency documented in README
    print("httpx is required: pip install httpx", file=sys.stderr)
    raise SystemExit(1) from None

AGENT_VERSION = "0.1.0"
CONFIG_PATH = Path.home() / ".adforge-worker" / "config.json"
HEARTBEAT_INTERVAL_SECONDS = 30
POLL_INTERVAL_SECONDS = 5


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


def detect_capabilities() -> dict[str, Any]:
    capabilities = ["synthetic_echo"]
    metadata: dict[str, Any] = {}
    adb = shutil.which("adb")
    emulator = shutil.which("emulator")
    if adb and emulator:
        capabilities.append("android_capture")
        metadata["adb"] = adb
        metadata["emulator"] = emulator
    browser = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chrome")
    if browser:
        capabilities.append("flow_generation")
        metadata["browser"] = browser
    return {"capabilities": capabilities, "metadata": metadata}


def cmd_doctor(_: argparse.Namespace) -> int:
    detected = detect_capabilities()
    report = {"os": platform.system(), "architecture": platform.machine(), **detected}
    print(json.dumps(report, indent=2))
    return 0


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
        return
    client.fail(
        job["id"],
        "EXTERNAL_ACTION_REQUIRED",
        f"{capability} has no real handler on this agent build yet",
    )


def cmd_start(args: argparse.Namespace) -> int:
    config = load_config()
    client = AgentClient(config["base_url"], config["token"])
    workdir = Path.home() / ".adforge-worker" / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    last_heartbeat = 0.0
    while True:
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS or last_heartbeat == 0.0:
            client.heartbeat(config["name"])
            last_heartbeat = now
        job = client.claim()
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
