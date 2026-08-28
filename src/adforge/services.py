"""Application service assembly for domain repositories and storage."""

from __future__ import annotations

from pathlib import Path

from adforge.database import Database
from adforge.ledger import ProductionLedger
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
from adforge.repository import ImmutableRepository, Repository
from adforge.storage import LocalStorage


class Services:
    def __init__(self, runtime_root: Path, schema_root: Path) -> None:
        self.storage = LocalStorage(runtime_root, schema_root)
        self.database = Database(runtime_root / "data" / "adforge.sqlite3")
        self.products = Repository(self.database, "products", Product)
        self.truth_snapshots = ImmutableRepository(
            self.database, "product_truth_snapshots", ProductTruthSnapshot
        )
        self.campaigns = Repository(self.database, "campaigns", Campaign)
        self.tasks = Repository(self.database, "campaign_tasks", CampaignTask)
        self.assets = Repository(self.database, "assets", Asset)
        self.provider_executions = Repository(
            self.database, "provider_executions", ProviderExecution
        )
        self.qc_results = Repository(self.database, "qc_results", QCResult)
        self.handoffs = Repository(self.database, "handoff_packages", HandoffPackage)
        self.ledger_events = Repository(self.database, "ledger_events", LedgerEvent)
        self.renders = Repository(self.database, "renders", Render)
        self.configurations = Repository(self.database, "configurations", Configuration)
        self.ledger = ProductionLedger(self.storage, self.ledger_events)

    def initialize(self) -> None:
        self.storage.initialize()
        self.database.migrate()
