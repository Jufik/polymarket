"""Backward-compat shim -- re-exports from pm_strategy.protocols."""

from pm_strategy.protocols import (  # noqa: F401
    Executor,
    FeatureBackend,
    FeatureProvider,
    Strategy,
    StrategyContext,
    VectorizedStrategy,
)

__all__ = [
    "Executor",
    "FeatureBackend",
    "FeatureProvider",
    "Strategy",
    "StrategyContext",
    "VectorizedStrategy",
]
