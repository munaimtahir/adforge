"""Repository boundary for typed domain records."""

from __future__ import annotations

import builtins
import json
from typing import TypeVar

from adforge.database import TABLES, Database
from adforge.models import Record, utc_now

RecordT = TypeVar("RecordT", bound=Record)


class Repository[RecordT: Record]:
    def __init__(self, database: Database, table: str, model: type[RecordT]) -> None:
        if table not in TABLES:
            raise ValueError(f"unsupported repository table: {table}")
        self.database = database
        self.table = table
        self.model = model

    def save(self, record: RecordT) -> RecordT:
        saved = record.model_copy(update={"updated_at": utc_now()})
        payload = saved.model_dump_json()
        with self.database.connect() as connection:
            connection.execute(
                f"INSERT INTO {self.table}(id, payload_json, created_at, updated_at) "  # noqa: S608
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (saved.id, payload, saved.created_at.isoformat(), saved.updated_at.isoformat()),
            )
        return saved

    def get(self, record_id: str) -> RecordT | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {self.table} WHERE id = ?",  # noqa: S608
                (record_id,),
            ).fetchone()
        return None if row is None else self.model.model_validate_json(row["payload_json"])

    def list(self) -> builtins.list[RecordT]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM {self.table} ORDER BY created_at"  # noqa: S608
            ).fetchall()
        return [self.model.model_validate_json(row["payload_json"]) for row in rows]

    def delete(self, record_id: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute(
                f"DELETE FROM {self.table} WHERE id = ?",  # noqa: S608
                (record_id,),
            )
        return result.rowcount > 0

    def find_by(self, field: str, value: object) -> builtins.list[RecordT]:
        if field not in self.model.model_fields:
            raise ValueError(f"unknown {self.model.__name__} field: {field}")
        return [record for record in self.list() if getattr(record, field) == value]

    def export_json(self) -> str:
        return json.dumps([record.model_dump(mode="json") for record in self.list()])


class ImmutableRepository[RecordT: Record](Repository[RecordT]):
    """Repository that allows initial persistence but rejects record replacement."""

    def save(self, record: RecordT) -> RecordT:
        existing = self.get(record.id)
        if existing is not None:
            existing_payload = existing.model_dump(exclude={"updated_at"})
            proposed_payload = record.model_dump(exclude={"updated_at"})
            if existing_payload != proposed_payload:
                raise ValueError(f"immutable {self.model.__name__} cannot be changed")
            return existing
        return super().save(record)
