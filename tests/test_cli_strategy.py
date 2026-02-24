"""Tests for pm-strategy CLI."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_cli_module_imports() -> None:
    """CLI module should be importable."""
    from polymarket_pipeline.cli.strategy import app  # noqa: F401


def test_cli_build_runner_from_config(tmp_path: Path) -> None:
    """Test that _build_runner assembles the runner correctly from TOML."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[provider.skilled_traders]
enabled = true
refresh_interval_s = 900
[provider.skilled_traders.params]
min_trades = 5

[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
features = ["skilled_traders"]
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0
""")

    runner = _build_runner(config_file)
    assert len(runner.strategies) == 1
    assert len(runner.providers) == 1
    assert runner.providers[0].name == "skilled_traders"


def test_cli_build_runner_validates_features(tmp_path: Path) -> None:
    """Should raise if strategy declares a feature that isn't configured."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
features = ["nonexistent_provider"]
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0
""")

    with pytest.raises(ValueError, match="nonexistent_provider"):
        _build_runner(config_file)


def test_cli_build_runner_only_filter(tmp_path: Path) -> None:
    """--only should filter to a single strategy."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.consensus_copy]
enabled = true
mode = "paper_dev"
capital_usd = 1000.0
max_position_usd = 100.0
max_open_positions = 20
cooldown_s = 300
[strategy.consensus_copy.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0

[strategy.other_strat]
enabled = true
mode = "paper_dev"
capital_usd = 500.0
max_position_usd = 50.0
max_open_positions = 5
cooldown_s = 60
[strategy.other_strat.params]
min_traders = 3
agreement_pct = 0.80
direction = "NO"
delay_s = 60
base_bet_usd = 10.0
""")

    runner = _build_runner(config_file, only="consensus_copy")
    assert len(runner.strategies) == 1
    assert runner.strategies[0][0].name == "consensus_copy"


def test_all_strategies_registered() -> None:
    """All four strategies should be registered in the factory."""
    from polymarket_pipeline.cli.strategy import _STRATEGY_FACTORIES, _register_strategies

    _register_strategies()
    assert "consensus_copy" in _STRATEGY_FACTORIES
    assert "crypto_otm_no" in _STRATEGY_FACTORIES
    assert "will_no" in _STRATEGY_FACTORIES
    assert "proportional_copy" in _STRATEGY_FACTORIES


def test_all_providers_registered() -> None:
    """All providers should be registered."""
    from polymarket_pipeline.cli.strategy import _PROVIDER_REGISTRY, _register_providers

    _register_providers()
    assert "skilled_traders" in _PROVIDER_REGISTRY
    assert "crypto_markets" in _PROVIDER_REGISTRY
    assert "will_markets" in _PROVIDER_REGISTRY
    assert "pool_traders" in _PROVIDER_REGISTRY
