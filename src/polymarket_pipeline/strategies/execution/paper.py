"""Backward-compat shim -- re-exports from pm_strategy.execution.paper."""

from pm_strategy.execution.paper import PaperExecutor  # noqa: F401

__all__ = ["PaperExecutor"]
