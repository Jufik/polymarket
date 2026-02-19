"""Informed MM estimate: two-stage analytical comparison of maker vs taker execution.

Stage 1 — Per-bet overlay: For each consistency-pool signal fire, compute both
taker-NO PnL and maker-sell-YES PnL across a grid of spread edges and fill rates.
Quantifies the maker edge per bet.

Stage 2 — Capital simulation: Month-by-month FIFO simulation with capital
constraints, lockup, and fill probability.  Shows realistic PnL trajectory.

Usage:
    PYTHONPATH=. uv run python scripts/informed_mm_estimate.py
    PYTHONPATH=. uv run python scripts/informed_mm_estimate.py --force-volume
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import polars as pl

from strategies.consistency_copy.backtester.config import load_config
from strategies.consistency_copy.backtester.runner import (
    PRICE_CACHE_DIR,
    _apply_precomputed_prices,
    _compute_trader_median_entry,
    _load_data,
    _precompute_entry_prices,
    _precompute_mvf_subsets,
    get_consistent_traders,
)
from strategies.consistency_copy.backtester.signal_table import build_signal_table

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("data")
DERIVED_DIR = DATA_DIR / "derived"
COMPACT_DIR = DATA_DIR / "compact"
METADATA_DIR = DATA_DIR / "metadata"
VOLUME_CACHE = DERIVED_DIR / "yes_buy_volume.parquet"
CONFIG_PATH = Path("strategies/consistency_copy/sweep_config.toml")

# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------
SPREAD_EDGES = [0.005, 0.01, 0.015, 0.02]
FLAT_FILL_RATES = [0.25, 0.50, 0.75, 1.00]
CAPITAL = 1000.0
BET_SIZE = 100.0
TOP_N_FOR_SIM = 5

SIGNAL_CONFIGS = [
    {"min_traders": 5, "agreement_pct": 0.70},
    {"min_traders": 5, "agreement_pct": 0.80},
    {"min_traders": 7, "agreement_pct": 0.70},
    {"min_traders": 7, "agreement_pct": 0.80},
    {"min_traders": 10, "agreement_pct": 0.70},
    {"min_traders": 10, "agreement_pct": 0.80},
    {"min_traders": 5, "agreement_pct": 0.90},
    {"min_traders": 5, "agreement_pct": 1.00},
    {"min_traders": 7, "agreement_pct": 0.90},
    {"min_traders": 10, "agreement_pct": 0.90},
]

POOL_CONFIGS = [
    {"n_months": 9, "min_mkts": 20, "mvf_band": "pure_taker", "max_entry": 0.80},
    {"n_months": 6, "min_mkts": 10, "mvf_band": "pure_taker", "max_entry": 0.90},
]


# ---------------------------------------------------------------------------
# Core PnL functions
# ---------------------------------------------------------------------------


def compute_taker_bet_pnl(
    yes_price: float,
    bet_size: float,
    no_won: bool,
    fee_pct: float,
) -> float:
    """Compute PnL for a taker buying NO tokens.

    Taker pays NO price = 1 - yes_price. Gets bet_size / no_price tokens.
    If NO wins: each token pays $1, profit = tokens * yes_price.
    If YES wins: tokens worthless, loss = -bet_size.
    Fee = fee_pct * min(yes_price, 1 - yes_price) * bet_size.
    """
    no_price = 1.0 - yes_price
    fee = fee_pct * min(yes_price, no_price) * bet_size
    if no_won:
        # tokens = bet_size / no_price; profit per token = yes_price
        profit = bet_size * yes_price / no_price
        return profit - fee
    else:
        return -bet_size - fee


def compute_maker_bet_pnl(
    yes_price: float,
    spread_edge: float,
    bet_size: float,
    no_won: bool,
    fee_pct: float,
) -> float:
    """Compute PnL for a maker selling YES tokens (equivalent to buying NO at a better price).

    Maker sells YES at yes_price + spread_edge.  Effective NO cost = 1 - (yes_price + spread_edge).
    Same token economics as taker but at the improved effective price.
    """
    effective_yes = yes_price + spread_edge
    effective_no = 1.0 - effective_yes
    fee = fee_pct * min(effective_yes, effective_no) * bet_size
    if no_won:
        profit = bet_size * effective_yes / effective_no
        return profit - fee
    else:
        return -bet_size - fee


# ---------------------------------------------------------------------------
# Fill probability
# ---------------------------------------------------------------------------


def estimate_fill_probability(yes_buy_volume: float, bet_size: float) -> float:
    """Estimate fill probability from observed YES-buy volume.

    Simple heuristic: min(1.0, volume / bet_size).
    """
    if bet_size <= 0:
        return 0.0
    return min(1.0, yes_buy_volume / bet_size)


# ---------------------------------------------------------------------------
# YES-buy volume computation
# ---------------------------------------------------------------------------


def compute_yes_buy_volume_per_market(
    trades: pl.DataFrame,
    token_map: pl.DataFrame,
) -> pl.DataFrame:
    """Compute YES-buy volume per market from trade-level data.

    YES-buy = (side=BUY and token_index=0) or (side=SELL and token_index=1).

    Parameters
    ----------
    trades
        Trade-level data with columns: condition_id, asset_id, side, price,
        amount_usd, timestamp.
    token_map
        Token map with columns: asset_id, condition_id, token_index.

    Returns
    -------
    pl.DataFrame
        Columns: condition_id, yes_buy_volume, yes_buy_count.
    """
    # Join trades with token_map on asset_id; suffix handles duplicate condition_id
    joined = trades.join(
        token_map.select("asset_id", "condition_id", "token_index"),
        on="asset_id",
        how="inner",
        suffix="_tm",
    )

    # Use condition_id from token_map if present (more reliable mapping)
    cid_col = "condition_id_tm" if "condition_id_tm" in joined.columns else "condition_id"

    # YES-buy: (BUY + YES token) or (SELL + NO token)
    yes_buys = joined.filter(
        ((pl.col("side") == "BUY") & (pl.col("token_index") == 0))
        | ((pl.col("side") == "SELL") & (pl.col("token_index") == 1))
    )

    result = yes_buys.group_by(cid_col).agg(
        pl.col("amount_usd").sum().alias("yes_buy_volume"),
        pl.len().alias("yes_buy_count"),
    )

    # Normalize column name
    if cid_col != "condition_id":
        result = result.rename({cid_col: "condition_id"})

    return result


def load_or_compute_yes_buy_volume(force: bool = False) -> pl.DataFrame:
    """Load cached YES-buy volume or compute from compact parquet files.

    Parameters
    ----------
    force
        If True, recompute even if the cache exists.

    Returns
    -------
    pl.DataFrame
        Columns: condition_id, yes_buy_volume, yes_buy_count.
    """
    if not force and VOLUME_CACHE.exists():
        print(f"[volume] Loading cached {VOLUME_CACHE}")
        return pl.read_parquet(VOLUME_CACHE)

    print("[volume] Scanning compact parquet files (streaming) ...")
    t0 = time.time()

    token_map = pl.read_parquet(METADATA_DIR / "token_map.parquet").select(
        "asset_id", "condition_id", "token_index"
    )
    token_map_lazy = token_map.lazy()

    # Stream through 27GB of compact parquet without loading into memory:
    # join with token_map, filter to YES-buys, aggregate — all in lazy mode.
    result = (
        pl.scan_parquet(str(COMPACT_DIR / "compact_*.parquet"))
        .select("asset_id", "side", "amount_usd")
        .join(token_map_lazy, on="asset_id", how="inner")
        .filter(
            ((pl.col("side") == "BUY") & (pl.col("token_index") == 0))
            | ((pl.col("side") == "SELL") & (pl.col("token_index") == 1))
        )
        .group_by("condition_id")
        .agg(
            pl.col("amount_usd").sum().alias("yes_buy_volume"),
            pl.len().alias("yes_buy_count"),
        )
        .collect(streaming=True)
    )

    VOLUME_CACHE.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(VOLUME_CACHE)
    elapsed = time.time() - t0
    print(
        f"[volume] Computed {result.height:,} markets in {elapsed:.1f}s, "
        f"cached to {VOLUME_CACHE}"
    )

    return result


# ---------------------------------------------------------------------------
# Stage 1 — Per-bet MM overlay
# ---------------------------------------------------------------------------


def stage1_mm_overlay(
    signals: pl.DataFrame,
    volume: pl.DataFrame,
    spread_edges: list[float],
    flat_fill_rates: list[float],
    bet_size: float,
    fee_pct: float,
) -> pl.DataFrame:
    """Stage 1: compute per-bet taker and maker PnL across spread/fill grid.

    For each spread_edge, computes taker and maker PnL per signal fire.
    Aggregates across flat fill rates and a volume-derived fill model.

    Parameters
    ----------
    signals
        Signal fires with columns: condition_id, trigger_entry_price,
        signal_direction, yes_won, n_traders, agreement_frac.
    volume
        Per-market YES-buy volume: condition_id, yes_buy_volume, yes_buy_count.
    spread_edges
        List of spread edge values to evaluate.
    flat_fill_rates
        List of flat fill rates to evaluate.
    bet_size
        Dollar amount per bet.
    fee_pct
        Fee as fraction of the smaller side.

    Returns
    -------
    pl.DataFrame
        Aggregated results with columns: spread_edge, fill_model, fill_rate,
        n_signals, n_filled, taker_pnl, maker_pnl, taker_pnl_per_bet,
        maker_pnl_per_bet, maker_delta, maker_delta_pct, taker_hr, maker_hr.
    """
    # Join signals with volume
    sig_vol = signals.join(volume, on="condition_id", how="left").with_columns(
        pl.col("yes_buy_volume").fill_null(0.0),
        pl.col("yes_buy_count").fill_null(0),
    )

    rows: list[dict] = []

    for spread_edge in spread_edges:
        # Compute per-bet PnL for every signal
        taker_pnls: list[float] = []
        maker_pnls: list[float] = []
        fill_probs: list[float] = []
        taker_wins: list[bool] = []
        maker_wins: list[bool] = []

        for row in sig_vol.iter_rows(named=True):
            # trigger_entry_price is the NO-side price for NO signals
            # yes_price = 1 - trigger_entry_price
            yes_price = 1.0 - row["trigger_entry_price"]
            no_won = not row["yes_won"]

            t_pnl = compute_taker_bet_pnl(yes_price, bet_size, no_won, fee_pct)
            m_pnl = compute_maker_bet_pnl(yes_price, spread_edge, bet_size, no_won, fee_pct)
            fp = estimate_fill_probability(float(row["yes_buy_volume"]), bet_size)

            taker_pnls.append(t_pnl)
            maker_pnls.append(m_pnl)
            fill_probs.append(fp)
            taker_wins.append(t_pnl > 0)
            maker_wins.append(m_pnl > 0)

        n_signals = len(taker_pnls)
        if n_signals == 0:
            continue

        # Flat fill rate models
        for fr in flat_fill_rates:
            n_filled = int(n_signals * fr)
            if n_filled == 0:
                continue
            t_total = sum(taker_pnls[:n_filled])
            m_total = sum(maker_pnls[:n_filled])
            t_wins = sum(taker_wins[:n_filled])
            m_wins = sum(maker_wins[:n_filled])

            rows.append({
                "spread_edge": spread_edge,
                "fill_model": f"flat_{fr:.0%}",
                "fill_rate": fr,
                "n_signals": n_signals,
                "n_filled": n_filled,
                "taker_pnl": t_total,
                "maker_pnl": m_total,
                "taker_pnl_per_bet": t_total / n_filled,
                "maker_pnl_per_bet": m_total / n_filled,
                "maker_delta": m_total - t_total,
                "maker_delta_pct": (
                    (m_total / t_total - 1.0) * 100.0 if t_total != 0 else 0.0
                ),
                "taker_hr": t_wins / n_filled,
                "maker_hr": m_wins / n_filled,
            })

        # Volume-derived fill model
        weighted_t = sum(
            p * fp for p, fp in zip(taker_pnls, fill_probs, strict=True)
        )
        weighted_m = sum(
            p * fp for p, fp in zip(maker_pnls, fill_probs, strict=True)
        )
        effective_fills = sum(fill_probs)
        weighted_t_wins = sum(
            (1.0 if tw else 0.0) * fp
            for tw, fp in zip(taker_wins, fill_probs, strict=True)
        )
        weighted_m_wins = sum(
            (1.0 if mw else 0.0) * fp
            for mw, fp in zip(maker_wins, fill_probs, strict=True)
        )

        if effective_fills > 0:
            rows.append({
                "spread_edge": spread_edge,
                "fill_model": "volume_derived",
                "fill_rate": effective_fills / n_signals,
                "n_signals": n_signals,
                "n_filled": int(effective_fills),
                "taker_pnl": weighted_t,
                "maker_pnl": weighted_m,
                "taker_pnl_per_bet": weighted_t / effective_fills,
                "maker_pnl_per_bet": weighted_m / effective_fills,
                "maker_delta": weighted_m - weighted_t,
                "maker_delta_pct": (
                    (weighted_m / weighted_t - 1.0) * 100.0 if weighted_t != 0 else 0.0
                ),
                "taker_hr": weighted_t_wins / effective_fills,
                "maker_hr": weighted_m_wins / effective_fills,
            })

    return pl.DataFrame(rows) if rows else pl.DataFrame(
        schema={
            "spread_edge": pl.Float64,
            "fill_model": pl.Utf8,
            "fill_rate": pl.Float64,
            "n_signals": pl.Int64,
            "n_filled": pl.Int64,
            "taker_pnl": pl.Float64,
            "maker_pnl": pl.Float64,
            "taker_pnl_per_bet": pl.Float64,
            "maker_pnl_per_bet": pl.Float64,
            "maker_delta": pl.Float64,
            "maker_delta_pct": pl.Float64,
            "taker_hr": pl.Float64,
            "maker_hr": pl.Float64,
        }
    )


# ---------------------------------------------------------------------------
# Signal selection
# ---------------------------------------------------------------------------


def _select_signal_fires(
    signal_table: pl.DataFrame,
    min_traders: int,
    agreement_pct: float,
    price_lo: float = 0.05,
    price_hi: float = 0.95,
) -> pl.DataFrame:
    """Filter to NO-only signals meeting thresholds, first per market by arrival_idx.

    Parameters
    ----------
    signal_table
        Full signal table from build_signal_table.
    min_traders
        Minimum number of traders in the consensus pool.
    agreement_pct
        Minimum agreement fraction.
    price_lo, price_hi
        Entry price band for trigger_entry_price.

    Returns
    -------
    pl.DataFrame
        One row per market: the first qualifying signal fire.
    """
    qualified = signal_table.filter(
        (pl.col("n_traders") >= min_traders)
        & (pl.col("agreement_frac") >= agreement_pct)
        & (pl.col("signal_direction") == "NO")
        & (pl.col("trigger_entry_price") >= price_lo)
        & (pl.col("trigger_entry_price") <= price_hi)
    )
    # First qualifying row per market (lowest arrival_idx)
    return qualified.sort("arrival_idx").group_by("condition_id").first()


# ---------------------------------------------------------------------------
# Stage 2 — Capital simulation
# ---------------------------------------------------------------------------


def stage2_capital_sim(
    signals: pl.DataFrame,
    volume: pl.DataFrame,
    capital: float,
    bet_size: float,
    spread_edge: float,
    fill_rate: float,
    fee_pct: float,
) -> dict:
    """Stage 2: month-by-month FIFO capital simulation.

    Parameters
    ----------
    signals
        Signal fires with trigger_time, resolved_at, trigger_entry_price,
        signal_direction, yes_won.
    volume
        Per-market YES-buy volume.
    capital
        Total capital available.
    bet_size
        Dollar amount per bet.
    spread_edge
        Spread edge for maker pricing.
    fill_rate
        Flat fill rate to apply.
    fee_pct
        Fee fraction.

    Returns
    -------
    dict
        Keys: total_bets, total_wins, total_pnl, months (list of monthly dicts).
    """
    max_slots = int(capital / bet_size)

    # Join with volume for fill probability
    sig = signals.join(volume, on="condition_id", how="left").with_columns(
        pl.col("yes_buy_volume").fill_null(0.0),
    )

    # Add month column
    sig = sig.with_columns(
        pl.col("trigger_time").dt.strftime("%Y-%m").alias("month"),
    )

    months_list = sorted(sig["month"].unique().to_list())

    total_bets = 0
    total_wins = 0
    total_pnl = 0.0
    monthly_results: list[dict] = []

    for m in months_list:
        month_df = sig.filter(pl.col("month") == m).sort("trigger_time")
        if month_df.height == 0:
            continue

        # Estimate median lockup in days
        lockup_series = (
            (pl.col("resolved_at") - pl.col("trigger_time")).dt.total_days()
        )
        month_df = month_df.with_columns(lockup_series.alias("lockup_days"))
        med_lockup = max(float(month_df["lockup_days"].median()), 1.0)

        # Capital-constrained capacity
        capacity_days = max_slots * 30
        max_bets = int(capacity_days / med_lockup)

        available = month_df.height
        placed = min(available, max_bets)
        if placed == 0:
            monthly_results.append({
                "month": m,
                "available": available,
                "placed": 0,
                "wins": 0,
                "pnl": 0.0,
            })
            continue

        selected = month_df.head(placed)

        # Apply fill rate
        effective_placed = max(1, int(placed * fill_rate))
        selected = selected.head(effective_placed)

        month_pnl = 0.0
        month_wins = 0
        for row in selected.iter_rows(named=True):
            yes_price = 1.0 - row["trigger_entry_price"]
            no_won = not row["yes_won"]

            pnl = compute_maker_bet_pnl(yes_price, spread_edge, bet_size, no_won, fee_pct)
            month_pnl += pnl
            if pnl > 0:
                month_wins += 1

        total_bets += effective_placed
        total_wins += month_wins
        total_pnl += month_pnl

        monthly_results.append({
            "month": m,
            "available": available,
            "placed": effective_placed,
            "wins": month_wins,
            "pnl": month_pnl,
        })

    return {
        "total_bets": total_bets,
        "total_wins": total_wins,
        "total_pnl": total_pnl,
        "months": monthly_results,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_stage1_report(all_results: pl.DataFrame) -> None:
    """Print formatted Stage 1 results table."""
    print(f"\n{'=' * 90}")
    print("  STAGE 1: Per-Bet MM Overlay (maker vs taker)")
    print(f"{'=' * 90}\n")

    has_window = "window" in all_results.columns

    if has_window:
        print(
            f"  {'Window':<16} {'Pool':<20} {'Signal':<12} {'Sprd':>5} {'Fill':<10} "
            f"{'#Bet':>5} {'T$/b':>7} {'M$/b':>7} {'T-HR':>6} {'M-HR':>6}"
        )
        print(f"  {'-' * 106}")
        for row in all_results.filter(
            pl.col("fill_model") == "flat_100%"
        ).sort(
            ["window", "pool_label", "signal_label", "spread_edge"]
        ).iter_rows(named=True):
            print(
                f"  {row['window']:<16} {row['pool_label']:<20} "
                f"{row['signal_label']:<12} {row['spread_edge']:>4.1f}c "
                f"{row['fill_model']:<10} {row['n_filled']:>5} "
                f"${row['taker_pnl_per_bet']:>5.1f} ${row['maker_pnl_per_bet']:>5.1f} "
                f"{row['taker_hr']:>5.1%} {row['maker_hr']:>5.1%}"
            )
    else:
        print(
            f"  {'Pool':<20} {'Signal':<14} {'Spread':>7} {'Fill Model':<16} "
            f"{'#Bets':>6} {'Taker$/b':>9} {'Maker$/b':>9} {'Delta':>8} "
            f"{'T-HR':>6} {'M-HR':>6}"
        )
        print(f"  {'-' * 88}")
        for row in all_results.sort(
            ["pool_label", "signal_label", "spread_edge", "fill_model"]
        ).iter_rows(named=True):
            print(
                f"  {row['pool_label']:<20} {row['signal_label']:<14} "
                f"{row['spread_edge']:>7.3f} {row['fill_model']:<16} "
                f"{row['n_filled']:>6} ${row['taker_pnl_per_bet']:>7.2f} "
                f"${row['maker_pnl_per_bet']:>7.2f} ${row['maker_delta']:>6.0f} "
                f"{row['taker_hr']:>5.1%} {row['maker_hr']:>5.1%}"
            )


def print_stage2_report(sim_results: list[dict]) -> None:
    """Print formatted Stage 2 monthly simulation results."""
    print(f"\n{'=' * 90}")
    print("  STAGE 2: Capital Simulation (month-by-month)")
    print(f"{'=' * 90}\n")

    for sim in sim_results:
        label = sim.get("label", "unknown")
        print(f"\n  --- {label} ---")
        print(
            f"  {'Month':<10} {'Avail':>6} {'Placed':>7} {'Wins':>6} "
            f"{'HR':>7} {'PnL':>10}"
        )
        print(f"  {'-' * 50}")

        cum_pnl = 0.0
        for mr in sim["months"]:
            cum_pnl += mr["pnl"]
            if mr["placed"] > 0:
                hr = mr["wins"] / mr["placed"] * 100
                print(
                    f"  {mr['month']:<10} {mr['available']:>6} {mr['placed']:>7} "
                    f"{mr['wins']:>6} {hr:>6.1f}% ${mr['pnl']:>8,.0f}"
                )
            else:
                print(
                    f"  {mr['month']:<10} {mr['available']:>6} {mr['placed']:>7} "
                    f"{'---':>6} {'---':>7} {'$0':>10}"
                )

        n_bets = sim["total_bets"]
        n_wins = sim["total_wins"]
        hr = n_wins / n_bets * 100 if n_bets > 0 else 0
        active_months = len([m for m in sim["months"] if m["placed"] > 0])
        avg_mo = sim["total_pnl"] / active_months if active_months > 0 else 0
        print(f"  {'-' * 50}")
        print(
            f"  TOTAL: {n_bets} bets, {hr:.1f}% HR, ${sim['total_pnl']:,.0f} PnL, "
            f"${avg_mo:,.0f}/month"
        )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def main() -> None:
    """Full orchestration: load data, build signals, run Stage 1 + Stage 2."""
    force_volume = "--force-volume" in sys.argv
    all_windows_mode = "--all-windows" in sys.argv
    t0 = time.time()

    # Step 1: Load data
    df_pnl, mvf_df, markets, price_ts = _load_data()

    # Step 2: Load config and generate windows
    config = load_config(CONFIG_PATH)
    windows = config.generate_windows()

    dev_windows = [w for w in windows if not w.is_test]
    if not dev_windows:
        print("[error] No dev windows found in config.")
        return

    if all_windows_mode:
        target_windows = dev_windows
        print(f"[config] Running ALL {len(target_windows)} dev windows")
    else:
        target_windows = [dev_windows[-1]]
        print(f"[config] Using window: {target_windows[0].name}")

    for win in target_windows:
        print(
            f"[config] Window {win.name}: "
            f"train {win.train_start:%Y-%m-%d} to {win.train_end:%Y-%m-%d}, "
            f"holdout {win.holdout_start:%Y-%m-%d} to {win.holdout_end:%Y-%m-%d}"
        )

    # Step 3: Pre-compute MVF subsets and trader median entry
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    for band, traders in mvf_subsets.items():
        print(f"[mvf] {band}: {len(traders):,} traders")

    trader_median_entry = _compute_trader_median_entry(df_pnl)
    print(f"[entry] Computed median directional entry for {trader_median_entry.height:,} traders")

    # Step 4: Load/compute YES-buy volume
    volume = load_or_compute_yes_buy_volume(force=force_volume)
    print(f"[volume] {volume.height:,} markets with YES-buy volume")

    # Step 5 + 6: For each window, build signals and run Stage 1
    all_stage1: list[pl.DataFrame] = []
    signal_cache: dict[str, pl.DataFrame] = {}
    delay_s = 60.0

    for win in target_windows:
        holdout_data = df_pnl.filter(
            (pl.col("resolved_at") >= win.holdout_start)
            & (pl.col("resolved_at") < win.holdout_end)
        )
        if holdout_data.height == 0:
            print(f"[{win.name}] No holdout data, skipping")
            continue
        print(f"\n[{win.name}] {holdout_data.height:,} trader-market rows in holdout")

        entry_prices = None
        if price_ts is not None:
            PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path = PRICE_CACHE_DIR / f"{win.name}_delay_{delay_s}.parquet"
            if cache_path.exists():
                entry_prices = pl.read_parquet(cache_path)
                print(f"[{win.name}] Loaded cached forward prices ({entry_prices.height:,} rows)")
            else:
                print(f"[{win.name}] Pre-computing forward prices delay={delay_s}s...")
                entry_prices = _precompute_entry_prices(
                    holdout_data, price_ts,
                    execution_delay_s=delay_s,
                    max_price_delay_s=config.max_price_delay_s,
                )
                entry_prices.write_parquet(cache_path)
                print(f"[{win.name}] Cached {entry_prices.height:,} rows")

        for pcfg in POOL_CONFIGS:
            n_months = pcfg["n_months"]
            min_mkts = pcfg["min_mkts"]
            mvf_band = pcfg["mvf_band"]
            max_entry = pcfg["max_entry"]
            pool_label = f"m{n_months}_k{min_mkts}_{mvf_band}_e{max_entry}"

            skilled = get_consistent_traders(
                df_pnl, win.train_start, win.train_end,
                int(n_months), int(min_mkts),
            )
            pool = skilled & mvf_subsets[str(mvf_band)]

            eligible = set(
                trader_median_entry.filter(
                    pl.col("median_entry") <= float(max_entry)
                )["trader"].to_list()
            )
            pool = pool & eligible

            if len(pool) < 5:
                continue

            signal_table = build_signal_table(holdout_data, pool, mvf_df)
            if signal_table.height < 20:
                continue

            if entry_prices is not None:
                signal_table = _apply_precomputed_prices(signal_table, entry_prices)

            for scfg in SIGNAL_CONFIGS:
                min_t = scfg["min_traders"]
                agree = scfg["agreement_pct"]
                signal_label = f"t{min_t}_a{agree:.0%}"

                fires = _select_signal_fires(signal_table, min_t, agree)
                if fires.height < 5:
                    continue

                cache_key = f"{win.name}_{pool_label}_{signal_label}"
                signal_cache[cache_key] = fires

                result = stage1_mm_overlay(
                    signals=fires,
                    volume=volume,
                    spread_edges=SPREAD_EDGES,
                    flat_fill_rates=FLAT_FILL_RATES,
                    bet_size=BET_SIZE,
                    fee_pct=0.0,
                )

                if result.height > 0:
                    result = result.with_columns(
                        pl.lit(win.name).alias("window"),
                        pl.lit(pool_label).alias("pool_label"),
                        pl.lit(signal_label).alias("signal_label"),
                        pl.lit(fires.height).alias("n_signal_fires"),
                    )
                    all_stage1.append(result)

    if not all_stage1:
        print("\n[done] No Stage 1 results. Check data availability.")
        return

    combined_s1 = pl.concat(all_stage1, how="diagonal_relaxed")
    print(f"\n[stage1] {combined_s1.height} result rows across all configs")

    print_stage1_report(combined_s1)

    # Step 7: Multi-window summary — aggregate across windows at best signal config
    if all_windows_mode and "window" in combined_s1.columns:
        full_fill = combined_s1.filter(pl.col("fill_model") == "flat_100%")
        if full_fill.height > 0:
            print(f"\n{'=' * 90}")
            print("  MULTI-WINDOW SUMMARY (100% fill, 2c spread)")
            print(f"{'=' * 90}\n")

            best_spread = full_fill.filter(pl.col("spread_edge") == 0.02)
            if best_spread.height > 0:
                summary = best_spread.group_by(
                    ["pool_label", "signal_label"]
                ).agg(
                    pl.col("taker_pnl_per_bet").mean().alias("avg_taker_ppb"),
                    pl.col("maker_pnl_per_bet").mean().alias("avg_maker_ppb"),
                    pl.col("maker_delta").mean().alias("avg_delta"),
                    pl.col("taker_hr").mean().alias("avg_taker_hr"),
                    pl.col("maker_hr").mean().alias("avg_maker_hr"),
                    pl.col("n_filled").sum().alias("total_bets"),
                    pl.col("window").n_unique().alias("n_windows"),
                ).sort("avg_maker_ppb", descending=True)

                print(
                    f"  {'Pool':<28} {'Signal':<12} {'#Win':>5} {'#Bets':>6} "
                    f"{'T-$/bet':>8} {'M-$/bet':>8} {'Delta':>7} {'T-HR':>6} {'M-HR':>6}"
                )
                print(f"  {'-' * 90}")
                for row in summary.iter_rows(named=True):
                    print(
                        f"  {row['pool_label']:<28} {row['signal_label']:<12} "
                        f"{row['n_windows']:>5} {row['total_bets']:>6} "
                        f"${row['avg_taker_ppb']:>6.2f} ${row['avg_maker_ppb']:>6.2f} "
                        f"${row['avg_delta']:>5.0f} {row['avg_taker_hr']:>5.1%} "
                        f"{row['avg_maker_hr']:>5.1%}"
                    )

    # Step 8: Stage 2 — pick top configs by maker_pnl_per_bet and simulate
    # Filter to flat_100% fill model for fair comparison
    full_fill = combined_s1.filter(pl.col("fill_model") == "flat_100%")
    if full_fill.height == 0:
        print("[stage2] No full-fill results for ranking.")
        return

    top = full_fill.sort("maker_pnl_per_bet", descending=True).head(TOP_N_FOR_SIM)

    sim_results: list[dict] = []
    taker_results: list[dict] = []

    for row in top.iter_rows(named=True):
        win_name = row.get("window", target_windows[-1].name)
        cache_key = f"{win_name}_{row['pool_label']}_{row['signal_label']}"
        fires = signal_cache.get(cache_key)
        if fires is None or fires.height == 0:
            continue

        spread = row["spread_edge"]
        label = f"{row['pool_label']} {row['signal_label']} spread={spread}"

        # Maker sim
        sim = stage2_capital_sim(
            signals=fires,
            volume=volume,
            capital=CAPITAL,
            bet_size=BET_SIZE,
            spread_edge=spread,
            fill_rate=0.50,
            fee_pct=0.0,
        )
        sim["label"] = f"MM: {label}"
        sim_results.append(sim)

        # Taker baseline (same signals, spread_edge=0 approximation via taker PnL)
        taker_sim = stage2_capital_sim(
            signals=fires,
            volume=volume,
            capital=CAPITAL,
            bet_size=BET_SIZE,
            spread_edge=0.0,
            fill_rate=1.0,
            fee_pct=0.0,
        )
        taker_sim["label"] = f"Taker: {label}"
        taker_results.append(taker_sim)

    print_stage2_report(sim_results)

    # Step 8: Summary comparison
    print(f"\n{'=' * 90}")
    print("  SUMMARY: Maker vs Taker (top configs)")
    print(f"{'=' * 90}\n")

    print(
        f"  {'Config':<50} {'Mode':<8} {'Bets':>5} {'HR':>7} "
        f"{'PnL':>10} {'$/bet':>8}"
    )
    print(f"  {'-' * 90}")

    for mm_sim, tk_sim in zip(sim_results, taker_results, strict=True):
        mm_label = mm_sim["label"].replace("MM: ", "")
        mm_bets = mm_sim["total_bets"]
        mm_hr = mm_sim["total_wins"] / mm_bets * 100 if mm_bets > 0 else 0
        mm_ppb = mm_sim["total_pnl"] / mm_bets if mm_bets > 0 else 0

        tk_bets = tk_sim["total_bets"]
        tk_hr = tk_sim["total_wins"] / tk_bets * 100 if tk_bets > 0 else 0
        tk_ppb = tk_sim["total_pnl"] / tk_bets if tk_bets > 0 else 0

        print(
            f"  {mm_label:<50} {'Maker':<8} {mm_bets:>5} {mm_hr:>6.1f}% "
            f"${mm_sim['total_pnl']:>8,.0f} ${mm_ppb:>6.2f}"
        )
        print(
            f"  {'':<50} {'Taker':<8} {tk_bets:>5} {tk_hr:>6.1f}% "
            f"${tk_sim['total_pnl']:>8,.0f} ${tk_ppb:>6.2f}"
        )

    elapsed = time.time() - t0
    print(f"\n[done] Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
