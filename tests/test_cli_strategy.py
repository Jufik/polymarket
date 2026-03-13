"""Tests for pm-strategy CLI."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_cli_module_imports() -> None:
    """CLI module should be importable."""
    from polymarket_pipeline.cli.strategy import app  # noqa: F401


def test_cli_build_runner_empty_config(tmp_path: Path) -> None:
    """Build runner with no strategies should produce empty runner."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("")

    runner = _build_runner(config_file)
    assert len(runner.strategies) == 0
    assert len(runner.providers) == 0


def test_cli_build_runner_validates_features(tmp_path: Path) -> None:
    """Should raise if strategy declares a feature that isn't configured."""
    from polymarket_pipeline.cli.strategy import _build_runner

    config_file = tmp_path / "strategies.toml"
    config_file.write_text("""
[strategy.test_strat]
enabled = true
mode = "paper_dev"
capital_usd = 5000.0
max_position_usd = 150.0
max_open_positions = 100
cooldown_s = 0
features = ["nonexistent_provider"]
[strategy.test_strat.params]
base_bet_usd = 50
""")

    with pytest.raises(ValueError, match="nonexistent_provider"):
        _build_runner(config_file)


def test_registries_have_active_strategies() -> None:
    """Active strategies and providers should be registered."""
    from polymarket_pipeline.cli.strategy import (
        _PROVIDER_REGISTRY,
        _STRATEGY_FACTORIES,
        _register_providers,
        _register_strategies,
    )

    _register_strategies()
    _register_providers()
    assert "tag_hr_copy" in _STRATEGY_FACTORIES
    assert "crypto_gbm" in _STRATEGY_FACTORIES
    assert "tag_hr_provider" in _PROVIDER_REGISTRY
