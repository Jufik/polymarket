"""Backward-compat shim -- re-exports from pm_strategy.features.clickhouse."""

from pm_strategy.features.clickhouse import ClickHouseBackend  # noqa: F401

__all__ = ["ClickHouseBackend"]
