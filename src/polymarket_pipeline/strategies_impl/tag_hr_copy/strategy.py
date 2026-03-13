"""Backward-compat shim -- re-exports from pm_strategy.impl.tag_hr_copy.strategy."""

from pm_strategy.impl.tag_hr_copy.strategy import (  # noqa: F401
    TagHRCopyStrategy,
    create_tag_hr_copy_strategy,
)

__all__ = ["TagHRCopyStrategy", "create_tag_hr_copy_strategy"]
