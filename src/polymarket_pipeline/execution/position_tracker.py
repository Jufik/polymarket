"""Fill-based position tracker with PostgreSQL persistence."""

from __future__ import annotations

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
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fills_condition ON fills(condition_id);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy);
"""


class PositionTracker:
    """Tracks positions based on fills, with PostgreSQL persistence.

    Positions are computed from fills: each fill updates the running position
    for that condition_id.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._positions: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        """Create tables and load current positions from DB."""
        async with self._pool.acquire() as conn:
            await conn.execute(POSITIONS_DDL)
            await conn.execute(FILLS_DDL)

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

    async def record_fill(self, fill: FillRecord) -> Position:
        """Record a fill and update the position. Returns updated position."""
        # Persist fill
        await self._execute(
            """
            INSERT INTO fills (intent_id, strategy, condition_id, asset_id, side,
                             outcome, price, size_usd, fee_usd, filled_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
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

        # Update position in memory
        tokens = fill.size_usd / fill.price if fill.price > 0 else 0
        pos = self._positions.get(fill.condition_id)

        if pos is None:
            # New position
            pos = {
                "condition_id": fill.condition_id,
                "asset_id": fill.asset_id,
                "side": fill.side,
                "size": tokens,
                "avg_entry": fill.price,
                "cost_basis": fill.size_usd,
                "last_price": fill.price,
            }
        elif fill.side == pos["side"]:
            # Adding to position
            new_size = pos["size"] + tokens
            new_cost = pos["cost_basis"] + fill.size_usd
            pos["avg_entry"] = new_cost / new_size if new_size > 0 else 0
            pos["size"] = new_size
            pos["cost_basis"] = new_cost
            pos["last_price"] = fill.price
        else:
            # Reducing/closing position
            pos["size"] = pos["size"] - tokens
            if pos["size"] <= 0:
                pos["size"] = 0
                pos["cost_basis"] = 0
            else:
                pos["cost_basis"] = pos["size"] * pos["avg_entry"]
            pos["last_price"] = fill.price

        self._positions[fill.condition_id] = pos

        # Persist position
        await self._execute(
            """
            INSERT INTO positions (condition_id, asset_id, side, size, avg_entry,
                                   cost_basis, last_price, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (condition_id) DO UPDATE SET
                side = EXCLUDED.side,
                size = EXCLUDED.size,
                avg_entry = EXCLUDED.avg_entry,
                cost_basis = EXCLUDED.cost_basis,
                last_price = EXCLUDED.last_price,
                updated_at = NOW()
            """,
            pos["condition_id"],
            pos.get("asset_id", ""),
            pos["side"],
            pos["size"],
            pos["avg_entry"],
            pos["cost_basis"],
            pos["last_price"],
        )

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
