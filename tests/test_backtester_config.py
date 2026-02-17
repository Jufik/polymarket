"""Tests for backtester TOML config parser and window generator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from strategies.consistency_copy.backtester.config import BacktestConfig, load_config
from strategies.consistency_copy.backtester.sweep import SweepConfig

SAMPLE_TOML = """\
[windows]
window_strategy = "anchored_expanding"
train_anchor = 2023-01-01
holdout_months = 3
step_months = 3
first_holdout = 2024-01-01
last_holdout = 2024-07-01
test_after = 2024-07-01

[pool]
consistency_months = [6, 9]
min_markets = [10, 20]
mvf_bands = ["all", "pure_taker"]

[pricing]
execution_delays = [0, 60]
max_price_delay_s = 3600.0

[sweep]
min_traders = [3, 5]
agreement_pct = [0.70, 0.90]
directions = ["NO-only", "both"]
price_bands = [[0.05, 0.95], [0.10, 0.90]]
sizing_strategies = ["fixed"]
min_bets = 20
base_bet = 100.0
fee_pct = 0.02

[metrics]
base_rate_adjustment = false

[ranking]
top_n = 50
min_windows = 2
"""


@pytest.fixture()
def sample_toml(tmp_path: Path) -> Path:
    """Write a minimal TOML config to a temp file and return the path."""
    p = tmp_path / "backtest.toml"
    p.write_text(SAMPLE_TOML)
    return p


# --------------------------------------------------------------------------
# Test 1: load_config returns BacktestConfig
# --------------------------------------------------------------------------
def test_load_config_returns_backtest_config(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    assert isinstance(cfg, BacktestConfig)


# --------------------------------------------------------------------------
# Test 2: window generation count (2 dev + 1 test)
# --------------------------------------------------------------------------
def test_window_generation_count(sample_toml: Path) -> None:
    """first_holdout=2024-01-01, step=3mo, last_holdout=2024-07-01 ->
    holdout starts: 2024-01-01, 2024-04-01, 2024-07-01 = 3 windows.
    test_after=2024-07-01 -> first two are dev, last is test."""
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    assert len(windows) == 3

    dev_windows = [w for w in windows if not w.is_test]
    test_windows = [w for w in windows if w.is_test]
    assert len(dev_windows) == 2
    assert len(test_windows) == 1


# --------------------------------------------------------------------------
# Test 3: anchored expanding — all train_start == train_anchor
# --------------------------------------------------------------------------
def test_window_generation_anchored_expanding(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    anchor = datetime(2023, 1, 1)
    for w in windows:
        assert w.train_start == anchor, f"{w.name}: train_start={w.train_start} != {anchor}"


# --------------------------------------------------------------------------
# Test 4: train_end == holdout_start (no gap)
# --------------------------------------------------------------------------
def test_window_train_end_equals_holdout_start(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    for w in windows:
        assert w.train_end == w.holdout_start, (
            f"{w.name}: train_end={w.train_end} != holdout_start={w.holdout_start}"
        )


# --------------------------------------------------------------------------
# Test 5: holdout duration == holdout_months (3 months each)
# --------------------------------------------------------------------------
def test_window_holdout_length(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    for w in windows:
        # Each holdout should span exactly 3 months
        delta_months = (w.holdout_end.year - w.holdout_start.year) * 12 + (
            w.holdout_end.month - w.holdout_start.month
        )
        assert delta_months == 3, (
            f"{w.name}: holdout spans {delta_months} months, expected 3"
        )


# --------------------------------------------------------------------------
# Test 6: dev window names are sequential "dev_NN_YYYYQq"
# --------------------------------------------------------------------------
def test_window_names_sequential(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    dev_windows = [w for w in windows if not w.is_test]
    for i, w in enumerate(dev_windows):
        prefix = f"dev_{i:02d}_"
        assert w.name.startswith(prefix), f"Expected name starting with {prefix!r}, got {w.name!r}"


# --------------------------------------------------------------------------
# Test 7: test window name starts with "test_"
# --------------------------------------------------------------------------
def test_test_window_name(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    windows = cfg.generate_windows()
    test_windows = [w for w in windows if w.is_test]
    for w in test_windows:
        assert w.name.startswith("test_"), f"Expected name starting with 'test_', got {w.name!r}"


# --------------------------------------------------------------------------
# Test 8: to_sweep_config produces a valid SweepConfig
# --------------------------------------------------------------------------
def test_sweep_config_from_toml(sample_toml: Path) -> None:
    cfg = load_config(sample_toml)
    sc = cfg.to_sweep_config()
    assert isinstance(sc, SweepConfig)
    assert sc.min_traders_values == [3, 5]
    assert sc.agreement_pct_values == [0.70, 0.90]
    assert sc.direction_values == ["NO-only", "both"]
    assert sc.entry_price_bands == [(0.05, 0.95), (0.10, 0.90)]
    assert sc.sizing_strategies == ["fixed"]
    assert sc.min_bets == 20
