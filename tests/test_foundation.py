from __future__ import annotations

from pathlib import Path

import pytest

from adforge.database import TABLES, Database
from adforge.models import (
    Asset,
    Campaign,
    CampaignTask,
    Configuration,
    HandoffPackage,
    LedgerEvent,
    Product,
    ProductTruthSnapshot,
    ProviderExecution,
    QCResult,
    Render,
)
from adforge.services import Services
from adforge.storage import UnsafePathError, contained_path, sha256_file


@pytest.fixture
def services(tmp_path: Path) -> Services:
    value = Services(tmp_path / "runtime", Path("schemas"))
    value.initialize()
    return value


def test_database_migration_is_repeatable(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "test.sqlite3")
    database.migrate()
    database.migrate()
    with database.connect() as connection:
        names = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        versions = connection.execute("SELECT COUNT(*) AS count FROM schema_migrations").fetchone()
    assert set(TABLES).issubset(names)
    assert versions["count"] == 1


def test_all_domain_repositories_support_crud(services: Services) -> None:
    product = Product(name="Fixture", slug="fixture")
    campaign = Campaign(product_id=product.id, name="Launch", brief="Show a real feature")
    records = (
        (services.products, product),
        (
            services.truth_snapshots,
            ProductTruthSnapshot(
                product_id=product.id,
                campaign_id=campaign.id,
                checksum="a" * 64,
                truth={"approved_features": ["Feature"]},
            ),
        ),
        (services.campaigns, campaign),
        (
            services.tasks,
            CampaignTask(
                campaign_id=campaign.id, task_type="strategy", idempotency_key="strategy-v1"
            ),
        ),
        (
            services.assets,
            Asset(
                campaign_id=campaign.id,
                asset_type="image",
                status="READY",
                filepath="generated/images/a.png",
            ),
        ),
        (
            services.provider_executions,
            ProviderExecution(
                campaign_id=campaign.id,
                task_id="task-1",
                provider="fixture",
                attempt=1,
                status="COMPLETE",
            ),
        ),
        (services.qc_results, QCResult(campaign_id=campaign.id, passed=True)),
        (
            services.handoffs,
            HandoffPackage(
                campaign_id=campaign.id,
                handoff_type="generation",
                status="CREATED",
                request_path="handoffs/generation",
                return_path="handoffs/generation/return",
            ),
        ),
        (
            services.ledger_events,
            LedgerEvent(
                campaign_id=campaign.id, stage="CREATED", event_type="test", status="OK"
            ),
        ),
        (
            services.renders,
            Render(
                campaign_id=campaign.id,
                status="PENDING",
                spec_path="edit/spec.json",
                output_path="renders/final/final.mp4",
                aspect_ratio="9:16",
                duration_seconds=20,
            ),
        ),
        (services.configurations, Configuration(key="production.retry", value=2)),
    )
    for repository, record in records:
        saved = repository.save(record)
        assert repository.get(record.id) == saved
        assert len(repository.list()) == 1
        assert repository.delete(record.id) is True
        assert repository.get(record.id) is None


@pytest.mark.parametrize("component", ["../escape", "..", "/absolute", "a/b", "a\\b"])
def test_path_traversal_is_rejected(tmp_path: Path, component: str) -> None:
    with pytest.raises(UnsafePathError):
        contained_path(tmp_path, component)


def test_campaign_workspaces_are_isolated_and_manifests_validate(services: Services) -> None:
    first = services.storage.campaign_workspace("campaign-one")
    second = services.storage.campaign_workspace("campaign-two")
    assert first != second
    assert services.storage.read_manifest("campaign-one") == {
        "campaign_id": "campaign-one",
        "assets": [],
    }
    with pytest.raises(ValueError, match="does not match"):
        services.storage.write_manifest(
            "campaign-one", {"campaign_id": "campaign-two", "assets": []}
        )


def test_checksum_and_append_only_ledger(services: Services) -> None:
    services.storage.campaign_workspace("campaign-one")
    source = services.storage.campaign_path("campaign-one", "brief", "brief.txt")
    source.write_text("brief")
    assert sha256_file(source) == "29a8825bd242f14386ee528d76e0e8f1e38f3c8c4047d7b2d6df7493368a17d0"
    first = LedgerEvent(
        campaign_id="campaign-one", stage="CREATED", event_type="created", status="OK"
    )
    second = LedgerEvent(
        campaign_id="campaign-one", stage="CREATED", event_type="updated", status="OK"
    )
    services.ledger.append(first)
    services.ledger.append(second)
    persisted = services.ledger.read("campaign-one")
    assert [event.id for event in persisted] == [first.id, second.id]
    assert [event.event_type for event in persisted] == ["created", "updated"]


def test_ledger_redacts_secrets(services: Services) -> None:
    services.storage.campaign_workspace("campaign-one")
    event = LedgerEvent(
        campaign_id="campaign-one",
        stage="CREATED",
        event_type="provider",
        status="FAILED",
        details={"api_token": "token-not-for-logs", "message": "Bearer abcdefghi"},
    )
    services.ledger.append(event)
    serialized = services.storage.campaign_path(
        "campaign-one", "production-ledger.jsonl"
    ).read_text()
    assert "token-not-for-logs" not in serialized
    assert "abcdefghi" not in serialized
    assert serialized.count("[REDACTED]") == 2
