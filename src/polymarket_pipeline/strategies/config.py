"""TOML-based strategy configuration loading.

Each strategy section lives under ``[strategy.<name>]`` in the TOML file.
An optional ``[strategy.<name>.params]`` subsection carries arbitrary
key-value pairs that are forwarded to the strategy constructor.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polymarket_pipeline.strategies.types import ExecutionMode


@dataclass(frozen=True)
class StrategyConfig:
    """Immutable configuration for a single strategy."""

    enabled: bool
    mode: ExecutionMode
    capital_usd: float
    max_position_usd: float
    max_open_positions: int
    cooldown_s: int
    params: dict[str, Any] = field(default_factory=dict)


def load_strategy_configs(
    path: Path,
    *,
    enabled_only: bool = False,
) -> dict[str, StrategyConfig]:
    """Parse a TOML file and return a mapping of strategy name to config.

    Parameters
    ----------
    path:
        Path to the TOML configuration file.
    enabled_only:
        When *True*, skip strategies whose ``enabled`` field is *False*.

    Returns
    -------
    dict[str, StrategyConfig]
        Mapping from strategy name to its parsed configuration.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    strategies: dict[str, StrategyConfig] = {}
    for name, section in raw.get("strategy", {}).items():
        # Extract the params sub-table before building the config.
        params: dict[str, Any] = dict(section.pop("params", {}))

        cfg = StrategyConfig(
            enabled=section["enabled"],
            mode=ExecutionMode(section["mode"]),
            capital_usd=float(section["capital_usd"]),
            max_position_usd=float(section["max_position_usd"]),
            max_open_positions=int(section["max_open_positions"]),
            cooldown_s=int(section["cooldown_s"]),
            params=params,
        )

        if enabled_only and not cfg.enabled:
            continue

        strategies[name] = cfg

    return strategies
