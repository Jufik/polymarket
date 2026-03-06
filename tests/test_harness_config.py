"""Tests for HarnessConfig loading from TOML."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.toml"
    p.write_text(dedent(content))
    return p


def test_load_harness_config_defaults(tmp_path: Path) -> None:
    """Missing [harness] section returns all defaults."""
    from polymarket_pipeline.strategies.config import load_harness_config

    p = _write_toml(tmp_path, """\
        [strategy.x]
        enabled = true
        mode = "replay"
        capital_usd = 100
        max_position_usd = 50
        max_open_positions = 5
        cooldown_s = 0
    """)
    cfg = load_harness_config(p)
    assert cfg.executor == "realistic"
    assert cfg.settlement_enabled is True
    assert cfg.walk_forward_train_months == 12
    assert cfg.walk_forward_test_months == 1


def test_load_harness_config_custom(tmp_path: Path) -> None:
    """Custom [harness] values are parsed correctly."""
    from polymarket_pipeline.strategies.config import load_harness_config

    p = _write_toml(tmp_path, """\
        [harness]
        executor = "simulated"
        bootstrap_hours = 48
        settlement_enabled = false

        [harness.walk_forward]
        train_months = 6
        test_months = 2
    """)
    cfg = load_harness_config(p)
    assert cfg.executor == "simulated"
    assert cfg.bootstrap_hours == 48
    assert cfg.settlement_enabled is False
    assert cfg.walk_forward_train_months == 6
    assert cfg.walk_forward_test_months == 2


def test_load_harness_config_partial(tmp_path: Path) -> None:
    """Partial [harness] uses defaults for missing fields."""
    from polymarket_pipeline.strategies.config import load_harness_config

    p = _write_toml(tmp_path, """\
        [harness]
        executor = "simulated"
    """)
    cfg = load_harness_config(p)
    assert cfg.executor == "simulated"
    assert cfg.bootstrap_hours == 168  # default
    assert cfg.pre_filter_makers is True  # default
