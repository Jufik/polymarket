"""Fill-based position tracker with PostgreSQL persistence.

Positions are computed atomically from fills within a DB transaction.
An asyncio.Lock serializes concurrent ``record_fill`` calls to prevent
race conditions on the in-memory cache.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from polymarket_pipeline.execution.models import FillRecord, Position

log = structlog.get_logger()

# SQL for position and fill tables
POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS positions (
    condition_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_entry DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_basis DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

FILLS_DDL = """
CREATE TABLE IF NOT EXISTS fills (
    id SERIAL PRIMARY KEY,
    intent_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL,
    outcome TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    size_usd DOUBLE PRECISION NOT NULL,
    fee_usd DOUBLE PRECISION NOT NULL,
    filled_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_fills_intent_id UNIQUE (intent_id)
);
CREATE INDEX IF NOT EXISTS idx_fills_condition ON fills(condition_id);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy);
"""

# Migrations for existing installs.
_MIGRATE_FILLS_UNIQUE = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_fills_intent_id'
    ) THEN
        ALTER TABLE fills ADD CONSTRAINT uq_fills_intent_id UNIQUE (intent_id);
    END IF;
END $$;
"""

_MIGRATE_POSITIONS_REALIZED_PNL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'positions' AND column_name = 'realized_pnl'
    ) THEN
        ALTER TABLE positions ADD COLUMN realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0;
    END IF;
END $$;
"""


class PositionTracker:
    """Tracks positions based on fills, with PostgreSQL persistence.

    Positions are computed atomically inside a DB transaction: each fill is
    inserted (with duplicate-intent protection) and the position is
    recomputed from the full fill history for that ``condition_id``.

    An :class:`asyncio.Lock` serializes concurrent ``record_fill`` calls so
    the in-memory cache never drifts from the database.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._positions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Create tables and load current positions from DB."""
        async with self._pool.acquire() as conn:
            await conn.execute(POSITIONS_DDL)
            await conn.execute(FILLS_DDL)
            # Backfill migrations for existing installs
            await conn.execute(_MIGRATE_FILLS_UNIQUE)
            await conn.execute(_MIGRATE_POSITIONS_REALIZED_PNL)

        rows = await self._query("SELECT * FROM positions WHERE size > 0")
        for row in rows:
            self._positions[row["condition_id"]] = dict(row)
        log.info("position_tracker.loaded", open_positions=len(self._positions))

    async def _query(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]

    async def _execute(self, sql: str, *args: Any) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def record_fill(self, fill: FillRecord) -> Position | None:
        """Record a fill and update the position atomically.

        Returns the updated :class:`Position`, or ``None`` if *fill* was a
        duplicate (same ``intent_id`` already recorded).
        """
        async with self._lock:
            return await self._record_fill_locked(fill)

    async def _record_fill_locked(self, fill: FillRecord) -> Position | None:
        """Inner implementation — must be called under ``self._lock``."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # 1. Insert fill — duplicate intent_id is silently skipped.
                tag = await conn.execute(
                    """
                    INSERT INTO fills (intent_id, strategy, condition_id,
                                       asset_id, side, outcome, price,
                                       size_usd, fee_usd, filled_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (intent_id) DO NOTHING
                    """,
                    fill.intent_id,
                    fill.strategy,
                    fill.condition_id,
                    fill.asset_id,
                    fill.side,
                    fill.outcome,
                    fill.price,
                    fill.size_usd,
                    fill.fee_usd,
                    fill.filled_at,
                )

                # Duplicate fill — nothing inserted.
                if tag == "INSERT 0 0":
                    log.warning(
                        "position_tracker.duplicate_fill",
                        intent_id=fill.intent_id,
                        condition_id=fill.condition_id,
                    )
                    return None

                # 2. Recompute position from ALL fills for this condition_id.
                #    This is the single source of truth — no memory-first math.
                #    Realized PnL = sell revenue - (avg_entry × tokens_sold) - fees.
                pos_row = await conn.fetchrow(
                    """
                    WITH ordered AS (
                        SELECT side, price, size_usd, fee_usd,
                               ROW_NUMBER() OVER (ORDER BY filled_at, id) AS rn
                        FROM fills
                        WHERE condition_id = $1
                    ),
                    agg AS (
                        SELECT
                            SUM(CASE WHEN side = 'BUY'
                                     THEN size_usd / NULLIF(price, 0)
                                     ELSE -(size_usd / NULLIF(price, 0))
                                END) AS net_tokens,
                            SUM(CASE WHEN side = 'BUY' THEN size_usd ELSE 0 END)
                                AS total_bought_usd,
                            SUM(CASE WHEN side = 'BUY'
                                     THEN size_usd / NULLIF(price, 0)
                                     ELSE 0 END) AS total_bought_tokens,
                            SUM(CASE WHEN side = 'SELL' THEN size_usd ELSE 0 END)
                                AS total_sold_usd,
                            SUM(CASE WHEN side = 'SELL'
                                     THEN size_usd / NULLIF(price, 0)
                                     ELSE 0 END) AS total_sold_tokens,
                            COALESCE(SUM(fee_usd), 0) AS total_fees,
                            (SELECT price FROM ordered ORDER BY rn DESC LIMIT 1)
                                AS last_price
                        FROM ordered
                    )
                    SELECT
                        GREATEST(net_tokens, 0) AS size,
                        CASE WHEN total_bought_tokens > 0
                             THEN total_bought_usd / total_bought_tokens
                             ELSE 0 END AS avg_entry,
                        GREATEST(
                            total_bought_usd - total_sold_usd, 0
                        ) AS cost_basis,
                        COALESCE(last_price, 0) AS last_price,
                        -- realized = sell revenue - cost of tokens sold - fees
                        CASE WHEN total_bought_tokens > 0 AND total_sold_tokens > 0
                             THEN total_sold_usd
                                  - (total_bought_usd / total_bought_tokens)
                                    * total_sold_tokens
                                  - total_fees
                             ELSE -total_fees END AS realized_pnl
                    FROM agg
                    """,
                    fill.condition_id,
                )

                size = float(pos_row["size"]) if pos_row else 0
                avg_entry = float(pos_row["avg_entry"]) if pos_row else 0
                cost_basis = float(pos_row["cost_basis"]) if pos_row else 0
                last_price = float(pos_row["last_price"]) if pos_row else 0
                realized_pnl = float(pos_row["realized_pnl"]) if pos_row else 0

                # 3. Upsert positions table.
                await conn.execute(
                    """
                    INSERT INTO positions (condition_id, asset_id, side, size,
                                           avg_entry, cost_basis, last_price,
                                           realized_pnl, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                    ON CONFLICT (condition_id) DO UPDATE SET
                        side = EXCLUDED.side,
                        size = EXCLUDED.size,
                        avg_entry = EXCLUDED.avg_entry,
                        cost_basis = EXCLUDED.cost_basis,
                        last_price = EXCLUDED.last_price,
                        realized_pnl = EXCLUDED.realized_pnl,
                        updated_at = NOW()
                    """,
                    fill.condition_id,
                    fill.asset_id,
                    fill.side,
                    size,
                    avg_entry,
                    cost_basis,
                    last_price,
                    realized_pnl,
                )

        # 4. Update memory cache AFTER the transaction commits.
        pos = {
            "condition_id": fill.condition_id,
            "asset_id": fill.asset_id,
            "side": fill.side,
            "size": size,
            "avg_entry": avg_entry,
            "cost_basis": cost_basis,
            "last_price": last_price,
            "realized_pnl": realized_pnl,
        }
        self._positions[fill.condition_id] = pos
        return self._to_position(pos)

    def _to_position(self, pos: dict[str, Any]) -> Position:
        unrealized = (pos.get("last_price", 0) - pos["avg_entry"]) * pos["size"]
        if pos["side"] == "SELL":
            unrealized = -unrealized
        return Position(
            condition_id=pos["condition_id"],
            asset_id=pos.get("asset_id", ""),
            side=pos["side"],
            size=pos["size"],
            avg_entry=pos["avg_entry"],
            cost_basis=pos["cost_basis"],
            unrealized_pnl=unrealized,
            last_price=pos.get("last_price", 0),
            realized_pnl=pos.get("realized_pnl", 0.0),
            updated_at=pos.get("updated_at"),
        )

    def get_position(self, condition_id: str) -> Position | None:
        """Get current position for a market, or None if flat."""
        pos = self._positions.get(condition_id)
        if pos is None or pos["size"] <= 0:
            return None
        return self._to_position(pos)

    def get_all_positions(self) -> list[Position]:
        """Get all open positions."""
        return [self._to_position(p) for p in self._positions.values() if p["size"] > 0]

    def get_total_exposure(self) -> float:
        """Get total USD exposure across all positions."""
        return sum(
            p["size"] * p.get("last_price", 0) for p in self._positions.values() if p["size"] > 0
        )

    async def reconcile(
        self,
        api_balances: dict[str, float],
        *,
        tolerance: float = 0.01,
    ) -> list[dict[str, object]]:
        """Compare tracked positions against CLOB API balances.

        Parameters
        ----------
        api_balances:
            Mapping of ``asset_id`` → token balance from ``ClobClient.get_balances()``.
        tolerance:
            Absolute token difference below which drift is ignored.

        Returns
        -------
        list[dict]
            List of drift records.  Empty if everything matches.
        """
        drifts: list[dict[str, object]] = []

        # Build reverse map: asset_id → tracked size
        tracked_by_asset: dict[str, float] = {}
        for pos in self._positions.values():
            if pos["size"] > 0 and pos.get("asset_id"):
                tracked_by_asset[pos["asset_id"]] = pos["size"]

        # Check tracked positions against API
        for asset_id, tracked_size in tracked_by_asset.items():
            api_size = api_balances.get(asset_id, 0.0)
            diff = abs(tracked_size - api_size)
            if diff > tolerance:
                drift = {
                    "asset_id": asset_id,
                    "tracked": round(tracked_size, 6),
                    "actual": round(api_size, 6),
                    "diff": round(diff, 6),
                }
                drifts.append(drift)
                log.error("position_tracker.drift", **drift)

        # Check API balances not tracked (phantom positions)
        for asset_id, api_size in api_balances.items():
            if api_size > tolerance and asset_id not in tracked_by_asset:
                drift = {
                    "asset_id": asset_id,
                    "tracked": 0.0,
                    "actual": round(api_size, 6),
                    "diff": round(api_size, 6),
                    "phantom": True,
                }
                drifts.append(drift)
                log.error("position_tracker.phantom_position", **drift)

        if not drifts:
            log.info("position_tracker.reconciled", positions=len(tracked_by_asset))

        return drifts
