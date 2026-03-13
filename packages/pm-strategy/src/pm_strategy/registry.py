"""Strategy registry with entry_point auto-discovery."""

from __future__ import annotations

import importlib.metadata

import structlog

log = structlog.get_logger()


class StrategyRegistry:
    """Maps strategy names to their implementing classes.

    Supports both manual registration (``register()``) and automatic
    discovery via ``[project.entry-points."pm.strategies"]`` and
    ``[project.entry-points."pm.providers"]`` entry points.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, type] = {}
        self._providers: dict[str, type] = {}

    def discover(self) -> None:
        """Auto-discover strategies and providers from installed entry points."""
        for ep in importlib.metadata.entry_points(group="pm.strategies"):
            try:
                self._strategies[ep.name] = ep.load()
            except Exception as e:
                log.warning("strategy_discovery_failed", name=ep.name, error=str(e))
        for ep in importlib.metadata.entry_points(group="pm.providers"):
            try:
                self._providers[ep.name] = ep.load()
            except Exception as e:
                log.warning("provider_discovery_failed", name=ep.name, error=str(e))

    def register(self, name: str, cls: type) -> None:
        """Register a strategy class under *name*."""
        self._strategies[name] = cls

    def list_strategies(self) -> list[str]:
        """Return the names of all known strategies."""
        return sorted(self._strategies)

    def list_providers(self) -> list[str]:
        """Return the names of all known providers."""
        return sorted(self._providers)

    def list_registered(self) -> list[str]:
        """Backward compat alias for list_strategies."""
        return self.list_strategies()

    def get_strategy_class(self, name: str) -> type:
        """Return the strategy class for *name*.

        Raises
        ------
        KeyError
            If *name* has not been registered or discovered.
        """
        if name not in self._strategies:
            raise KeyError(f"Strategy not found: {name!r}. Available: {self.list_strategies()}")
        return self._strategies[name]

    def get_provider_class(self, name: str) -> type:
        """Return the provider class for *name*.

        Raises
        ------
        KeyError
            If *name* has not been registered or discovered.
        """
        if name not in self._providers:
            raise KeyError(f"Provider not found: {name!r}. Available: {self.list_providers()}")
        return self._providers[name]

    def create(self, name: str, config: object) -> object:
        """Instantiate the strategy registered as *name*.

        Backward-compatible with the old registry API: forwards
        ``config.params`` as keyword arguments.
        """
        cls = self.get_strategy_class(name)
        params = getattr(config, "params", {}) or {}
        return cls(**params)
