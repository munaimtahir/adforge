from __future__ import annotations

import logging
from pathlib import Path

import pytest

from adforge.models import Campaign, CampaignTask, TaskState
from adforge.operations import (
    BackupManager,
    OperationsError,
    RedactingLogFilter,
    recover_startup,
    secure_runtime_permissions,
    storage_report,
)
from adforge.services import Services


def test_controlled_restart_recovers_interrupted_task(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = Services(root, Path("schemas"))
    first.initialize()
    campaign = first.campaigns.save(
        Campaign(product_id="product", name="Recovery", brief="Resume", active=True)
    )
    task = first.tasks.save(
        CampaignTask(
            campaign_id=campaign.id,
            task_type="script",
            idempotency_key="script-v1",
            state=TaskState.RUNNING,
            attempt=1,
        )
    )
    restarted = Services(root, Path("schemas"))
    restarted.initialize()
    report = recover_startup(restarted)
    assert report.active_campaigns == [campaign.id]
    assert report.recovered_tasks == 1
    recovered = restarted.tasks.get(task.id)
    assert recovered is not None and recovered.state == TaskState.PENDING


def test_backup_restore_preserves_metadata_and_campaign_files(tmp_path: Path) -> None:
    services = Services(tmp_path / "runtime", Path("schemas"))
    services.initialize()
    campaign = services.campaigns.save(
        Campaign(product_id="product", name="Backup", brief="Preserve metadata")
    )
    workspace = services.storage.campaign_workspace(campaign.id)
    (workspace / "truth" / "snapshot.json").write_text("{}")
    archive = BackupManager(services).create(
        services.storage.root / "backups" / "metadata.tar.gz"
    )
    restored_root = BackupManager.restore(archive, tmp_path / "restored")
    restored = Services(restored_root, Path("schemas"))
    restored.initialize()
    assert restored.campaigns.get(campaign.id) is not None
    assert (restored_root / "campaigns" / campaign.id / "truth" / "snapshot.json").is_file()
    assert not any((restored_root / "browser-profiles").iterdir())


def test_restore_rejects_nonempty_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "existing").write_text("keep")
    with pytest.raises(OperationsError, match="empty"):
        BackupManager.restore(tmp_path / "missing.tar.gz", target)


def test_storage_pressure_reports_without_pruning(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    retained = root / "retained.mp4"
    retained.write_bytes(b"retain")
    report = storage_report(root, pressure_free_bytes=10**30)
    assert report.pressure is True
    assert report.auto_pruned is False
    assert retained.is_file()


def test_log_redaction_and_secure_profile_permissions(tmp_path: Path) -> None:
    record = logging.LogRecord(
        "adforge",
        logging.ERROR,
        __file__,
        1,
        "Bearer secret-provider-token %s",
        ("token-fixture-secret",),
        None,
    )
    RedactingLogFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-provider-token" not in rendered
    assert "token-fixture-secret" not in rendered
    root = tmp_path / "runtime"
    secure_runtime_permissions(root)
    assert (root / "browser-profiles").stat().st_mode & 0o777 == 0o700
