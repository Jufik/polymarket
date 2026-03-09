"""Tick-by-tick validation of BTC Up/Down scalp convergence strategy.

Strategy:
- Entry: BUY underpriced PM side when |GBM_P_up - PM_P_up| > ENTRY_THRESHOLD
- Exit: SELL when deviation < EXIT_THRESHOLD OR time remaining < 30s
- Position: $50/scalp, 1 position per window max

Fill model:
- Entry fill: last PM trade price + 0.01 slippage
- Exit fill: last PM trade price - 0.01 slippage
- Fee: 3% per side on notional
- Latency: 1-second delay between signal and fill
- Skip if no PM trade within 5 seconds of signal
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import polars as pl

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

from polymarket_pipeline.strategies_impl.crypto_gbm.gbm import (
    compute_gbm_p_up,
    estimate_rolling_sigma,
)

# ─── Parameters ────────────────────────────────────────────────────────────────
ENTRY_THRESHOLD = 0.10       # GBM vs PM deviation threshold to enter
EXIT_THRESHOLD = 0.02        # Deviation threshold to exit (convergence complete)
MIN_TIME_REMAINING_SEC = 30  # Exit if less than this many seconds remain
ENTRY_SLIPPAGE = 0.01        # Half-spread cost on entry
EXIT_SLIPPAGE = 0.01         # Half-spread cost on exit
FEE_PCT = 0.03               # Fee per side (3%)
POSITION_USD = 50.0          # Notional per scalp
LATENCY_SEC = 1              # Signal → fill delay
MAX_FILL_WAIT_SEC = 5        # Max seconds to wait for a PM trade after signal

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/validate_scalp.log")
OUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-scalp-convergence/validation")

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
DATE_START = "2025-12-09"
DATE_END = "2026-03-10"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# ─── Data Loading ──────────────────────────────────────────────────────────────

def load_exchange_bars() -> dict[int, float]:
    """Load Binance 1s bars from ClickHouse.
    Returns dict of unix_timestamp_sec -> BTC close price.
    """
    import clickhouse_connect
    log("Loading Binance 1s bars from ClickHouse...")
    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    result = client.query(f"""
        SELECT toUnixTimestamp(ts) AS ts_sec, close
        FROM {CH_DB}.exchange_bars
        WHERE exchange = 'BINANCE' AND symbol = 'BTC-USDT'
        AND ts >= '{DATE_START}' AND ts < '{DATE_END}'
        ORDER BY ts
    """)
    bars: dict[int, float] = {}
    for ts_sec, close in result.result_rows:
        bars[int(ts_sec)] = float(close)
    log(f"  Loaded {len(bars):,} bars ({len(bars)//3600:.0f} hours)")
    return bars


def load_markets() -> pl.DataFrame:
    """Load BTC Up/Down market windows."""
    log("Loading markets.parquet...")
    df = pl.read_parquet("data/research/btc_updown/markets.parquet")
    # Filter to our date range
    df = df.filter(
        (pl.col("window_start_utc") >= pl.lit(DATE_START).str.to_datetime(time_zone="UTC"))
        & (pl.col("window_start_utc") < pl.lit(DATE_END).str.to_datetime(time_zone="UTC"))
    )
    log(f"  Loaded {len(df):,} markets in date range")
    return df


def load_trades_for_windows(condition_ids: list[str]) -> pl.DataFrame:
    """Load PM trades for specific windows, sorted by timestamp."""
    log("Loading PM trades.parquet (lazy scan)...")
    df = (
        pl.scan_parquet("data/research/btc_updown/trades.parquet")
        .filter(pl.col("condition_id").is_in(condition_ids))
        .filter(pl.col("timestamp") >= pl.lit(DATE_START).str.to_datetime(time_zone="UTC"))
        .sort("timestamp")
        .collect()
    )
    log(f"  Loaded {len(df):,} trades for {df['condition_id'].n_unique()} markets")
    return df


# ─── Sigma Estimation ──────────────────────────────────────────────────────────

class SigmaEstimator:
    """Rolling 1440-min sigma from 1-second bars, downsampled to 1-minute closes."""

    def __init__(self, bars: dict[int, float]):
        # Build sorted list of (ts_sec, close) for minute-bar computation
        sorted_ts = sorted(bars.keys())
        self._bars = bars
        self._sorted_ts = sorted_ts
        self._cache: dict[int, float | None] = {}  # minute_ts -> sigma

    def get_sigma_at(self, ts_sec: int, window_start_sec: int) -> float | None:
        """Get per-minute sigma using data available before window_start.

        Uses 1-minute closes from the 1440 minutes before window_start.
        """
        # Round to minute
        minute_key = (window_start_sec // 60) * 60
        if minute_key in self._cache:
            return self._cache[minute_key]

        # Build 1-minute closes up to window_start
        lookback_start = minute_key - 1440 * 60  # 24h
        closes = []
        for m in range(lookback_start, minute_key, 60):
            # Take the close of this minute = last bar at m+59
            best = None
            for s in range(m, m + 60):
                if s in self._bars:
                    best = self._bars[s]
            if best is not None:
                closes.append(best)

        sigma = estimate_rolling_sigma(closes, window=1440, min_samples=60)
        self._cache[minute_key] = sigma
        return sigma


# ─── Scalp Record ──────────────────────────────────────────────────────────────

@dataclass
class ScalpRecord:
    condition_id: str
    window_start_sec: int
    window_end_sec: int
    duration_min: int
    winner_outcome: str    # 'Up' or 'Down'
    token_yes: str
    token_no: str

    # Signal
    signal_time_sec: int
    signal_direction: str  # 'YES' or 'NO'
    gbm_p_up: float
    pm_p_up: float
    deviation: float

    # Fill
    fill_time_sec: Optional[int] = None
    entry_price: Optional[float] = None
    entry_reason: str = ""

    # Exit
    exit_time_sec: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""

    # Outcome
    pnl_gross_pct: Optional[float] = None
    pnl_net_pct: Optional[float] = None
    pnl_usd: Optional[float] = None
    hold_seconds: Optional[int] = None
    outcome: str = ""  # 'win', 'loss', 'miss_entry', 'miss_exit'
    won_gbm: Optional[bool] = None   # Did GBM prediction come true?


# ─── Window Processor ──────────────────────────────────────────────────────────

def process_window(
    market: dict,
    window_trades: pl.DataFrame,
    bars: dict[int, float],
    sigma_estimator: SigmaEstimator,
) -> Optional[ScalpRecord]:
    """Process one BTC Up/Down window. Returns a ScalpRecord or None if no signal."""

    cid = market["condition_id"]
    token_yes = market["token_yes"]
    token_no = market["token_no"]
    winner = market["winner_outcome"]  # 'Up' or 'Down'
    window_start_sec = int(market["window_start_utc"].timestamp())
    window_end_sec = int(market["window_end_utc"].timestamp())
    duration_min = market["duration_min"]

    # S₀ = BTC price at window open
    s_start = bars.get(window_start_sec)
    if s_start is None:
        # Try ±1 second
        for delta in [1, -1, 2, -2, 3]:
            s_start = bars.get(window_start_sec + delta)
            if s_start is not None:
                break
    if s_start is None:
        return None

    # Sigma from training data up to window start
    sigma = sigma_estimator.get_sigma_at(window_start_sec, window_start_sec)
    if sigma is None or sigma <= 0:
        return None  # Not enough history

    # Build per-second PM price series for this window
    # Last YES token trade price at each second
    yes_trades = window_trades.filter(pl.col("asset_id") == token_yes)
    no_trades = window_trades.filter(pl.col("asset_id") == token_no)

    # Build second-level PM price index: ts_sec -> last_yes_price
    pm_price_by_sec: dict[int, float] = {}
    all_pm = window_trades.sort("timestamp")
    cur_yes_price: float | None = None

    for row in all_pm.iter_rows(named=True):
        ts = int(row["timestamp"].timestamp())
        asset = row["asset_id"]
        price = float(row["price"])
        if asset == token_yes:
            cur_yes_price = price
        elif asset == token_no:
            # NO token price → YES probability = 1 - NO price
            cur_yes_price = 1.0 - price
        if cur_yes_price is not None:
            pm_price_by_sec[ts] = cur_yes_price

    if not pm_price_by_sec:
        return None

    # ── Signal detection: walk second by second ────────────────────────────────
    state = "open"     # open | waiting_fill | in_position | closed
    signal_time_sec: int | None = None
    signal_direction: str | None = None
    signal_gbm: float | None = None
    signal_pm: float | None = None
    signal_dev: float | None = None
    fill_time_sec: int | None = None
    fill_price: float | None = None
    fill_reason: str = ""
    exit_time_sec: int | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    waiting_fill_since: int | None = None

    # We need sorted seconds in window
    all_secs = sorted(set(
        list(bars.keys()) + list(pm_price_by_sec.keys())
    ))
    window_secs = [s for s in all_secs if window_start_sec <= s < window_end_sec]

    # Carry forward last known PM price
    last_pm_p_up: float | None = None

    for sec in window_secs:
        # Update PM price if we have a trade at this second
        if sec in pm_price_by_sec:
            last_pm_p_up = pm_price_by_sec[sec]

        if last_pm_p_up is None:
            continue  # No PM data yet

        # Compute GBM
        s_current = bars.get(sec)
        if s_current is None:
            continue

        time_remaining_min = (window_end_sec - sec) / 60.0
        if time_remaining_min <= 0:
            break

        gbm_p_up = compute_gbm_p_up(s_start, s_current, sigma, time_remaining_min)
        pm_p_up = last_pm_p_up
        deviation = gbm_p_up - pm_p_up  # + = YES underpriced, - = NO underpriced
        abs_dev = abs(deviation)

        if state == "open":
            # Look for entry signal
            if abs_dev >= ENTRY_THRESHOLD:
                # Signal! Now wait LATENCY_SEC seconds for fill
                signal_time_sec = sec
                signal_direction = "YES" if deviation > 0 else "NO"
                signal_gbm = gbm_p_up
                signal_pm = pm_p_up
                signal_dev = deviation
                state = "waiting_fill"
                waiting_fill_since = sec

        elif state == "waiting_fill":
            # Wait LATENCY_SEC seconds, then fill at next PM trade price
            assert waiting_fill_since is not None
            elapsed = sec - waiting_fill_since
            if elapsed >= LATENCY_SEC:
                # Check if PM price available within MAX_FILL_WAIT_SEC
                if elapsed > (LATENCY_SEC + MAX_FILL_WAIT_SEC):
                    # Missed fill — no PM trade in time window
                    return ScalpRecord(
                        condition_id=cid,
                        window_start_sec=window_start_sec,
                        window_end_sec=window_end_sec,
                        duration_min=duration_min,
                        winner_outcome=winner,
                        token_yes=token_yes,
                        token_no=token_no,
                        signal_time_sec=signal_time_sec,
                        signal_direction=signal_direction,
                        gbm_p_up=signal_gbm,
                        pm_p_up=signal_pm,
                        deviation=signal_dev,
                        outcome="miss_entry",
                    )
                # Check for PM trade at this second
                if sec in pm_price_by_sec:
                    raw_fill = pm_price_by_sec[sec]
                    if signal_direction == "YES":
                        # Buying YES: we pay price + slippage (worse for buyer)
                        fill_price = min(raw_fill + ENTRY_SLIPPAGE, 0.99)
                    else:
                        # Buying NO → we see this as (1 - fill price for YES)
                        # but the NO token: we pay higher NO price → lower YES price seen
                        fill_price = max(raw_fill - ENTRY_SLIPPAGE, 0.01)  # YES equiv price falls
                    fill_time_sec = sec
                    fill_reason = "pm_trade"
                    state = "in_position"

        elif state == "in_position":
            assert fill_price is not None
            time_remaining_sec = window_end_sec - sec

            # Exit conditions:
            # 1. Deviation converged (< EXIT_THRESHOLD)
            # 2. Time running out (< MIN_TIME_REMAINING_SEC)
            should_exit = False
            cur_exit_reason = ""

            if time_remaining_sec <= MIN_TIME_REMAINING_SEC:
                should_exit = True
                cur_exit_reason = "time_stop"
            elif abs_dev < EXIT_THRESHOLD:
                should_exit = True
                cur_exit_reason = "converged"

            if should_exit:
                # Find PM price for exit (current second or next available)
                raw_exit = pm_price_by_sec.get(sec, last_pm_p_up)
                if signal_direction == "YES":
                    # Selling YES: we receive price - slippage
                    exit_price_val = max(raw_exit - EXIT_SLIPPAGE, 0.01)
                else:
                    # Selling NO → exit YES equiv: we receive price + slippage
                    exit_price_val = min(raw_exit + EXIT_SLIPPAGE, 0.99)

                exit_time_sec = sec
                exit_price = exit_price_val
                exit_reason = cur_exit_reason
                state = "closed"
                break

    # If we hit window end in position, exit at last price
    if state == "in_position" and fill_price is not None:
        raw_exit = last_pm_p_up or fill_price
        if signal_direction == "YES":
            exit_price = max(raw_exit - EXIT_SLIPPAGE, 0.01)
        else:
            exit_price = min(raw_exit + EXIT_SLIPPAGE, 0.99)
        exit_time_sec = window_end_sec
        exit_reason = "window_end"
        state = "closed"

    if state not in ("closed",):
        return None  # No signal fired or no fill

    if signal_time_sec is None or fill_time_sec is None:
        return None

    # ── Compute PnL ────────────────────────────────────────────────────────────
    assert fill_price is not None and exit_price is not None

    # For YES: bought at fill_price, sold at exit_price
    # For NO: bought NO at (1-fill_price), sold at (1-exit_price)
    # Either way, express as fraction of YES probability:
    # Entry cost: fill_price, Exit revenue: exit_price
    gross_pct = exit_price - fill_price  # positive = profit
    fee_cost = FEE_PCT * fill_price + FEE_PCT * exit_price  # fees on both sides
    net_pct = gross_pct - fee_cost

    # Did GBM prediction come true?
    won_gbm = (winner == "Up") == (signal_direction == "YES")

    pnl_usd = net_pct * POSITION_USD
    hold_sec = exit_time_sec - fill_time_sec if exit_time_sec is not None else None

    outcome = "win" if net_pct > 0 else "loss"

    return ScalpRecord(
        condition_id=cid,
        window_start_sec=window_start_sec,
        window_end_sec=window_end_sec,
        duration_min=duration_min,
        winner_outcome=winner,
        token_yes=token_yes,
        token_no=token_no,
        signal_time_sec=signal_time_sec,
        signal_direction=signal_direction,
        gbm_p_up=signal_gbm,
        pm_p_up=signal_pm,
        deviation=signal_dev,
        fill_time_sec=fill_time_sec,
        entry_price=fill_price,
        entry_reason=fill_reason,
        exit_time_sec=exit_time_sec,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_gross_pct=gross_pct,
        pnl_net_pct=net_pct,
        pnl_usd=pnl_usd,
        hold_seconds=hold_sec,
        outcome=outcome,
        won_gbm=won_gbm,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    open(LOG_PATH, "w").close()

    log("=== BTC Scalp Convergence Tick-by-Tick Validation ===")
    log(f"Entry threshold: {ENTRY_THRESHOLD:.0%}, Exit: {EXIT_THRESHOLD:.0%}")
    log(f"Latency: {LATENCY_SEC}s, Max fill wait: {MAX_FILL_WAIT_SEC}s")
    log(f"Position: ${POSITION_USD}, Fee: {FEE_PCT:.0%}/side")

    # 1. Load data
    bars = load_exchange_bars()
    markets_df = load_markets()
    markets = markets_df.to_dicts()
    log(f"Processing {len(markets):,} windows")

    condition_ids = [m["condition_id"] for m in markets]
    all_trades = load_trades_for_windows(condition_ids)

    # 2. Build sigma estimator
    sigma_estimator = SigmaEstimator(bars)
    log("Sigma estimator ready")

    # 3. Group trades by condition_id for fast lookup
    log("Grouping trades by condition_id...")
    trades_by_cid: dict[str, pl.DataFrame] = {}
    for cid, grp in all_trades.group_by("condition_id"):
        trades_by_cid[cid[0]] = grp.sort("timestamp")
    log(f"  {len(trades_by_cid)} condition_ids with trades")

    # 4. Process windows
    records: list[ScalpRecord] = []
    n_no_signal = 0
    n_no_trades = 0
    n_no_bars = 0
    n_no_sigma = 0

    log("Processing windows...")
    for i, market in enumerate(markets):
        if i % 1000 == 0:
            log(f"  Progress: {i}/{len(markets)} ({i/len(markets):.0%})")

        cid = market["condition_id"]
        window_trades = trades_by_cid.get(cid, pl.DataFrame())

        if len(window_trades) == 0:
            n_no_trades += 1
            continue

        window_start_sec = int(market["window_start_utc"].timestamp())
        if bars.get(window_start_sec) is None:
            # Check ±3 seconds
            found = any(bars.get(window_start_sec + d) is not None for d in range(-3, 4))
            if not found:
                n_no_bars += 1
                continue

        rec = process_window(market, window_trades, bars, sigma_estimator)
        if rec is None:
            n_no_signal += 1
        else:
            records.append(rec)

    log(f"\nProcessing complete: {len(records):,} records")
    log(f"  No trades: {n_no_trades}, No bars: {n_no_bars}, No signal: {n_no_signal}")

    # 5. Categorize
    filled = [r for r in records if r.outcome in ("win", "loss")]
    misses = [r for r in records if r.outcome == "miss_entry"]

    log(f"\n=== Results ===")
    log(f"Total signals: {len(records):,}")
    log(f"  Filled (win/loss): {len(filled):,}")
    log(f"  Missed entry: {len(misses):,}")

    if not filled:
        log("No filled trades — aborting analysis")
        return

    # 6. Compute metrics
    net_pnls = [r.pnl_net_pct for r in filled if r.pnl_net_pct is not None]
    pnl_usd_list = [r.pnl_usd for r in filled if r.pnl_usd is not None]
    hold_secs = [r.hold_seconds for r in filled if r.hold_seconds is not None]
    won_gbm_list = [r.won_gbm for r in filled if r.won_gbm is not None]

    n_wins = sum(1 for p in net_pnls if p > 0)
    n_losses = sum(1 for p in net_pnls if p <= 0)
    hit_rate = sum(1 for r in filled if r.won_gbm) / len(won_gbm_list) if won_gbm_list else 0
    pct_profitable = n_wins / len(net_pnls) if net_pnls else 0
    median_net_pnl = float(np.median(net_pnls))
    mean_net_pnl = float(np.mean(net_pnls))
    median_hold = float(np.median(hold_secs)) if hold_secs else 0
    total_pnl_usd = sum(pnl_usd_list)

    # Sharpe (daily)
    # Group PnL by day
    daily_pnl: dict[str, float] = defaultdict(float)
    for r in filled:
        if r.fill_time_sec and r.pnl_usd is not None:
            day = datetime.fromtimestamp(r.fill_time_sec, tz=timezone.utc).strftime("%Y-%m-%d")
            daily_pnl[day] += r.pnl_usd

    daily_vals = list(daily_pnl.values())
    if len(daily_vals) > 1:
        daily_mean = np.mean(daily_vals)
        daily_std = np.std(daily_vals, ddof=1)
        sharpe = (daily_mean / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown (cumulative PnL series)
    sorted_records = sorted(filled, key=lambda r: r.fill_time_sec or 0)
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in sorted_records:
        if r.pnl_usd is not None:
            cum_pnl += r.pnl_usd
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd:
                max_dd = dd

    # Exit reason breakdown
    exit_reasons: dict[str, int] = defaultdict(int)
    for r in filled:
        exit_reasons[r.exit_reason] += 1

    # 7. Regime breakdown
    def regime_stats(recs: list[ScalpRecord], label: str) -> dict:
        if not recs:
            return {}
        pnls = [r.pnl_net_pct for r in recs if r.pnl_net_pct is not None]
        holds = [r.hold_seconds for r in recs if r.hold_seconds is not None]
        gbm_wins = [r.won_gbm for r in recs if r.won_gbm is not None]
        profitable = sum(1 for p in pnls if p > 0)
        return {
            "n": len(recs),
            "median_net_pnl": round(float(np.median(pnls)), 4) if pnls else None,
            "pct_profitable": round(profitable / len(pnls), 4) if pnls else None,
            "hit_rate": round(sum(gbm_wins) / len(gbm_wins), 4) if gbm_wins else None,
            "median_hold_sec": round(float(np.median(holds)), 1) if holds else None,
        }

    regime_5min = [r for r in filled if r.duration_min == 5]
    regime_15min = [r for r in filled if r.duration_min == 15]
    regime_yes = [r for r in filled if r.signal_direction == "YES"]
    regime_no = [r for r in filled if r.signal_direction == "NO"]

    # By hour
    def get_hour(r: ScalpRecord) -> int:
        if r.fill_time_sec:
            return datetime.fromtimestamp(r.fill_time_sec, tz=timezone.utc).hour
        return -1
    by_hour: dict[str, list] = defaultdict(list)
    for r in filled:
        h = get_hour(r)
        bucket = f"UTC_{(h//6)*6:02d}-{(h//6)*6+5:02d}"
        by_hour[bucket].append(r)

    # By week
    def get_week(r: ScalpRecord) -> str:
        if r.fill_time_sec:
            dt = datetime.fromtimestamp(r.fill_time_sec, tz=timezone.utc)
            return dt.strftime("W%V_%Y")
        return "unknown"
    by_week: dict[str, list] = defaultdict(list)
    for r in filled:
        by_week[get_week(r)].append(r)

    # 8. Print comparison table
    log("\n" + "="*65)
    log("COMPARISON: Vectorized (UB) vs Tick-by-Tick")
    log("="*65)
    log(f"{'Metric':<30} {'Vectorized':>12} {'Tick':>12} {'Degrad':>10}")
    log("-"*65)
    n_vec = 12564
    hr_vec = 0.4541
    prof_vec = 0.8312
    pnl_vec = 0.0866
    hold_vec = 22
    log(f"{'Events'::<30} {n_vec:>12,} {len(filled):>12,} {'':>10}")
    log(f"{'Hit Rate (GBM correct)'::<30} {hr_vec:>11.1%} {hit_rate:>11.1%} {hit_rate-hr_vec:>+9.1%}")
    log(f"{'Profitable %'::<30} {prof_vec:>11.1%} {pct_profitable:>11.1%} {pct_profitable-prof_vec:>+9.1%}")
    log(f"{'Median Net PnL %'::<30} {pnl_vec:>11.1%} {median_net_pnl:>11.1%} {median_net_pnl-pnl_vec:>+9.1%}")
    log(f"{'Median Hold (s)'::<30} {hold_vec:>12} {median_hold:>12.0f} {'':>10}")
    log(f"{'Total PnL ($50/scalp)'::<30} {'N/A':>12} {total_pnl_usd:>11,.0f} {'':>10}")
    log(f"{'Sharpe (daily)'::<30} {'N/A':>12} {sharpe:>12.2f} {'':>10}")
    log(f"{'Max Drawdown ($)'::<30} {'N/A':>12} {max_dd:>11,.0f} {'':>10}")
    log(f"{'Miss Entry Rate'::<30} {'N/A':>12} {len(misses)/(len(records) or 1):>11.1%} {'':>10}")
    log("")
    log("Exit reason breakdown:")
    for reason, cnt in sorted(exit_reasons.items()):
        log(f"  {reason}: {cnt} ({cnt/len(filled):.1%})")

    log("\nRegime Breakdown:")
    log(f"  5-min windows:  {regime_stats(regime_5min, '5min')}")
    log(f"  15-min windows: {regime_stats(regime_15min, '15min')}")
    log(f"  Buy YES:        {regime_stats(regime_yes, 'YES')}")
    log(f"  Buy NO:         {regime_stats(regime_no, 'NO')}")
    for h_bucket in sorted(by_hour.keys()):
        log(f"  {h_bucket}: {regime_stats(by_hour[h_bucket], h_bucket)}")
    log("\nWeekly PnL stability:")
    for w in sorted(by_week.keys()):
        ws = by_week[w]
        w_pnl = sum(r.pnl_usd for r in ws if r.pnl_usd is not None)
        w_pnls = [r.pnl_net_pct for r in ws if r.pnl_net_pct is not None]
        w_prof = sum(1 for p in w_pnls if p > 0) / len(w_pnls) if w_pnls else 0
        log(f"  {w}: n={len(ws)}, total_pnl=${w_pnl:.0f}, profitable={w_prof:.0%}")

    # 9. Verdict
    log("\n=== VERDICT ===")
    if pct_profitable >= 0.55 and median_net_pnl > 0 and total_pnl_usd > 0:
        if pct_profitable >= 0.65 and total_pnl_usd > 1000:
            verdict = "GO"
        else:
            verdict = "NEEDS-MORE-WORK"
    else:
        verdict = "NO-GO"
    log(f"Verdict: {verdict}")
    log(f"Rationale: {pct_profitable:.0%} profitable, median net PnL {median_net_pnl:.1%}, total ${total_pnl_usd:.0f} over 90 days")

    # 10. Save results
    results = {
        "hypothesis": "crypto-scalp-convergence",
        "phase": "tick-by-tick-validation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "entry_threshold": ENTRY_THRESHOLD,
            "exit_threshold": EXIT_THRESHOLD,
            "min_time_remaining_sec": MIN_TIME_REMAINING_SEC,
            "entry_slippage": ENTRY_SLIPPAGE,
            "exit_slippage": EXIT_SLIPPAGE,
            "fee_pct": FEE_PCT,
            "position_usd": POSITION_USD,
            "latency_sec": LATENCY_SEC,
            "max_fill_wait_sec": MAX_FILL_WAIT_SEC,
        },
        "metrics": {
            "n_windows": len(markets),
            "n_signals": len(records),
            "n_filled": len(filled),
            "n_miss_entry": len(misses),
            "fill_rate": len(filled) / len(records) if records else 0,
            "hit_rate_gbm": round(hit_rate, 4),
            "pct_profitable": round(pct_profitable, 4),
            "median_net_pnl": round(median_net_pnl, 4),
            "mean_net_pnl": round(mean_net_pnl, 4),
            "median_hold_sec": round(median_hold, 1),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "sharpe_daily": round(sharpe, 3),
            "max_drawdown_usd": round(max_dd, 2),
        },
        "vectorized_comparison": {
            "n_events": {"vectorized": n_vec, "tick": len(filled), "ratio": len(filled)/n_vec if n_vec else 0},
            "hit_rate": {"vectorized": hr_vec, "tick": round(hit_rate, 4), "degradation_pp": round((hit_rate-hr_vec)*100, 1)},
            "pct_profitable": {"vectorized": prof_vec, "tick": round(pct_profitable, 4), "degradation_pp": round((pct_profitable-prof_vec)*100, 1)},
            "median_net_pnl": {"vectorized": pnl_vec, "tick": round(median_net_pnl, 4), "degradation_pp": round((median_net_pnl-pnl_vec)*100, 1)},
            "median_hold_sec": {"vectorized": hold_vec, "tick": round(median_hold, 1)},
        },
        "exit_reasons": dict(exit_reasons),
        "regime_breakdown": {
            "duration_5min": regime_stats(regime_5min, "5min"),
            "duration_15min": regime_stats(regime_15min, "15min"),
            "buy_yes": regime_stats(regime_yes, "YES"),
            "buy_no": regime_stats(regime_no, "NO"),
            "by_hour": {h: regime_stats(by_hour[h], h) for h in sorted(by_hour.keys())},
            "by_week": {
                w: {
                    "n": len(by_week[w]),
                    "total_pnl_usd": round(sum(r.pnl_usd for r in by_week[w] if r.pnl_usd is not None), 2),
                    **regime_stats(by_week[w], w),
                }
                for w in sorted(by_week.keys())
            },
        },
        "verdict": verdict,
    }

    results_path = OUT_DIR / "results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    log(f"\nSaved results to {results_path}")

    # 11. Save per-scalp ledger
    if filled:
        ledger_rows = []
        for r in filled:
            ledger_rows.append({
                "condition_id": r.condition_id,
                "window_start": datetime.fromtimestamp(r.window_start_sec, tz=timezone.utc),
                "window_end": datetime.fromtimestamp(r.window_end_sec, tz=timezone.utc),
                "duration_min": r.duration_min,
                "winner_outcome": r.winner_outcome,
                "signal_time": datetime.fromtimestamp(r.signal_time_sec, tz=timezone.utc) if r.signal_time_sec else None,
                "signal_direction": r.signal_direction,
                "gbm_p_up": r.gbm_p_up,
                "pm_p_up": r.pm_p_up,
                "deviation": r.deviation,
                "entry_time": datetime.fromtimestamp(r.fill_time_sec, tz=timezone.utc) if r.fill_time_sec else None,
                "entry_price": r.entry_price,
                "exit_time": datetime.fromtimestamp(r.exit_time_sec, tz=timezone.utc) if r.exit_time_sec else None,
                "exit_price": r.exit_price,
                "exit_reason": r.exit_reason,
                "pnl_gross_pct": r.pnl_gross_pct,
                "pnl_net_pct": r.pnl_net_pct,
                "pnl_usd": r.pnl_usd,
                "hold_seconds": r.hold_seconds,
                "outcome": r.outcome,
                "won_gbm": r.won_gbm,
            })
        ledger_df = pl.DataFrame(ledger_rows)
        ledger_path = OUT_DIR / "ledger.parquet"
        ledger_df.write_parquet(ledger_path)
        log(f"Saved ledger ({len(ledger_rows)} records) to {ledger_path}")


if __name__ == "__main__":
    main()
