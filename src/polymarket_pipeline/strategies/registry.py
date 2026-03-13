"""Backward-compat shim -- re-exports from pm_strategy.registry."""

from pm_strategy.registry import StrategyRegistry  # noqa: F401

__all__ = ["StrategyRegistry"]
