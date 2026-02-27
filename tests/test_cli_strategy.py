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
[provider.will_markets]
enabled = true
refresh_interval_s = 900
[provider.will_markets.params]
question_pattern = "^Will\\\\b"

[strategy.will_no]
enabled = true
mode = "paper_dev"
capital_usd = 5000.0
max_position_usd = 150.0
max_open_positions = 100
cooldown_s = 0
features = ["will_markets"]
[strategy.will_no.params]
base_bet_usd = 50
""")

    runner = _build_runner(config_file)
    assert len(runner.strategies) == 1
    assert len(runner.providers) == 1
    assert runner.providers[0].name == "will_markets"


def test_cli_build_runner_validates_features(tmp_path: Path) -> None:
    """Should raise if strategy declares a feature that isn't configured."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.will_no]
enabled = true
mode = "paper_dev"
capital_usd = 5000.0
max_position_usd = 150.0
max_open_positions = 100
cooldown_s = 0
features = ["nonexistent_provider"]
[strategy.will_no.params]
base_bet_usd = 50
""")

    with pytest.raises(ValueError, match="nonexistent_provider"):
        _build_runner(config_file)


def test_cli_build_runner_only_filter(tmp_path: Path) -> None:
    """--only should filter to a single strategy."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.will_no]
enabled = true
mode = "paper_dev"
capital_usd = 5000.0
max_position_usd = 150.0
max_open_positions = 100
cooldown_s = 0
[strategy.will_no.params]
base_bet_usd = 50

[strategy.hr_pool]
enabled = true
mode = "paper_dev"
capital_usd = 10000.0
max_position_usd = 200.0
max_open_positions = 50
cooldown_s = 0
[strategy.hr_pool.params]
bet_size_usd = 100
direction = "NO"
max_entry_price = 0.70
""")

    runner = _build_runner(config_file, only="will_no")
    assert len(runner.strategies) == 1
    assert runner.strategies[0][0].name == "will_no"


def test_active_strategies_registered() -> None:
    """Active strategies (hr_pool, will_no) should be registered."""
    from polymarket_pipeline.cli.strategy import _STRATEGY_FACTORIES, _register_strategies

    _register_strategies()
    assert "will_no" in _STRATEGY_FACTORIES
    assert "hr_pool" in _STRATEGY_FACTORIES


def test_active_providers_registered() -> None:
    """Active providers should be registered."""
    from polymarket_pipeline.cli.strategy import _PROVIDER_REGISTRY, _register_providers

    _register_providers()
    assert "will_markets" in _PROVIDER_REGISTRY
    assert "market_size" in _PROVIDER_REGISTRY
    assert "hr_pool" in _PROVIDER_REGISTRY
