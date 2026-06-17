"""Shared checkpoint table helpers.

Both ``LocalMemoryStore`` (memory.sqlite) and ``EventStore`` (events.sqlite)
keep a ``checkpoints`` table of ``name -> value`` resumable bookkeeping. The
table is intentionally co-located with the data each checkpoint guards so the
checkpoint advance can commit in the same transaction as the rows it protects
(SQLite cannot wrap a transaction across two database files).

These functions operate on a caller-provided connection so they can participate
in a larger transaction.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

CREATE_CHECKPOINTS_TABLE = """
CREATE TABLE IF NOT EXISTS checkpoints (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def create_checkpoints_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_CHECKPOINTS_TABLE)


def get_checkpoint(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM checkpoints WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return str(row["value"])


def set_checkpoint(
    conn: sqlite3.Connection,
    name: str,
    value: str,
    *,
    now: datetime | None = None,
) -> None:
    updated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO checkpoints (name, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (name, value, updated_at),
    )
