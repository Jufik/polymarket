"""Backward-compat shim -- re-exports from pm_strategy.impl.tag_hr_copy."""

from pm_strategy.impl.tag_hr_copy.provider import TagHRProvider  # noqa: F401
from pm_strategy.impl.tag_hr_copy.strategy import TagHRCopyStrategy  # noqa: F401

__all__ = ["TagHRCopyStrategy", "TagHRProvider"]
