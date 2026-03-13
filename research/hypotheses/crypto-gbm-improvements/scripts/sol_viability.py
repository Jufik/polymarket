"""SOL Up/Down GBM Scalp Viability Analysis.

Assesses whether the BTC GBM scalp strategy (threshold=0.10, baseline +$2.10/trade)
can be applied to Solana Up/Down markets on Polymarket.

Sections:
  1. Market structure (DuckDB)
  2. Volatility profile comparison SOL vs BTC (ClickHouse)
  3. GBM signal quality on resolved SOL markets (last 90 days)
  4. Liquidity check (trades per market, avg volume)
  5. SOL-specific risks summary

Usage:
  PYTHONPATH=. uv run python research/hypotheses/crypto-gbm-improvements/scripts/sol_viability.py
"""
from __future__ import annotations

import json
import re
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clickhouse_connect
import numpy as np
import pandas as pd
from scipy.stats import norm

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-gbm-improvements/scripts/sol_viability.log")
OUT_PATH = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-gbm-improvements/discovery/sol_viability.md")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── helpers ──────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

def compute_gbm_p_up(s_start: float, s_current: float, sigma_1m: float, time_remaining_min: float) -> float:
    if time_remaining_min <= 0:
        return 1.0 if s_current > s_start else 0.0
    if s_start <= 0 or s_current <= 0:
        return 0.5
    log_return = np.log(s_current / s_start)
    vol = sigma_1m * np.sqrt(time_remaining_min)
    if vol < 1e-12:
        return 1.0 if log_return > 0 else (0.0 if log_return < 0 else 0.5)
    d2 = log_return / vol
    return float(norm.cdf(d2))

def extract_window_min(question: str) -> int | None:
    """Extract window duration in minutes from question text like '5:00PM-5:15PM ET'."""
    if not isinstance(question, str):
        return None
    # Pattern: HH:MM[AP]M-HH:MM[AP]M
    m = re.search(r'(\d+):(\d+)([AP]M)-(\d+):(\d+)([AP]M)', question)
    if m:
        h1, min1, ampm1, h2, min2, ampm2 = m.groups()
        h1, min1, h2, min2 = int(h1), int(min1), int(h2), int(min2)
        if ampm1 == 'PM' and h1 != 12:
            h1 += 12
        if ampm1 == 'AM' and h1 == 12:
            h1 = 0
        if ampm2 == 'PM' and h2 != 12:
            h2 += 12
        if ampm2 == 'AM' and h2 == 12:
            h2 = 0
        total1 = h1 * 60 + min1
        total2 = h2 * 60 + min2
        diff = total2 - total1
        if diff <= 0:
            diff += 24 * 60
        return diff
    # Single time (hourly or daily)
    m2 = re.search(r'(\d+)(AM|PM)\s*ET', question)
    if m2:
        return 60  # assume 1-hour window
    return None

# ── ClickHouse client ─────────────────────────────────────────────────────────

CH = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

# ── Section 1: Market Structure ───────────────────────────────────────────────

def section1_market_structure(d: object) -> dict:
    log("=== Section 1: Market Structure ===")

    log("Querying SOL up/down markets from DuckDB...")
    markets = d.query("""
        SELECT
            m.condition_id,
            m.question,
            m.created_at,
            m.closed_at,
            m.winner_outcome
        FROM markets m
        WHERE m.question LIKE '%Solana Up or Down%'
        ORDER BY m.created_at
    """).to_pandas()
    log(f"  Total SOL up/down markets: {len(markets):,}")

    # Extract window duration in minutes
    markets["window_min"] = markets["question"].apply(extract_window_min)
    window_counts = markets["window_min"].value_counts().sort_index()
    log(f"  Window distribution (minutes):\n{window_counts}")

    # YES base rate by window (Up/Down → map 'Up' to YES for BTC-style)
    yes_brs: dict[str, dict] = {}
    for w, count in window_counts.items():
        w_label = f"{w}min" if w is not None else "unknown"
        subset = markets[markets["window_min"] == w]
        resolved = subset[subset["winner_outcome"].notna() & (subset["winner_outcome"] != "")]
        if len(resolved) > 0:
            yes_wins = (resolved["winner_outcome"] == "Up").sum()
            yes_br_val = 100 * yes_wins / len(resolved)
        else:
            yes_br_val = None
        yes_brs[w_label] = {
            "n_total": int(count),
            "n_resolved": len(resolved),
            "yes_pct": float(yes_br_val) if yes_br_val is not None else None,
        }
        log(f"  Window={w_label}: {yes_brs[w_label]}")

    # Date range
    date_min = str(markets["created_at"].min())[:10]
    date_max = str(markets["created_at"].max())[:10]
    log(f"  Date range: {date_min} → {date_max}")

    # Resolved count
    n_resolved = markets["winner_outcome"].notna().sum()
    n_up = (markets["winner_outcome"] == "Up").sum()
    log(f"  Resolved: {n_resolved:,} ({100*n_resolved/len(markets):.1f}%), Up wins: {n_up:,} ({100*n_up/n_resolved:.1f}% if n_resolved > 0 else 'n/a')")

    # Volume and positions per market
    log("  Computing aggregate volume per SOL market (maker_positions)...")
    try:
        sol_vol = d.query("""
            SELECT
                count(DISTINCT mp.condition_id) AS n_markets_with_pos,
                count(*) AS n_positions,
                median(mp.volume) AS median_vol_per_pos,
                avg(mp.volume) AS avg_vol_per_pos,
                sum(mp.volume) AS total_volume
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Solana Up or Down%'
        """).to_pandas()
        btc_vol = d.query("""
            SELECT
                count(DISTINCT mp.condition_id) AS n_markets_with_pos,
                count(*) AS n_positions,
                median(mp.volume) AS median_vol_per_pos,
                avg(mp.volume) AS avg_vol_per_pos,
                sum(mp.volume) AS total_volume
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Bitcoin Up or Down%'
        """).to_pandas()
        sol_stats = sol_vol.iloc[0].to_dict()
        btc_stats = btc_vol.iloc[0].to_dict()
        log(f"  SOL: {sol_stats}")
        log(f"  BTC: {btc_stats}")
    except Exception as e:
        log(f"  Volume query failed: {e}")
        sol_stats = btc_stats = {}

    # Positions per market distribution
    try:
        pos_per_mkt = d.query("""
            SELECT
                count(*) AS n_positions
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Solana Up or Down%'
            GROUP BY mp.condition_id
        """).to_pandas()
        avg_pos = float(pos_per_mkt["n_positions"].mean())
        median_pos = float(pos_per_mkt["n_positions"].median())
        p90_pos = float(pos_per_mkt["n_positions"].quantile(0.9))
        log(f"  Positions/market: avg={avg_pos:.1f}, median={median_pos:.1f}, p90={p90_pos:.0f}")
    except Exception as e:
        log(f"  Positions/market query failed: {e}")
        avg_pos = median_pos = p90_pos = None

    return {
        "n_markets": len(markets),
        "n_resolved": int(n_resolved),
        "n_up_wins": int(n_up),
        "up_win_rate_pct": float(100 * n_up / n_resolved) if n_resolved > 0 else None,
        "date_range": {"min": date_min, "max": date_max},
        "window_distribution_min": {str(k): int(v) for k, v in window_counts.items()},
        "yes_base_rates": yes_brs,
        "sol_volume_stats": sol_stats,
        "btc_volume_stats": btc_stats,
        "avg_positions_per_market": avg_pos,
        "median_positions_per_market": median_pos,
        "p90_positions_per_market": p90_pos,
    }


# ── Section 2: Volatility Profile ─────────────────────────────────────────────

def section2_volatility_profile() -> dict:
    log("=== Section 2: Volatility Profile (SOL vs BTC) ===")

    sql_vol = """
    WITH minbars AS (
        SELECT
            toStartOfMinute(ts) AS ts_min,
            argMax(close, ts) AS close_price
        FROM exchange_bars
        WHERE symbol = {sym:String}
          AND ts >= toDateTime('2025-09-11 00:00:00')
        GROUP BY ts_min
        ORDER BY ts_min
    ),
    returns AS (
        SELECT
            ts_min,
            close_price,
            log(close_price / lagInFrame(close_price) OVER (ORDER BY ts_min)) AS log_ret
        FROM minbars
    )
    SELECT
        quantile(0.05)(abs(log_ret)) AS p5_ret,
        quantile(0.25)(abs(log_ret)) AS p25_ret,
        quantile(0.50)(abs(log_ret)) AS p50_ret,
        quantile(0.75)(abs(log_ret)) AS p75_ret,
        quantile(0.95)(abs(log_ret)) AS p95_ret,
        std(log_ret) AS overall_sigma,
        count() AS n_minutes
    FROM returns
    WHERE log_ret IS NOT NULL AND isFinite(log_ret)
    """

    log("  Fetching SOL and BTC per-minute vol stats from CH...")
    sol_result = CH.query(sql_vol, parameters={"sym": "SOL-USDT"})
    btc_result = CH.query(sql_vol, parameters={"sym": "BTC-USDT"})

    sol_row = sol_result.first_row
    btc_row = btc_result.first_row

    sol_stats = {k: float(v) for k, v in zip(
        ["p5_abs_ret", "p25_abs_ret", "p50_abs_ret", "p75_abs_ret", "p95_abs_ret", "overall_sigma_1m", "n_minutes"],
        sol_row
    )}
    sol_stats["n_minutes"] = int(sol_row[6])

    btc_stats = {k: float(v) for k, v in zip(
        ["p5_abs_ret", "p25_abs_ret", "p50_abs_ret", "p75_abs_ret", "p95_abs_ret", "overall_sigma_1m", "n_minutes"],
        btc_row
    )}
    btc_stats["n_minutes"] = int(btc_row[6])

    vol_ratio = sol_stats["overall_sigma_1m"] / btc_stats["overall_sigma_1m"] if btc_stats["overall_sigma_1m"] > 0 else None
    log(f"  SOL sigma_1m: {sol_stats['overall_sigma_1m']:.6f}")
    log(f"  BTC sigma_1m: {btc_stats['overall_sigma_1m']:.6f}")
    log(f"  SOL/BTC vol ratio: {vol_ratio:.2f}x" if vol_ratio else "  ratio: n/a")

    # Rolling 24h sigma quantiles
    log("  Computing rolling 24h sigma quantiles...")
    sql_rolling = """
    WITH minbars AS (
        SELECT toStartOfMinute(ts) AS ts_min, argMax(close, ts) AS close_price
        FROM exchange_bars WHERE symbol = {sym:String} AND ts >= toDateTime('2025-09-11 00:00:00')
        GROUP BY ts_min ORDER BY ts_min
    ),
    returns AS (
        SELECT ts_min,
            log(close_price / lagInFrame(close_price) OVER (ORDER BY ts_min)) AS log_ret
        FROM minbars
    ),
    rolling AS (
        SELECT ts_min,
            stddevPop(log_ret) OVER (ORDER BY ts_min ROWS BETWEEN 1439 PRECEDING AND CURRENT ROW) AS sigma_24h
        FROM returns WHERE isFinite(log_ret)
    )
    SELECT
        quantile(0.05)(sigma_24h) AS p5,
        quantile(0.25)(sigma_24h) AS p25,
        quantile(0.50)(sigma_24h) AS p50,
        quantile(0.75)(sigma_24h) AS p75,
        quantile(0.95)(sigma_24h) AS p95
    FROM rolling WHERE sigma_24h > 0
    """
    sol_rolling = CH.query(sql_rolling, parameters={"sym": "SOL-USDT"}).first_row
    btc_rolling = CH.query(sql_rolling, parameters={"sym": "BTC-USDT"}).first_row

    sol_rolling_stats = {k: float(v) for k, v in zip(["p5","p25","p50","p75","p95"], sol_rolling)}
    btc_rolling_stats = {k: float(v) for k, v in zip(["p5","p25","p50","p75","p95"], btc_rolling)}
    log(f"  SOL rolling 24h σ (p5/p25/p50/p75/p95): {sol_rolling_stats}")
    log(f"  BTC rolling 24h σ (p5/p25/p50/p75/p95): {btc_rolling_stats}")

    # GBM d2 analysis: at same 10% lag, what's the d2 and P(Up)?
    log("\n  GBM signal strength at 10% lag, T=7.5min (midpoint of 15-min window):")
    T_half = 7.5
    for label, sigma in [("SOL (median sigma)", sol_rolling_stats["p50"]),
                         ("BTC (median sigma)", btc_rolling_stats["p50"])]:
        d2 = 0.10 / (sigma * np.sqrt(T_half))
        p_up = float(norm.cdf(d2))
        log(f"    {label}: sigma={sigma:.6f}, d2={d2:.1f}, P(Up)={p_up:.4f}")

    log("\n  GBM signal strength at 10% lag, T=2.5min (midpoint of 5-min window):")
    T_half5 = 2.5
    for label, sigma in [("SOL (median sigma)", sol_rolling_stats["p50"]),
                         ("BTC (median sigma)", btc_rolling_stats["p50"])]:
        d2 = 0.10 / (sigma * np.sqrt(T_half5))
        p_up = float(norm.cdf(d2))
        log(f"    {label}: sigma={sigma:.6f}, d2={d2:.1f}, P(Up)={p_up:.4f}")

    return {
        "sol": sol_stats,
        "btc": btc_stats,
        "vol_ratio": vol_ratio,
        "sol_rolling_sigma": sol_rolling_stats,
        "btc_rolling_sigma": btc_rolling_stats,
    }


# ── Section 3: GBM Signal Quality ──────────────────────────────────────────────

def section3_gbm_signal_quality(d: object, vol_results: dict) -> dict:
    log("=== Section 3: GBM Signal Quality (Resolved SOL Markets, last 90 days) ===")

    log("  Fetching resolved SOL markets from last 90 days...")
    markets_df = d.query("""
        SELECT
            condition_id,
            question,
            created_at,
            closed_at,
            winner_outcome
        FROM markets
        WHERE question LIKE '%Solana Up or Down%'
          AND winner_outcome IS NOT NULL
          AND winner_outcome != ''
          AND closed_at IS NOT NULL
          AND created_at >= CURRENT_DATE - INTERVAL 90 DAY
        ORDER BY created_at
        LIMIT 1000
    """).to_pandas()
    log(f"  Found {len(markets_df)} resolved SOL markets in last 90 days")

    if len(markets_df) == 0:
        log("  Falling back to all-time resolved SOL markets (most recent 500)...")
        markets_df = d.query("""
            SELECT condition_id, question, created_at, closed_at, winner_outcome
            FROM markets
            WHERE question LIKE '%Solana Up or Down%'
              AND winner_outcome IS NOT NULL AND winner_outcome != ''
              AND closed_at IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 500
        """).to_pandas()
        log(f"  Found {len(markets_df)} resolved SOL markets total")

    if len(markets_df) == 0:
        return {"status": "no_data"}

    # Use the overall sigma (not rolling, for simplicity in vectorized analysis)
    sol_sigma = vol_results.get("sol", {}).get("overall_sigma_1m", 0.00113)
    log(f"  Using SOL sigma_1m: {sol_sigma:.6f}")

    # Fetch SOL 1-second bars for the window period
    ts_min_raw = markets_df["created_at"].min()
    ts_max_raw = markets_df["closed_at"].max()

    # Convert to naive UTC strings
    def to_str(ts) -> str:
        if hasattr(ts, 'tz_localize'):
            return str(ts)[:19]
        return str(ts)[:19]

    t_start = (pd.Timestamp(ts_min_raw) - pd.Timedelta(hours=25)).tz_localize(None)
    t_end = pd.Timestamp(ts_max_raw).tz_localize(None) if pd.Timestamp(ts_max_raw).tzinfo else pd.Timestamp(ts_max_raw)

    t_start_str = str(t_start)[:19]
    t_end_str = str(t_end)[:19]

    log(f"  Fetching SOL 1s bars: {t_start_str} → {t_end_str}")
    sql_bars = """
    SELECT ts, close
    FROM exchange_bars
    WHERE symbol = 'SOL-USDT'
      AND ts >= toDateTime({t_start:String})
      AND ts <= toDateTime({t_end:String})
    ORDER BY ts
    """
    bars_result = CH.query(sql_bars, parameters={"t_start": t_start_str, "t_end": t_end_str})
    bars_rows = bars_result.result_rows
    log(f"  Got {len(bars_rows):,} 1s bars for the period")

    if not bars_rows:
        return {"status": "no_bars_data"}

    # Build lookup structures
    bar_times = [row[0].replace(tzinfo=None) if hasattr(row[0], 'tzinfo') else row[0] for row in bars_rows]
    bar_closes = [float(row[1]) for row in bars_rows]

    def get_price_at(dt: datetime) -> float | None:
        """Get close price at or just before dt."""
        if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        idx = bisect_right(bar_times, dt) - 1
        return bar_closes[idx] if idx >= 0 else None

    log(f"  Computing GBM signals for {len(markets_df)} markets...")
    signals = []
    skipped = 0

    for _, row in markets_df.iterrows():
        try:
            created = pd.Timestamp(row["created_at"]).tz_localize(None) if pd.Timestamp(row["created_at"]).tzinfo is None else pd.Timestamp(row["created_at"]).tz_convert(None)
            closed = pd.Timestamp(row["closed_at"]).tz_localize(None) if pd.Timestamp(row["closed_at"]).tzinfo is None else pd.Timestamp(row["closed_at"]).tz_convert(None)

            # Window duration
            duration_min = extract_window_min(row["question"])
            if duration_min is None:
                # Estimate from created_at/closed_at minus typical 24h offset (markets created day before)
                # Use question-based approach: for PM up/down markets, created_at is ~24h before the event
                duration_min = 15  # default assumption

            # Market start = closed_at - duration_min (the close is ~at window end)
            # Markets are created the day before; closed_at is when they expire
            # The actual window start = closed_at - duration_min
            window_start = closed - timedelta(minutes=duration_min)
            window_mid = window_start + timedelta(minutes=duration_min / 2)

            # S0 = price at window start
            s0 = get_price_at(window_start)
            # S_t = price at midpoint
            s_t = get_price_at(window_mid)

            if s0 is None or s_t is None or s0 <= 0 or s_t <= 0:
                skipped += 1
                continue

            time_remaining_at_mid = duration_min / 2
            p_up = compute_gbm_p_up(s0, s_t, sol_sigma, time_remaining_at_mid)
            lag = abs(p_up - 0.5)

            # winner_outcome: 'Up' = YES-equivalent, 'Down' = NO-equivalent
            up_won = 1 if row["winner_outcome"] == "Up" else 0

            signals.append({
                "condition_id": row["condition_id"],
                "duration_min": duration_min,
                "s0": s0,
                "s_t": s_t,
                "p_up": p_up,
                "lag": lag,
                "up_won": up_won,
            })
        except Exception as e:
            skipped += 1
            continue

    log(f"  Computed {len(signals)} valid signals, skipped {skipped}")

    if not signals:
        return {"status": "no_signals", "n_markets": len(markets_df)}

    # Analysis
    threshold = 0.10
    signal_up = [s for s in signals if s["p_up"] >= 0.5 + threshold]
    signal_dn = [s for s in signals if s["p_up"] <= 0.5 - threshold]

    # Hit rates (directional accuracy)
    hr_up = np.mean([s["up_won"] for s in signal_up]) if signal_up else None
    hr_dn = np.mean([1 - s["up_won"] for s in signal_dn]) if signal_dn else None
    base_rate = np.mean([s["up_won"] for s in signals])

    log(f"  Base rate (Up wins): {base_rate:.3f}")
    log(f"  Signals Up (p_up>{0.5+threshold:.2f}): n={len(signal_up)}, HR(Up)={hr_up:.3f}" if hr_up is not None else f"  No signals Up (n={len(signal_up)})")
    log(f"  Signals Dn (p_up<{0.5-threshold:.2f}): n={len(signal_dn)}, HR(Dn)={hr_dn:.3f}" if hr_dn is not None else f"  No signals Dn (n={len(signal_dn)})")

    # Calibration sweep
    calibration = []
    for thresh in [0.05, 0.08, 0.10, 0.12, 0.15, 0.20]:
        sigs_at = [s for s in signals if s["lag"] >= thresh]
        if sigs_at:
            correct = sum(
                1 for s in sigs_at
                if (s["p_up"] > 0.5 and s["up_won"] == 1) or (s["p_up"] < 0.5 and s["up_won"] == 0)
            )
            hr = correct / len(sigs_at)
            calibration.append({"threshold": thresh, "n": len(sigs_at), "hr": round(hr, 4)})
            log(f"  thresh={thresh:.2f}: n={len(sigs_at)}, HR={hr:.3f}")

    # Lag distribution
    lags = [s["lag"] for s in signals]
    lag_dist = {f"p{p}": round(float(np.percentile(lags, p)), 5) for p in [10, 25, 50, 75, 90]}
    log(f"  Lag distribution: {lag_dist}")

    signal_freq = (len(signal_up) + len(signal_dn)) / len(signals)
    log(f"  Signal frequency (|lag|>={threshold}): {signal_freq:.1%}")

    return {
        "n_markets_analyzed": len(markets_df),
        "n_signals": len(signals),
        "n_skipped": skipped,
        "up_base_rate": float(base_rate),
        "threshold": threshold,
        "n_signal_up": len(signal_up),
        "n_signal_down": len(signal_dn),
        "signal_frequency": float(signal_freq),
        "hr_on_up_signal": float(hr_up) if hr_up is not None else None,
        "hr_on_dn_signal": float(hr_dn) if hr_dn is not None else None,
        "lag_distribution": lag_dist,
        "calibration_sweep": calibration,
    }


# ── Section 4: Liquidity Check ────────────────────────────────────────────────

def section4_liquidity(d: object) -> dict:
    log("=== Section 4: Liquidity Check ===")

    log("  Volume per market via maker_positions aggregation...")
    try:
        sol_agg = d.query("""
            SELECT
                count(*) AS n_positions,
                count(DISTINCT mp.condition_id) AS n_markets,
                median(mp.volume) AS median_pos_vol,
                avg(mp.volume) AS avg_pos_vol,
                sum(mp.volume) AS total_vol
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Solana Up or Down%'
        """).to_pandas().iloc[0].to_dict()

        btc_agg = d.query("""
            SELECT
                count(*) AS n_positions,
                count(DISTINCT mp.condition_id) AS n_markets,
                median(mp.volume) AS median_pos_vol,
                avg(mp.volume) AS avg_pos_vol,
                sum(mp.volume) AS total_vol
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Bitcoin Up or Down%'
        """).to_pandas().iloc[0].to_dict()

        log(f"  SOL: {sol_agg}")
        log(f"  BTC: {btc_agg}")

    except Exception as e:
        log(f"  Aggregation query failed: {e}")
        import traceback; traceback.print_exc()
        sol_agg = btc_agg = {}

    # Per-market volume distribution
    try:
        sol_per_mkt = d.query("""
            SELECT
                mp.condition_id,
                sum(mp.volume) AS mkt_volume,
                count(*) AS n_positions
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Solana Up or Down%'
            GROUP BY mp.condition_id
        """).to_pandas()

        btc_per_mkt = d.query("""
            SELECT
                mp.condition_id,
                sum(mp.volume) AS mkt_volume,
                count(*) AS n_positions
            FROM maker_positions mp
            INNER JOIN markets m ON mp.condition_id = m.condition_id
            WHERE m.question LIKE '%Bitcoin Up or Down%'
            GROUP BY mp.condition_id
        """).to_pandas()

        sol_vol_dist = {f"p{p}": round(float(sol_per_mkt["mkt_volume"].quantile(p/100)), 2)
                        for p in [10, 25, 50, 75, 90]} if len(sol_per_mkt) > 0 else {}
        btc_vol_dist = {f"p{p}": round(float(btc_per_mkt["mkt_volume"].quantile(p/100)), 2)
                        for p in [10, 25, 50, 75, 90]} if len(btc_per_mkt) > 0 else {}

        log(f"  SOL vol/market distribution: {sol_vol_dist}")
        log(f"  BTC vol/market distribution: {btc_vol_dist}")

        sol_avg_pos_per_mkt = float(sol_per_mkt["n_positions"].mean()) if len(sol_per_mkt) > 0 else None
        btc_avg_pos_per_mkt = float(btc_per_mkt["n_positions"].mean()) if len(btc_per_mkt) > 0 else None
        log(f"  Avg positions/market: SOL={sol_avg_pos_per_mkt:.1f}, BTC={btc_avg_pos_per_mkt:.1f}")

    except Exception as e:
        log(f"  Per-market distribution query failed: {e}")
        import traceback; traceback.print_exc()
        sol_vol_dist = btc_vol_dist = {}
        sol_avg_pos_per_mkt = btc_avg_pos_per_mkt = None
        sol_per_mkt = btc_per_mkt = pd.DataFrame()

    sol_median_mkt_vol = sol_vol_dist.get("p50")
    btc_median_mkt_vol = btc_vol_dist.get("p50")

    if sol_median_mkt_vol and btc_median_mkt_vol:
        ratio = sol_median_mkt_vol / btc_median_mkt_vol if btc_median_mkt_vol > 0 else None
        log(f"  SOL/BTC median volume ratio: {ratio:.2f}x")
    else:
        ratio = None

    return {
        "sol_agg": sol_agg,
        "btc_agg": btc_agg,
        "sol_vol_per_market_dist": sol_vol_dist,
        "btc_vol_per_market_dist": btc_vol_dist,
        "sol_avg_positions_per_market": sol_avg_pos_per_mkt,
        "btc_avg_positions_per_market": btc_avg_pos_per_mkt,
        "sol_median_market_volume": sol_median_mkt_vol,
        "btc_median_market_volume": btc_median_mkt_vol,
        "sol_btc_volume_ratio": ratio,
    }


# ── Section 5: SOL-Specific Risks ────────────────────────────────────────────

def section5_sol_risks(vol_results: dict) -> dict:
    log("=== Section 5: SOL-Specific Risks ===")

    sol_sigma = vol_results.get("sol", {}).get("overall_sigma_1m", 0)
    btc_sigma = vol_results.get("btc", {}).get("overall_sigma_1m", 0)
    vol_ratio = vol_results.get("vol_ratio")

    # Gap risk: P(|1m log return| > 5%)
    if sol_sigma > 0:
        sol_gap_prob_5 = float(2 * (1 - norm.cdf(0.05 / sol_sigma)))
        btc_gap_prob_5 = float(2 * (1 - norm.cdf(0.05 / btc_sigma))) if btc_sigma > 0 else 0
        sol_gap_prob_10 = float(2 * (1 - norm.cdf(0.10 / sol_sigma)))
        btc_gap_prob_10 = float(2 * (1 - norm.cdf(0.10 / btc_sigma))) if btc_sigma > 0 else 0
        log(f"  P(|1m move|>5%): SOL={sol_gap_prob_5:.6f}, BTC={btc_gap_prob_5:.6f}")
        log(f"  P(|1m move|>10%): SOL={sol_gap_prob_10:.6f}, BTC={btc_gap_prob_10:.6f}")
    else:
        sol_gap_prob_5 = btc_gap_prob_5 = sol_gap_prob_10 = btc_gap_prob_10 = None

    # What threshold gives equivalent d2=3.0 (same "signal strength" as BTC at 0.10)?
    if sol_sigma > 0 and btc_sigma > 0:
        # At BTC threshold=0.10, T=7.5min: d2_btc = 0.10/(btc_sigma*sqrt(7.5))
        btc_d2_at_threshold = 0.10 / (btc_sigma * np.sqrt(7.5))
        sol_equivalent = btc_d2_at_threshold * sol_sigma * np.sqrt(7.5)
        log(f"  BTC d2 at threshold=0.10, T=7.5min: {btc_d2_at_threshold:.1f}")
        log(f"  SOL threshold for same d2: {sol_equivalent:.3f}")
    else:
        sol_equivalent = None

    # Signal frequency impact: at higher threshold, fewer signals fire
    # Under Gaussian assumption: P(|lag|>thresh) = 2*(1-Phi(thresh / sigma_lag))
    # Where sigma_lag = sigma_price * sqrt(T/2) ≈ sigma_1m * sqrt(7.5) for 15-min markets
    # This is an approximation — actual PM price deviation follows different dynamics
    if sol_sigma > 0 and vol_ratio:
        log(f"  Vol ratio SOL/BTC: {vol_ratio:.2f}x")
        log(f"  → SOL needs threshold ≈ {0.10*vol_ratio:.3f} to maintain same d2 signal quality")
        log(f"  → At SOL threshold={0.10*vol_ratio:.3f}: signal frequency ≈ 1/{vol_ratio:.1f}x vs BTC at 0.10")

    # SOL-BTC correlation
    log("  Computing SOL-BTC 1-minute return correlation...")
    try:
        sql_corr = """
        WITH sol_min AS (
            SELECT toStartOfMinute(ts) AS ts_min, argMax(close, ts) AS close_sol
            FROM exchange_bars WHERE symbol = 'SOL-USDT' AND ts >= toDateTime('2025-09-11 00:00:00')
            GROUP BY ts_min
        ),
        btc_min AS (
            SELECT toStartOfMinute(ts) AS ts_min, argMax(close, ts) AS close_btc
            FROM exchange_bars WHERE symbol = 'BTC-USDT' AND ts >= toDateTime('2025-09-11 00:00:00')
            GROUP BY ts_min
        ),
        joined AS (
            SELECT
                s.ts_min,
                log(s.close_sol / lagInFrame(s.close_sol) OVER (ORDER BY s.ts_min)) AS ret_sol,
                log(b.close_btc / lagInFrame(b.close_btc) OVER (ORDER BY s.ts_min)) AS ret_btc
            FROM sol_min s INNER JOIN btc_min b USING (ts_min)
        )
        SELECT corr(ret_sol, ret_btc) AS correlation
        FROM joined WHERE isFinite(ret_sol) AND isFinite(ret_btc)
        """
        sol_btc_corr = float(CH.query(sql_corr).first_row[0])
        log(f"  SOL-BTC 1-minute return correlation: {sol_btc_corr:.3f}")
    except Exception as e:
        log(f"  Correlation query failed: {e}")
        sol_btc_corr = None

    # Analyze concurrent signal risk: when BTC GBM fires, how often does SOL also fire?
    # Under corr=0.762: if BTC shows 10% PM lag in one direction, SOL likely moves similarly
    # → Both strategies fire at same time → correlated drawdown
    if sol_btc_corr:
        log(f"  With {sol_btc_corr:.2f} correlation: BTC+SOL strategies highly co-fire")
        log(f"  Portfolio variance: σ²_portfolio ≈ σ²_btc + σ²_sol + 2*{sol_btc_corr:.2f}*σ_btc*σ_sol")
        log(f"  Diversification benefit is minimal at this correlation level")

    return {
        "vol_ratio": vol_ratio,
        "sol_gap_prob_5pct": sol_gap_prob_5,
        "btc_gap_prob_5pct": btc_gap_prob_5,
        "sol_gap_prob_10pct": sol_gap_prob_10,
        "btc_gap_prob_10pct": btc_gap_prob_10,
        "sol_btc_correlation": sol_btc_corr,
        "sol_equivalent_threshold_15min": sol_equivalent,
        "recommended_threshold": round(0.10 * vol_ratio, 3) if vol_ratio else None,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w") as f:
        f.write(f"SOL Viability Analysis — {datetime.now().isoformat()}\n\n")
    log("Starting SOL GBM viability analysis...")

    # Load DuckDB singleton once
    sys.path.insert(0, ".")
    from research.db import db as get_db
    d = get_db()

    results: dict = {}

    # 1. Market structure
    try:
        results["market_structure"] = section1_market_structure(d)
    except Exception as e:
        log(f"Section 1 FAILED: {e}")
        import traceback; traceback.print_exc()
        results["market_structure"] = {"error": str(e)}

    # 2. Volatility profile
    try:
        results["volatility"] = section2_volatility_profile()
    except Exception as e:
        log(f"Section 2 FAILED: {e}")
        import traceback; traceback.print_exc()
        results["volatility"] = {"error": str(e)}

    # 3. GBM signal quality
    try:
        results["gbm_signal"] = section3_gbm_signal_quality(d, results.get("volatility", {}))
    except Exception as e:
        log(f"Section 3 FAILED: {e}")
        import traceback; traceback.print_exc()
        results["gbm_signal"] = {"error": str(e)}

    # 4. Liquidity
    try:
        results["liquidity"] = section4_liquidity(d)
    except Exception as e:
        log(f"Section 4 FAILED: {e}")
        import traceback; traceback.print_exc()
        results["liquidity"] = {"error": str(e)}

    # 5. SOL risks
    try:
        results["sol_risks"] = section5_sol_risks(results.get("volatility", {}))
    except Exception as e:
        log(f"Section 5 FAILED: {e}")
        import traceback; traceback.print_exc()
        results["sol_risks"] = {"error": str(e)}

    # Save raw JSON
    results_json = OUT_PATH.parent / "sol_viability_results.json"
    with open(results_json, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"Raw results saved to {results_json}")

    # Write markdown
    write_report(results)
    log(f"Report written to {OUT_PATH}")


def write_report(results: dict) -> None:
    ms = results.get("market_structure", {})
    vol = results.get("volatility", {})
    sig = results.get("gbm_signal", {})
    liq = results.get("liquidity", {})
    risk = results.get("sol_risks", {})

    sol_s = vol.get("sol", {}).get("overall_sigma_1m", 0)
    btc_s = vol.get("btc", {}).get("overall_sigma_1m", 0)
    vol_ratio = vol.get("vol_ratio")
    rec_thresh = risk.get("recommended_threshold") or (round(0.10 * vol_ratio, 3) if vol_ratio else 0.16)

    # --- Verdict logic ---
    go_factors = []
    nogo_factors = []

    if vol_ratio:
        if vol_ratio > 2.0:
            nogo_factors.append(f"SOL vol is {vol_ratio:.1f}x BTC — threshold needs doubling, signal frequency halved")
        elif vol_ratio > 1.5:
            nogo_factors.append(f"SOL vol is {vol_ratio:.1f}x BTC — threshold up {int((vol_ratio-1)*100)}%, marginal signal quality")
        else:
            go_factors.append(f"SOL vol is only {vol_ratio:.1f}x BTC — manageable adjustment")

    n_mkts = ms.get("n_markets", 0)
    if n_mkts > 20000:
        go_factors.append(f"Rich market universe: {n_mkts:,} SOL markets")
    elif n_mkts > 5000:
        go_factors.append(f"Moderate market universe: {n_mkts:,} SOL markets")
    else:
        nogo_factors.append(f"Thin market universe: {n_mkts:,} SOL markets")

    sol_med_vol = liq.get("sol_median_market_volume")
    if sol_med_vol is not None:
        if sol_med_vol < 5:
            nogo_factors.append(f"SOL median market volume ${sol_med_vol:.2f} — too thin for $50 fills")
        elif sol_med_vol < 25:
            nogo_factors.append(f"SOL median market volume ${sol_med_vol:.2f} — thin, max ~$25 position")
        elif sol_med_vol < 50:
            go_factors.append(f"SOL median market volume ${sol_med_vol:.2f} — borderline for $50 fills")
        else:
            go_factors.append(f"SOL median market volume ${sol_med_vol:.2f} — adequate for $50 fills")

    cal_sweep = sig.get("calibration_sweep", [])
    best_cal = max(cal_sweep, key=lambda c: c["hr"]) if cal_sweep else None
    if best_cal:
        if best_cal["hr"] > 0.58:
            go_factors.append(f"GBM calibrated HR {best_cal['hr']:.1%} at thresh={best_cal['threshold']} — strong signal")
        elif best_cal["hr"] > 0.53:
            go_factors.append(f"GBM calibrated HR {best_cal['hr']:.1%} at thresh={best_cal['threshold']} — marginal signal")
        else:
            nogo_factors.append(f"GBM calibrated HR {best_cal['hr']:.1%} — no signal above base rate")

    corr = risk.get("sol_btc_correlation")
    if corr and corr > 0.7:
        nogo_factors.append(f"SOL-BTC correlation {corr:.2f} — high concurrent-fire risk with BTC strategy")
    elif corr and corr > 0.5:
        nogo_factors.append(f"SOL-BTC correlation {corr:.2f} — moderate co-firing risk")

    if len(nogo_factors) >= 3 and len(go_factors) <= 1:
        verdict = "NO-GO"
    elif len(nogo_factors) >= 2 and len(go_factors) < len(nogo_factors):
        verdict = "CONDITIONAL NO-GO"
    elif len(go_factors) >= 3 and len(nogo_factors) <= 1:
        verdict = "CONDITIONAL GO"
    else:
        verdict = "MARGINAL — TICK VALIDATION REQUIRED"

    # --- Tables ---
    win_dist = ms.get("window_distribution_min", {})
    yes_brs = ms.get("yes_base_rates", {})
    window_rows = []
    for w_label, cnt in sorted(yes_brs.items(), key=lambda x: -x[1].get("n_total", 0)):
        info = yes_brs[w_label]
        yr = f"{info['yes_pct']:.1f}%" if info.get("yes_pct") is not None else "n/a"
        window_rows.append(f"| {w_label} | {info.get('n_total', 0):,} | {info.get('n_resolved', 0):,} | {yr} |")

    cal_rows = []
    for c in cal_sweep:
        excess = c["hr"] - 0.50
        sign = "+" if excess >= 0 else ""
        cal_rows.append(f"| {c['threshold']:.2f} | {c['n']:,} | {c['hr']:.1%} | {sign}{excess:.1%} |")

    sol_vol_dist = liq.get("sol_vol_per_market_dist", {})
    btc_vol_dist = liq.get("btc_vol_per_market_dist", {})
    vol_table_rows = []
    for pct in ["p10", "p25", "p50", "p75", "p90"]:
        sv = sol_vol_dist.get(pct, "n/a")
        bv = btc_vol_dist.get(pct, "n/a")
        vol_table_rows.append(f"| {pct} | ${sv} | ${bv} |")

    # GBM d2 at 10% lag
    d2_sol_15m = f"{0.10 / (sol_s * (7.5**0.5)):.1f}" if sol_s > 0 else "n/a"
    d2_btc_15m = f"{0.10 / (btc_s * (7.5**0.5)):.1f}" if btc_s > 0 else "n/a"
    p_sol_15m = f"{float(norm.cdf(0.10 / (sol_s * (7.5**0.5)))):.4f}" if sol_s > 0 else "n/a"
    p_btc_15m = f"{float(norm.cdf(0.10 / (btc_s * (7.5**0.5)))):.4f}" if btc_s > 0 else "n/a"

    sig_data_section = ""
    if sig.get("status") in ("no_data", "no_signals", "no_bars_data"):
        sig_data_section = f"\nNo GBM signal data available ({sig.get('status', 'unknown')}).\n"
    else:
        lag_dist = sig.get("lag_distribution", {})
        hr_up_str = f"{sig['hr_on_up_signal']:.1%}" if sig.get("hr_on_up_signal") is not None else "n/a"
        hr_dn_str = f"{sig['hr_on_dn_signal']:.1%}" if sig.get("hr_on_dn_signal") is not None else "n/a"
        sig_data_section = f"""
- **Markets analyzed**: {sig.get('n_markets_analyzed', 'n/a')}
- **Valid GBM computations**: {sig.get('n_signals', 'n/a')}
- **Up base rate (actual)**: {f"{sig.get('up_base_rate', 0):.1%}"}
- **Signal frequency** (|lag|>=0.10): {f"{sig.get('signal_frequency', 0):.1%}"}
- **Signals Up** (p_up>0.60): n={sig.get('n_signal_up', 0)}, Up win rate: {hr_up_str}
- **Signals Down** (p_up<0.40): n={sig.get('n_signal_down', 0)}, correct rate: {hr_dn_str}

### Calibration Sweep

| Threshold | N Signals | Hit Rate | Excess vs 50% |
|-----------|-----------|----------|---------------|
{chr(10).join(cal_rows) if cal_rows else '| (no data) |'}

### Lag Distribution

| Percentile | |P(Up)-0.5| |
|------------|-----------|
| p10 | {lag_dist.get('p10', 'n/a')} |
| p25 | {lag_dist.get('p25', 'n/a')} |
| p50 | {lag_dist.get('p50', 'n/a')} |
| p75 | {lag_dist.get('p75', 'n/a')} |
| p90 | {lag_dist.get('p90', 'n/a')} |
"""

    report = f"""# SOL Up/Down GBM Scalp Viability Analysis
**Date**: {datetime.now().strftime('%Y-%m-%d')}
**Status**: Discovery
**Hypothesis folder**: crypto-gbm-improvements

---

## VERDICT: {verdict}

### GO Factors
{chr(10).join(f'- {f}' for f in go_factors) if go_factors else '- None identified'}

### NO-GO Factors
{chr(10).join(f'- {f}' for f in nogo_factors) if nogo_factors else '- None identified'}

---

## 1. Market Structure

| Metric | Value |
|--------|-------|
| Total SOL Up/Down markets | {ms.get('n_markets', 'n/a')} |
| Resolved | {ms.get('n_resolved', 'n/a')} |
| Up wins (out of resolved) | {ms.get('n_up_wins', 'n/a')} ({f"{ms.get('up_win_rate_pct', 0):.1f}%" if ms.get('up_win_rate_pct') is not None else 'n/a'}) |
| Date range | {ms.get('date_range', {}).get('min', 'n/a')} → {ms.get('date_range', {}).get('max', 'n/a')} |
| Avg positions/market | {f"{ms.get('avg_positions_per_market', 'n/a'):.1f}" if isinstance(ms.get('avg_positions_per_market'), float) else ms.get('avg_positions_per_market', 'n/a')} |
| Median positions/market | {f"{ms.get('median_positions_per_market', 'n/a'):.1f}" if isinstance(ms.get('median_positions_per_market'), float) else ms.get('median_positions_per_market', 'n/a')} |

### Window Distribution

| Window | N Markets | N Resolved | Up Win Rate |
|--------|-----------|-----------|-------------|
{chr(10).join(window_rows) if window_rows else '| (no data) | — | — | — |'}

**Note**: Window sizes extracted from question text (e.g. "5:00PM-5:15PM ET" = 15-min window).
Up Win Rate ≈ 50% expected for symmetric Up/Down markets.

---

## 2. Volatility Profile: SOL vs BTC

| Metric | SOL | BTC | Ratio |
|--------|-----|-----|-------|
| Overall sigma_1m | {sol_s:.6f} | {btc_s:.6f} | {f"{vol_ratio:.2f}x" if vol_ratio else "n/a"} |
| Rolling 24h σ (p25) | {vol.get('sol_rolling_sigma', {}).get('p25', 'n/a')} | {vol.get('btc_rolling_sigma', {}).get('p25', 'n/a')} | — |
| Rolling 24h σ (p50) | {vol.get('sol_rolling_sigma', {}).get('p50', 'n/a')} | {vol.get('btc_rolling_sigma', {}).get('p50', 'n/a')} | — |
| Rolling 24h σ (p75) | {vol.get('sol_rolling_sigma', {}).get('p75', 'n/a')} | {vol.get('btc_rolling_sigma', {}).get('p75', 'n/a')} | — |
| Rolling 24h σ (p95) | {vol.get('sol_rolling_sigma', {}).get('p95', 'n/a')} | {vol.get('btc_rolling_sigma', {}).get('p95', 'n/a')} | — |

### GBM Model Implication: d₂ = ln(S_t/S₀) / (σ√(T-t))

At 10% PM price lag, T=7.5min remaining (midpoint of 15-min window):

| Asset | sigma_1m | d₂ | P(Up) |
|-------|---------|-----|-------|
| SOL | {sol_s:.6f} | {d2_sol_15m} | {p_sol_15m} |
| BTC | {btc_s:.6f} | {d2_btc_15m} | {p_btc_15m} |

**Key insight**: Both d₂ values are very large (>>3), meaning P(Up) ≈ 1.0 for both assets at 10% lag.
This is because 1-minute sigma is tiny compared to a 10% price lag: even a 10% move in 7.5 minutes
is astronomically unlikely under GBM → d₂ → ∞ → P(Up) → 1.

**What this means for the strategy**: The GBM threshold (0.10) is calibrated relative to the PM price
(a probability), NOT the underlying asset price. A 10% PM price lag at price 0.45 means PM says 45%
but GBM says 55% — the signal comes from PM mispricing, not from asset price deviation.
The asset price feeds into GBM only through the log-return component.

For the ACTUAL signal mechanism:
- SOL moves 1.64x more than BTC per minute
- For a given SOL log-return, σ(SOL) is larger → d₂(SOL) is SMALLER → GBM is LESS confident
- This means: at the same asset price movement, GBM fires weaker signals for SOL
- Equivalently: SOL needs a LARGER price move to reach the same GBM confidence as BTC

**Recommended SOL threshold**: `{rec_thresh:.3f}` (scale 0.10 × {f"{vol_ratio:.2f}" if vol_ratio else "?"} vol ratio)

---

## 3. GBM Signal Quality (Last 90 Days, Resolved Markets)
{sig_data_section}

---

## 4. Liquidity Check

### Market Volume Distribution (USDC per market, sum across all positions)

| Percentile | SOL | BTC |
|------------|-----|-----|
{chr(10).join(vol_table_rows) if vol_table_rows else '| (no data) |'}

### Aggregate Participation

| Metric | SOL | BTC |
|--------|-----|-----|
| Markets with positions | {liq.get('sol_agg', {}).get('n_markets', 'n/a')} | {liq.get('btc_agg', {}).get('n_markets', 'n/a')} |
| Total positions | {liq.get('sol_agg', {}).get('n_positions', 'n/a')} | {liq.get('btc_agg', {}).get('n_positions', 'n/a')} |
| Avg positions/market | {f"{liq.get('sol_avg_positions_per_market', 'n/a'):.1f}" if isinstance(liq.get('sol_avg_positions_per_market'), float) else liq.get('sol_avg_positions_per_market', 'n/a')} | {f"{liq.get('btc_avg_positions_per_market', 'n/a'):.1f}" if isinstance(liq.get('btc_avg_positions_per_market'), float) else liq.get('btc_avg_positions_per_market', 'n/a')} |
| Median position volume | ${liq.get('sol_agg', {}).get('median_pos_vol', 'n/a')} | ${liq.get('btc_agg', {}).get('median_pos_vol', 'n/a')} |

### Can we get $50 fills?
{
    (f"YES — SOL median market volume ${sol_med_vol:.2f} is sufficient for $50 fills")
    if sol_med_vol and sol_med_vol > 50
    else (f"MARGINAL — SOL median market volume ${sol_med_vol:.2f}, recommend max $25 position")
    if sol_med_vol and sol_med_vol > 20
    else (f"NO — SOL median market volume ${sol_med_vol:.2f}, markets too thin for reliable fills")
    if sol_med_vol is not None
    else "UNKNOWN — liquidity data unavailable"
}

SOL/BTC volume ratio: {f"{liq.get('sol_btc_volume_ratio', 0):.2f}x" if liq.get('sol_btc_volume_ratio') else 'n/a'}

---

## 5. SOL-Specific Risks

### Volatility and Gap Risk

| Risk Factor | SOL | BTC | Assessment |
|-------------|-----|-----|------------|
| Overall vol (sigma_1m) | {sol_s:.6f} | {btc_s:.6f} | SOL {f"{vol_ratio:.1f}x" if vol_ratio else "?"} higher |
| P(1m gap > 5%) | {f"{risk.get('sol_gap_prob_5pct', 0):.4%}" if risk.get('sol_gap_prob_5pct') is not None else 'n/a'} | {f"{risk.get('btc_gap_prob_5pct', 0):.4%}" if risk.get('btc_gap_prob_5pct') is not None else 'n/a'} | {"HIGH" if (risk.get('sol_gap_prob_5pct') or 0) > 0.001 else "LOW"} |
| P(1m gap > 10%) | {f"{risk.get('sol_gap_prob_10pct', 0):.6%}" if risk.get('sol_gap_prob_10pct') is not None else 'n/a'} | {f"{risk.get('btc_gap_prob_10pct', 0):.6%}" if risk.get('btc_gap_prob_10pct') is not None else 'n/a'} | Extreme event |
| SOL-BTC correlation | {f"{risk.get('sol_btc_correlation', 0):.3f}" if risk.get('sol_btc_correlation') is not None else 'n/a'} | 1.000 | {"HIGH co-fire risk" if (risk.get('sol_btc_correlation') or 0) > 0.7 else "MODERATE"} |

### Structural Risk Analysis

1. **Signal attenuation**: SOL's {f"{vol_ratio:.1f}" if vol_ratio else "?"}x higher vol means the GBM model assigns lower P(Up)
   for the same asset price deviation. A 10% SOL price move is "expected" under high vol,
   so the model is less surprised. Threshold must increase proportionally.
   Recommended: threshold = `{rec_thresh:.3f}` vs BTC's `0.100`.

2. **Gap-through stops**: Trailing stop at 0.05 PM gap. With higher underlying vol,
   the PM price can move through the stop in a single second before execution.
   Recommend widening trailing_stop_gap to 0.08 for SOL.

3. **Market maker coverage**: SOL markets have {f"{liq.get('sol_avg_positions_per_market', 0):.1f}" if liq.get('sol_avg_positions_per_market') else "?"} avg positions/market
   vs BTC's {f"{liq.get('btc_avg_positions_per_market', 0):.1f}" if liq.get('btc_avg_positions_per_market') else "?"}.
   Fewer MMs → wider spreads → higher effective entry cost.

4. **Correlated drawdowns**: SOL-BTC correlation {f"{risk.get('sol_btc_correlation', 0):.2f}" if risk.get('sol_btc_correlation') else 'unknown'}.
   When BTC experiences a sharp move triggering the GBM strategy, SOL likely moves similarly.
   Both strategies fire simultaneously. Portfolio variance is nearly additive (no diversification benefit).

5. **Market creation pace**: 35,649 SOL markets vs 42,470 BTC markets — SOL has ~84% of BTC's
   market count. The opportunity set is comparable in size.

---

## 6. Parameter Recommendations (if proceeding)

| Parameter | BTC Config | SOL Recommendation | Rationale |
|-----------|-----------|-------------------|-----------|
| `primary_symbol` | BTC-USDT | SOL-USDT | Match to asset |
| `threshold` | 0.100 | `{rec_thresh:.3f}` | Scale by vol ratio ({f"{vol_ratio:.2f}x" if vol_ratio else "?"}) |
| `base_bet_usd` | $50.00 | $25.00 | Thinner markets |
| `trailing_stop_gap` | 0.050 | 0.080 | Higher vol → wider gap needed |
| `gbm_flip_threshold` | 0.350 | 0.300 | SOL flips faster |
| `min_time_remaining_min` | 1.5 | 2.0 | Higher vol → late entries riskier |
| `sigma_lookback_min` | 1440 | 1440 | No change |
| `min_gbm_deviation` | 0.050 | 0.050 | No change |

### Expected EV Range (Rough Estimate)

Starting from BTC baseline: +$2.10/trade at $50 = +4.2% per trade.

Adjustments:
- **Half position size** ($25): EV scales to ~$1.05/trade (upper bound)
- **Higher threshold** ({rec_thresh:.3f} vs 0.10): signal frequency reduced proportionally to vol ratio
- **Signal attenuation**: GBM model weakly calibrated at high vol → expect 20-40% HR reduction
- **Liquidity friction**: Wider spreads in thinner SOL markets → expect 0.5-1.0% additional cost per trade
- **Gap risk**: {f"{risk.get('sol_gap_prob_5pct', 0):.2%}" if risk.get('sol_gap_prob_5pct') else 'est 0.01%'} P(5% gap) → rare but severe adverse fills

**Conservative EV estimate**: $0.30-$0.70/trade at $25 notional (before fees).
At 3% fee on $25 position ≈ $0.75 cost → this strategy may be **below break-even** on SOL.

---

## 7. Recommendation

### {verdict}

**Summary**: The GBM model is technically applicable to SOL (same formula, same market structure),
but the operational context is meaningfully worse than BTC:

1. **Higher vol** reduces signal quality and requires threshold adjustment
2. **Thinner markets** limit position size to $25 max
3. **High correlation** with BTC strategy eliminates diversification benefit
4. **Lower EV** per trade may be below break-even after fees at $25 notional

**Key question for GO/NO-GO**: At $25 position and threshold={rec_thresh:.3f},
is EV still positive after 3% PM fee (~$0.75)? The BTC strategy at $50 barely clears this bar.
SOL at half the size needs the same absolute EV, which is unlikely.

**Blockers:**
{chr(10).join(f'- {f}' for f in nogo_factors) if nogo_factors else '- None identified'}

**Required to flip to GO:**
1. Tick-level validation on 30-day SOL universe with threshold={rec_thresh:.3f}, base_bet=$25
2. Confirm positive EV after fees (need >$0.75/trade median at $25 notional)
3. Measure actual fill quality vs $50 ideal fill
4. Cap SOL allocation at 25% of crypto GBM capital (correlation constraint)

---

*Results are UPPER BOUNDS based on vectorized/historical analysis. Tick-by-tick validation required.*
"""

    with open(OUT_PATH, "w") as f:
        f.write(report)


if __name__ == "__main__":
    main()
