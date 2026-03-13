"""Backward-compat shim -- re-exports from pm_strategy.execution.realistic."""

from pm_strategy.execution.realistic import (  # noqa: F401
    FillModelConfig,
    RealisticFillSimulator,
)

__all__ = ["FillModelConfig", "RealisticFillSimulator"]
