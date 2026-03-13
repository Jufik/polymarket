"""Backward-compat shim — re-exports from pm_pipeline.app."""

from pm_pipeline.app import (  # noqa: F401
    app,
    asgi_app,
    broker,
    handle_market_event,
    handle_status,
    on_shutdown,
    on_startup,
    settings,
)

__all__ = [
    "app",
    "asgi_app",
    "broker",
    "handle_market_event",
    "handle_status",
    "on_shutdown",
    "on_startup",
    "settings",
]
