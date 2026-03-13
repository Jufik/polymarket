"""
GBM Flip Stop-Loss Optimization — Baseline Analysis
====================================================

Strategy:
1. Use DuckDB to precompute per-market: sigma, s0, bars×PM prices joined table
2. Use scipy/numpy to compute GBM P(Up) at each bar
3. Python loop per position: find entry, simulate exits for all variants

NOTE: Uses actual PM trade prices (ASOF joined to exchange bars) as PM price proxy.
      Satisfies the Skeptic's constraint: real PM trade prices used for exit PnL,
      not pure GBM trajectory speculation.

LABEL: Results are UPPER BOUNDS — no live orderbook, no slippage, clean 1s bar data.
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

LOG = OUT / "analysis.log"
# Clear log
with open(LOG, "w") as f:
    f.write(f"[analysis started {datetime.now().isoformat()}]\n")

def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


# ── GBM computation ────────────────────────────────────────────────────────────
def gbm_p_up_arr(s0: float, s_arr: np.ndarray, sigma: float, remaining_min_arr: np.ndarray) -> np.ndarray:
    """GBM P(Up) vectorized over bars. Returns array."""
    out = np.full(len(s_arr), 0.5)
    valid = (remaining_min_arr > 0) & (s_arr > 0) & (s0 > 0)
    if not np.any(valid):
        # expired
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


# ── Strategy config ────────────────────────────────────────────────────────────
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


# ── Exit simulation (per position, fast inner loop) ──────────────────────────
def simulate(
    gbm_ours: np.ndarray,   # GBM P(our outcome) at each bar after entry
    gbm_p_up: np.ndarray,   # raw GBM P(Up) for lag check
    pm_ours: np.ndarray,    # PM P(our outcome) at each bar; NaN = no data
    pm_p_up: np.ndarray,    # raw PM P(Up) for lag check; NaN = no data
    rem_s: np.ndarray,      # seconds remaining at each bar
    entry_pm: float,        # PM price for our outcome at entry
    won: bool,              # did we win at resolution?
    flip_thr: float,        # GBM flip threshold (0 = disabled)
    delay: int = 1,         # confirmation ticks required
    time_adapt: bool = False,
    sigma_ratio: float = 1.0,  # for sigma-adaptive
) -> dict:
    """Simulate position exit. Returns dict with exit_type, exit_pm, pnl, won, hold_s."""
    n = len(gbm_ours)
    if n == 0:
        return {"exit_type": "no_bars", "exit_pm": entry_pm, "pnl": 0.0, "won": False, "hold_s": 0.0}

    hwm = entry_pm
    armed = False
    consec = 0

    # Fill-forward NaN pm values
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

        # 1. Time stop
        if rs < TIME_STOP_S:
            if go < HOLD_RESOL:
                return {"exit_type": "time_stop", "exit_pm": po, "pnl": po - entry_pm,
                        "won": po > entry_pm, "hold_s": DUR_S - rs}
            else:
                res = 1.0 if won else 0.0
                return {"exit_type": "hold_resolution", "exit_pm": res, "pnl": res - entry_pm,
                        "won": won, "hold_s": DUR_S - rs}

        # Effective flip threshold
        if time_adapt:
            eff_flip = max(GBM_FLIP_BL * np.sqrt(max(rs / DUR_S, 0.01)), 0.10)
        elif sigma_ratio != 1.0:
            eff_flip = max(min(GBM_FLIP_BL * sigma_ratio, 0.50), 0.15)
        else:
            eff_flip = flip_thr

        # 2. GBM flip stop
        if eff_flip > 0 and go < eff_flip:
            consec += 1
            if consec >= delay:
                return {"exit_type": "flip_stop", "exit_pm": po, "pnl": po - entry_pm,
                        "won": po > entry_pm, "hold_s": DUR_S - rs}
        else:
            consec = 0

        # 3. Trailing stop
        if armed:
            if po < hwm - TRAIL_GAP:
                return {"exit_type": "trailing_stop", "exit_pm": po, "pnl": po - entry_pm,
                        "won": po > entry_pm, "hold_s": DUR_S - rs}

        # 4. Convergence → arm
        if not armed and has_pm and abs(gup - pup) < EXIT_LAG_THR:
            armed = True

    # Resolution
    res = 1.0 if won else 0.0
    return {"exit_type": "resolution", "exit_pm": res, "pnl": res - entry_pm,
            "won": won, "hold_s": float(DUR_S - rem_s[-1]) if n > 0 else 0.0}


def summarize(results: list[dict], regimes: list[str], label: str) -> dict:
    df = pd.DataFrame(results)
    n = len(df)
    nf = int((df["exit_type"] == "flip_stop").sum())
    hr = float(df["won"].mean())
    ap = float(df["pnl"].mean())
    tp = float(df["pnl"].sum())
    ah = float(df["hold_s"].mean())
    df["sr"] = regimes
    row: dict = {
        "variant": label, "n_signals": n,
        "n_flip_exits": nf, "flip_exit_pct": 100.0 * nf / n if n > 0 else 0.0,
        "hit_rate": hr, "avg_pnl": ap, "total_pnl": tp, "avg_hold_s": ah,
    }
    for r in ["low", "mid", "high"]:
        s = df[df["sr"] == r]
        row[f"hr_{r}"] = float(s["won"].mean()) if len(s) > 0 else 0.0
        row[f"pnl_{r}"] = float(s["pnl"].mean()) if len(s) > 0 else 0.0
        row[f"flips_{r}"] = int((s["exit_type"] == "flip_stop").sum())
    return row


def main() -> None:
    log("GBM flip stop optimization — baseline analysis")
    log("=" * 60)

    con = duckdb.connect()

    # ── Step 1: Precompute sigma per market ───────────────────────────────────
    log("Computing sigma per market (30min pre-window bars)...")
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
    log("Computing S0 (first bar in window)...")
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

    # ── Step 3: Full bar-level table with PM prices (ASOF JOIN) ──────────────
    log("Building full bar-level table with PM prices (ASOF JOIN)...")
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

    # ── Step 4: Merge metadata ────────────────────────────────────────────────
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

    # ── Step 5: Entry detection + exit simulation ─────────────────────────────
    log("Running per-market simulation...")

    FLIP_THRESHOLDS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
    DELAYS = [1, 2, 3, 5]

    # Pre-group bar_table by condition_id for fast access
    log("Grouping bars by condition_id...")
    bt_grouped = {cid: grp.reset_index(drop=True) for cid, grp in bar_table.groupby("condition_id")}
    log(f"Grouped {len(bt_grouped)} markets")

    # Variants to test: (label, flip_thr, delay, time_adapt, sigma_adapt)
    variants = [
        ("no_flip_stop",     0.00, 1, False, False),
        ("thr=0.20,delay=1", 0.20, 1, False, False),
        ("thr=0.25,delay=1", 0.25, 1, False, False),
        ("thr=0.30,delay=1", 0.30, 1, False, False),
        ("thr=0.35,delay=1", 0.35, 1, False, False),  # BASELINE
        ("thr=0.40,delay=1", 0.40, 1, False, False),
        ("thr=0.45,delay=1", 0.45, 1, False, False),
        ("thr=0.35,delay=2", 0.35, 2, False, False),
        ("thr=0.35,delay=3", 0.35, 3, False, False),
        ("thr=0.35,delay=5", 0.35, 5, False, False),
        ("time_adaptive",   -1.0,  1, True,  False),
        ("sigma_adaptive",  -2.0,  1, False, True),
        ("thr=0.25,delay=3", 0.25, 3, False, False),
    ]

    # All results indexed by variant label → list of result dicts
    all_results: dict[str, list[dict]] = {v[0]: [] for v in variants}
    sigma_regimes: list[str] = []
    sigmas: list[float] = []
    n_entries = 0
    n_skipped = {"no_sigma": 0, "no_bars": 0, "no_signal": 0}

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
        pm_p_up_arr_all = bars["pm_p_up"].values.astype(np.float64)  # NaN where no trade

        # Remaining time arrays
        rem_min = (end_ts - ts_arr) / 60.0
        rem_s = (end_ts - ts_arr)
        elapsed_s = (ts_arr - start_ts)

        # GBM P(Up) for all bars (vectorized)
        gbm_up = gbm_p_up_arr(s0, close_arr, sigma, rem_min)

        # Entry eligibility
        eligible = (
            (elapsed_s >= SKIP_SECONDS) &
            (rem_min >= MIN_REM_MIN) &
            (rem_s >= NO_ENTRY_S) &
            (np.abs(gbm_up - 0.5) >= 0.05)
        )

        found = False
        for idx in np.where(eligible)[0]:
            gbm_pu = gbm_up[idx]
            ts_bar = ts_arr[idx]

            # PM price at entry
            pm_pu = pm_p_up_arr_all[idx]
            if np.isnan(pm_pu):
                # Look back: find last non-NaN pm price within 60s
                lookback = np.where((~np.isnan(pm_p_up_arr_all[:idx+1])) &
                                    (ts_bar - ts_arr[:idx+1] <= 60))[0]
                if len(lookback) == 0:
                    continue
                pm_pu = pm_p_up_arr_all[lookback[-1]]

            lag = gbm_pu - pm_pu
            thresh = eff_thresh(rem_min[idx], pm_pu)
            if abs(lag) <= thresh:
                continue

            # Entry found
            outcome = "YES" if lag > 0 else "NO"
            entry_pm = pm_pu if outcome == "YES" else (1.0 - pm_pu)
            won_at_res = (outcome == "YES" and winner == "Up") or \
                         (outcome == "NO" and winner == "Down")

            # Post-entry slices
            pe = ts_arr >= ts_bar
            post_gbm_up = gbm_up[pe]
            post_gbm_ours = post_gbm_up if outcome == "YES" else (1.0 - post_gbm_up)
            post_pm_up = pm_p_up_arr_all[pe]
            post_pm_ours = np.where(
                np.isnan(post_pm_up), np.nan,
                post_pm_up if outcome == "YES" else (1.0 - post_pm_up)
            )
            post_rem_s = rem_s[pe]

            # Simulate all variants
            sr = sigma / (sigmas[len(sigmas) - 1] if len(sigmas) > 0 else sigma)  # approx
            for vlabel, fthr, delay, tad, sad in variants:
                r = simulate(
                    post_gbm_ours, post_gbm_up,
                    post_pm_ours, post_pm_up,
                    post_rem_s, entry_pm, won_at_res,
                    flip_thr=fthr, delay=delay,
                    time_adapt=tad,
                    sigma_ratio=sr if sad else 1.0,
                )
                all_results[vlabel].append(r)

            sigmas.append(sigma)
            n_entries += 1
            found = True
            break

        if not found:
            n_skipped["no_signal"] += 1

    log(f"Simulation done:")
    log(f"  Entries: {n_entries}")
    log(f"  Skipped no_sigma: {n_skipped['no_sigma']}")
    log(f"  Skipped no_bars:  {n_skipped['no_bars']}")
    log(f"  Skipped no_signal:{n_skipped['no_signal']}")

    if n_entries == 0:
        log("ERROR: No entries found")
        return

    # ── Fix sigma regimes now that we have all sigmas ──────────────────────────
    sigma_arr = np.array(sigmas)
    sigma_median = float(np.median(sigma_arr))
    t33, t67 = np.percentile(sigma_arr, [33, 67])
    log(f"Sigma: median={sigma_median:.6f}, t33={t33:.6f}, t67={t67:.6f}")

    sigma_regimes = [
        "low" if s < t33 else ("mid" if s < t67 else "high")
        for s in sigmas
    ]

    # Fix sigma_ratio for sigma_adaptive (now we have the true median)
    sa_results = []
    # We need to re-simulate sigma_adaptive with correct ratios
    # But we'd need to re-run the loop — instead, just apply sigma ratios post-hoc
    # The sigma_adaptive was computed with sigma_ratio=1.0 (placeholder) — redo it
    log("Re-computing sigma_adaptive with correct sigma median...")
    # Re-run sigma_adaptive
    all_results["sigma_adaptive"] = []

    # Re-group meta for re-simulation
    for i, (_, row) in enumerate(meta.iterrows()):
        cid = str(row["condition_id"])
        start_ts = float(row["start_ts"])
        end_ts = float(row["end_ts"])
        winner = str(row["winner_outcome"])
        sigma = float(row["sigma"])
        s0 = float(row["s0"])

        if i >= n_entries:
            break

        # sigma_ratio for this market
        sr = sigma / sigma_median

        bars = bt_grouped.get(cid)
        if bars is None or len(bars) < 5:
            continue

        ts_arr = bars["ts"].values.astype(np.float64)
        close_arr = bars["close"].values.astype(np.float64)
        pm_p_up_arr_all = bars["pm_p_up"].values.astype(np.float64)
        rem_min = (end_ts - ts_arr) / 60.0
        rem_s = (end_ts - ts_arr)
        elapsed_s = (ts_arr - start_ts)
        gbm_up = gbm_p_up_arr(s0, close_arr, sigma, rem_min)

        eligible = (
            (elapsed_s >= SKIP_SECONDS) &
            (rem_min >= MIN_REM_MIN) &
            (rem_s >= NO_ENTRY_S) &
            (np.abs(gbm_up - 0.5) >= 0.05)
        )

        found = False
        for idx in np.where(eligible)[0]:
            gbm_pu = gbm_up[idx]
            ts_bar = ts_arr[idx]
            pm_pu = pm_p_up_arr_all[idx]
            if np.isnan(pm_pu):
                lookback = np.where((~np.isnan(pm_p_up_arr_all[:idx+1])) &
                                    (ts_bar - ts_arr[:idx+1] <= 60))[0]
                if len(lookback) == 0:
                    continue
                pm_pu = pm_p_up_arr_all[lookback[-1]]

            lag = gbm_pu - pm_pu
            thresh = eff_thresh(rem_min[idx], pm_pu)
            if abs(lag) <= thresh:
                continue

            outcome = "YES" if lag > 0 else "NO"
            entry_pm = pm_pu if outcome == "YES" else (1.0 - pm_pu)
            won_at_res = (outcome == "YES" and winner == "Up") or \
                         (outcome == "NO" and winner == "Down")

            pe = ts_arr >= ts_bar
            post_gbm_up = gbm_up[pe]
            post_gbm_ours = post_gbm_up if outcome == "YES" else (1.0 - post_gbm_up)
            post_pm_up = pm_p_up_arr_all[pe]
            post_pm_ours = np.where(np.isnan(post_pm_up), np.nan,
                                    post_pm_up if outcome == "YES" else (1.0 - post_pm_up))
            post_rem_s = rem_s[pe]

            r = simulate(post_gbm_ours, post_gbm_up, post_pm_ours, post_pm_up,
                        post_rem_s, entry_pm, won_at_res,
                        flip_thr=GBM_FLIP_BL, delay=1, sigma_ratio=sr)
            all_results["sigma_adaptive"].append(r)
            found = True
            break

        if not found:
            # Fill with empty result to maintain index alignment
            all_results["sigma_adaptive"].append({
                "exit_type": "no_entry", "exit_pm": 0.5, "pnl": 0.0, "won": False, "hold_s": 0.0
            })

    # Trim sigma_adaptive to match n_entries
    all_results["sigma_adaptive"] = all_results["sigma_adaptive"][:n_entries]

    # ── Step 6: Compute summaries ─────────────────────────────────────────────
    sweep_rows = []
    for vlabel, _, _, _, _ in variants:
        res = all_results[vlabel]
        if len(res) != n_entries:
            log(f"WARNING: {vlabel} has {len(res)} results vs {n_entries} entries")
            continue
        row = summarize(res, sigma_regimes, vlabel)
        sweep_rows.append(row)

    sweep_df = pd.DataFrame(sweep_rows)
    baseline_pnl = sweep_df[sweep_df["variant"] == "thr=0.35,delay=1"]["avg_pnl"].values[0]
    sweep_df["delta_vs_baseline"] = sweep_df["avg_pnl"] - baseline_pnl
    sweep_df = sweep_df.sort_values("avg_pnl", ascending=False).reset_index(drop=True)

    # ── Step 7: False stop analysis ───────────────────────────────────────────
    bl = pd.DataFrame(all_results["thr=0.35,delay=1"])
    cf = pd.DataFrame(all_results["no_flip_stop"])
    bl_flip = bl["exit_type"] == "flip_stop"
    n_total = len(bl)
    n_flip = int(bl_flip.sum())
    n_false = int(cf["won"].values[bl_flip.values].sum()) if n_flip > 0 else 0
    n_true = n_flip - n_false

    fl_pnl_bl = bl["pnl"].values[bl_flip.values]
    fl_pnl_cf = cf["pnl"].values[bl_flip.values]
    cf_won_arr = cf["won"].values[bl_flip.values]

    avg_pnl_lost = float(np.mean(fl_pnl_cf[cf_won_arr] - fl_pnl_bl[cf_won_arr])) if n_false > 0 else 0.0
    avg_pnl_saved = float(np.mean(fl_pnl_bl[~cf_won_arr] - fl_pnl_cf[~cf_won_arr])) if n_true > 0 else 0.0

    log(f"\n{'='*60}")
    log(f"BASELINE ANALYSIS (flip_threshold=0.35, delay=1)")
    log(f"{'='*60}")
    log(f"Total signals:         {n_total}")
    log(f"Overall hit rate:      {bl['won'].mean():.3f}")
    log(f"Avg PnL per trade:     {bl['pnl'].mean():+.4f}")
    log(f"Total PnL (relative):  {bl['pnl'].sum():+.2f}")
    log(f"")
    log(f"Exit type breakdown:")
    for et, cnt in bl["exit_type"].value_counts().items():
        log(f"  {et:20s}: {cnt:5d} ({100*cnt/n_total:.1f}%)")
    log(f"")
    log(f"Flip-stop exits:       {n_flip} ({100*n_flip/n_total:.1f}%)")
    if n_flip > 0:
        log(f"  False stops:         {n_false} ({100*n_false/n_flip:.1f}% of flips)")
        log(f"  True stops:          {n_true} ({100*n_true/n_flip:.1f}% of flips)")
        log(f"  Avg PnL lost/false:  {avg_pnl_lost:+.4f}")
        log(f"  Avg PnL saved/true:  {avg_pnl_saved:+.4f}")
        net = n_true * avg_pnl_saved - n_false * avg_pnl_lost
        log(f"  Net flip stop value: {net:+.2f} (units of pnl per trade × count)")

    log(f"")
    log(f"--- By sigma regime ---")
    bl["sr"] = sigma_regimes
    cf["sr"] = sigma_regimes
    for r in ["low", "mid", "high"]:
        sb = bl[bl["sr"] == r]
        sc = cf[cf["sr"] == r]
        if len(sb) == 0:
            continue
        nr = len(sb)
        nfr = int((sb["exit_type"] == "flip_stop").sum())
        log(f"  {r:4s}: n={nr:4d} flips={nfr:3d} ({100*nfr/nr:.0f}%) "
            f"HR_bl={sb['won'].mean():.3f} HR_cf={(sc['pnl']>0).mean():.3f} "
            f"PnL_bl={sb['pnl'].mean():+.4f} PnL_cf={sc['pnl'].mean():+.4f}")

    log(f"\n{'='*60}")
    log(f"SWEEP RESULTS (sorted by avg_pnl)")
    log(f"{'='*60}")
    cols = ["variant", "hit_rate", "avg_pnl", "delta_vs_baseline", "n_flip_exits", "flip_exit_pct", "avg_hold_s"]
    print(sweep_df[cols].to_string(index=False))

    top3 = sweep_df[sweep_df["variant"] != "thr=0.35,delay=1"].head(3)
    log(f"\nTop 3 variants (vs. baseline):")
    for _, r in top3.iterrows():
        log(f"  {r['variant']:30s}: HR={r['hit_rate']:.3f} avgPnL={r['avg_pnl']:+.4f} "
            f"delta={r['delta_vs_baseline']:+.4f} flips={r['n_flip_exits']}")

    # ── Step 8: Save artifacts ────────────────────────────────────────────────
    def _fix(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, dict):
            return {k: _fix(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_fix(v) for v in obj]
        return obj

    net_value = float(n_true * avg_pnl_saved - n_false * avg_pnl_lost)
    res_json = {
        "meta": {
            "run_date": datetime.now().isoformat(),
            "n_markets_total": len(meta),
            "n_entries_found": n_entries,
            "duration_min": 15,
            "sigma": {"median": float(sigma_median), "t33": float(t33), "t67": float(t67)},
            "note": "UPPER BOUNDS — vectorized sim, PM prices from ASOF-joined trade data",
        },
        "baseline": {
            "n_signals": n_total,
            "hit_rate": float(bl["won"].mean()),
            "avg_pnl": float(bl["pnl"].mean()),
            "total_pnl": float(bl["pnl"].sum()),
            "exit_types": bl["exit_type"].value_counts().to_dict(),
            "flip_exits": n_flip,
            "flip_pct": float(100.0 * n_flip / n_total),
            "false_stops": n_false,
            "false_pct": float(100.0 * n_false / n_flip) if n_flip > 0 else 0.0,
            "true_stops": n_true,
            "avg_pnl_lost_per_false": float(avg_pnl_lost),
            "avg_pnl_saved_per_true": float(avg_pnl_saved),
            "net_flip_stop_value": net_value,
            "by_sigma_regime": {},
        },
        "sweep": sweep_df.to_dict(orient="records"),
        "top3_vs_baseline": top3[["variant", "hit_rate", "avg_pnl", "delta_vs_baseline", "n_flip_exits"]].to_dict(orient="records"),
    }
    for r in ["low", "mid", "high"]:
        sb = bl[bl["sr"] == r]
        sc = cf[cf["sr"] == r]
        if len(sb) > 0:
            res_json["baseline"]["by_sigma_regime"][r] = {
                "n": int(len(sb)),
                "hit_rate_baseline": float(sb["won"].mean()),
                "hit_rate_no_flip": float((sc["pnl"] > 0).mean()),
                "avg_pnl_baseline": float(sb["pnl"].mean()),
                "avg_pnl_no_flip": float(sc["pnl"].mean()),
                "flip_exits": int((sb["exit_type"] == "flip_stop").sum()),
                "flip_pct": float(100.0 * (sb["exit_type"] == "flip_stop").sum() / len(sb)),
            }

    with open(OUT / "results.json", "w") as f:
        json.dump(_fix(res_json), f, indent=2)
    sweep_df.to_csv(OUT / "sweep_table.csv", index=False)

    log(f"\nArtifacts saved:")
    log(f"  {OUT}/results.json")
    log(f"  {OUT}/sweep_table.csv")
    log(f"  {OUT}/analysis.log")
    log("Done.")


if __name__ == "__main__":
    main()
