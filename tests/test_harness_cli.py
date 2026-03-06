"""Tests for pm-harness CLI — smoke tests for argument parsing and wiring."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def sample_toml(tmp_path: Path) -> Path:
    p = tmp_path / "test.toml"
    p.write_text(dedent("""\
        [strategy.test_strat]
        enabled = true
        mode = "replay"
        capital_usd = 1000
        max_position_usd = 100
        max_open_positions = 20
        cooldown_s = 0
        features = ["test_provider"]

        [provider.test_provider]
        enabled = true
        refresh_interval_s = 900

        [harness]
        executor = "realistic"
        settlement_enabled = true

        [harness.walk_forward]
        train_months = 12
        test_months = 1
    """))
    return p


def test_harness_cli_exists(runner: CliRunner) -> None:
    """The harness CLI app can be imported and shows help."""
    from polymarket_pipeline.cli.harness import app

    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--period" in result.output


def test_harness_cli_missing_config(runner: CliRunner) -> None:
    """Exits with error when config file doesn't exist."""
    from polymarket_pipeline.cli.harness import app

    result = runner.invoke(
        app, ["run", "--config", "/nonexistent.toml", "--period", "2025-01-01:2025-02-01"]
    )
    assert result.exit_code != 0
