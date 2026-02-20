"""Tests for informed MM estimate helpers."""
from __future__ import annotations

import polars as pl


def test_taker_pnl_no_wins():
    """Taker buys NO at 75c, NO wins -> profit."""
    from scripts.informed_mm_estimate import compute_taker_bet_pnl

    pnl = compute_taker_bet_pnl(yes_price=0.25, bet_size=100.0, no_won=True, fee_pct=0.0)
    # NO cost = 0.75, tokens = 100/0.75 = 133.33, profit = 133.33 * 0.25 = 33.33
    assert abs(pnl - 33.33) < 0.01


def test_taker_pnl_no_loses():
    from scripts.informed_mm_estimate import compute_taker_bet_pnl

    pnl = compute_taker_bet_pnl(yes_price=0.25, bet_size=100.0, no_won=False, fee_pct=0.0)
    assert pnl == -100.0


def test_maker_pnl_no_wins():
    from scripts.informed_mm_estimate import compute_maker_bet_pnl

    pnl = compute_maker_bet_pnl(
        yes_price=0.25, spread_edge=0.01, bet_size=100.0, no_won=True, fee_pct=0.0
    )
    expected = 100.0 * 0.26 / 0.74
    assert abs(pnl - expected) < 0.01


def test_maker_pnl_no_loses():
    from scripts.informed_mm_estimate import compute_maker_bet_pnl

    pnl = compute_maker_bet_pnl(
        yes_price=0.25, spread_edge=0.01, bet_size=100.0, no_won=False, fee_pct=0.0
    )
    assert pnl == -100.0


def test_maker_better_than_taker_on_win():
    from scripts.informed_mm_estimate import compute_maker_bet_pnl, compute_taker_bet_pnl

    for yes_p in [0.15, 0.25, 0.35, 0.45]:
        taker = compute_taker_bet_pnl(yes_p, 100.0, True, 0.0)
        maker = compute_maker_bet_pnl(yes_p, 0.01, 100.0, True, 0.0)
        assert maker > taker, f"Maker should beat taker at yes_price={yes_p}"


def test_maker_pnl_with_fees():
    from scripts.informed_mm_estimate import compute_maker_bet_pnl

    no_fee = compute_maker_bet_pnl(0.25, 0.01, 100.0, True, 0.0)
    with_fee = compute_maker_bet_pnl(0.25, 0.01, 100.0, True, 0.02)
    assert with_fee < no_fee


def test_fill_prob_from_volume():
    from scripts.informed_mm_estimate import estimate_fill_probability

    assert estimate_fill_probability(200.0, 100.0) == 1.0
    assert estimate_fill_probability(50.0, 100.0) == 0.5
    assert estimate_fill_probability(0.0, 100.0) == 0.0


def test_compute_yes_buy_volume_per_market():
    from scripts.informed_mm_estimate import compute_yes_buy_volume_per_market

    trades = pl.DataFrame({
        "condition_id": ["m1", "m1", "m1", "m2", "m2"],
        "asset_id": ["a1", "a1", "a1", "a2", "a2"],
        "side": ["BUY", "SELL", "BUY", "BUY", "BUY"],
        "price": [0.20, 0.80, 0.30, 0.15, 0.25],
        "amount_usd": [100.0, 50.0, 200.0, 150.0, 100.0],
        "timestamp": [1.0, 2.0, 3.0, 4.0, 5.0],
    })
    token_map = pl.DataFrame({
        "asset_id": ["a1", "a2"],
        "condition_id": ["m1", "m2"],
        "token_index": [0, 0],
    })

    result = compute_yes_buy_volume_per_market(trades, token_map)

    m1 = result.filter(pl.col("condition_id") == "m1")
    m2 = result.filter(pl.col("condition_id") == "m2")
    # m1: BUY+YES=100, SELL+YES(not YES-buy)=skip, BUY+YES=200 -> 300
    assert abs(float(m1["yes_buy_volume"][0]) - 300.0) < 0.01
    # m2: BUY+YES=150, BUY+YES=100 -> 250
    assert abs(float(m2["yes_buy_volume"][0]) - 250.0) < 0.01


def test_stage1_mm_overlay():
    from scripts.informed_mm_estimate import stage1_mm_overlay

    signals = pl.DataFrame({
        "condition_id": ["m1", "m2"],
        "trigger_entry_price": [0.70, 0.65],
        "signal_direction": ["NO", "NO"],
        "yes_won": [False, True],
        "n_traders": [5, 7],
        "agreement_frac": [0.80, 0.71],
    })
    volume = pl.DataFrame({
        "condition_id": ["m1", "m2"],
        "yes_buy_volume": [500.0, 200.0],
        "yes_buy_count": [10, 5],
    })

    result = stage1_mm_overlay(
        signals=signals,
        volume=volume,
        spread_edges=[0.01],
        flat_fill_rates=[1.0],
        bet_size=100.0,
        fee_pct=0.0,
    )

    assert "taker_pnl" in result.columns
    assert "maker_pnl" in result.columns
    assert "maker_delta" in result.columns
    assert "fill_model" in result.columns
    assert result.height > 0


def test_stage2_capital_sim():
    from datetime import datetime, timedelta

    from scripts.informed_mm_estimate import stage2_capital_sim

    base_trigger = datetime(2025, 1, 5)
    signals = pl.DataFrame({
        "condition_id": [f"m{i}" for i in range(15)],
        "trigger_entry_price": [0.70] * 15,
        "signal_direction": ["NO"] * 15,
        "yes_won": [False] * 15,
        "trigger_time": [base_trigger + timedelta(days=i) for i in range(15)],
        "resolved_at": [base_trigger + timedelta(days=15 + i) for i in range(15)],
    })
    volume = pl.DataFrame({
        "condition_id": [f"m{i}" for i in range(15)],
        "yes_buy_volume": [500.0] * 15,
        "yes_buy_count": [10] * 15,
    })

    result = stage2_capital_sim(
        signals=signals,
        volume=volume,
        capital=1000.0,
        bet_size=100.0,
        spread_edge=0.01,
        fill_rate=1.0,
        fee_pct=0.0,
    )

    total_placed = sum(m["placed"] for m in result["months"])
    assert total_placed <= 15
    assert total_placed >= 10
    assert result["total_pnl"] > 0
