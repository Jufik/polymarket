"""Backward-compat shim -- re-exports from pm_strategy.promotion."""

from pm_strategy.promotion import (  # noqa: F401
    GateResult,
    PromotionChecker,
    PromotionReport,
    PromotionThresholds,
)

__all__ = [
    "GateResult",
    "PromotionChecker",
    "PromotionReport",
    "PromotionThresholds",
]
