"""Strategy registry for discovery and instantiation.

Strategies register themselves by name; the registry creates instances
by looking up the class and forwarding ``config.params`` as keyword arguments.
"""

from __future__ import annotations

from typing import Any

from polymarket_pipeline.strategies.config import StrategyConfig


class StrategyRegistry:
    """Maps strategy names to their implementing classes."""

    def __init__(self) -> None:
        self._registry: dict[str, type[Any]] = {}

    def register(self, name: str, cls: type[Any]) -> None:
        """Register a strategy class under *name*."""
        self._registry[name] = cls

    def create(self, name: str, config: StrategyConfig) -> Any:
        """Instantiate the strategy registered as *name*.

        Raises
        ------
        KeyError
            If *name* has not been registered.
        """
        try:
            cls = self._registry[name]
        except KeyError:
            msg = f"Strategy not registered: {name!r}"
            raise KeyError(msg) from None
        return cls(**config.params)

    def list_registered(self) -> list[str]:
        """Return the names of all registered strategies."""
        return list(self._registry)
