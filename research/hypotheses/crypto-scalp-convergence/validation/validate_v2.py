"""Tick-by-tick validation of BTC Up/Down scalp convergence strategy.

Efficient approach:
1. Use DuckDB to compute per-second PM YES prices (last trade price per second)
2. Use DuckDB ASOF JOIN to match Binance bars to each PM trade second
3. Compute GBM P(Up) at each second using vectorized numpy
4. Walk through windows chronologically to simulate fills with latency

Fill model:
- Entry fill: PM price at (signal_time + LATENCY_SEC), if a trade exists within MAX_FILL_WAIT_SEC
- Slippage: +0.01 on entry, -0.01 on exit
- Fee: 3% per side on notional
- Skip if no PM trade within 5 seconds of fill time
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import polars as pl

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

from polymarket_pipeline.strategies_impl.crypto_gbm.gbm import (
    compute_gbm_p_up,
    estimate_rolling_sigma,
)

# ─── Parameters ────────────────────────────────────────────────────────────────
ENTRY_THRESHOLD = 0.10
EXIT_THRESHOLD = 0.02
MIN_TIME_REMAINING_SEC = 30
ENTRY_SLIPPAGE = 0.01
EXIT_SLIPPAGE = 0.01
FEE_PCT = 0.03
POSITION_USD = 50.0
LATENCY_SEC = 1
MAX_FILL_WAIT_SEC = 5

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/validate_scalp_v2.log")
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


# ─── Step 1: Load Binance bars into DuckDB ────────────────────────────────────

def load_and_build_db() -> duckdb.DuckDBPyConnection:
    """Build an in-memory DuckDB with bars and trade data."""
    import clickhouse_connect

    log("Connecting to DuckDB...")
    con = duckdb.connect()

    log("Loading Binance 1s bars from ClickHouse...")
    client = clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
    result = client.query(f"""
        SELECT toUnixTimestamp(ts) AS ts_sec, close
        FROM {CH_DB}.exchange_bars
        WHERE exchange = 'BINANCE' AND symbol = 'BTC-USDT'
        AND ts >= '{DATE_START}' AND ts < '{DATE_END}'
        ORDER BY ts
    """)
    rows = result.result_rows
    bars_df = pl.DataFrame({
        "ts_sec": [int(r[0]) for r in rows],
        "close": [float(r[1]) for r in rows],
    })
    log(f"  Loaded {len(bars_df):,} bars")
    con.register("bars_raw", bars_df.to_arrow())

    # Build 1-minute close bars for sigma estimation (downsample from 1s)
    log("Building 1-minute bars for sigma estimation...")
    con.execute("""
        CREATE TABLE bars_1min AS
        SELECT
            (ts_sec // 60) * 60 AS minute_ts,
            last(close ORDER BY ts_sec) AS close
        FROM bars_raw
        GROUP BY minute_ts
        ORDER BY minute_ts
    """)
    log(f"  1-min bars: {con.execute('SELECT count() FROM bars_1min').fetchone()[0]:,}")

    # Keep 1s bars in-memory table
    con.execute("CREATE TABLE bars_1s AS SELECT * FROM bars_raw ORDER BY ts_sec")

    return con


# ─── Step 2: Build PM price series using DuckDB ───────────────────────────────

def build_pm_price_series(con: duckdb.DuckDBPyConnection) -> None:
    """Load PM trades and build per-second YES-side prices."""
    log("Loading PM trades from parquet (lazy scan via DuckDB)...")

    # Load markets to get token_yes mappings
    markets = pl.read_parquet("data/research/btc_updown/markets.parquet")
    markets = markets.filter(
        (pl.col("window_start_utc") >= pl.lit(DATE_START).str.to_datetime(time_zone="UTC"))
        & (pl.col("window_start_utc") < pl.lit(DATE_END).str.to_datetime(time_zone="UTC"))
    )
    log(f"  {len(markets):,} markets in range")

    # Register markets in DuckDB
    con.register("markets_tbl", markets.to_arrow())

    # Load PM trades via DuckDB scan — btc_updown/trades.parquet is already pre-filtered
    # to BTC Up/Down markets, so we only need date filter (no large IN clause needed)
    log("  Scanning trades parquet (DuckDB date filter only — pre-filtered parquet)...")
    con.execute(f"""
        CREATE TABLE pm_trades AS
        SELECT
            t.condition_id,
            t.asset_id,
            t.price,
            t.amount_usd,
            epoch(t.timestamp) AS ts_sec
        FROM read_parquet('data/research/btc_updown/trades.parquet') t
        INNER JOIN markets_tbl m ON t.condition_id = m.condition_id
        WHERE t.timestamp >= TIMESTAMPTZ '{DATE_START}'
        AND t.timestamp < TIMESTAMPTZ '{DATE_END}'
    """)
    n = con.execute("SELECT count() FROM pm_trades").fetchone()[0]
    log(f"  Loaded {n:,} PM trades")

    # Build per-second YES-side price:
    # - YES token trade → price is YES probability
    # - NO token trade → YES probability = 1 - price
    log("  Building per-second YES prices...")
    con.execute("""
        CREATE TABLE pm_yes_by_sec AS
        SELECT
            t.condition_id,
            CAST(t.ts_sec AS BIGINT) AS ts_sec,
            -- last trade price at this second, converted to YES probability
            -- use ANY_VALUE for token_yes since it's constant per condition_id
            CASE
                WHEN last(t.asset_id ORDER BY t.ts_sec) = ANY_VALUE(m.token_yes)
                    THEN last(t.price ORDER BY t.ts_sec)
                ELSE 1.0 - last(t.price ORDER BY t.ts_sec)
            END AS yes_price,
            count() AS n_trades,
            max(t.ts_sec) AS last_trade_ts_sec
        FROM pm_trades t
        JOIN markets_tbl m ON t.condition_id = m.condition_id
        GROUP BY t.condition_id, CAST(t.ts_sec AS BIGINT)
        ORDER BY t.condition_id, ts_sec
    """)
    n2 = con.execute("SELECT count() FROM pm_yes_by_sec").fetchone()[0]
    log(f"  Per-second PM YES prices: {n2:,} rows")


# ─── Step 3: Compute sigma for each window ────────────────────────────────────

def compute_window_sigmas(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Pre-compute sigma for each window using 1440 1-minute bars before window_start."""
    log("Pre-computing sigma for each window...")

    markets = con.execute("""
        SELECT
            condition_id,
            epoch(window_start_utc) AS ws_sec
        FROM markets_tbl
        ORDER BY ws_sec
    """).fetchall()

    # Get 1-min closes as numpy array for fast sigma computation
    min_bars = con.execute("SELECT minute_ts, close FROM bars_1min ORDER BY minute_ts").fetchall()
    min_ts_arr = np.array([r[0] for r in min_bars], dtype=np.int64)
    min_close_arr = np.array([r[1] for r in min_bars], dtype=np.float64)

    sigmas: dict[str, float] = {}
    for cid, ws_sec in markets:
        # Find index of minute just before window_start
        ws_min = (int(ws_sec) // 60) * 60
        idx = np.searchsorted(min_ts_arr, ws_min, side="left")
        # Take 1440 bars before window start
        start_idx = max(0, idx - 1440)
        closes = min_close_arr[start_idx:idx]
        if len(closes) < 60:
            # Fall back to nearest 60 bars
            closes = min_close_arr[max(0, idx-60):idx]
        if len(closes) < 2:
            continue
        log_returns = np.diff(np.log(closes))
        log_returns = log_returns[np.isfinite(log_returns)]
        if len(log_returns) < 10:
            continue
        sigma = float(np.std(log_returns, ddof=1))
        if sigma > 0:
            sigmas[cid] = sigma

    log(f"  Sigma computed for {len(sigmas):,}/{len(markets):,} windows")
    return sigmas


# ─── Step 4: Process windows using DuckDB ────────────────────────────────────

def process_windows(con: duckdb.DuckDBPyConnection, sigmas: dict[str, float]) -> list[dict]:
    """Process each window to find entry/exit signals."""

    log("Fetching markets with window boundaries...")
    markets = con.execute("""
        SELECT
            condition_id,
            token_yes,
            token_no,
            winner_outcome,
            epoch(window_start_utc) AS ws_sec,
            epoch(window_end_utc) AS we_sec,
            duration_min
        FROM markets_tbl
        ORDER BY ws_sec
    """).fetchall()

    log(f"  Processing {len(markets):,} windows...")

    # Pre-fetch all data we need: bars and pm_yes_by_sec
    # Load into memory once rather than querying per window
    log("  Loading 1s bars into memory...")
    bar_rows = con.execute("SELECT ts_sec, close FROM bars_1s ORDER BY ts_sec").fetchall()
    bars_dict: dict[int, float] = {int(r[0]): float(r[1]) for r in bar_rows}
    log(f"  Loaded {len(bars_dict):,} bars")

    log("  Loading PM yes-by-sec into memory...")
    pm_rows = con.execute("""
        SELECT condition_id, ts_sec, yes_price
        FROM pm_yes_by_sec
        ORDER BY condition_id, ts_sec
    """).fetchall()
    pm_by_cid: dict[str, dict[int, float]] = defaultdict(dict)
    for cid, ts, price in pm_rows:
        pm_by_cid[cid][int(ts)] = float(price)
    log(f"  PM data for {len(pm_by_cid):,} markets")

    records: list[dict] = []
    n_no_sigma = 0
    n_no_trades = 0
    n_no_bars = 0
    n_no_signal = 0

    for i, (cid, token_yes, token_no, winner, ws_sec, we_sec, dur_min) in enumerate(markets):
        if i % 2000 == 0:
            log(f"  Progress: {i}/{len(markets)} ({i/len(markets):.0%}) | records={len(records)}")

        ws_sec = int(ws_sec)
        we_sec = int(we_sec)

        if cid not in sigmas:
            n_no_sigma += 1
            continue

        sigma = sigmas[cid]
        pm_sec = pm_by_cid.get(cid)
        if not pm_sec:
            n_no_trades += 1
            continue

        # S₀ = BTC price at window open
        s_start = None
        for delta in range(-3, 4):
            s_start = bars_dict.get(ws_sec + delta)
            if s_start is not None:
                break
        if s_start is None:
            n_no_bars += 1
            continue

        # Walk through window second by second
        # Only iterate seconds where we have EITHER bars OR PM data
        # For performance: find all seconds in window
        last_pm_price: float | None = None

        # State machine
        state = "open"
        signal_sec: int | None = None
        signal_dir: str | None = None
        signal_gbm: float | None = None
        signal_pm_val: float | None = None
        signal_dev: float | None = None
        waiting_since: int | None = None
        entry_sec: int | None = None
        entry_price: float | None = None
        exit_sec: int | None = None
        exit_price: float | None = None
        exit_reason: str = ""

        # Get sorted seconds with PM data in this window
        pm_secs_in_window = sorted(s for s in pm_sec.keys() if ws_sec <= s < we_sec)
        # Build sorted all-secs covering window (only where we have bars OR PM)
        # Use pm_secs + every second where bars exists in window (sparse scan)
        bar_secs_in_window = [s for s in range(ws_sec, we_sec) if s in bars_dict]

        all_secs_set = set(bar_secs_in_window) | set(pm_secs_in_window)
        all_secs = sorted(all_secs_set)

        if not all_secs:
            n_no_signal += 1
            continue

        for sec in all_secs:
            # Update PM price if we have a trade at this second
            if sec in pm_sec:
                last_pm_price = pm_sec[sec]

            if last_pm_price is None:
                continue

            s_current = bars_dict.get(sec)
            if s_current is None:
                continue

            time_remaining_min = (we_sec - sec) / 60.0
            if time_remaining_min <= 0:
                break

            # Compute GBM P(Up)
            log_ret = math.log(s_current / s_start)
            vol = sigma * math.sqrt(time_remaining_min)
            if vol < 1e-12:
                gbm_p_up = 1.0 if log_ret > 0 else (0.0 if log_ret < 0 else 0.5)
            else:
                from scipy.stats import norm
                gbm_p_up = float(norm.cdf(log_ret / vol))

            pm_p_up = last_pm_price
            deviation = gbm_p_up - pm_p_up
            abs_dev = abs(deviation)

            if state == "open":
                if abs_dev >= ENTRY_THRESHOLD:
                    signal_sec = sec
                    signal_dir = "YES" if deviation > 0 else "NO"
                    signal_gbm = gbm_p_up
                    signal_pm_val = pm_p_up
                    signal_dev = deviation
                    state = "waiting_fill"
                    waiting_since = sec

            elif state == "waiting_fill":
                assert waiting_since is not None
                elapsed = sec - waiting_since
                if elapsed >= LATENCY_SEC:
                    if elapsed > LATENCY_SEC + MAX_FILL_WAIT_SEC:
                        # Missed fill
                        records.append({
                            "condition_id": cid,
                            "duration_min": dur_min,
                            "winner_outcome": winner,
                            "signal_sec": signal_sec,
                            "signal_dir": signal_dir,
                            "gbm_p_up": signal_gbm,
                            "pm_p_up": signal_pm_val,
                            "deviation": signal_dev,
                            "outcome": "miss_entry",
                        })
                        break
                    # Check if PM price available (need a fresh trade after latency)
                    if sec in pm_sec:
                        raw_fill = pm_sec[sec]
                        if signal_dir == "YES":
                            fill_p = min(raw_fill + ENTRY_SLIPPAGE, 0.99)
                        else:
                            fill_p = max(raw_fill - ENTRY_SLIPPAGE, 0.01)
                        entry_sec = sec
                        entry_price = fill_p
                        state = "in_position"

            elif state == "in_position":
                time_remaining_sec = we_sec - sec
                should_exit = False
                cur_exit_reason = ""

                if time_remaining_sec <= MIN_TIME_REMAINING_SEC:
                    should_exit = True
                    cur_exit_reason = "time_stop"
                elif abs_dev < EXIT_THRESHOLD:
                    should_exit = True
                    cur_exit_reason = "converged"

                if should_exit:
                    raw_exit = pm_sec.get(sec, last_pm_price)
                    if signal_dir == "YES":
                        exit_p = max(raw_exit - EXIT_SLIPPAGE, 0.01)
                    else:
                        exit_p = min(raw_exit + EXIT_SLIPPAGE, 0.99)
                    exit_sec = sec
                    exit_price = exit_p
                    exit_reason = cur_exit_reason
                    state = "closed"
                    break

        # If still in position at end of window
        if state == "in_position" and entry_price is not None:
            raw_exit = last_pm_price or entry_price
            if signal_dir == "YES":
                exit_p = max(raw_exit - EXIT_SLIPPAGE, 0.01)
            else:
                exit_p = min(raw_exit + EXIT_SLIPPAGE, 0.99)
            exit_sec = we_sec
            exit_price = exit_p
            exit_reason = "window_end"
            state = "closed"

        if state == "closed" and entry_price is not None and exit_price is not None:
            assert signal_sec is not None and entry_sec is not None and exit_sec is not None

            # entry_price and exit_price are stored as YES-equivalent probabilities.
            # For YES position: bought YES at entry, sold YES at exit.
            #   PnL = exit_price - entry_price  (positive when YES rises)
            # For NO position: bought NO at (1-entry_price), sold NO at (1-exit_price).
            #   PnL on NO tokens = (1-exit_price) - (1-entry_price) = entry_price - exit_price
            #   (positive when YES falls = NO rises)
            # Fees should also use actual token prices (NO price = 1-YES equiv).
            if signal_dir == "YES":
                gross_pct = exit_price - entry_price
                fee_cost = FEE_PCT * entry_price + FEE_PCT * exit_price
            else:  # NO
                gross_pct = entry_price - exit_price
                no_entry_price = 1.0 - entry_price
                no_exit_price = 1.0 - exit_price
                fee_cost = FEE_PCT * no_entry_price + FEE_PCT * no_exit_price

            net_pct = gross_pct - fee_cost
            pnl_usd = net_pct * POSITION_USD

            won_gbm = (winner == "Up") == (signal_dir == "YES")

            records.append({
                "condition_id": cid,
                "duration_min": dur_min,
                "winner_outcome": winner,
                "signal_sec": signal_sec,
                "signal_dir": signal_dir,
                "gbm_p_up": signal_gbm,
                "pm_p_up": signal_pm_val,
                "deviation": signal_dev,
                "entry_sec": entry_sec,
                "entry_price": entry_price,
                "exit_sec": exit_sec,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "gross_pct": gross_pct,
                "net_pct": net_pct,
                "pnl_usd": pnl_usd,
                "hold_sec": exit_sec - entry_sec,
                "won_gbm": won_gbm,
                "outcome": "win" if net_pct > 0 else "loss",
            })
        elif state not in ("closed", "waiting_fill"):
            n_no_signal += 1

    log(f"\nProcessing complete:")
    log(f"  Total records: {len(records):,}")
    log(f"  No sigma: {n_no_sigma}, No trades: {n_no_trades}, No bars: {n_no_bars}, No signal: {n_no_signal}")
    return records


# ─── Step 5: Compute metrics and output ───────────────────────────────────────

def compute_metrics(records: list[dict]) -> dict:
    """Compute validation metrics from records."""
    filled = [r for r in records if r.get("outcome") in ("win", "loss")]
    misses = [r for r in records if r.get("outcome") == "miss_entry"]

    log(f"\nFilled: {len(filled):,}, Misses: {len(misses):,}")

    if not filled:
        return {"error": "no_filled_trades"}

    net_pnls = [r["net_pct"] for r in filled]
    pnl_usd_list = [r["pnl_usd"] for r in filled]
    hold_secs = [r["hold_sec"] for r in filled]
    won_gbm_list = [r["won_gbm"] for r in filled]

    n_wins = sum(1 for p in net_pnls if p > 0)
    hit_rate = sum(1 for w in won_gbm_list if w) / len(won_gbm_list) if won_gbm_list else 0
    pct_profitable = n_wins / len(net_pnls)
    median_net_pnl = float(np.median(net_pnls))
    mean_net_pnl = float(np.mean(net_pnls))
    median_hold = float(np.median(hold_secs)) if hold_secs else 0
    total_pnl_usd = sum(pnl_usd_list)

    # Daily Sharpe
    daily_pnl: dict[str, float] = defaultdict(float)
    for r in filled:
        if r.get("entry_sec"):
            day = datetime.fromtimestamp(r["entry_sec"], tz=timezone.utc).strftime("%Y-%m-%d")
            daily_pnl[day] += r["pnl_usd"]
    daily_vals = list(daily_pnl.values())
    if len(daily_vals) > 1:
        daily_mean = np.mean(daily_vals)
        daily_std = np.std(daily_vals, ddof=1)
        sharpe = (daily_mean / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown
    sorted_recs = sorted(filled, key=lambda r: r.get("entry_sec", 0))
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in sorted_recs:
        cum_pnl += r["pnl_usd"]
        peak = max(peak, cum_pnl)
        max_dd = max(max_dd, peak - cum_pnl)

    # Exit reasons
    exit_reasons: dict[str, int] = defaultdict(int)
    for r in filled:
        exit_reasons[r.get("exit_reason", "unknown")] += 1

    # Regime breakdowns
    def stats(recs: list[dict]) -> dict:
        if not recs:
            return {}
        pnls = [r["net_pct"] for r in recs if "net_pct" in r]
        holds = [r["hold_sec"] for r in recs if "hold_sec" in r]
        gbm_wins = [r["won_gbm"] for r in recs if "won_gbm" in r]
        prof = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
        return {
            "n": len(recs),
            "median_net_pnl": round(float(np.median(pnls)), 4) if pnls else None,
            "pct_profitable": round(prof, 4),
            "hit_rate": round(sum(gbm_wins) / len(gbm_wins), 4) if gbm_wins else None,
            "median_hold_sec": round(float(np.median(holds)), 1) if holds else None,
            "total_pnl_usd": round(sum(r.get("pnl_usd", 0) for r in recs), 2),
        }

    regime_5min = [r for r in filled if r.get("duration_min") == 5]
    regime_15min = [r for r in filled if r.get("duration_min") == 15]
    regime_yes = [r for r in filled if r.get("signal_dir") == "YES"]
    regime_no = [r for r in filled if r.get("signal_dir") == "NO"]

    by_hour: dict[str, list] = defaultdict(list)
    for r in filled:
        if r.get("entry_sec"):
            h = datetime.fromtimestamp(r["entry_sec"], tz=timezone.utc).hour
            bucket = f"UTC_{(h//6)*6:02d}-{(h//6)*6+5:02d}"
            by_hour[bucket].append(r)

    by_week: dict[str, list] = defaultdict(list)
    for r in filled:
        if r.get("entry_sec"):
            dt = datetime.fromtimestamp(r["entry_sec"], tz=timezone.utc)
            by_week[dt.strftime("W%V_%Y")].append(r)

    # Print comparison table
    n_vec = 12564
    hr_vec = 0.4541
    prof_vec = 0.8312
    pnl_vec = 0.0866
    hold_vec = 22

    log("\n" + "="*65)
    log("COMPARISON: Vectorized (UB) vs Tick-by-Tick")
    log("="*65)
    log(f"{'Metric':<30} {'Vectorized':>12} {'Tick':>12} {'Degrad':>10}")
    log("-"*65)
    log(f"{'Events (filled)'::<30} {n_vec:>12,} {len(filled):>12,} {len(filled)/n_vec:>+9.1%}")
    log(f"{'Hit Rate (GBM correct)'::<30} {hr_vec:>11.1%} {hit_rate:>11.1%} {hit_rate-hr_vec:>+9.1%}")
    log(f"{'Profitable %'::<30} {prof_vec:>11.1%} {pct_profitable:>11.1%} {pct_profitable-prof_vec:>+9.1%}")
    log(f"{'Median Net PnL %'::<30} {pnl_vec:>11.1%} {median_net_pnl:>11.1%} {median_net_pnl-pnl_vec:>+9.1%}")
    log(f"{'Median Hold (s)'::<30} {hold_vec:>12} {median_hold:>12.0f} {'':>10}")
    log(f"{'Total PnL ($50/scalp)'::<30} {'N/A':>12} {total_pnl_usd:>11,.0f} {'':>10}")
    log(f"{'Sharpe (daily)'::<30} {'N/A':>12} {sharpe:>12.2f} {'':>10}")
    log(f"{'Max Drawdown ($)'::<30} {'N/A':>12} {max_dd:>11,.0f} {'':>10}")
    log(f"{'Miss Entry Rate'::<30} {'N/A':>12} {len(misses)/max(len(records),1):>11.1%} {'':>10}")

    log("\nExit reason breakdown:")
    for reason, cnt in sorted(exit_reasons.items()):
        log(f"  {reason}: {cnt} ({cnt/len(filled):.1%})")

    log("\nRegime Breakdown:")
    log(f"  5-min: {stats(regime_5min)}")
    log(f"  15-min: {stats(regime_15min)}")
    log(f"  Buy YES: {stats(regime_yes)}")
    log(f"  Buy NO: {stats(regime_no)}")
    for h in sorted(by_hour.keys()):
        log(f"  {h}: {stats(by_hour[h])}")
    log("\nWeekly PnL:")
    for w in sorted(by_week.keys()):
        ws = by_week[w]
        w_total = sum(r.get("pnl_usd", 0) for r in ws)
        w_pnls = [r["net_pct"] for r in ws if "net_pct" in r]
        w_prof = sum(1 for p in w_pnls if p > 0) / len(w_pnls) if w_pnls else 0
        log(f"  {w}: n={len(ws)}, total=${w_total:.0f}, profitable={w_prof:.0%}")

    # Verdict
    log("\n=== VERDICT ===")
    if pct_profitable >= 0.55 and median_net_pnl > 0 and total_pnl_usd > 0:
        if pct_profitable >= 0.65 and total_pnl_usd > 500:
            verdict = "GO"
        else:
            verdict = "NEEDS-MORE-WORK"
    else:
        verdict = "NO-GO"
    log(f"Verdict: {verdict}")
    log(f"Rationale: {pct_profitable:.0%} profitable, median net PnL {median_net_pnl:.1%}, total ${total_pnl_usd:.0f}")

    n_total_signals = len(records)
    return {
        "n_windows": "see_markets",
        "n_signals": n_total_signals,
        "n_filled": len(filled),
        "n_miss_entry": len(misses),
        "fill_rate": len(filled) / max(n_total_signals, 1),
        "hit_rate_gbm": round(hit_rate, 4),
        "pct_profitable": round(pct_profitable, 4),
        "median_net_pnl": round(median_net_pnl, 4),
        "mean_net_pnl": round(mean_net_pnl, 4),
        "median_hold_sec": round(median_hold, 1),
        "total_pnl_usd": round(total_pnl_usd, 2),
        "sharpe_daily": round(sharpe, 3),
        "max_drawdown_usd": round(max_dd, 2),
        "exit_reasons": dict(exit_reasons),
        "regime": {
            "duration_5min": stats(regime_5min),
            "duration_15min": stats(regime_15min),
            "buy_yes": stats(regime_yes),
            "buy_no": stats(regime_no),
            "by_hour": {h: stats(by_hour[h]) for h in sorted(by_hour.keys())},
            "by_week": {w: {"n": len(by_week[w]), "total_pnl_usd": round(sum(r.get("pnl_usd",0) for r in by_week[w]), 2), **stats(by_week[w])} for w in sorted(by_week.keys())},
        },
        "vectorized_vs_tick": {
            "n_events": {"vectorized": n_vec, "tick": len(filled)},
            "hit_rate": {"vectorized": hr_vec, "tick": round(hit_rate, 4), "delta_pp": round((hit_rate-hr_vec)*100, 1)},
            "pct_profitable": {"vectorized": prof_vec, "tick": round(pct_profitable, 4), "delta_pp": round((pct_profitable-prof_vec)*100, 1)},
            "median_net_pnl": {"vectorized": pnl_vec, "tick": round(median_net_pnl, 4), "delta_pp": round((median_net_pnl-pnl_vec)*100, 1)},
        },
        "verdict": verdict,
    }


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    open(LOG_PATH, "w").close()

    log("=== BTC Scalp Convergence Tick-by-Tick Validation v2 ===")
    log(f"Entry threshold: {ENTRY_THRESHOLD:.0%}, Exit: {EXIT_THRESHOLD:.0%}")
    log(f"Latency: {LATENCY_SEC}s, Max fill wait: {MAX_FILL_WAIT_SEC}s")
    log(f"Position: ${POSITION_USD}, Fee: {FEE_PCT:.0%}/side, Date: {DATE_START} to {DATE_END}")

    # Build DB
    con = load_and_build_db()
    build_pm_price_series(con)

    # Sigma
    sigmas = compute_window_sigmas(con)

    # Process windows
    records = process_windows(con, sigmas)

    # Metrics
    metrics = compute_metrics(records)

    # Save results
    results = {
        "hypothesis": "crypto-scalp-convergence",
        "phase": "tick-by-tick-validation-v2",
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
        "metrics": metrics,
    }

    results_path = OUT_DIR / "results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    log(f"Saved results to {results_path}")

    # Save per-scalp ledger
    filled = [r for r in records if r.get("outcome") in ("win", "loss")]
    if filled:
        ledger_df = pl.DataFrame([{
            "condition_id": r["condition_id"],
            "duration_min": r["duration_min"],
            "winner_outcome": r["winner_outcome"],
            "signal_sec": r.get("signal_sec"),
            "signal_dir": r.get("signal_dir"),
            "gbm_p_up": r.get("gbm_p_up"),
            "pm_p_up": r.get("pm_p_up"),
            "deviation": r.get("deviation"),
            "entry_sec": r.get("entry_sec"),
            "entry_price": r.get("entry_price"),
            "exit_sec": r.get("exit_sec"),
            "exit_price": r.get("exit_price"),
            "exit_reason": r.get("exit_reason", ""),
            "net_pct": r.get("net_pct"),
            "pnl_usd": r.get("pnl_usd"),
            "hold_sec": r.get("hold_sec"),
            "won_gbm": r.get("won_gbm"),
            "outcome": r.get("outcome", ""),
        } for r in filled])
        ledger_path = OUT_DIR / "ledger.parquet"
        ledger_df.write_parquet(ledger_path)
        log(f"Saved ledger ({len(filled)} records) to {ledger_path}")


if __name__ == "__main__":
    main()
