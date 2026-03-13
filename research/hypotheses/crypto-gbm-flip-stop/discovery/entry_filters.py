"""
Entry Filter Analysis — Vol Spike and Flat Window Suppression
=============================================================

Tests two entry filter ideas for the crypto GBM strategy:
  A) Vol Spike Entry Suppression: suppress when sigma_60s / sigma_1800s > threshold
  B) Flat Window Entry Suppression: suppress when |log(spot/s0)| < threshold

Uses the same data loading pattern as 01_baseline_analysis.py.
LABEL: Results are UPPER BOUNDS — vectorized sim, no live orderbook.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path("/mnt/nvme/git/polymarket/polymarket")
OUT = ROOT / "research" / "hypotheses" / "crypto-gbm-flip-stop" / "discovery"
OUT.mkdir(parents=True, exist_ok=True)

LOG = OUT / "entry_filters.log"
with open(LOG, "w") as f:
    f.write(f"[entry_filters started {datetime.now().isoformat()}]\n")


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


# ── GBM computation ────────────────────────────────────────────────────────────
def gbm_p_up_arr(s0: float, s_arr: np.ndarray, sigma: float, remaining_min_arr: np.ndarray) -> np.ndarray:
    out = np.full(len(s_arr), 0.5)
    valid = (remaining_min_arr > 0) & (s_arr > 0) & (s0 > 0)
    if not np.any(valid):
        out[:] = np.where(s_arr > s0, 1.0, 0.0)
        return out
    lr = np.log(np.where(valid, s_arr / s0, 1.0))
    vol = sigma * np.sqrt(np.where(valid, remaining_min_arr, 1.0))
    d2 = np.where(vol > 1e-12, lr / vol, 0.0)
    out[valid] = norm.cdf(d2[valid])
    expired = remaining_min_arr <= 0
    if np.any(expired):
        out[expired] = np.where(s_arr[expired] > s0, 1.0, 0.0)
    return out


# ── Strategy config (same as baseline) ─────────────────────────────────────────
THRESHOLD = 0.10
DECAY_POWER = 0.5
THRESHOLD_FLOOR = 0.03
SKIP_SECONDS = 20.0
MIN_REM_MIN = 2.0
NO_ENTRY_S = 90.0
EXIT_LAG_THR = 0.02
TRAIL_GAP = 0.05
GBM_FLIP_BL = 0.35
HOLD_RESOL = 0.65
TIME_STOP_S = 30.0
DUR_MIN = 15.0
DUR_S = DUR_MIN * 60


def eff_thresh(remaining_min: float, pm_p: float) -> float:
    ratio = max(0.0, min(1.0, remaining_min / DUR_MIN))
    base = max(THRESHOLD * (ratio ** DECAY_POWER), THRESHOLD_FLOOR)
    return base + 0.25 * (pm_p * (1.0 - pm_p)) ** 2


# ── Exit simulation (same as baseline, flip_thr=0.35, delay=1) ───────────────
def simulate_baseline(
    gbm_ours: np.ndarray,
    gbm_p_up: np.ndarray,
    pm_ours: np.ndarray,
    pm_p_up: np.ndarray,
    rem_s: np.ndarray,
    entry_pm: float,
    won: bool,
) -> dict:
    n = len(gbm_ours)
    if n == 0:
        return {"exit_type": "no_bars", "exit_pm": entry_pm, "pnl": 0.0, "won": False, "hold_s": 0.0}

    hwm = entry_pm
    armed = False
    consec = 0

    pm_o_ff = np.copy(pm_ours)
    pm_up_ff = np.copy(pm_p_up)
    if np.isnan(pm_o_ff[0]):
        pm_o_ff[0] = entry_pm
        pm_up_ff[0] = entry_pm
    for i in range(1, n):
        if np.isnan(pm_o_ff[i]):
            pm_o_ff[i] = pm_o_ff[i - 1]
            pm_up_ff[i] = pm_up_ff[i - 1]

    for i in range(n):
        go = float(gbm_ours[i])
        gup = float(gbm_p_up[i])
        po = float(pm_o_ff[i])
        pup = float(pm_up_ff[i])
        rs = float(rem_s[i])
        has_pm = not np.isnan(pm_ours[i])
        if has_pm:
            hwm = max(hwm, po)

        if rs < TIME_STOP_S:
            if go < HOLD_RESOL:
                return {"exit_type": "time_stop", "exit_pm": po, "pnl": po - entry_pm,
                        "won": po > entry_pm, "hold_s": DUR_S - rs}
            else:
                res = 1.0 if won else 0.0
                return {"exit_type": "hold_resolution", "exit_pm": res, "pnl": res - entry_pm,
                        "won": won, "hold_s": DUR_S - rs}

        if go < GBM_FLIP_BL:
            consec += 1
            if consec >= 1:
                return {"exit_type": "flip_stop", "exit_pm": po, "pnl": po - entry_pm,
                        "won": po > entry_pm, "hold_s": DUR_S - rs}
        else:
            consec = 0

        if armed:
            if po < hwm - TRAIL_GAP:
                return {"exit_type": "trailing_stop", "exit_pm": po, "pnl": po - entry_pm,
                        "won": po > entry_pm, "hold_s": DUR_S - rs}

        if not armed and has_pm and abs(gup - pup) < EXIT_LAG_THR:
            armed = True

    res = 1.0 if won else 0.0
    return {"exit_type": "resolution", "exit_pm": res, "pnl": res - entry_pm,
            "won": won, "hold_s": float(DUR_S - rem_s[-1]) if n > 0 else 0.0}


def main() -> None:
    log("Entry Filter Analysis (Vol Spike + Flat Window)")
    log("=" * 60)

    con = duckdb.connect()

    # ── Step 1: Sigma per market (30min pre-window) ────────────────────────────
    log("Computing sigma per market...")
    sigma_df = con.execute("""
        WITH markets AS (
            SELECT condition_id,
                   epoch(window_start_utc) AS start_ts,
                   epoch(window_end_utc) AS end_ts
            FROM read_parquet('data/research/btc_updown/markets.parquet')
            WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
        ),
        pre_bars AS (
            SELECT m.condition_id, b.ts, b.close,
                   lag(b.close) OVER (PARTITION BY m.condition_id ORDER BY b.ts) AS prev_close
            FROM read_parquet('data/research/exchange_bars/btc_usdt.parquet') b
            JOIN markets m ON b.ts BETWEEN (m.start_ts - 1800) AND (m.start_ts - 1)
            WHERE b.exchange = 'BINANCE'
        ),
        log_ret AS (
            SELECT condition_id, ln(close / prev_close) AS lr
            FROM pre_bars
            WHERE prev_close > 0 AND close > 0 AND prev_close IS NOT NULL
        )
        SELECT condition_id,
               stddev_samp(lr) * sqrt(60.0) AS sigma,
               count(*) AS n_lr
        FROM log_ret
        GROUP BY condition_id
        HAVING count(*) >= 60 AND stddev_samp(lr) > 1e-7
    """).df()
    log(f"Sigma computed for {len(sigma_df)} markets")

    # ── Step 2: S0 per market ─────────────────────────────────────────────────
    log("Computing S0...")
    s0_df = con.execute("""
        WITH markets AS (
            SELECT condition_id,
                   epoch(window_start_utc) AS start_ts,
                   epoch(window_end_utc) AS end_ts
            FROM read_parquet('data/research/btc_updown/markets.parquet')
            WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
        )
        SELECT m.condition_id, first(b.close ORDER BY b.ts) AS s0
        FROM read_parquet('data/research/exchange_bars/btc_usdt.parquet') b
        JOIN markets m ON b.ts BETWEEN m.start_ts AND m.end_ts
        WHERE b.exchange = 'BINANCE'
        GROUP BY m.condition_id
    """).df()
    log(f"S0 computed for {len(s0_df)} markets")

    # ── Step 3: Full bar table with PM prices ─────────────────────────────────
    log("Building bar table with PM prices (ASOF JOIN)...")
    bar_table = con.execute("""
        WITH markets AS (
            SELECT condition_id, token_yes, token_no, winner_outcome,
                   epoch(window_start_utc) AS start_ts,
                   epoch(window_end_utc) AS end_ts
            FROM read_parquet('data/research/btc_updown/markets.parquet')
            WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
        ),
        pm_raw AS (
            SELECT t.condition_id,
                   CASE WHEN t.asset_id = m.token_yes THEN CAST(t.price AS DOUBLE)
                        ELSE 1.0 - CAST(t.price AS DOUBLE) END AS p_up,
                   epoch(t.timestamp) AS ts
            FROM read_parquet('data/research/btc_updown/trades.parquet') t
            JOIN markets m ON t.condition_id = m.condition_id
            WHERE t.size > 0
        ),
        bars AS (
            SELECT b.ts, b.close, m.condition_id, m.start_ts, m.end_ts, m.winner_outcome
            FROM read_parquet('data/research/exchange_bars/btc_usdt.parquet') b
            JOIN markets m ON b.ts BETWEEN m.start_ts AND m.end_ts
            WHERE b.exchange = 'BINANCE'
        )
        SELECT b.condition_id, b.ts, b.close, b.start_ts, b.end_ts, b.winner_outcome,
               pm.p_up AS pm_p_up
        FROM bars b
        ASOF LEFT JOIN pm_raw pm
            ON b.condition_id = pm.condition_id AND pm.ts <= b.ts
        ORDER BY b.condition_id, b.ts
    """).df()
    log(f"Bar table: {len(bar_table):,} rows")

    # ── Step 4: Load per-bar rolling vol data ─────────────────────────────────
    # For each bar in the window, we need sigma_60s (last 60 bars before that bar)
    # and sigma_1800s (last 1800 bars before that bar) from the exchange_bars file.
    # We do this with DuckDB window functions over the pre-window + window bars.
    log("Computing per-bar rolling volatility (sigma_60s, sigma_1800s)...")

    # Get the full time range we need
    mkt_meta = con.execute("""
        SELECT min(epoch(window_start_utc)) - 1800 AS min_ts,
               max(epoch(window_end_utc)) AS max_ts
        FROM read_parquet('data/research/btc_updown/markets.parquet')
        WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
    """).fetchone()
    min_ts, max_ts = mkt_meta[0], mkt_meta[1]
    log(f"Time range: {min_ts} → {max_ts}")

    # Pre-compute rolling sigma on the full bar series, then ASOF-join to market bars
    # We compute per-bar: stddev of log-returns in trailing 60 and 1800 bars
    # DuckDB rows_between is efficient here
    log("Computing rolling sigmas on exchange bars (this may take a moment)...")
    rolling_sigma = con.execute("""
        WITH raw_bars AS (
            SELECT ts, close,
                   ln(close / lag(close) OVER (ORDER BY ts)) AS lr
            FROM read_parquet('data/research/exchange_bars/btc_usdt.parquet')
            WHERE exchange = 'BINANCE'
              AND ts >= $min_ts AND ts <= $max_ts
            ORDER BY ts
        ),
        sigma_window AS (
            SELECT ts,
                   stddev_samp(lr) OVER (ORDER BY ts ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING) AS sigma_60s,
                   stddev_samp(lr) OVER (ORDER BY ts ROWS BETWEEN 1800 PRECEDING AND 1 PRECEDING) AS sigma_1800s
            FROM raw_bars
        )
        SELECT ts, sigma_60s, sigma_1800s,
               CASE WHEN sigma_1800s > 1e-10 THEN sigma_60s / sigma_1800s ELSE NULL END AS vol_ratio
        FROM sigma_window
        WHERE sigma_60s IS NOT NULL AND sigma_1800s IS NOT NULL
    """, parameters={"min_ts": int(min_ts), "max_ts": int(max_ts)}).df()
    log(f"Rolling sigma computed: {len(rolling_sigma):,} rows")

    # ── Step 5: ASOF join rolling sigma onto market bars ─────────────────────
    log("ASOF joining rolling sigma to bar table...")
    rolling_sigma_sorted = rolling_sigma.sort_values("ts").reset_index(drop=True)
    bar_table_sorted = bar_table.sort_values(["condition_id", "ts"]).reset_index(drop=True)

    # Use DuckDB for efficient ASOF join
    con.register("bar_table_reg", bar_table_sorted)
    con.register("rolling_sigma_reg", rolling_sigma_sorted)

    bar_with_vol = con.execute("""
        SELECT b.condition_id, b.ts, b.close, b.start_ts, b.end_ts, b.winner_outcome,
               b.pm_p_up,
               r.sigma_60s, r.sigma_1800s, r.vol_ratio
        FROM bar_table_reg b
        ASOF LEFT JOIN rolling_sigma_reg r ON r.ts <= b.ts
        ORDER BY b.condition_id, b.ts
    """).df()
    log(f"Bar table with vol: {len(bar_with_vol):,} rows, "
        f"vol_ratio nulls: {bar_with_vol['vol_ratio'].isna().sum():,}")

    # ── Step 6: Merge market metadata ────────────────────────────────────────
    mkt_df = con.execute("""
        SELECT condition_id, winner_outcome,
               epoch(window_start_utc) AS start_ts,
               epoch(window_end_utc) AS end_ts
        FROM read_parquet('data/research/btc_updown/markets.parquet')
        WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
    """).df()

    meta = mkt_df.merge(sigma_df[["condition_id", "sigma"]], on="condition_id", how="inner")
    meta = meta.merge(s0_df, on="condition_id", how="inner")
    log(f"Markets with valid sigma + s0: {len(meta)}")

    # Pre-group bar table by condition_id
    log("Grouping bars by condition_id...")
    bt_grouped = {cid: grp.reset_index(drop=True) for cid, grp in bar_with_vol.groupby("condition_id")}
    log(f"Grouped {len(bt_grouped)} markets")

    # ── Step 7: Entry detection loop — collect entry-level features ───────────
    log("Running entry detection loop...")

    # Per-entry records: baseline result + filter features
    entry_records = []
    n_skipped = {"no_bars": 0, "no_signal": 0}

    for _, row in meta.iterrows():
        cid = str(row["condition_id"])
        start_ts = float(row["start_ts"])
        end_ts = float(row["end_ts"])
        winner = str(row["winner_outcome"])
        sigma = float(row["sigma"])
        s0 = float(row["s0"])

        bars = bt_grouped.get(cid)
        if bars is None or len(bars) < 5:
            n_skipped["no_bars"] += 1
            continue

        ts_arr = bars["ts"].values.astype(np.float64)
        close_arr = bars["close"].values.astype(np.float64)
        pm_p_up_raw = bars["pm_p_up"].values.astype(np.float64)
        vol_ratio_arr = bars["vol_ratio"].values.astype(np.float64)
        sigma_60s_arr = bars["sigma_60s"].values.astype(np.float64)
        sigma_1800s_arr = bars["sigma_1800s"].values.astype(np.float64)

        rem_min = (end_ts - ts_arr) / 60.0
        rem_s_arr = (end_ts - ts_arr)
        elapsed_s = (ts_arr - start_ts)

        gbm_up = gbm_p_up_arr(s0, close_arr, sigma, rem_min)

        eligible = (
            (elapsed_s >= SKIP_SECONDS) &
            (rem_min >= MIN_REM_MIN) &
            (rem_s_arr >= NO_ENTRY_S) &
            (np.abs(gbm_up - 0.5) >= 0.05)
        )

        found = False
        for idx in np.where(eligible)[0]:
            gbm_pu = gbm_up[idx]
            ts_bar = ts_arr[idx]
            pm_pu = pm_p_up_raw[idx]

            if np.isnan(pm_pu):
                lookback = np.where(
                    (~np.isnan(pm_p_up_raw[:idx+1])) &
                    (ts_bar - ts_arr[:idx+1] <= 60)
                )[0]
                if len(lookback) == 0:
                    continue
                pm_pu = pm_p_up_raw[lookback[-1]]

            lag = gbm_pu - pm_pu
            thresh = eff_thresh(rem_min[idx], pm_pu)
            if abs(lag) <= thresh:
                continue

            # Entry found — compute filter features
            outcome = "YES" if lag > 0 else "NO"
            entry_pm = pm_pu if outcome == "YES" else (1.0 - pm_pu)
            won_at_res = (outcome == "YES" and winner == "Up") or \
                         (outcome == "NO" and winner == "Down")

            # Feature A: vol_ratio at entry
            vol_ratio_entry = float(vol_ratio_arr[idx]) if not np.isnan(vol_ratio_arr[idx]) else np.nan
            sigma_60s_entry = float(sigma_60s_arr[idx]) if not np.isnan(sigma_60s_arr[idx]) else np.nan

            # Feature B: abs(log(spot/s0)) at entry
            spot_entry = float(close_arr[idx])
            log_move = float(abs(np.log(spot_entry / s0))) if s0 > 0 and spot_entry > 0 else np.nan

            # Baseline simulation
            pe = ts_arr >= ts_bar
            post_gbm_up = gbm_up[pe]
            post_gbm_ours = post_gbm_up if outcome == "YES" else (1.0 - post_gbm_up)
            post_pm_up = pm_p_up_raw[pe]
            post_pm_ours = np.where(
                np.isnan(post_pm_up), np.nan,
                post_pm_up if outcome == "YES" else (1.0 - post_pm_up)
            )
            post_rem_s = rem_s_arr[pe]

            result = simulate_baseline(
                post_gbm_ours, post_gbm_up, post_pm_ours, post_pm_up,
                post_rem_s, entry_pm, won_at_res
            )

            # Also compute GBM deviation (| GBM - 0.5 | normalized)
            gbm_deviation = abs(gbm_pu - 0.5)

            entry_records.append({
                "condition_id": cid,
                "outcome": outcome,
                "entry_pm": entry_pm,
                "lag": lag,
                "sigma": sigma,
                "s0": s0,
                "spot_entry": spot_entry,
                "log_move": log_move,
                "vol_ratio": vol_ratio_entry,
                "sigma_60s": sigma_60s_entry,
                "gbm_deviation": gbm_deviation,
                "rem_min_at_entry": float(rem_min[idx]),
                "won": result["won"],
                "pnl": result["pnl"],
                "exit_type": result["exit_type"],
            })

            found = True
            break

        if not found:
            n_skipped["no_signal"] += 1

    log(f"Entry detection done:")
    log(f"  Entries: {len(entry_records)}")
    log(f"  Skipped no_bars:  {n_skipped['no_bars']}")
    log(f"  Skipped no_signal:{n_skipped['no_signal']}")

    if len(entry_records) == 0:
        log("ERROR: No entries found")
        return

    df = pd.DataFrame(entry_records)
    n_total = len(df)
    log(f"\nBaseline: n={n_total}, HR={df['won'].mean():.4f}, "
        f"avg_pnl={df['pnl'].mean():+.4f}")
    log(f"vol_ratio null count: {df['vol_ratio'].isna().sum()}")
    log(f"log_move null count:  {df['log_move'].isna().sum()}")

    # ── Step 8: Idea A — Vol Spike Suppression ─────────────────────────────────
    log("\n" + "=" * 60)
    log("IDEA A: Vol Spike Entry Suppression (sigma_60s / sigma_1800s)")
    log("=" * 60)

    VOL_THRESHOLDS = [1.5, 2.0, 2.5, 3.0, 4.0]
    baseline_hr = float(df["won"].mean())
    baseline_pnl = float(df["pnl"].mean())
    baseline_total_pnl = float(df["pnl"].sum())

    vol_results = []
    df_vol_valid = df[~df["vol_ratio"].isna()].copy()
    n_vol_valid = len(df_vol_valid)
    n_vol_null = n_total - n_vol_valid
    log(f"Entries with valid vol_ratio: {n_vol_valid} / {n_total} ({100*n_vol_valid/n_total:.1f}%)")
    log(f"  (null vol_ratio entries treated as: allowed through all filters)")
    log(f"")
    log(f"  vol_ratio distribution:")
    for pct in [25, 50, 75, 90, 95, 99]:
        v = df_vol_valid["vol_ratio"].quantile(pct / 100)
        log(f"    p{pct}: {v:.3f}")

    for thr in VOL_THRESHOLDS:
        # "suppressed" = vol_ratio > thr (high vol spike)
        # We treat null vol_ratio as "allowed" (conservative)
        suppressed = df["vol_ratio"] > thr
        n_supp = int(suppressed.sum())
        allowed = ~suppressed
        n_allow = int(allowed.sum())

        if n_supp == 0 or n_allow == 0:
            log(f"thr={thr}: skip (n_supp={n_supp}, n_allow={n_allow})")
            continue

        df_supp = df[suppressed]
        df_allow = df[allowed]

        hr_supp = float(df_supp["won"].mean())
        hr_allow = float(df_allow["won"].mean())
        pnl_supp = float(df_supp["pnl"].mean())
        pnl_allow = float(df_allow["pnl"].mean())
        flip_supp = float((df_supp["exit_type"] == "flip_stop").mean())
        flip_allow = float((df_allow["exit_type"] == "flip_stop").mean())

        # Filter quality ratio: HR_suppressed / HR_allowed
        # If < 1.0 → filter removes worse entries → good
        fqr = hr_supp / hr_allow if hr_allow > 0 else float("nan")

        # PnL impact: if we suppress the bad entries, the new avg_pnl = pnl_allow
        # Net PnL change = (n_allow * pnl_allow) - (n_total * baseline_pnl)
        #   = total_pnl_allowed - total_pnl_baseline
        #   expressed per-trade relative to baseline
        new_total_pnl = float(df_allow["pnl"].sum())
        pnl_impact = new_total_pnl - baseline_total_pnl  # absolute PnL change
        pnl_per_trade_change = pnl_allow - baseline_pnl  # per-trade PnL change

        row = {
            "threshold": thr,
            "n_total": n_total,
            "n_suppressed": n_supp,
            "n_allowed": n_allow,
            "pct_suppressed": 100.0 * n_supp / n_total,
            "hr_baseline": baseline_hr,
            "hr_suppressed": hr_supp,
            "hr_allowed": hr_allow,
            "hr_improvement": hr_allow - baseline_hr,
            "filter_quality_ratio": fqr,
            "pnl_baseline": baseline_pnl,
            "pnl_suppressed": pnl_supp,
            "pnl_allowed": pnl_allow,
            "pnl_per_trade_change": pnl_per_trade_change,
            "pnl_total_impact": pnl_impact,
            "flip_rate_suppressed": flip_supp,
            "flip_rate_allowed": flip_allow,
        }
        vol_results.append(row)

        log(f"  thr={thr:.1f}: suppress {n_supp:5d} ({100*n_supp/n_total:.1f}%) "
            f"| HR_supp={hr_supp:.4f} HR_allow={hr_allow:.4f} HR_base={baseline_hr:.4f} "
            f"| FQR={fqr:.3f} | PnL_allow={pnl_allow:+.4f} (Δ{pnl_per_trade_change:+.4f}) "
            f"| total_pnl_Δ={pnl_impact:+.2f}"
            f"| flip_supp={100*flip_supp:.1f}% flip_allow={100*flip_allow:.1f}%")

    # ── Step 9: Idea B — Flat Window Suppression ──────────────────────────────
    log("\n" + "=" * 60)
    log("IDEA B: Flat Window Entry Suppression (|log(spot/s0)|)")
    log("=" * 60)

    FLAT_THRESHOLDS = [0.0005, 0.001, 0.0015, 0.002, 0.003]

    df_lm_valid = df[~df["log_move"].isna()].copy()
    log(f"Entries with valid log_move: {len(df_lm_valid)} / {n_total}")
    log(f"  log_move distribution:")
    for pct in [5, 10, 25, 50, 75, 90, 95]:
        v = df_lm_valid["log_move"].quantile(pct / 100)
        log(f"    p{pct}: {v:.5f} ({100*v:.3f}%)")

    flat_results = []

    for thr in FLAT_THRESHOLDS:
        # "suppressed" = log_move < thr (too flat)
        suppressed = df["log_move"] < thr
        n_supp = int(suppressed.sum())
        allowed = ~suppressed
        n_allow = int(allowed.sum())

        if n_supp == 0 or n_allow == 0:
            log(f"thr={thr}: skip (n_supp={n_supp}, n_allow={n_allow})")
            continue

        df_supp = df[suppressed]
        df_allow = df[allowed]

        hr_supp = float(df_supp["won"].mean())
        hr_allow = float(df_allow["won"].mean())
        pnl_supp = float(df_supp["pnl"].mean())
        pnl_allow = float(df_allow["pnl"].mean())
        flip_supp = float((df_supp["exit_type"] == "flip_stop").mean())
        flip_allow = float((df_allow["exit_type"] == "flip_stop").mean())
        avg_dev_supp = float(df_supp["gbm_deviation"].mean())
        avg_dev_allow = float(df_allow["gbm_deviation"].mean())

        fqr = hr_supp / hr_allow if hr_allow > 0 else float("nan")
        new_total_pnl = float(df_allow["pnl"].sum())
        pnl_impact = new_total_pnl - baseline_total_pnl
        pnl_per_trade_change = pnl_allow - baseline_pnl

        row = {
            "threshold": thr,
            "n_total": n_total,
            "n_suppressed": n_supp,
            "n_allowed": n_allow,
            "pct_suppressed": 100.0 * n_supp / n_total,
            "hr_baseline": baseline_hr,
            "hr_suppressed": hr_supp,
            "hr_allowed": hr_allow,
            "hr_improvement": hr_allow - baseline_hr,
            "filter_quality_ratio": fqr,
            "pnl_baseline": baseline_pnl,
            "pnl_suppressed": pnl_supp,
            "pnl_allowed": pnl_allow,
            "pnl_per_trade_change": pnl_per_trade_change,
            "pnl_total_impact": pnl_impact,
            "flip_rate_suppressed": flip_supp,
            "flip_rate_allowed": flip_allow,
            "avg_gbm_dev_suppressed": avg_dev_supp,
            "avg_gbm_dev_allowed": avg_dev_allow,
        }
        flat_results.append(row)

        log(f"  thr={thr:.4f}: suppress {n_supp:5d} ({100*n_supp/n_total:.1f}%) "
            f"| HR_supp={hr_supp:.4f} HR_allow={hr_allow:.4f} "
            f"| FQR={fqr:.3f} | PnL_allow={pnl_allow:+.4f} (Δ{pnl_per_trade_change:+.4f}) "
            f"| total_pnl_Δ={pnl_impact:+.2f}"
            f"| avg_gbm_dev_supp={avg_dev_supp:.4f}")

    # ── Step 10: Combined Filter ──────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("COMBINED FILTER: Best A + Best B")
    log("=" * 60)

    # Find "best" thresholds: highest HR improvement with reasonable coverage
    # Use FQR < 0.98 (removes worse than average) AND < 10% suppression as tiebreaker
    combined_results = []

    vol_thr_candidates = [r for r in vol_results if r["filter_quality_ratio"] < 1.0]
    flat_thr_candidates = [r for r in flat_results if r["filter_quality_ratio"] < 1.0]

    all_vol_thrs = [r["threshold"] for r in vol_results]
    all_flat_thrs = [r["threshold"] for r in flat_results]

    for vthr in all_vol_thrs:
        for fthr in all_flat_thrs:
            supp_vol = df["vol_ratio"] > vthr
            supp_flat = df["log_move"] < fthr
            suppressed = supp_vol | supp_flat
            allowed = ~suppressed

            n_supp = int(suppressed.sum())
            n_allow = int(allowed.sum())
            if n_supp == 0 or n_allow == 0:
                continue

            df_supp = df[suppressed]
            df_allow = df[allowed]

            hr_allow = float(df_allow["won"].mean())
            pnl_allow = float(df_allow["pnl"].mean())
            new_total_pnl = float(df_allow["pnl"].sum())
            pnl_impact = new_total_pnl - baseline_total_pnl
            fqr_combined = float(df_supp["won"].mean()) / hr_allow if hr_allow > 0 else float("nan")

            combined_results.append({
                "vol_thr": vthr,
                "flat_thr": fthr,
                "n_suppressed": n_supp,
                "pct_suppressed": 100.0 * n_supp / n_total,
                "hr_allowed": hr_allow,
                "hr_improvement": hr_allow - baseline_hr,
                "pnl_allowed": pnl_allow,
                "pnl_per_trade_change": pnl_allow - baseline_pnl,
                "pnl_total_impact": pnl_impact,
                "fqr_combined": fqr_combined,
            })

    # Log top combinations by hr_improvement
    comb_df = pd.DataFrame(combined_results)
    if len(comb_df) > 0:
        comb_df = comb_df.sort_values("hr_improvement", ascending=False)
        log(f"\nTop combined filter configurations (by HR improvement):")
        for _, r in comb_df.head(10).iterrows():
            log(f"  vol>{r['vol_thr']:.1f} + flat<{r['flat_thr']:.4f}: "
                f"suppress {int(r['n_suppressed']):5d} ({r['pct_suppressed']:.1f}%) "
                f"HR_allow={r['hr_allowed']:.4f} (Δ{r['hr_improvement']:+.4f}) "
                f"PnL_allow={r['pnl_allowed']:+.4f} (Δ{r['pnl_per_trade_change']:+.4f}) "
                f"FQR={r['fqr_combined']:.3f}")

    # ── Step 11: Diagnostic breakdown ────────────────────────────────────────
    log("\n" + "=" * 60)
    log("DIAGNOSTIC: vol_ratio distribution for suppressed vs allowed entries")
    log("=" * 60)

    # Bucket vol_ratio into ranges and show HR per bucket
    df_vr = df[~df["vol_ratio"].isna()].copy()
    if len(df_vr) > 0:
        bins = [0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, np.inf]
        labels = ["<1.0", "1.0-1.5", "1.5-2.0", "2.0-2.5", "2.5-3.0", "3.0-4.0", ">4.0"]
        df_vr["vr_bucket"] = pd.cut(df_vr["vol_ratio"], bins=bins, labels=labels)
        log("  vol_ratio bucket | n | HR | avg_pnl | flip_pct")
        for b in labels:
            s = df_vr[df_vr["vr_bucket"] == b]
            if len(s) == 0:
                continue
            log(f"  {b:12s}: n={len(s):6d} HR={s['won'].mean():.4f} "
                f"pnl={s['pnl'].mean():+.4f} "
                f"flip={100*(s['exit_type']=='flip_stop').mean():.1f}%")

    log("\n" + "=" * 60)
    log("DIAGNOSTIC: log_move distribution for suppressed vs allowed entries")
    log("=" * 60)

    df_lm = df[~df["log_move"].isna()].copy()
    if len(df_lm) > 0:
        bins_lm = [0, 0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005, np.inf]
        labels_lm = ["<0.05%", "0.05-0.1%", "0.1-0.15%", "0.15-0.2%", "0.2-0.3%", "0.3-0.5%", ">0.5%"]
        df_lm["lm_bucket"] = pd.cut(df_lm["log_move"], bins=bins_lm, labels=labels_lm)
        log("  log_move bucket | n | HR | avg_pnl | flip_pct | avg_gbm_dev")
        for b in labels_lm:
            s = df_lm[df_lm["lm_bucket"] == b]
            if len(s) == 0:
                continue
            log(f"  {b:14s}: n={len(s):6d} HR={s['won'].mean():.4f} "
                f"pnl={s['pnl'].mean():+.4f} "
                f"flip={100*(s['exit_type']=='flip_stop').mean():.1f}% "
                f"gbm_dev={s['gbm_deviation'].mean():.4f}")

    # ── Step 12: Save artifacts ───────────────────────────────────────────────
    log("\nSaving artifacts...")

    def _fix(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, dict):
            return {k: _fix(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_fix(v) for v in obj]
        if isinstance(obj, float) and np.isnan(obj):
            return None
        return obj

    # Identify best filters
    best_vol = min(vol_results, key=lambda r: r["filter_quality_ratio"]) if vol_results else None
    best_flat = min(flat_results, key=lambda r: r["filter_quality_ratio"]) if flat_results else None
    best_combined = comb_df.iloc[0].to_dict() if len(comb_df) > 0 else None

    results_json = {
        "meta": {
            "run_date": datetime.now().isoformat(),
            "n_entries_baseline": n_total,
            "baseline_hr": baseline_hr,
            "baseline_avg_pnl": baseline_pnl,
            "baseline_total_pnl": baseline_total_pnl,
            "note": "UPPER BOUNDS — vectorized sim, PM prices from ASOF-joined trade data",
        },
        "idea_a_vol_spike": {
            "description": "Suppress entries when sigma_60s / sigma_1800s > threshold",
            "results": vol_results,
            "best": best_vol,
        },
        "idea_b_flat_window": {
            "description": "Suppress entries when |log(spot/s0)| < threshold",
            "results": flat_results,
            "best": best_flat,
        },
        "combined": {
            "description": "Both filters applied simultaneously",
            "results": combined_results,
            "best": best_combined,
        },
    }

    with open(OUT / "entry_filters_results.json", "w") as f:
        json.dump(_fix(results_json), f, indent=2)

    pd.DataFrame(vol_results).to_csv(OUT / "vol_spike_sweep.csv", index=False)
    pd.DataFrame(flat_results).to_csv(OUT / "flat_window_sweep.csv", index=False)
    if len(combined_results) > 0:
        comb_df.to_csv(OUT / "combined_filter_sweep.csv", index=False)

    log(f"\nArtifacts saved:")
    log(f"  {OUT}/entry_filters_results.json")
    log(f"  {OUT}/vol_spike_sweep.csv")
    log(f"  {OUT}/flat_window_sweep.csv")
    log(f"  {OUT}/combined_filter_sweep.csv")
    log(f"  {OUT}/entry_filters.log")
    log("Done.")


if __name__ == "__main__":
    main()
