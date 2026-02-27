"""Consumer for markets.events Kafka topic -- resolution tracking + pool refresh."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class MarketEventsConsumer:
    """Processes market events and triggers debounced pool refresh.

    Parameters
    ----------
    pg_pool:
        asyncpg connection pool for PG upserts. ``None`` skips PG updates.
    runner:
        LiveRunner instance -- ``request_refresh()`` is called after debounce.
    debounce_s:
        Seconds to wait after last event before triggering refresh.
    """

    def __init__(
        self,
        pg_pool: Any | None,
        runner: Any,
        debounce_s: float = 5.0,
    ) -> None:
        self._pg_pool = pg_pool
        self._runner = runner
        self._debounce_s = debounce_s
        self._debounce_task: asyncio.Task[None] | None = None
        self._pending_resolutions: int = 0

    async def handle(self, raw: str) -> None:
        """Process a single market event message."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("market_events.invalid_json", raw=raw[:100])
            return

        event_type = data.get("type")
        condition_id = data.get("condition_id", "")

        if event_type == "market_resolved":
            await self._handle_resolved(condition_id, data.get("payload", {}))
        elif event_type == "new_market":
            await self._handle_new_market(condition_id, data.get("payload", {}))

    async def _handle_resolved(self, condition_id: str, payload: dict[str, Any]) -> None:
        """Handle a market resolution -- settle positions, update PG, schedule refresh."""
        log.info("market_events.resolved", condition_id=condition_id)

        # Settle paper positions before refresh (frees budget + max_open slots)
        winner = payload.get("resolution", "")
        if winner in ("YES", "NO") and hasattr(self._runner, "settle_resolved_market"):
            self._runner.settle_resolved_market(condition_id, winner)

        if self._pg_pool is not None:
            await self._upsert_resolution(condition_id, payload)

        self._pending_resolutions += 1
        self._schedule_refresh()

    async def _handle_new_market(self, condition_id: str, payload: dict[str, Any]) -> None:
        """Handle a new market -- log only (no PnL impact, no refresh needed)."""
        log.info("market_events.new_market", condition_id=condition_id)

    async def _upsert_resolution(
        self, condition_id: str, payload: dict[str, Any]
    ) -> None:
        """Update the markets table in PostgreSQL with resolution data."""
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE markets
                    SET resolution_value = 1,
                        winner_outcome = $1,
                        resolved_at = NOW()
                    WHERE condition_id = $2
                    """,
                    payload.get("resolution", ""),
                    condition_id,
                )
        except Exception:
            log.exception("market_events.pg_upsert_error", condition_id=condition_id)

    def _schedule_refresh(self) -> None:
        """Schedule a debounced refresh -- resets timer on each call."""
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounced_refresh())

    async def _debounced_refresh(self) -> None:
        """Wait for debounce period, then trigger refresh."""
        await asyncio.sleep(self._debounce_s)
        count = self._pending_resolutions
        self._pending_resolutions = 0
        log.info("market_events.triggering_refresh", batched_resolutions=count)
        self._runner.request_refresh()
