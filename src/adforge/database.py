"""SQLite connection management and repeatable schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

TABLES = (
    "products",
    "product_truth_snapshots",
    "campaigns",
    "campaign_tasks",
    "assets",
    "provider_executions",
    "qc_results",
    "handoff_packages",
    "ledger_events",
    "renders",
    "configurations",
)

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
            *(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                "id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                for table in TABLES
            ),
        ),
    ),
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(MIGRATIONS[0][1][0])
            rows = connection.execute("SELECT version FROM schema_migrations")
            applied = {row["version"] for row in rows}
            for version, statements in MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements[1:]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (version,),
                )
