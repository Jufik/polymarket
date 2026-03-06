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
class ProviderConfig:
    """Immutable configuration for a single feature provider."""

    enabled: bool
    refresh_interval_s: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyConfig:
    """Immutable configuration for a single strategy."""

    name: str
    enabled: bool
    mode: ExecutionMode
    capital_usd: float
    max_position_usd: float
    max_open_positions: int
    cooldown_s: int
    params: dict[str, Any] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    subscribe_pending: bool = False


@dataclass(frozen=True)
class HarnessConfig:
    """Configuration for the production replay harness (pm-harness)."""

    executor: str = "realistic"
    fill_model: str = "calibrated_slippage"
    bootstrap_hours: int = 168
    pre_filter_makers: bool = True
    settlement_enabled: bool = True
    resolution_source: str = "asset_id"
    walk_forward_train_months: int = 12
    walk_forward_test_months: int = 1


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
        # Extract features and params without mutating the input dict.
        features: list[str] = section.get("features", [])
        params: dict[str, Any] = dict(section.get("params", {}))

        cfg = StrategyConfig(
            name=name,
            enabled=section["enabled"],
            mode=ExecutionMode(section["mode"]),
            capital_usd=float(section["capital_usd"]),
            max_position_usd=float(section["max_position_usd"]),
            max_open_positions=int(section["max_open_positions"]),
            cooldown_s=int(section["cooldown_s"]),
            params=params,
            features=features,
            subscribe_pending=bool(section.get("subscribe_pending", False)),
        )

        if enabled_only and not cfg.enabled:
            continue

        strategies[name] = cfg

    return strategies


def load_promotion_thresholds(path: Path) -> dict[str, Any]:
    """Load promotion thresholds from the ``[promotion]`` TOML section.

    Returns a dict of threshold values that can be unpacked into
    ``PromotionThresholds(**values)``.  If the section is missing,
    returns an empty dict (all defaults apply).
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    return dict(raw.get("promotion", {}))


def load_execution_config(path: Path) -> dict[str, Any]:
    """Load execution config from the ``[execution]`` TOML section.

    Returns a dict that can be unpacked into ``OrderConfig(**values)``.
    If the section is missing, returns an empty dict (all defaults apply).
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    return dict(raw.get("execution", {}))


def load_harness_config(path: Path) -> HarnessConfig:
    """Load harness configuration from the ``[harness]`` TOML section.

    If the section is missing, all defaults apply. The optional
    ``[harness.walk_forward]`` subsection sets walk-forward window sizes.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    section = raw.get("harness", {})
    flat = dict(section)
    wf = flat.pop("walk_forward", {}) if isinstance(flat.get("walk_forward"), dict) else {}

    return HarnessConfig(
        executor=str(flat.get("executor", "realistic")),
        fill_model=str(flat.get("fill_model", "calibrated_slippage")),
        bootstrap_hours=int(flat.get("bootstrap_hours", 168)),
        pre_filter_makers=bool(flat.get("pre_filter_makers", True)),
        settlement_enabled=bool(flat.get("settlement_enabled", True)),
        resolution_source=str(flat.get("resolution_source", "asset_id")),
        walk_forward_train_months=int(wf.get("train_months", 12)),
        walk_forward_test_months=int(wf.get("test_months", 1)),
    )


def load_provider_configs(
    path: Path,
    *,
    enabled_only: bool = False,
) -> dict[str, ProviderConfig]:
    """Parse a TOML file and return a mapping of provider name to config.

    Provider sections live under ``[provider.<name>]``.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    providers: dict[str, ProviderConfig] = {}
    for name, section in raw.get("provider", {}).items():
        params: dict[str, Any] = dict(section.get("params", {}))

        cfg = ProviderConfig(
            enabled=section["enabled"],
            refresh_interval_s=float(section.get("refresh_interval_s", 900)),
            params=params,
        )

        if enabled_only and not cfg.enabled:
            continue

        providers[name] = cfg

    return providers
