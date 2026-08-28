"""Append-only campaign production ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path

from adforge.models import LedgerEvent
from adforge.repository import Repository
from adforge.security import redact
from adforge.storage import LocalStorage


class ProductionLedger:
    def __init__(
        self,
        storage: LocalStorage,
        repository: Repository[LedgerEvent] | None = None,
    ) -> None:
        self.storage = storage
        self.repository = repository

    def append(self, event: LedgerEvent) -> Path:
        event = LedgerEvent.model_validate(redact(event.model_dump(mode="python")))
        if self.repository is not None:
            event = self.repository.save(event)
        path = self.storage.campaign_path(event.campaign_id, "production-ledger.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json() + "\n"
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, line.encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return path

    def read(self, campaign_id: str) -> list[LedgerEvent]:
        path = self.storage.campaign_path(campaign_id, "production-ledger.jsonl")
        if not path.exists():
            return []
        return [
            LedgerEvent.model_validate(json.loads(line)) for line in path.read_text().splitlines()
        ]
