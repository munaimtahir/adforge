"""Recovery, backup/restore, storage reporting, permissions, and safe logging."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import tarfile
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from adforge.orchestrator import Orchestrator
from adforge.security import redact, redact_text
from adforge.services import Services


class OperationsError(RuntimeError):
    pass


class StorageReport(BaseModel):
    root: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int
    root_bytes: int
    pressure: bool
    auto_pruned: bool = False


class RecoveryReport(BaseModel):
    active_campaigns: list[str] = Field(default_factory=list)
    recovered_tasks: int = 0


def storage_report(root: Path, *, pressure_free_bytes: int = 5 * 1024**3) -> StorageReport:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    root_bytes = sum(
        path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    )
    return StorageReport(
        root=root,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        root_bytes=root_bytes,
        pressure=usage.free < pressure_free_bytes,
        auto_pruned=False,
    )


def recover_startup(services: Services) -> RecoveryReport:
    before = {
        task.id: task.state for task in services.tasks.list()
    }
    active = Orchestrator(services).recover()
    after = {task.id: task.state for task in services.tasks.list()}
    recovered = sum(1 for task_id, state in before.items() if after.get(task_id) != state)
    return RecoveryReport(
        active_campaigns=[campaign.id for campaign in active], recovered_tasks=recovered
    )


class BackupManager:
    INCLUDED_DIRECTORIES = ("products", "campaigns", "exports")

    def __init__(self, services: Services) -> None:
        self.services = services

    def create(self, destination: Path) -> Path:
        destination = destination.resolve()
        if destination.suffixes[-2:] != [".tar", ".gz"]:
            raise OperationsError("backup destination must end with .tar.gz")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = self.services.storage.root / "temp" / f"backup-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, mode=0o700)
        try:
            data_dir = staging / "data"
            data_dir.mkdir()
            with self.services.database.connect() as source:
                target = sqlite3.connect(data_dir / "adforge.sqlite3")
                try:
                    source.backup(target)
                finally:
                    target.close()
            for directory in self.INCLUDED_DIRECTORIES:
                source_dir = self.services.storage.root / directory
                if source_dir.exists():
                    shutil.copytree(source_dir, staging / directory)
            with tarfile.open(destination, "w:gz") as archive:
                for path in sorted(staging.iterdir()):
                    archive.add(path, arcname=path.name, recursive=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        os.chmod(destination, 0o600)
        return destination

    @staticmethod
    def restore(archive_path: Path, target_root: Path) -> Path:
        archive_path = archive_path.resolve()
        target_root = target_root.resolve()
        if target_root.exists() and any(target_root.iterdir()):
            raise OperationsError("restore target must be empty")
        target_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise OperationsError("backup contains an unsafe path")
            archive.extractall(target_root, filter="data")
        database = target_root / "data" / "adforge.sqlite3"
        if not database.is_file():
            raise OperationsError("restored backup is missing metadata database")
        return target_root


class RedactingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(item) for item in record.args)
        return True


def secure_runtime_permissions(root: Path) -> None:
    root = root.resolve()
    for relative in (".", "data", "browser-profiles", "logs", "backups", "temp"):
        path = (root / relative).resolve()
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)


def run_bounded(command: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    if not command or not Path(command[0]).is_absolute():
        raise OperationsError("bounded command requires an absolute executable path")
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise OperationsError("invalid process timeout")
    try:
        return subprocess.run(  # noqa: S603 - absolute argv, shell disabled
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise OperationsError(f"process timed out after {timeout_seconds:g}s") from exc
