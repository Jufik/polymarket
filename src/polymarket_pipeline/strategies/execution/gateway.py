"""Backward-compat shim -- re-exports from pm_strategy.execution.gateway."""

from pm_strategy.execution.gateway import ExecutionGateway  # noqa: F401

__all__ = ["ExecutionGateway"]
