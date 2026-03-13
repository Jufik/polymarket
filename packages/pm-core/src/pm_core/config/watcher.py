"""ConfigWatcher — typed, validated, auto-updating config for consumers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

import structlog

from pm_core.config.store import ConfigStore

log = structlog.get_logger()

T = TypeVar("T")


class ConfigWatcher(Generic[T]):
    """Typed, validated, auto-updating config for consumers.

    Wraps a ConfigStore section with a Pydantic schema. Bad overrides are
    rejected (logged) and the previous valid config is retained.
    """

    def __init__(self, store: ConfigStore, section: str, schema: type[T]) -> None:
        self._store = store
        self._section = section
        self._schema = schema
        self._callbacks: list[Callable[[T, T], Awaitable[None] | None]] = []

        # Build initial config
        raw = store.get_section(section)
        self._current: T = schema(**raw)  # type: ignore[call-arg]

        # Register for change notifications
        store.subscribe(section, self._on_store_change)

    @property
    def current(self) -> T:
        """Always valid. Bad overrides rejected, not applied."""
        return self._current

    def on_change(self, cb: Callable[[T, T], Awaitable[None] | None]) -> None:
        """Register callback: (old_config, new_config) -> side effects."""
        self._callbacks.append(cb)

    def refresh(self) -> None:
        """Force re-read from store and validate."""
        self._on_store_change()

    def _on_store_change(self, *_args: Any) -> None:
        """Called when the store publishes a change for our section."""
        raw = self._store.get_section(self._section)
        try:
            new_config: T = self._schema(**raw)  # type: ignore[call-arg]
        except Exception:
            log.warning(
                "config_validation_failed",
                section=self._section,
                raw=raw,
                exc_info=True,
            )
            return  # Keep current valid config

        old_config = self._current
        self._current = new_config

        for cb in self._callbacks:
            try:
                cb(old_config, new_config)
            except Exception:
                log.warning(
                    "config_change_callback_failed",
                    section=self._section,
                    exc_info=True,
                )
