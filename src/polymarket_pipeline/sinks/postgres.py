"""PostgreSQL sink for market metadata persistence."""

from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped]

from polymarket_pipeline.models import Event, Market, Tag, TokenMarketEntry

_BATCH_SIZE = 5000


class PostgresSink:
    """Async PostgreSQL sink using asyncpg connection pool."""

    def __init__(self, dsn: str = ""):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @staticmethod
    async def _executemany_chunked(
        conn: asyncpg.Connection,
        sql: str,
        rows: list[Any],
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        """Execute in chunks to avoid oversized packets and enable progress."""
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            await conn.executemany(sql, chunk)

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
            await self._executemany_chunked(conn, sql, rows)

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
            await self._executemany_chunked(conn, sql, rows)

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
            await self._executemany_chunked(conn, sql, rows)

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
            await self._executemany_chunked(conn, sql, pairs)

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
            await self._executemany_chunked(conn, sql, rows)

    async def fetch_token_market_map(self) -> dict[str, tuple[str, str]]:
        """Load asset_id -> (condition_id, outcome) map from token_market_map table."""
        if not self._pool:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT asset_id, condition_id, outcome FROM token_market_map")
            return {r["asset_id"]: (r["condition_id"], r["outcome"]) for r in rows}

    # ── Recovery job tracking ─────────────────────────────────────────

    _RECOVERY_JOBS_DDL = """
        CREATE TABLE IF NOT EXISTS recovery_jobs (
            id SERIAL PRIMARY KEY,
            from_ts BIGINT NOT NULL,
            cursor_ts BIGINT NOT NULL,
            cursor_id TEXT NOT NULL DEFAULT '',
            target_ts BIGINT NOT NULL,
            total_published INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_recovery_jobs_status
            ON recovery_jobs(status);
    """

    async def _ensure_recovery_table(self, conn: Any) -> None:
        """Create recovery_jobs table if it doesn't exist."""
        await conn.execute(self._RECOVERY_JOBS_DDL)
        # Add cursor_id column if missing (for existing tables)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE recovery_jobs ADD COLUMN cursor_id TEXT NOT NULL DEFAULT '';
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)

    async def create_recovery_job(self, from_ts: int, target_ts: int) -> int:
        """Create a new recovery job, returns job ID."""
        if not self._pool:
            raise RuntimeError("PostgresSink not connected")
        async with self._pool.acquire() as conn:
            await self._ensure_recovery_table(conn)
            row = await conn.fetchrow(
                """
                INSERT INTO recovery_jobs (from_ts, cursor_ts, target_ts, status)
                VALUES ($1, $1, $2, 'running')
                RETURNING id
                """,
                from_ts,
                target_ts,
            )
            return int(row["id"])  # type: ignore[index]

    async def update_recovery_cursor(
        self, job_id: int, cursor_ts: int, total_published: int, cursor_id: str = ""
    ) -> None:
        """Update cursor position, cursor_id, and total published count."""
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE recovery_jobs
                SET cursor_ts = $1, cursor_id = $2, total_published = $3, updated_at = NOW()
                WHERE id = $4
                """,
                cursor_ts,
                cursor_id,
                total_published,
                job_id,
            )

    async def complete_recovery_job(self, job_id: int, status: str = "completed") -> None:
        """Mark job as completed or failed."""
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE recovery_jobs
                SET status = $1, updated_at = NOW()
                WHERE id = $2
                """,
                status,
                job_id,
            )

    async def get_active_recovery_job(self) -> dict[str, Any] | None:
        """Get the most recent running or failed job, if any."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            await self._ensure_recovery_table(conn)
            row = await conn.fetchrow(
                """
                SELECT id, from_ts, cursor_ts, cursor_id, target_ts, total_published,
                       status, created_at, updated_at
                FROM recovery_jobs
                WHERE status IN ('running', 'failed')
                ORDER BY id DESC
                LIMIT 1
                """
            )
            return dict(row) if row else None

    async def query(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]
