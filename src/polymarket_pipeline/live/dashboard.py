"""Backward-compat shim — re-exports from pm_pipeline.dashboard."""

from pm_pipeline.dashboard import (  # noqa: F401
    build_dashboard_html,
    make_dashboard_route,
)

__all__ = ["build_dashboard_html", "make_dashboard_route"]
