"""MarketLifecycleService: manages market state transitions."""

from __future__ import annotations

import structlog

from pm_pipeline.lifecycle.states import MarketState

log = structlog.get_logger()


class MarketLifecycleService:
    """Manages market state transitions: DETECTED -> ACTIVE -> RESOLVING -> RESOLVED -> ARCHIVED."""

    def __init__(self) -> None:
        self._states: dict[str, MarketState] = {}

    async def on_new_market(self, condition_id: str, source: str) -> None:
        """DETECTED -> fetch metadata -> ACTIVE."""
        self._states[condition_id] = MarketState.ACTIVE
        log.info("market_activated", condition_id=condition_id, source=source)

    async def on_resolution_event(self, condition_id: str, winner: str, source: str) -> None:
        """ACTIVE/RESOLVING -> RESOLVED."""
        self._states[condition_id] = MarketState.RESOLVED
        log.info("market_resolved", condition_id=condition_id, winner=winner, source=source)

    def get_state(self, condition_id: str) -> MarketState | None:
        return self._states.get(condition_id)
