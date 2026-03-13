"""Backward-compat shim — re-exports from pm_pipeline.protection."""

from pm_pipeline.protection import auto_protect  # noqa: F401

__all__ = ["auto_protect"]
