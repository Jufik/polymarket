"""Market changelog table operations for MarketLifecycleService.

Tracks market state transitions (status changes, resolution events, etc.)
in PostgreSQL for audit trail and event replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg  # type: ignore[import-untyped]

_CHANGELOG_DDL = """
CREATE TABLE IF NOT EXISTS market_changelog (
    id              BIGSERIAL PRIMARY KEY,
    condition_id    TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    source          TEXT NOT NULL DEFAULT 'system',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_market_changelog_cid
    ON market_changelog(condition_id);
CREATE INDEX IF NOT EXISTS idx_market_changelog_type
    ON market_changelog(event_type);
CREATE INDEX IF NOT EXISTS idx_market_changelog_created
    ON market_changelog(created_at);
"""


async def ensure_changelog_table(conn: asyncpg.Connection) -> None:
    """Create the market_changelog table if it doesn't exist."""
    await conn.execute(_CHANGELOG_DDL)


async def insert_changelog_entry(
    conn: asyncpg.Connection,
    condition_id: str,
    event_type: str,
    *,
    old_value: str | None = None,
    new_value: str | None = None,
    source: str = "system",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Insert a changelog entry. Returns the entry ID."""
    import json

    row = await conn.fetchrow(
        """
        INSERT INTO market_changelog
            (condition_id, event_type, old_value, new_value, source, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id
        """,
        condition_id,
        event_type,
        old_value,
        new_value,
        source,
        json.dumps(metadata or {}),
    )
    return int(row["id"])  # type: ignore[index]


async def query_changelog(
    conn: asyncpg.Connection,
    *,
    condition_id: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query changelog entries with optional filters."""
    conditions = []
    params: list[Any] = []
    idx = 1

    if condition_id is not None:
        conditions.append(f"condition_id = ${idx}")
        params.append(condition_id)
        idx += 1

    if event_type is not None:
        conditions.append(f"event_type = ${idx}")
        params.append(event_type)
        idx += 1

    if since is not None:
        conditions.append(f"created_at >= ${idx}")
        params.append(since)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = await conn.fetch(
        f"""
        SELECT id, condition_id, event_type, old_value, new_value,
               source, metadata, created_at
        FROM market_changelog
        {where}
        ORDER BY created_at DESC
        LIMIT ${idx}
        """,
        *params,
    )
    return [dict(r) for r in rows]
