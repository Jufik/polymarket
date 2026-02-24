"""Tests for TOML-based strategy config loading and strategy registry."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from polymarket_pipeline.strategies.config import (
    StrategyConfig,
    load_provider_configs,
    load_strategy_configs,
)
from polymarket_pipeline.strategies.registry import StrategyRegistry
from polymarket_pipeline.strategies.types import ExecutionMode

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_toml(tmp_path: Path) -> Path:
    """Write a sample TOML config and return its path."""
    content = textwrap.dedent("""\
        [strategy.consensus_copy]
        enabled = true
        mode = "replay"
        capital_usd = 1000.0
        max_position_usd = 100.0
        max_open_positions = 20
        cooldown_s = 300

        [strategy.consensus_copy.params]
        min_agreement = 0.8
        lookback_hours = 24

        [strategy.overprice_no]
        enabled = false
        mode = "paper_dev"
        capital_usd = 500.0
        max_position_usd = 50.0
        max_open_positions = 10
        cooldown_s = 60

        [strategy.overprice_no.params]
        threshold = 0.65

        [strategy.no_params]
        enabled = true
        mode = "vectorized"
        capital_usd = 2000.0
        max_position_usd = 200.0
        max_open_positions = 5
        cooldown_s = 120
    """)
    path = tmp_path / "strategies.toml"
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# StrategyConfig tests
# ---------------------------------------------------------------------------


class TestStrategyConfig:
    def test_frozen(self) -> None:
        cfg = StrategyConfig(
            enabled=True,
            mode=ExecutionMode.REPLAY,
            capital_usd=1000.0,
            max_position_usd=100.0,
            max_open_positions=20,
            cooldown_s=300,
        )
        with pytest.raises(AttributeError):
            cfg.enabled = False  # type: ignore[misc]

    def test_default_params(self) -> None:
        cfg = StrategyConfig(
            enabled=True,
            mode=ExecutionMode.REPLAY,
            capital_usd=1000.0,
            max_position_usd=100.0,
            max_open_positions=20,
            cooldown_s=300,
        )
        assert cfg.params == {}


# ---------------------------------------------------------------------------
# load_strategy_configs tests
# ---------------------------------------------------------------------------


class TestLoadStrategyConfigs:
    def test_load_all(self, sample_toml: Path) -> None:
        configs = load_strategy_configs(sample_toml)
        assert len(configs) == 3
        assert "consensus_copy" in configs
        assert "overprice_no" in configs
        assert "no_params" in configs

    def test_mode_parsed_as_enum(self, sample_toml: Path) -> None:
        configs = load_strategy_configs(sample_toml)
        assert configs["consensus_copy"].mode is ExecutionMode.REPLAY
        assert configs["overprice_no"].mode is ExecutionMode.PAPER_DEV
        assert configs["no_params"].mode is ExecutionMode.VECTORIZED

    def test_params_extracted(self, sample_toml: Path) -> None:
        configs = load_strategy_configs(sample_toml)
        assert configs["consensus_copy"].params == {
            "min_agreement": 0.8,
            "lookback_hours": 24,
        }
        assert configs["overprice_no"].params == {"threshold": 0.65}

    def test_missing_params_defaults_empty(self, sample_toml: Path) -> None:
        configs = load_strategy_configs(sample_toml)
        assert configs["no_params"].params == {}

    def test_enabled_only(self, sample_toml: Path) -> None:
        configs = load_strategy_configs(sample_toml, enabled_only=True)
        assert len(configs) == 2
        assert "consensus_copy" in configs
        assert "no_params" in configs
        assert "overprice_no" not in configs

    def test_scalar_fields(self, sample_toml: Path) -> None:
        cfg = load_strategy_configs(sample_toml)["consensus_copy"]
        assert cfg.enabled is True
        assert cfg.capital_usd == 1000.0
        assert cfg.max_position_usd == 100.0
        assert cfg.max_open_positions == 20
        assert cfg.cooldown_s == 300


# ---------------------------------------------------------------------------
# StrategyRegistry tests
# ---------------------------------------------------------------------------


class _DummyStrategy:
    """Trivial strategy for registry tests."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class TestStrategyRegistry:
    def test_register_and_create(self) -> None:
        registry = StrategyRegistry()
        registry.register("dummy", _DummyStrategy)
        cfg = StrategyConfig(
            enabled=True,
            mode=ExecutionMode.REPLAY,
            capital_usd=1000.0,
            max_position_usd=100.0,
            max_open_positions=20,
            cooldown_s=300,
            params={"min_agreement": 0.8},
        )
        instance = registry.create("dummy", cfg)
        assert isinstance(instance, _DummyStrategy)
        assert instance.kwargs == {"min_agreement": 0.8}

    def test_unknown_strategy_raises(self) -> None:
        registry = StrategyRegistry()
        cfg = StrategyConfig(
            enabled=True,
            mode=ExecutionMode.REPLAY,
            capital_usd=1000.0,
            max_position_usd=100.0,
            max_open_positions=20,
            cooldown_s=300,
        )
        with pytest.raises(KeyError, match="nonexistent"):
            registry.create("nonexistent", cfg)

    def test_list_registered(self) -> None:
        registry = StrategyRegistry()
        assert registry.list_registered() == []
        registry.register("alpha", _DummyStrategy)
        registry.register("beta", _DummyStrategy)
        assert sorted(registry.list_registered()) == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------


def test_load_provider_configs(tmp_path: Path) -> None:
    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[provider.skilled_traders]
enabled = true
refresh_interval_s = 900
[provider.skilled_traders.params]
min_trades = 50
min_pnl = 100.0

[provider.disabled_one]
enabled = false
refresh_interval_s = 60
""")
    configs = load_provider_configs(config_file)
    assert "skilled_traders" in configs
    assert "disabled_one" in configs
    assert configs["skilled_traders"].enabled is True
    assert configs["skilled_traders"].refresh_interval_s == 900.0
    assert configs["skilled_traders"].params == {"min_trades": 50, "min_pnl": 100.0}


def test_load_provider_configs_enabled_only(tmp_path: Path) -> None:
    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[provider.active]
enabled = true
refresh_interval_s = 300

[provider.inactive]
enabled = false
refresh_interval_s = 600
""")
    configs = load_provider_configs(config_file, enabled_only=True)
    assert "active" in configs
    assert "inactive" not in configs


# ---------------------------------------------------------------------------
# Strategy features field
# ---------------------------------------------------------------------------


def test_strategy_config_with_features(tmp_path: Path) -> None:
    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[strategy.my_strat]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 10
cooldown_s = 300
features = ["skilled_traders", "mvf_bands"]
""")
    configs = load_strategy_configs(config_file)
    assert configs["my_strat"].features == ["skilled_traders", "mvf_bands"]


def test_subscribe_pending_defaults_false(tmp_path: Path) -> None:
    toml_content = """
[strategy.basic]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 10
cooldown_s = 300
"""
    p = tmp_path / "cfg.toml"
    p.write_text(toml_content)
    configs = load_strategy_configs(p)
    assert configs["basic"].subscribe_pending is False


def test_subscribe_pending_true(tmp_path: Path) -> None:
    toml_content = """
[strategy.early_bird]
enabled = true
mode = "paper_dev"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 10
cooldown_s = 300
subscribe_pending = true
"""
    p = tmp_path / "cfg.toml"
    p.write_text(toml_content)
    configs = load_strategy_configs(p)
    assert configs["early_bird"].subscribe_pending is True


def test_strategy_config_features_defaults_empty(tmp_path: Path) -> None:
    config_file = tmp_path / "test.toml"
    config_file.write_text("""
[strategy.basic]
enabled = true
mode = "replay"
capital_usd = 500.0
max_position_usd = 50.0
max_open_positions = 5
cooldown_s = 60
""")
    configs = load_strategy_configs(config_file)
    assert configs["basic"].features == []
