"""Backward-compat shim -- re-exports from pm_strategy.features.polars."""

from pm_strategy.features.polars import PolarsBackend  # noqa: F401

__all__ = ["PolarsBackend"]
