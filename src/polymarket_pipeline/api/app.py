"""Backward-compat shim: re-exports from pm_api package."""

from pm_api.app import app, create_app, main  # noqa: F401

__all__ = ["app", "create_app", "main"]
