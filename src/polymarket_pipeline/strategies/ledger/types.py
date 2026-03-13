"""Backward-compat shim -- re-exports from pm_strategy.ledger.types."""

from pm_strategy.ledger.types import LedgerRecord  # noqa: F401

__all__ = ["LedgerRecord"]
