"""PostgreSQL sink for market metadata persistence."""

from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped]

from polymarket_pipeline.models import Event, Market, Tag, TokenMarketEntry


class PostgresSink:
    """Async PostgreSQL sink using asyncpg connection pool."""

    def __init__(self, dsn: str = "postgresql://polymarket:polymarket@localhost:15432/polymarket"):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> PostgresSink:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def upsert_events(self, events: list[Event]) -> None:
        """Upsert events into PostgreSQL. Must be called before upsert_markets (FK)."""
        if not events or not self._pool:
            return

        sql = """
            INSERT INTO events (
                id, slug, title, category, neg_risk,
                active, closed, archived,
                liquidity, volume,
                start_date, end_date, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
            ON CONFLICT (id) DO UPDATE SET
                slug = EXCLUDED.slug,
                title = EXCLUDED.title,
                category = EXCLUDED.category,
                neg_risk = EXCLUDED.neg_risk,
                active = EXCLUDED.active,
                closed = EXCLUDED.closed,
                archived = EXCLUDED.archived,
                liquidity = EXCLUDED.liquidity,
                volume = EXCLUDED.volume,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                created_at = EXCLUDED.created_at,
                updated_at = NOW()
        """

        rows = [
            (
                e.id,
                e.slug,
                e.title,
                e.category,
                e.neg_risk,
                e.active,
                e.closed,
                e.archived,
                e.liquidity,
                e.volume,
                e.start_date,
                e.end_date,
                e.created_at,
            )
            for e in events
        ]

        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)

    async def upsert_markets(self, markets: list[Market]) -> None:
        """Upsert markets into PostgreSQL. Must be called after upsert_events (FK)."""
        if not markets or not self._pool:
            return

        sql = """
            INSERT INTO markets (
                condition_id, event_id, question, slug, category,
                token_yes, token_no, neg_risk, status,
                resolution_value, winner_outcome,
                created_at, closed_at, resolved_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, NOW())
            ON CONFLICT (condition_id) DO UPDATE SET
                event_id = EXCLUDED.event_id,
                question = EXCLUDED.question,
                slug = EXCLUDED.slug,
                category = EXCLUDED.category,
                token_yes = EXCLUDED.token_yes,
                token_no = EXCLUDED.token_no,
                neg_risk = EXCLUDED.neg_risk,
                status = EXCLUDED.status,
                resolution_value = EXCLUDED.resolution_value,
                winner_outcome = EXCLUDED.winner_outcome,
                created_at = EXCLUDED.created_at,
                closed_at = EXCLUDED.closed_at,
                resolved_at = EXCLUDED.resolved_at,
                updated_at = NOW()
        """

        rows = [
            (
                m.condition_id,
                m.event_id,
                m.question,
                m.slug,
                m.category,
                m.token_yes,
                m.token_no,
                m.neg_risk,
                m.status.value,
                m.resolution_value,
                m.winner_outcome,
                m.created_at,
                m.closed_at,
                m.resolved_at,
            )
            for m in markets
        ]

        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)

    async def upsert_tags(self, tags: list[Tag]) -> None:
        """Upsert tags into PostgreSQL. Must be called before upsert_event_tags (FK)."""
        if not tags or not self._pool:
            return

        sql = """
            INSERT INTO tags (id, label, slug)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                label = EXCLUDED.label,
                slug = EXCLUDED.slug
        """

        rows = [(t.id, t.label, t.slug) for t in tags]

        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)

    async def upsert_event_tags(self, pairs: list[tuple[int, int]]) -> None:
        """Upsert event-tag associations. Call after upsert_events and upsert_tags."""
        if not pairs or not self._pool:
            return

        sql = """
            INSERT INTO event_tags (event_id, tag_id)
            VALUES ($1, $2)
            ON CONFLICT (event_id, tag_id) DO NOTHING
        """

        async with self._pool.acquire() as conn:
            await conn.executemany(sql, pairs)

    async def upsert_token_map(self, entries: list[TokenMarketEntry]) -> None:
        """Upsert token-market mappings. Call after upsert_markets (FK constraint)."""
        if not entries or not self._pool:
            return

        sql = """
            INSERT INTO token_market_map (asset_id, condition_id, outcome, winner)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (asset_id) DO UPDATE SET
                condition_id = EXCLUDED.condition_id,
                outcome = EXCLUDED.outcome,
                winner = EXCLUDED.winner
        """

        rows = [(e.asset_id, e.condition_id, e.outcome, e.winner) for e in entries]

        async with self._pool.acquire() as conn:
            await conn.executemany(sql, rows)

    async def fetch_token_market_map(self) -> dict[str, tuple[str, str]]:
        """Load asset_id -> (condition_id, outcome) map from token_market_map table."""
        if not self._pool:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT asset_id, condition_id, outcome FROM token_market_map"
            )
            return {r["asset_id"]: (r["condition_id"], r["outcome"]) for r in rows}

    async def query(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]
