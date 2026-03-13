"""Backward-compat shim — re-exports from pm_pipeline.quality.checker."""

from pm_pipeline.quality.checker import QualityChecker  # noqa: F401

__all__ = ["QualityChecker"]
