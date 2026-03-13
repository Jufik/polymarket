"""Backward-compat shim — re-exports from pm_pipeline.consumers.market_events."""

from pm_pipeline.consumers.market_events import (  # noqa: F401
    MarketEventsConsumer,
    ResolutionPoller,
)

__all__ = ["MarketEventsConsumer", "ResolutionPoller"]
