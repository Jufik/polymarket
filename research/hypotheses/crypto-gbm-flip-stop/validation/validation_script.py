"""
GBM Flip Stop-Loss Optimization — Tick-by-Tick Validation
==========================================================

This is the VALIDATED (not upper bound) version. Key differences vs vectorized:

1. Slippage: entry price = PM trade price + 0.01 (half-spread), exit = - 0.01
2. Fee: 3% round-trip applied explicitly to PnL
3. PM price carry-forward: only allow entry if last PM trade is < 60s old (else skip)
4. Confirmation delay: requires N consecutive 1s bars below flip threshold
5. No look-ahead: entry at the FIRST eligible bar (same as vectorized),
   but exit evaluated bar-by-bar with realistic state tracking
6. Sharpe: computed over daily PnL using $50 base bet

Configs tested (5):
  1. Baseline:       gbm_flip_threshold=0.35, confirmation_ticks=1
  2. Primary:        gbm_flip_threshold=0.25, confirmation_ticks=3
  3. Aggressive:     gbm_flip_threshold=0.20, confirmation_ticks=1
  4. Delay-only:     gbm_flip_threshold=0.35, confirmation_ticks=5
  5. Sigma-cond:     threshold = 0.20/0.25/0.35 by sigma tercile, confirmation_ticks=2

LABEL: Results are TICK-VALIDATED with slippage + fees — not upper bounds.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path("/mnt/nvme/git/polymarket/polymarket")
OUT = ROOT / "research" / "hypotheses" / "crypto-gbm-flip-stop" / "validation"
OUT.mkdir(parents=True, exist_ok=True)

LOG = OUT / "validation.log"
with open(LOG, "w") as f:
    f.write(f"[validation started {datetime.now().isoformat()}]\n")


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


# ── GBM computation (vectorized) ────────────────────────────────────────────
def gbm_p_up_arr(
    s0: float,
    s_arr: np.ndarray,
    sigma: float,
    remaining_min_arr: np.ndarray,
) -> np.ndarray:
    out = np.full(len(s_arr), 0.5)
    valid = (remaining_min_arr > 0) & (s_arr > 0) & (s0 > 0)
    if not np.any(valid):
        return out
    lr = np.log(np.where(valid, s_arr / s0, 1.0))
    vol = sigma * np.sqrt(np.where(valid, remaining_min_arr, 1.0))
    d2 = np.where(vol > 1e-12, lr / vol, 0.0)
    out[valid] = norm.cdf(d2[valid])
    expired = remaining_min_arr <= 0
    if np.any(expired):
        out[expired] = np.where(s_arr[expired] > s0, 1.0, 0.0)
    return out


# ── Strategy constants (matching production config) ───────────────────────
THRESHOLD = 0.10
DECAY_POWER = 0.5
THRESHOLD_FLOOR = 0.03
SKIP_SECONDS = 20.0
MIN_REM_MIN = 2.0
NO_ENTRY_S = 90.0
EXIT_LAG_THR = 0.02
TRAIL_GAP = 0.05
HOLD_RESOL = 0.65
TIME_STOP_S = 30.0
DUR_MIN = 15.0
DUR_S = DUR_MIN * 60

# Realistic simulation parameters
HALF_SPREAD = 0.01    # 1% half-spread for entry and exit
FEE_PCT = 0.03        # 3% round-trip fee
BASE_BET_USD = 50.0   # base position size in USD
PM_STALE_S = 60.0     # max age of PM price to accept for entry


def eff_thresh(remaining_min: float, pm_p: float) -> float:
    ratio = max(0.0, min(1.0, remaining_min / DUR_MIN))
    base = max(THRESHOLD * (ratio ** DECAY_POWER), THRESHOLD_FLOOR)
    return base + 0.25 * (pm_p * (1.0 - pm_p)) ** 2


def sigma_conditional_threshold(
    sigma: float, t33: float, t67: float, confirmation_ticks: int = 2
) -> tuple[float, int]:
    """Return (flip_threshold, confirmation_ticks) based on sigma regime."""
    if sigma > t67:
        return 0.20, confirmation_ticks
    elif sigma > t33:
        return 0.25, confirmation_ticks
    else:
        return 0.35, confirmation_ticks


# ── Tick-by-tick exit simulation ─────────────────────────────────────────────
def simulate_tick(
    gbm_ours: np.ndarray,    # GBM P(our outcome) per 1s bar after entry
    gbm_p_up: np.ndarray,    # raw GBM P(Up) per bar
    pm_ours: np.ndarray,     # PM P(our outcome) per bar; NaN where no recent trade
    pm_p_up: np.ndarray,     # PM P(Up) per bar; NaN where no recent trade
    rem_s: np.ndarray,       # seconds remaining per bar
    entry_pm: float,         # PM price for our outcome at entry (after spread adjustment)
    entry_pm_raw: float,     # raw PM mid price at entry (for break-even calc)
    won: bool,               # did we win at resolution?
    flip_thr: float,         # GBM flip threshold
    delay: int = 1,          # consecutive bars required below flip_thr
) -> dict:
    """Tick-by-tick simulation with slippage + fees applied to PnL."""
    n = len(gbm_ours)
    if n == 0:
        return {
            "exit_type": "no_bars",
            "exit_pm": entry_pm,
            "pnl_raw": 0.0,
            "pnl_net": 0.0,
            "won": False,
            "hold_s": 0.0,
            "break_even_price": entry_pm * (1 + FEE_PCT),
        }

    hwm = entry_pm_raw
    armed = False
    consec = 0

    # Carry-forward NaN PM values
    pm_o_ff = np.copy(pm_ours)
    pm_up_ff = np.copy(pm_p_up)
    if np.isnan(pm_o_ff[0]):
        pm_o_ff[0] = entry_pm_raw
        pm_up_ff[0] = entry_pm_raw
    for i in range(1, n):
        if np.isnan(pm_o_ff[i]):
            pm_o_ff[i] = pm_o_ff[i - 1]
            pm_up_ff[i] = pm_up_ff[i - 1]

    def make_result(
        exit_type: str,
        exit_pm_raw: float,
        hold_s: float,
        won_flag: bool,
    ) -> dict:
        # Apply slippage on exit (sell at bid = mid - half_spread, but capped at 0.01)
        exit_pm_net = max(exit_pm_raw - HALF_SPREAD, 0.01)
        pnl_raw = exit_pm_raw - entry_pm_raw
        # Net PnL: difference from entry (with spread), minus fee on transaction value
        # entry cost: bought at entry_pm (= raw + half_spread)
        # exit proceeds: exit_pm_net
        # fee: FEE_PCT * entry_pm (PM charges on buy side)
        pnl_net = (exit_pm_net - entry_pm) - FEE_PCT * entry_pm
        return {
            "exit_type": exit_type,
            "exit_pm_raw": exit_pm_raw,
            "exit_pm_net": exit_pm_net,
            "pnl_raw": pnl_raw,
            "pnl_net": pnl_net,
            "won": won_flag,
            "hold_s": hold_s,
            "break_even_price": entry_pm * (1 + FEE_PCT),
        }

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
                return make_result("time_stop", po, DUR_S - rs, po > entry_pm)
            else:
                res = 1.0 if won else 0.0
                return make_result("hold_resolution", res, DUR_S - rs, won)

        # 2. GBM flip stop (with confirmation delay)
        eff_flip = flip_thr  # already resolved for sigma-adaptive before call
        if eff_flip > 0 and go < eff_flip:
            consec += 1
            if consec >= delay:
                return make_result("flip_stop", po, DUR_S - rs, po > entry_pm)
        else:
            consec = 0

        # 3. Trailing stop (if armed)
        if armed:
            floor = hwm - TRAIL_GAP
            if po < floor:
                return make_result("trailing_stop", po, DUR_S - rs, po > entry_pm)

        # 4. Convergence → arm trailing stop
        if not armed and has_pm and abs(gup - pup) < EXIT_LAG_THR:
            armed = True

    # Held to resolution
    res = 1.0 if won else 0.0
    return make_result("resolution", res, float(DUR_S - rem_s[-1]) if n > 0 else 0.0, won)


# ── Config definitions ─────────────────────────────────────────────────────
CONFIGS = [
    {
        "name": "baseline",
        "label": "Baseline (thr=0.35, delay=1)",
        "flip_thr": 0.35,
        "delay": 1,
        "sigma_cond": False,
    },
    {
        "name": "primary",
        "label": "Primary (thr=0.25, delay=3)",
        "flip_thr": 0.25,
        "delay": 3,
        "sigma_cond": False,
    },
    {
        "name": "aggressive",
        "label": "Aggressive (thr=0.20, delay=1)",
        "flip_thr": 0.20,
        "delay": 1,
        "sigma_cond": False,
    },
    {
        "name": "delay_only",
        "label": "Delay-only (thr=0.35, delay=5)",
        "flip_thr": 0.35,
        "delay": 5,
        "sigma_cond": False,
    },
    {
        "name": "sigma_cond",
        "label": "Sigma-conditional (delay=2)",
        "flip_thr": None,  # dynamically computed
        "delay": 2,
        "sigma_cond": True,
    },
]


def main() -> None:
    log("GBM Flip Stop Validation — Tick-by-Tick with Slippage + Fees")
    log("=" * 65)
    log(f"Half-spread: {HALF_SPREAD:.2%}, Fee: {FEE_PCT:.2%}, Base bet: ${BASE_BET_USD}")

    con = duckdb.connect()

    # ── Step 1: Load markets ───────────────────────────────────────────────
    log("Loading markets...")
    mkt_df = con.execute("""
        SELECT condition_id, winner_outcome,
               epoch(window_start_utc) AS start_ts,
               epoch(window_end_utc) AS end_ts
        FROM read_parquet('data/research/btc_updown/markets.parquet')
        WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
        ORDER BY start_ts
    """).df()
    log(f"Markets: {len(mkt_df):,}")

    # ── Step 2: Compute sigma per market ──────────────────────────────────
    log("Computing sigma (30-min pre-window 1s bars)...")
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
    log(f"Sigma computed for {len(sigma_df):,} markets")

    # ── Step 3: S0 per market ─────────────────────────────────────────────
    log("Computing S0 (first exchange bar in window)...")
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
    log(f"S0 for {len(s0_df):,} markets")

    # ── Step 4: Full bar table with PM prices (ASOF join) ─────────────────
    log("Building bar+PM table (ASOF join)...")
    bar_df = con.execute("""
        WITH markets AS (
            SELECT condition_id, token_yes, token_no, winner_outcome,
                   epoch(window_start_utc) AS start_ts,
                   epoch(window_end_utc) AS end_ts
            FROM read_parquet('data/research/btc_updown/markets.parquet')
            WHERE duration_min = 15 AND winner_outcome IN ('Up', 'Down')
        ),
        pm_raw AS (
            SELECT t.condition_id,
                   CASE WHEN t.asset_id = m.token_yes
                        THEN CAST(t.price AS DOUBLE)
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
               pm.p_up AS pm_p_up,
               pm.ts AS pm_ts
        FROM bars b
        ASOF LEFT JOIN pm_raw pm
            ON b.condition_id = pm.condition_id AND pm.ts <= b.ts
        ORDER BY b.condition_id, b.ts
    """).df()
    log(f"Bar table: {len(bar_df):,} rows")

    # ── Step 5: Merge metadata ─────────────────────────────────────────────
    meta = mkt_df.merge(sigma_df[["condition_id", "sigma"]], on="condition_id", how="inner")
    meta = meta.merge(s0_df, on="condition_id", how="inner")
    log(f"Markets with valid sigma + s0: {len(meta):,}")

    # Compute sigma terciles for sigma-conditional config
    sigma_arr = meta["sigma"].values
    t33, t67 = float(np.percentile(sigma_arr, 33)), float(np.percentile(sigma_arr, 67))
    sigma_median = float(np.median(sigma_arr))
    log(f"Sigma: median={sigma_median:.6f}, t33={t33:.6f}, t67={t67:.6f}")

    # Group bar_df by condition_id for fast lookup
    log("Grouping bars by condition_id...")
    bt_grouped = {
        cid: grp.reset_index(drop=True)
        for cid, grp in bar_df.groupby("condition_id")
    }
    log(f"Grouped {len(bt_grouped):,} markets")

    # ── Step 6: Per-config simulation ─────────────────────────────────────
    all_config_results: dict[str, list[dict]] = {c["name"]: [] for c in CONFIGS}
    trade_dates: list[float] = []  # for Sharpe calculation (use start_ts as date)
    sigmas_used: list[float] = []
    n_entries = 0
    n_skip = {"no_bars": 0, "no_signal": 0, "stale_pm": 0}

    for _, row in meta.iterrows():
        cid = str(row["condition_id"])
        start_ts = float(row["start_ts"])
        end_ts = float(row["end_ts"])
        winner = str(row["winner_outcome"])
        sigma = float(row["sigma"])
        s0 = float(row["s0"])

        bars = bt_grouped.get(cid)
        if bars is None or len(bars) < 5:
            n_skip["no_bars"] += 1
            continue

        ts_arr = bars["ts"].values.astype(np.float64)
        close_arr = bars["close"].values.astype(np.float64)
        pm_p_up_raw = bars["pm_p_up"].values.astype(np.float64)  # NaN where no trade
        pm_ts_raw = bars["pm_ts"].values.astype(np.float64)      # timestamp of last PM trade

        rem_min = (end_ts - ts_arr) / 60.0
        rem_s = (end_ts - ts_arr)
        elapsed_s = ts_arr - start_ts

        # GBM P(Up) for all bars
        gbm_up = gbm_p_up_arr(s0, close_arr, sigma, rem_min)

        # Entry eligibility (same as vectorized)
        eligible = (
            (elapsed_s >= SKIP_SECONDS)
            & (rem_min >= MIN_REM_MIN)
            & (rem_s >= NO_ENTRY_S)
            & (np.abs(gbm_up - 0.5) >= 0.05)
        )

        # Find first valid entry
        found = False
        for idx in np.where(eligible)[0]:
            gbm_pu = gbm_up[idx]
            ts_bar = ts_arr[idx]

            # PM price staleness check (CRITICAL for tick validation)
            pm_pu = pm_p_up_raw[idx]
            pm_ts_at_entry = pm_ts_raw[idx]
            if np.isnan(pm_pu) or np.isnan(pm_ts_at_entry):
                # Look back for recent PM trade
                lookback = np.where(
                    (~np.isnan(pm_p_up_raw[: idx + 1]))
                    & (ts_bar - ts_arr[: idx + 1] <= PM_STALE_S)
                )[0]
                if len(lookback) == 0:
                    continue  # stale PM → skip
                pm_pu = pm_p_up_raw[lookback[-1]]
            else:
                # Check staleness of ASOF-joined PM price
                if ts_bar - pm_ts_at_entry > PM_STALE_S:
                    continue  # PM price too old

            lag = gbm_pu - pm_pu
            thresh = eff_thresh(rem_min[idx], pm_pu)
            if abs(lag) <= thresh:
                continue

            # Entry confirmed
            outcome = "YES" if lag > 0 else "NO"
            # PM price for our outcome
            entry_pm_raw = pm_pu if outcome == "YES" else (1.0 - pm_pu)
            # Apply entry slippage: we buy at ask (raw + half_spread)
            entry_pm_slip = min(entry_pm_raw + HALF_SPREAD, 0.98)
            won_at_res = (outcome == "YES" and winner == "Up") or (
                outcome == "NO" and winner == "Down"
            )

            # Post-entry arrays
            pe = ts_arr >= ts_bar
            post_gbm_up = gbm_up[pe]
            post_gbm_ours = post_gbm_up if outcome == "YES" else (1.0 - post_gbm_up)
            post_pm_up = pm_p_up_raw[pe]
            post_pm_ours = np.where(
                np.isnan(post_pm_up),
                np.nan,
                post_pm_up if outcome == "YES" else (1.0 - post_pm_up),
            )
            post_rem_s = rem_s[pe]

            # Simulate for each config
            for cfg in CONFIGS:
                if cfg["sigma_cond"]:
                    flip_thr, delay = sigma_conditional_threshold(sigma, t33, t67, cfg["delay"])
                else:
                    flip_thr = float(cfg["flip_thr"])
                    delay = int(cfg["delay"])

                r = simulate_tick(
                    post_gbm_ours,
                    post_gbm_up,
                    post_pm_ours,
                    post_pm_up,
                    post_rem_s,
                    entry_pm=entry_pm_slip,
                    entry_pm_raw=entry_pm_raw,
                    won=won_at_res,
                    flip_thr=flip_thr,
                    delay=delay,
                )
                r["outcome"] = outcome
                r["sigma"] = sigma
                r["start_ts"] = start_ts
                all_config_results[cfg["name"]].append(r)

            trade_dates.append(start_ts)
            sigmas_used.append(sigma)
            n_entries += 1
            found = True
            break

        if not found:
            n_skip["no_signal"] += 1

    log(f"\nSimulation complete:")
    log(f"  Entries: {n_entries:,}")
    log(f"  Skipped no_bars:   {n_skip['no_bars']:,}")
    log(f"  Skipped no_signal: {n_skip['no_signal']:,}")

    if n_entries == 0:
        log("ERROR: No entries found!")
        sys.exit(1)

    # ── Step 7: Compute metrics per config ────────────────────────────────
    log("\nComputing metrics per config...")

    # Sigma regimes for breakdown
    sigma_arr2 = np.array(sigmas_used)
    regime_labels = [
        "low" if s < t33 else ("mid" if s < t67 else "high") for s in sigmas_used
    ]

    # Daily PnL for Sharpe (group by day)
    def compute_sharpe(pnls_usd: list[float], dates_ts: list[float]) -> float:
        if len(pnls_usd) < 2:
            return float("nan")
        df = pd.DataFrame({"pnl": pnls_usd, "day": [int(d // 86400) for d in dates_ts]})
        daily = df.groupby("day")["pnl"].sum()
        if len(daily) < 5:
            return float("nan")
        return float(daily.mean() / daily.std()) * np.sqrt(252) if daily.std() > 0 else 0.0

    results_summary: list[dict] = []
    config_dfs: dict[str, pd.DataFrame] = {}

    for cfg in CONFIGS:
        name = cfg["name"]
        res_list = all_config_results[name]
        df = pd.DataFrame(res_list)
        config_dfs[name] = df

        pnl_raw = df["pnl_raw"].values * BASE_BET_USD / 1.0  # pnl_raw is in units (0-1 scale)
        # pnl_net is already scaled: pnl_net * bet_usd
        # pnl_raw = (exit_pm_raw - entry_pm_raw)  (no fee, no spread)
        # pnl_net = (exit_pm_net - entry_pm_slip) - fee  (full cost)
        pnl_net_usd = df["pnl_net"].values * BASE_BET_USD

        hit_rate = float(df["won"].mean())
        avg_pnl_raw = float(df["pnl_raw"].mean())
        avg_pnl_net_usd = float(pnl_net_usd.mean())
        total_pnl_net_usd = float(pnl_net_usd.sum())
        avg_hold_s = float(df["hold_s"].mean())
        avg_hold_days = avg_hold_s / 86400.0

        # Exit type breakdown
        exit_counts = df["exit_type"].value_counts().to_dict()
        n_flip = int((df["exit_type"] == "flip_stop").sum())
        flip_pct = 100.0 * n_flip / len(df) if len(df) > 0 else 0.0

        # False stop rate: flip exits where the position would have won at resolution
        # (use the 'won' field from resolution — but we need to compare against "no_flip" variant)
        # Approximate: if exit_type=flip_stop and pnl_raw < 0 → true stop (market moved vs us)
        # if exit_type=flip_stop and pnl_raw >= 0 → false stop (we exited when still above entry)
        flip_mask = df["exit_type"] == "flip_stop"
        n_false_stops = int((flip_mask & (df["pnl_raw"] >= 0)).sum())
        false_stop_pct = 100.0 * n_false_stops / n_flip if n_flip > 0 else 0.0

        # Sharpe
        sharpe = compute_sharpe(pnl_net_usd.tolist(), trade_dates)

        # Max drawdown (per position — not portfolio)
        max_dd = float(np.min(pnl_net_usd)) if len(pnl_net_usd) > 0 else 0.0

        # Compounding score: excess_hr × avg_edge_usd / median_hold_days
        base_rate = 0.50  # BTC is ~50/50 baseline
        excess_hr = hit_rate - base_rate
        median_hold_days = float(np.median(df["hold_s"].values)) / 86400.0
        if median_hold_days > 0 and avg_pnl_net_usd > 0:
            comp_score = excess_hr * avg_pnl_net_usd / median_hold_days
        else:
            comp_score = 0.0

        # Trades per month (30 days)
        total_seconds = max(trade_dates) - min(trade_dates) if len(trade_dates) > 1 else 1
        trades_per_month = n_entries / (total_seconds / 86400.0 / 30.0) if total_seconds > 0 else 0.0

        # Regime breakdown
        regime_rows = {}
        for r in ["low", "mid", "high"]:
            mask = np.array([x == r for x in regime_labels])
            sr = df[mask]
            if len(sr) > 0:
                regime_rows[r] = {
                    "n": int(len(sr)),
                    "hit_rate": float(sr["won"].mean()),
                    "avg_pnl_net_usd": float(sr["pnl_net"].mean() * BASE_BET_USD),
                    "flip_exits": int((sr["exit_type"] == "flip_stop").sum()),
                }

        summary = {
            "config": name,
            "label": cfg["label"],
            "flip_thr": cfg["flip_thr"],
            "delay": cfg["delay"],
            "sigma_cond": cfg["sigma_cond"],
            "n_signals": n_entries,
            "hit_rate": hit_rate,
            "avg_pnl_raw": avg_pnl_raw,
            "avg_pnl_net_usd": avg_pnl_net_usd,
            "total_pnl_net_usd": total_pnl_net_usd,
            "avg_hold_s": avg_hold_s,
            "avg_hold_days": avg_hold_days,
            "median_hold_days": median_hold_days,
            "sharpe": sharpe,
            "max_dd_per_pos": max_dd,
            "n_flip_exits": n_flip,
            "flip_exit_pct": flip_pct,
            "false_stop_pct": false_stop_pct,
            "compounding_score": comp_score,
            "trades_per_month": trades_per_month,
            "exit_type_distribution": exit_counts,
            "by_sigma_regime": regime_rows,
        }
        results_summary.append(summary)

        log(f"\n--- {cfg['label']} ---")
        log(f"  n_signals:       {n_entries:,}")
        log(f"  hit_rate:        {hit_rate:.3f} ({100*hit_rate:.1f}%)")
        log(f"  avg_pnl_raw:     {avg_pnl_raw:+.4f} ({avg_pnl_raw*100:+.2f}pp)")
        log(f"  avg_pnl_net_usd: ${avg_pnl_net_usd:+.2f}")
        log(f"  total_pnl_net:   ${total_pnl_net_usd:+,.0f}")
        log(f"  sharpe:          {sharpe:.2f}")
        log(f"  avg_hold_s:      {avg_hold_s:.0f}s ({avg_hold_s/60:.1f}min)")
        log(f"  flip_exits:      {n_flip} ({flip_pct:.1f}%)")
        log(f"  false_stop_pct:  {false_stop_pct:.1f}%")
        log(f"  comp_score:      {comp_score:.3f}")
        log(f"  exit types:      {exit_counts}")

    # ── Step 8: Vectorized vs Tick comparison ─────────────────────────────
    VEC_RESULTS = {
        "baseline": {"hr": 0.7985, "pnl": 0.1066, "flip_pct": 13.9},
        "primary":  {"hr": 0.8444, "pnl": 0.1088, "flip_pct": 5.7},
        "aggressive": {"hr": 0.8368, "pnl": 0.1085, "flip_pct": 4.1},
        "delay_only": {"hr": 0.8544, "pnl": 0.1086, "flip_pct": 12.0},
        "sigma_cond": {"hr": None, "pnl": None, "flip_pct": None},  # not in vec sweep
    }

    log("\n" + "=" * 80)
    log("VECTORIZED vs TICK COMPARISON TABLE")
    log("=" * 80)
    log(f"{'Config':<25} {'Vec HR':>8} {'Tick HR':>8} {'Deg(pp)':>8} "
        f"{'Vec PnL':>9} {'Tick PnL($)':>11} {'Deg':>8}")
    log("-" * 80)

    comparison_rows = []
    for s in results_summary:
        name = s["config"]
        vec = VEC_RESULTS.get(name, {})
        vec_hr = vec.get("hr")
        vec_pnl = vec.get("pnl")
        tick_hr = s["hit_rate"]
        tick_pnl = s["avg_pnl_net_usd"]
        deg_hr = f"{(tick_hr - vec_hr)*100:+.1f}pp" if vec_hr else "N/A"
        deg_pnl = f"{tick_pnl - vec_pnl*BASE_BET_USD:+.2f}" if vec_pnl else "N/A"
        vec_hr_str = f"{vec_hr:.3f}" if vec_hr else "N/A"
        vec_pnl_str = f"{vec_pnl*BASE_BET_USD:.2f}" if vec_pnl else "N/A"
        log(f"{s['label']:<25} {vec_hr_str:>8} {tick_hr:.3f} {deg_hr:>8} "
            f"{vec_pnl_str:>9} ${tick_pnl:>10.2f} {deg_pnl:>8}")
        comparison_rows.append({
            "config": name,
            "label": s["label"],
            "vec_hr": vec_hr,
            "tick_hr": tick_hr,
            "hr_degradation_pp": (tick_hr - vec_hr) * 100 if vec_hr else None,
            "vec_pnl_usd": vec_pnl * BASE_BET_USD if vec_pnl else None,
            "tick_pnl_usd": tick_pnl,
            "pnl_degradation_usd": tick_pnl - vec_pnl * BASE_BET_USD if vec_pnl else None,
            "flip_exit_pct": s["flip_exit_pct"],
            "false_stop_pct": s["false_stop_pct"],
            "sharpe": s["sharpe"],
            "compounding_score": s["compounding_score"],
        })

    log("=" * 80)

    # ── Step 9: Best config recommendation ───────────────────────────────
    # Rank by: (1) positive total PnL, (2) Sharpe, (3) compounding score
    viable = [s for s in results_summary if s["total_pnl_net_usd"] > 0]
    if viable:
        best = max(viable, key=lambda x: x["sharpe"] if not np.isnan(x["sharpe"]) else -999)
        log(f"\nBEST CONFIG: {best['label']}")
        log(f"  Sharpe: {best['sharpe']:.2f}, Total PnL: ${best['total_pnl_net_usd']:+,.0f}")
        log(f"  Compounding score: {best['compounding_score']:.3f}")
    else:
        best = results_summary[0]
        log(f"\nWARNING: No config is profitable after fees. Least-bad: {best['label']}")

    # ── Step 10: Save artifacts ───────────────────────────────────────────
    def _fix(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj) if np.isfinite(obj) else None
        if isinstance(obj, dict):
            return {k: _fix(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_fix(v) for v in obj]
        return obj

    output = {
        "meta": {
            "run_date": datetime.now().isoformat(),
            "n_markets_total": len(meta),
            "n_entries": n_entries,
            "duration_min": 15,
            "sim_params": {
                "half_spread": HALF_SPREAD,
                "fee_pct": FEE_PCT,
                "base_bet_usd": BASE_BET_USD,
                "pm_stale_s": PM_STALE_S,
            },
            "sigma": {"median": sigma_median, "t33": t33, "t67": t67},
            "note": "TICK-VALIDATED — slippage + fees applied, PM staleness checked",
        },
        "configs": results_summary,
        "comparison": comparison_rows,
        "best_config": best["config"],
    }

    with open(OUT / "results.json", "w") as f:
        json.dump(_fix(output), f, indent=2)

    log(f"\nSaved: {OUT}/results.json")
    log("Done.")


if __name__ == "__main__":
    main()
