"""Consumer for markets.events Kafka topic -- resolution tracking + pool refresh.

Also includes ``ResolutionPoller`` which periodically checks the CLOB API
for resolution of markets we have filled intents on. This compensates for
the CLOB WS firehose not emitting ``market_resolved`` events.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_CLOB_BASE = "https://clob.polymarket.com"


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
        if winner in ("YES", "NO"):
            resolution_value = 1
            if hasattr(self._runner, "settle_resolved_market"):
                self._runner.settle_resolved_market(condition_id, winner)
        else:
            # Voided / Unknown / 50-50 — each token redeems at $0.50
            resolution_value = -1
            if hasattr(self._runner, "settle_voided_market"):
                self._runner.settle_voided_market(condition_id, 0.5)

        if self._pg_pool is not None:
            await self._upsert_resolution(condition_id, payload, resolution_value)

        self._pending_resolutions += 1
        self._schedule_refresh()

    async def _handle_new_market(self, condition_id: str, payload: dict[str, Any]) -> None:
        """Handle a new market -- log only (no PnL impact, no refresh needed)."""
        log.info("market_events.new_market", condition_id=condition_id)

    async def _upsert_resolution(
        self, condition_id: str, payload: dict[str, Any], resolution_value: int = 1
    ) -> None:
        """Update the markets table in PostgreSQL with resolution data."""
        try:
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE markets
                    SET resolution_value = $1,
                        winner_outcome = $2,
                        resolved_at = NOW()
                    WHERE condition_id = $3
                    """,
                    resolution_value,
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


class ResolutionPoller:
    """Periodically polls CLOB API for resolution of our filled markets.

    The CLOB WS firehose does not emit ``market_resolved`` events, so this
    poller checks markets where we have filled intents with ``end_date``
    passed but no ``resolved_at``.

    Parameters
    ----------
    pg_pool:
        asyncpg connection pool.
    runner:
        LiveRunner or None — calls ``settle_resolved_market()`` and
        ``request_refresh()`` when resolutions are detected.
    interval_s:
        Seconds between poll cycles.
    """

    def __init__(
        self,
        pg_pool: Any,
        runner: Any | None = None,
        interval_s: float = 60.0,
    ) -> None:
        self._pg_pool = pg_pool
        self._runner = runner
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background polling loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        """Stop the polling loop."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        """Main polling loop."""
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("resolution_poller.error")
            await asyncio.sleep(self._interval_s)

    async def _poll_once(self) -> None:
        """Check CLOB API for resolution of unresolved markets with fills."""
        async with self._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT si.condition_id
                   FROM strategy_intents si
                   JOIN markets m ON si.condition_id = m.condition_id
                   LEFT JOIN events e ON m.event_id = e.id
                   WHERE si.disposition = 'filled'
                     AND m.resolved_at IS NULL
                     AND (
                       (e.end_date IS NOT NULL AND e.end_date < NOW())
                       OR e.end_date IS NULL
                     )"""
            )

        if not rows:
            return

        cids = [r["condition_id"] for r in rows]
        log.info("resolution_poller.checking", count=len(cids))

        resolved_count = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            for cid in cids:
                try:
                    resp = await client.get(
                        f"{_CLOB_BASE}/markets/{cid}",
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    tokens = data.get("tokens") or []
                    closed = data.get("closed", False)
                    winners = [t for t in tokens if t.get("winner") is True]

                    if not closed:
                        continue

                    if len(winners) == 0:
                        # Voided / Unknown / 50-50 — payout vector [1,1]
                        async with self._pg_pool.acquire() as conn:
                            await conn.execute(
                                """UPDATE markets
                                   SET resolution_value = -1,
                                       winner_outcome = '',
                                       resolved_at = NOW()
                                   WHERE condition_id = $1
                                     AND resolved_at IS NULL""",
                                cid,
                            )

                        log.info(
                            "resolution_poller.voided",
                            condition_id=cid,
                        )
                        resolved_count += 1

                        if (
                            self._runner is not None
                            and hasattr(self._runner, "settle_voided_market")
                        ):
                            self._runner.settle_voided_market(cid, 0.5)

                        continue

                    if len(winners) != 1:
                        continue

                    winner_outcome = winners[0].get("outcome", "")
                    if not winner_outcome:
                        continue

                    # Update PG
                    async with self._pg_pool.acquire() as conn:
                        await conn.execute(
                            """UPDATE markets
                               SET resolution_value = 1,
                                   winner_outcome = $1,
                                   resolved_at = NOW()
                               WHERE condition_id = $2
                                 AND resolved_at IS NULL""",
                            winner_outcome,
                            cid,
                        )

                    log.info(
                        "resolution_poller.resolved",
                        condition_id=cid,
                        winner=winner_outcome,
                    )
                    resolved_count += 1

                    # Settle paper positions if runner attached
                    if (
                        self._runner is not None
                        and winner_outcome in ("YES", "NO")
                        and hasattr(self._runner, "settle_resolved_market")
                    ):
                        self._runner.settle_resolved_market(cid, winner_outcome)

                except Exception:
                    log.debug("resolution_poller.clob_error", condition_id=cid)

        if resolved_count > 0:
            log.info("resolution_poller.batch_complete", resolved=resolved_count)
            if self._runner is not None:
                self._runner.request_refresh()
