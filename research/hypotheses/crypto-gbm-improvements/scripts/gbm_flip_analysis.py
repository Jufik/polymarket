"""
GBM Flip Stop-Loss False Positive Analysis — Vectorized.

For each BTC 5-min/15-min up-or-down market:
  1. Look up S_t at 1-second resolution from pre-indexed exchange_bars
  2. Determine entry point (simulated: first second after skip_seconds with enough GBM deviation)
  3. Track GBM P(our_side) second-by-second after entry
  4. Detect first crossing below flip threshold (with optional confirmation delay)
  5. Check if GBM would have recovered within K seconds
  6. Compute PnL impact vs hold-to-resolution

UPPER BOUND — vectorized simulation, not tick-by-tick.
"""

import sys
import json
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import clickhouse_connect
from scipy.stats import norm

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

OUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-gbm-improvements/scripts")
RESULTS_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-gbm-improvements/discovery")
LOG_FILE = OUT_DIR / "gbm_flip_analysis.log"
LOG_FILE.write_text("")


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Config ────────────────────────────────────────────────────────────────────
ENTRY_THRESHOLD = 0.10
MIN_GBM_DEVIATION = 0.05
SKIP_SECONDS = 20
NO_ENTRY_WITHIN_S = 90
BET_SIZE = 50.0
SIGMA_LOOKBACK_MIN = 1440

FLIP_THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
CONFIRM_DELAYS = [0, 3, 5, 10, 15, 20]
RECOVERY_WINDOWS = [15, 30, 60, 120]
ADAPTIVE_WIDEN_PER_60S = [0.0, 0.02, 0.05]

log("=" * 70)
log("GBM FLIP STOP-LOSS FALSE POSITIVE ANALYSIS")
log("=" * 70)

# ── Step 1: Load BTC Up/Down markets ─────────────────────────────────────────
log("\n=== Step 1: Load markets ===")
import re

def parse_window_dur(question: str) -> int | None:
    m = re.search(r"(\d+):(\d+)(AM|PM)-(\d+):(\d+)(AM|PM)\s+ET", question)
    if m:
        h1, m1, mer1, h2, m2, mer2 = m.groups()
        h1, m1, h2, m2 = int(h1), int(m1), int(h2), int(m2)
        if mer1 == "PM" and h1 != 12: h1 += 12
        if mer1 == "AM" and h1 == 12: h1 = 0
        if mer2 == "PM" and h2 != 12: h2 += 12
        if mer2 == "AM" and h2 == 12: h2 = 0
        dur = (h2 * 60 + m2) - (h1 * 60 + m1)
        return int(dur) if dur > 0 else int(dur + 1440)
    return None


from research.db import db
d = db()

mkts = d.query("""
SELECT
    condition_id,
    first(question) as question,
    first(winner_outcome) as winner_outcome,
    first(token_yes) as token_yes,
    first(closed_at) as closed_at
FROM markets
WHERE lower(question) LIKE '%bitcoin%'
  AND lower(question) LIKE '%up%'
  AND lower(question) LIKE '%down%'
  AND winner_outcome IN ('Up','Down')
  AND closed_at >= '2025-09-10'
GROUP BY condition_id
""").to_pandas()

mkts["yes_won"] = mkts["winner_outcome"] == "Up"
mkts["window_dur_min"] = mkts["question"].apply(parse_window_dur)
mkts = mkts.dropna(subset=["window_dur_min"])
mkts["window_dur_min"] = mkts["window_dur_min"].astype(int)
mkts["closed_at"] = pd.to_datetime(mkts["closed_at"], utc=True).dt.floor("s")
mkts["window_start"] = mkts["closed_at"] - pd.to_timedelta(mkts["window_dur_min"], unit="m")
mkts = mkts[mkts["window_dur_min"].isin([5, 15])].copy()
# Normalize to nanoseconds then to seconds — handles both us and ns dtypes
mkts["ws_epoch"] = (pd.to_datetime(mkts["window_start"], utc=True).astype("datetime64[ns, UTC]").astype(np.int64) // 10**9)
mkts["we_epoch"] = (pd.to_datetime(mkts["closed_at"], utc=True).astype("datetime64[ns, UTC]").astype(np.int64) // 10**9)
log(f"Markets: {len(mkts):,} (5min={sum(mkts['window_dur_min']==5):,}, 15min={sum(mkts['window_dur_min']==15):,})")

# ── Step 2: Load BTC 1-second bars → contiguous numpy array ─────────────────
log("\n=== Step 2: Load BTC 1s bars ===")
ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")
log("Loading from ClickHouse...")
res = ch.query("""
    SELECT toUnixTimestamp(ts) AS epoch, close
    FROM exchange_bars
    WHERE symbol = 'BTC-USDT' AND exchange = 'BINANCE'
    ORDER BY ts
""")
bar_data = np.array(res.result_rows)  # shape (N, 2): [epoch, close]
bar_epochs_raw = bar_data[:, 0].astype(np.int64)
bar_closes_raw = bar_data[:, 1].astype(np.float64)
log(f"Loaded {len(bar_epochs_raw):,} bars")

# Create contiguous array indexed by (epoch - epoch_min)
# This gives O(1) price lookup by index arithmetic
epoch_min = int(bar_epochs_raw[0])
epoch_max = int(bar_epochs_raw[-1])
total_span = epoch_max - epoch_min + 1
log(f"Epoch range: {epoch_min} to {epoch_max} ({total_span:,} seconds = {total_span/86400:.1f} days)")

# Allocate price array, forward-fill gaps
price_array = np.zeros(total_span, dtype=np.float64)
indices = bar_epochs_raw - epoch_min
price_array[indices] = bar_closes_raw

# Forward-fill: where price is 0, use previous non-zero value
mask = price_array == 0
# Find first non-zero
first_nonzero = np.argmax(~mask)
# Forward fill
for i in range(first_nonzero + 1, total_span):
    if price_array[i] == 0:
        price_array[i] = price_array[i - 1]

log(f"Price array built: {total_span:,} seconds, {np.sum(mask):,} gaps forward-filled")
del bar_data, bar_epochs_raw, bar_closes_raw, mask  # free memory

def get_price(epoch: int) -> float:
    """O(1) price lookup."""
    idx = epoch - epoch_min
    if 0 <= idx < total_span:
        return price_array[idx]
    return 0.0

def get_prices_slice(start_epoch: int, end_epoch: int) -> np.ndarray:
    """Get a contiguous slice of prices. O(1) via numpy slicing."""
    i0 = max(0, start_epoch - epoch_min)
    i1 = min(total_span, end_epoch - epoch_min + 1)
    return price_array[i0:i1]

# ── Step 3: Compute rolling sigma at 1-min resolution ────────────────────────
log("\n=== Step 3: Rolling sigma ===")
# Resample to 1-min closes
n_minutes = total_span // 60 + 1
minute_closes = np.zeros(n_minutes, dtype=np.float64)
for i in range(n_minutes):
    offset = i * 60
    end = min(offset + 60, total_span)
    if end > offset:
        minute_closes[i] = price_array[end - 1]  # last second of the minute

# Log returns
log_returns = np.diff(np.log(np.maximum(minute_closes[1:], 1e-10)))  # skip first (possibly 0)

# Rolling sigma (1440-min = 24h)
sigma_arr = np.full(len(log_returns), np.nan)
for i in range(SIGMA_LOOKBACK_MIN, len(log_returns)):
    window = log_returns[i - SIGMA_LOOKBACK_MIN:i]
    sigma_arr[i] = np.std(window, ddof=1)

log(f"Sigma computed for {np.sum(~np.isnan(sigma_arr)):,} minutes. Median: {np.nanmedian(sigma_arr):.6f}")

def get_sigma(epoch: int) -> float:
    """Get sigma at the nearest minute."""
    minute_idx = (epoch - epoch_min) // 60
    if 0 <= minute_idx < len(sigma_arr):
        val = sigma_arr[minute_idx]
        if np.isfinite(val):
            return val
    # Search backward
    for offset in range(1, 60):
        idx = minute_idx - offset
        if 0 <= idx < len(sigma_arr) and np.isfinite(sigma_arr[idx]):
            return sigma_arr[idx]
    return 0.0

# ── Step 4: Simulate positions (vectorized per-market) ───────────────────────
log("\n=== Step 4: Simulate GBM trajectories ===")

# Pre-compute all position data
positions = []
skipped = {"no_coverage": 0, "no_sigma": 0, "no_entry": 0}

t0 = dt.datetime.now()
for idx, row in mkts.iterrows():
    dur_min = row["window_dur_min"]
    yes_won = bool(row["yes_won"])
    ws = int(row["ws_epoch"])
    we = int(row["we_epoch"])

    # Check coverage
    if ws < epoch_min or we > epoch_max:
        skipped["no_coverage"] += 1
        continue

    s0 = get_price(ws)
    if s0 <= 0:
        skipped["no_coverage"] += 1
        continue

    sigma = get_sigma(ws)
    if sigma <= 1e-7:
        skipped["no_sigma"] += 1
        continue

    # Entry scan: find first second with enough GBM deviation
    entry_start = ws + SKIP_SECONDS
    entry_end = we - NO_ENTRY_WITHIN_S
    if entry_start >= entry_end:
        skipped["no_entry"] += 1
        continue

    # Vectorized entry scan
    entry_epochs = np.arange(entry_start, entry_end + 1, dtype=np.int64)
    entry_prices = get_prices_slice(int(entry_start), int(entry_end))
    if len(entry_prices) != len(entry_epochs):
        entry_prices = entry_prices[:len(entry_epochs)]
    t_rem_entry = (we - entry_epochs) / 60.0

    lr_entry = np.log(np.maximum(entry_prices, 1e-10) / s0)
    vol_entry = sigma * np.sqrt(np.maximum(t_rem_entry, 1e-6))
    d2_entry = np.where(vol_entry > 1e-10, lr_entry / vol_entry, np.where(lr_entry > 0, 10.0, -10.0))
    p_up_entry = norm.cdf(d2_entry)

    # Find first entry point with enough deviation
    deviation = np.abs(p_up_entry - 0.5)
    valid_mask = deviation >= MIN_GBM_DEVIATION
    valid_indices = np.where(valid_mask)[0]
    if len(valid_indices) == 0:
        skipped["no_entry"] += 1
        continue

    ei = valid_indices[0]
    entry_epoch = int(entry_epochs[ei])
    entry_p_up = float(p_up_entry[ei])
    entry_side = "YES" if entry_p_up > 0.5 else "NO"

    # Build full trajectory from entry to window end
    full_prices = get_prices_slice(entry_epoch, we)
    n_seconds = we - entry_epoch + 1
    full_prices = full_prices[:n_seconds]

    t_rem_full = (we - np.arange(entry_epoch, entry_epoch + len(full_prices), dtype=np.int64)) / 60.0
    lr_full = np.log(np.maximum(full_prices, 1e-10) / s0)
    vol_full = sigma * np.sqrt(np.maximum(t_rem_full, 1e-6))
    d2_full = np.where(vol_full > 1e-10, lr_full / vol_full, np.where(lr_full > 0, 10.0, -10.0))
    p_up_full = norm.cdf(d2_full)

    if entry_side == "YES":
        p_ours = p_up_full
        we_won = yes_won
    else:
        p_ours = 1.0 - p_up_full
        we_won = not yes_won

    positions.append({
        "dur_min": dur_min,
        "entry_side": entry_side,
        "entry_gbm": entry_p_up,
        "we_won": we_won,
        "p_ours": p_ours,  # numpy array, second-by-second
        "hold_seconds": len(p_ours),
    })

elapsed = (dt.datetime.now() - t0).total_seconds()
log(f"Positions simulated: {len(positions):,} in {elapsed:.1f}s")
log(f"Skipped: {skipped}")
log(f"By duration: 5min={sum(1 for p in positions if p['dur_min']==5):,}, "
    f"15min={sum(1 for p in positions if p['dur_min']==15):,}")

# ── Step 5: Analyze flip crossings ───────────────────────────────────────────
log("\n=== Step 5: Flip analysis ===")

def find_first_confirmed_flip(traj: np.ndarray, threshold: float, confirm_s: int) -> int | None:
    """Find first index where traj is below threshold for confirm_s consecutive seconds.
    Returns the index of the FIRST second in the streak, or None."""
    below = traj < threshold
    if confirm_s == 0:
        indices = np.where(below)[0]
        return int(indices[0]) if len(indices) > 0 else None

    # Use convolution to find runs of confirm_s consecutive Trues
    kernel = np.ones(confirm_s)
    conv = np.convolve(below.astype(np.float64), kernel, mode="valid")
    matches = np.where(conv >= confirm_s)[0]
    return int(matches[0]) if len(matches) > 0 else None


results = {}
for flip_th in FLIP_THRESHOLDS:
    for confirm_s in CONFIRM_DELAYS:
        key = f"th={flip_th:.2f}_confirm={confirm_s}s"
        stats = {
            "flip_threshold": flip_th,
            "confirm_delay_s": confirm_s,
            "total": len(positions),
            "flipped": 0,
            "no_flip": 0,
            "flipped_won": 0,
            "flipped_lost": 0,
            "no_flip_won": 0,
            "no_flip_lost": 0,
            "recovered_15s": 0,
            "recovered_30s": 0,
            "recovered_60s": 0,
            "recovered_120s": 0,
            "flip_seconds_list": [],
            "flip_gbm_list": [],
            "5min": {"flipped": 0, "no_flip": 0, "flipped_won": 0, "flipped_lost": 0,
                     "no_flip_won": 0, "no_flip_lost": 0, "recovered_30s": 0, "recovered_60s": 0},
            "15min": {"flipped": 0, "no_flip": 0, "flipped_won": 0, "flipped_lost": 0,
                      "no_flip_won": 0, "no_flip_lost": 0, "recovered_30s": 0, "recovered_60s": 0},
        }

        for pos in positions:
            traj = pos["p_ours"]
            we_won = pos["we_won"]
            dk = f"{pos['dur_min']}min"

            flip_idx = find_first_confirmed_flip(traj, flip_th, confirm_s)

            if flip_idx is not None:
                stats["flipped"] += 1
                stats[dk]["flipped"] += 1
                stats["flip_seconds_list"].append(flip_idx)
                stats["flip_gbm_list"].append(float(traj[flip_idx]))

                if we_won:
                    stats["flipped_won"] += 1
                    stats[dk]["flipped_won"] += 1
                else:
                    stats["flipped_lost"] += 1
                    stats[dk]["flipped_lost"] += 1

                # Recovery check
                remaining = traj[flip_idx:]
                for rw in RECOVERY_WINDOWS:
                    window_end = min(rw + 1, len(remaining))
                    if np.any(remaining[:window_end] >= flip_th):
                        stats[f"recovered_{rw}s"] += 1
                        if rw in (30, 60):
                            stats[dk][f"recovered_{rw}s"] += 1
            else:
                stats["no_flip"] += 1
                stats[dk]["no_flip"] += 1
                if we_won:
                    stats["no_flip_won"] += 1
                    stats[dk]["no_flip_won"] += 1
                else:
                    stats["no_flip_lost"] += 1
                    stats[dk]["no_flip_lost"] += 1

        # Derived metrics
        fc = stats["flipped"]
        nfc = stats["no_flip"]
        stats["false_stop_rate"] = stats["flipped_won"] / fc if fc > 0 else 0
        stats["correct_stop_rate"] = stats["flipped_lost"] / fc if fc > 0 else 0
        stats["recovery_30s_rate"] = stats["recovered_30s"] / fc if fc > 0 else 0
        stats["recovery_60s_rate"] = stats["recovered_60s"] / fc if fc > 0 else 0
        stats["no_flip_win_rate"] = stats["no_flip_won"] / nfc if nfc > 0 else 0

        if stats["flip_seconds_list"]:
            fa = np.array(stats["flip_seconds_list"])
            stats["median_flip_seconds"] = float(np.median(fa))
            stats["mean_flip_seconds"] = float(np.mean(fa))
            stats["p10_flip_seconds"] = float(np.percentile(fa, 10))
            stats["p90_flip_seconds"] = float(np.percentile(fa, 90))
            stats["mean_flip_gbm"] = float(np.mean(stats["flip_gbm_list"]))

        # Remove large arrays from stored results
        del stats["flip_seconds_list"]
        del stats["flip_gbm_list"]

        results[key] = stats

log("Flip analysis complete.")

# ── Step 6: PnL impact analysis ──────────────────────────────────────────────
log("\n=== Step 6: PnL impact ===")

pnl_results = {}
for flip_th in FLIP_THRESHOLDS:
    for confirm_s in [0, 5, 10]:
        key = f"th={flip_th:.2f}_confirm={confirm_s}s"
        pnl = {
            "flip_threshold": flip_th,
            "confirm_delay_s": confirm_s,
            "pnl_with_stop": 0.0,
            "pnl_no_stop": 0.0,
            "n_stopped": 0,
            "n_held": 0,
            "pnl_stopped_only": 0.0,
            "pnl_stopped_if_held": 0.0,
        }

        for pos in positions:
            traj = pos["p_ours"]
            we_won = pos["we_won"]
            entry_price = float(traj[0]) if len(traj) > 0 else 0.5

            # Resolution PnL
            if we_won:
                res_pnl = (1.0 - entry_price) * BET_SIZE
            else:
                res_pnl = -entry_price * BET_SIZE

            pnl["pnl_no_stop"] += res_pnl

            flip_idx = find_first_confirmed_flip(traj, flip_th, confirm_s)
            if flip_idx is not None:
                exit_price = float(traj[flip_idx])
                stop_pnl = (exit_price - entry_price) * BET_SIZE
                pnl["pnl_with_stop"] += stop_pnl
                pnl["n_stopped"] += 1
                pnl["pnl_stopped_only"] += stop_pnl
                pnl["pnl_stopped_if_held"] += res_pnl
            else:
                pnl["pnl_with_stop"] += res_pnl
                pnl["n_held"] += 1

        pnl["pnl_delta"] = pnl["pnl_with_stop"] - pnl["pnl_no_stop"]
        pnl["stop_vs_hold"] = pnl["pnl_stopped_only"] - pnl["pnl_stopped_if_held"]
        pnl_results[key] = pnl

log("PnL analysis complete.")

# ── Step 7: Time-adaptive flip ────────────────────────────────────────────────
log("\n=== Step 7: Time-adaptive analysis ===")

adaptive_results = {}
for base_th in [0.30, 0.35]:
    for widen in ADAPTIVE_WIDEN_PER_60S:
        key = f"base={base_th}_widen={widen}/60s"
        stats = {"base": base_th, "widen": widen, "total": len(positions),
                 "flipped": 0, "flipped_won": 0, "flipped_lost": 0,
                 "no_flip_won": 0, "no_flip_lost": 0}

        for pos in positions:
            traj = pos["p_ours"]
            n = len(traj)
            secs = np.arange(n, dtype=np.float64)
            adap_th = np.maximum(base_th - widen * (secs / 60.0), 0.10)
            below = traj < adap_th
            if np.any(below):
                stats["flipped"] += 1
                if pos["we_won"]:
                    stats["flipped_won"] += 1
                else:
                    stats["flipped_lost"] += 1
            else:
                if pos["we_won"]:
                    stats["no_flip_won"] += 1
                else:
                    stats["no_flip_lost"] += 1

        fc = stats["flipped"]
        stats["flip_rate"] = fc / stats["total"] if stats["total"] > 0 else 0
        stats["false_stop_rate"] = stats["flipped_won"] / fc if fc > 0 else 0
        adaptive_results[key] = stats

log("Adaptive analysis complete.")

# ── Step 8: Print summary ────────────────────────────────────────────────────
log("\n" + "=" * 70)
log("SUMMARY: GBM FLIP STOP-LOSS ANALYSIS")
log("=" * 70)

total_wins = sum(1 for p in positions if p["we_won"])
overall_wr = total_wins / len(positions)
n5 = sum(1 for p in positions if p["dur_min"] == 5)
n5w = sum(1 for p in positions if p["dur_min"] == 5 and p["we_won"])
n15 = sum(1 for p in positions if p["dur_min"] == 15)
n15w = sum(1 for p in positions if p["dur_min"] == 15 and p["we_won"])
log(f"\nResolution win rate (our side): {overall_wr:.1%} ({total_wins}/{len(positions)})")
log(f"  5-min:  {n5w}/{n5} = {n5w/n5*100:.1f}%")
log(f"  15-min: {n15w}/{n15} = {n15w/n15*100:.1f}%")

log("\n--- Flip Rate by Threshold x Confirmation Delay ---")
header = f"{'Th':>6} {'Cfm':>5} {'Flipped%':>9} {'FalseStop%':>11} {'CorrStop%':>10} " \
         f"{'Rcv30s%':>8} {'Rcv60s%':>8} {'NoFlipWR%':>10} {'MedianFs':>9}"
log(header)
for flip_th in FLIP_THRESHOLDS:
    for confirm_s in CONFIRM_DELAYS:
        key = f"th={flip_th:.2f}_confirm={confirm_s}s"
        s = results[key]
        fp = s["flipped"] / s["total"] * 100
        mfs = s.get("median_flip_seconds", 0)
        log(f"{flip_th:>6.2f} {confirm_s:>4}s {fp:>8.1f}% {s['false_stop_rate']*100:>10.1f}% "
            f"{s['correct_stop_rate']*100:>9.1f}% {s['recovery_30s_rate']*100:>7.1f}% "
            f"{s['recovery_60s_rate']*100:>7.1f}% {s['no_flip_win_rate']*100:>9.1f}% "
            f"{mfs:>8.0f}s")
    log("")  # separator between thresholds

log("\n--- PnL Impact ---")
log(f"{'Th':>6} {'Cfm':>5} {'PnL_Stop':>11} {'PnL_Hold':>11} {'Delta':>10} "
    f"{'N_Stop':>7} {'StopPnL':>10} {'IfHeld':>10}")
for flip_th in FLIP_THRESHOLDS:
    for confirm_s in [0, 5, 10]:
        key = f"th={flip_th:.2f}_confirm={confirm_s}s"
        p = pnl_results[key]
        log(f"{flip_th:>6.2f} {confirm_s:>4}s ${p['pnl_with_stop']:>9,.0f} ${p['pnl_no_stop']:>9,.0f} "
            f"${p['pnl_delta']:>8,.0f} {p['n_stopped']:>7} "
            f"${p['pnl_stopped_only']:>8,.0f} ${p['pnl_stopped_if_held']:>8,.0f}")
    log("")

log("\n--- By Duration (th=0.35, instant) ---")
for dur in [5, 15]:
    s = results["th=0.35_confirm=0s"][f"{dur}min"]
    total = s["flipped"] + s["no_flip"]
    fp = s["flipped"] / total * 100 if total > 0 else 0
    fsr = s["flipped_won"] / s["flipped"] * 100 if s["flipped"] > 0 else 0
    r30 = s["recovered_30s"] / s["flipped"] * 100 if s["flipped"] > 0 else 0
    r60 = s["recovered_60s"] / s["flipped"] * 100 if s["flipped"] > 0 else 0
    log(f"  {dur}-min: n={total}, flipped={s['flipped']} ({fp:.1f}%), "
        f"false_stop={fsr:.1f}%, recov30s={r30:.1f}%, recov60s={r60:.1f}%")

log("\n--- Time-Adaptive ---")
for key, s in adaptive_results.items():
    fc = s["flipped"]
    fsr = s["false_stop_rate"]
    log(f"  {key}: flipped={fc}/{s['total']} ({s['flip_rate']*100:.1f}%), "
        f"false_stop={fsr*100:.1f}%")

# ── Step 9: Flip timing distribution (th=0.35) ──────────────────────────────
log("\n=== Flip timing (th=0.35, instant) ===")
s035 = results["th=0.35_confirm=0s"]
if s035.get("median_flip_seconds"):
    log(f"  Median: {s035['median_flip_seconds']:.0f}s")
    log(f"  Mean:   {s035['mean_flip_seconds']:.0f}s")
    log(f"  P10:    {s035['p10_flip_seconds']:.0f}s")
    log(f"  P90:    {s035['p90_flip_seconds']:.0f}s")
    log(f"  Mean GBM at flip: {s035['mean_flip_gbm']:.3f}")

# ── Step 10: Key comparison: current config vs alternatives ──────────────────
log("\n=== KEY COMPARISON: Current (th=0.35) vs Alternatives ===")

def summarize(key_str: str, label: str) -> None:
    s = results.get(key_str, {})
    p = pnl_results.get(key_str, {})
    if not s or not p:
        log(f"  {label}: NO DATA")
        return
    fp = s["flipped"] / s["total"] * 100
    fsr = s["false_stop_rate"] * 100
    r60 = s["recovery_60s_rate"] * 100
    delta = p["pnl_delta"]
    log(f"  {label:40s}: flipped={fp:5.1f}%, false_stop={fsr:5.1f}%, "
        f"recov60s={r60:5.1f}%, PnL_delta=${delta:>8,.0f}")

log("\nInstant flip (no confirmation):")
summarize("th=0.35_confirm=0s", "Current: th=0.35")
summarize("th=0.30_confirm=0s", "Alt: th=0.30")
summarize("th=0.25_confirm=0s", "Alt: th=0.25")
summarize("th=0.20_confirm=0s", "Alt: th=0.20")

log("\nWith 5s confirmation delay:")
summarize("th=0.35_confirm=5s", "Current+5s: th=0.35")
summarize("th=0.30_confirm=5s", "Alt+5s: th=0.30")
summarize("th=0.25_confirm=5s", "Alt+5s: th=0.25")

log("\nWith 10s confirmation delay:")
summarize("th=0.35_confirm=10s", "Current+10s: th=0.35")
summarize("th=0.30_confirm=10s", "Alt+10s: th=0.30")

# ── Save results ──────────────────────────────────────────────────────────────
log("\n=== Saving results ===")

def ser(obj):
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, np.ndarray): return obj.tolist()
    return obj

output = {
    "metadata": {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_positions": len(positions),
        "5min_positions": n5,
        "15min_positions": n15,
        "overall_win_rate": overall_wr,
        "config": {
            "entry_threshold": ENTRY_THRESHOLD,
            "min_gbm_deviation": MIN_GBM_DEVIATION,
            "skip_seconds": SKIP_SECONDS,
            "no_entry_within_s": NO_ENTRY_WITHIN_S,
            "bet_size": BET_SIZE,
        },
        "note": "UPPER BOUND — vectorized, 1-second resolution",
    },
    "flip_analysis": {k: {kk: ser(vv) for kk, vv in v.items()} for k, v in results.items()},
    "pnl_impact": {k: {kk: ser(vv) for kk, vv in v.items()} for k, v in pnl_results.items()},
    "adaptive": {k: {kk: ser(vv) for kk, vv in v.items()} for k, v in adaptive_results.items()},
}

out_path = RESULTS_DIR / "gbm_flip_results.json"
with open(out_path, "w") as f:
    json.dump(output, f, indent=2, default=ser)
log(f"Results saved to {out_path}")

log("\nDone.")
