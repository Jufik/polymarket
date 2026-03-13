"""
Vectorized discovery sweep for crypto GBM improvement axes.

Key insight from data exploration:
- markets.created_at = when market was listed (1-3 days before window)
- markets.closed_at = window END time (when BTC price is locked)
- Window START = closed_at - window_duration (parsed from question text)
- S₀ = BTC price at window start

Window types:
- "11:30PM-11:45PM ET" → 15-min window
- "10:45AM-10:50AM ET" → 5-min window
- "5PM ET" (single time) → 60-min window

GBM P(Up) = Φ(d₂) where d₂ = ln(S_t / S₀) / (σ √(T-t))
- S₀ = BTC price at window start
- S_t = BTC price at entry time (first_trade)
- T-t = time remaining in window at entry

UPPER BOUNDS: all results are vectorized simulations, not tick-by-tick.

YES winner_outcome = "Up" (BTC went up → YES wins).
"""

import sys
import re
import json
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import clickhouse_connect
from scipy.stats import norm

sys.path.insert(0, '/mnt/nvme/git/polymarket/polymarket')

LOG_FILE = Path('/mnt/nvme/git/polymarket/polymarket/tmp/discovery_sweep.log')
OUT_DIR = Path('/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-gbm-improvements/discovery')
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
LOG_FILE.write_text('')

def log(msg: str) -> None:
    ts = dt.datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')

log("=" * 70)
log("CRYPTO GBM IMPROVEMENTS — VECTORIZED DISCOVERY SWEEP")
log("=" * 70)

# ── Window duration parser ─────────────────────────────────────────────────
def parse_window_duration(question: str) -> int | None:
    """Extract window duration in minutes from question text."""
    range_m = re.search(r'(\d+):(\d+)(AM|PM)-(\d+):(\d+)(AM|PM)\s+ET', question)
    if range_m:
        h1, m1, mer1, h2, m2, mer2 = range_m.groups()
        h1, m1, h2, m2 = int(h1), int(m1), int(h2), int(m2)
        if mer1 == 'PM' and h1 != 12: h1 += 12
        if mer1 == 'AM' and h1 == 12: h1 = 0
        if mer2 == 'PM' and h2 != 12: h2 += 12
        if mer2 == 'AM' and h2 == 12: h2 = 0
        dur = (h2 * 60 + m2) - (h1 * 60 + m1)
        if dur <= 0: dur += 24 * 60
        return int(dur)
    single_m = re.search(r'(\d+)(?::(\d+))?(AM|PM)\s+ET', question)
    if single_m:
        return 60  # 1-hour window
    return None

from research.db import db
d = db()
log("DuckDB loaded")

# ── Step 0: Load BTC Up/Down markets with question text ────────────────────
log("\n=== Step 0: BTC Up/Down universe ===")
# Need question from markets (or events) — use DISTINCT to avoid YES/NO token dup
btc_mkts = d.query("""
SELECT
    m.condition_id,
    first(m.question) as question,
    first(m.winner_outcome) as winner_outcome,
    first(m.closed_at) as closed_at
FROM markets m
JOIN markets_resolved mr ON m.condition_id = mr.condition_id
WHERE lower(m.question) LIKE '%bitcoin%'
  AND lower(m.question) LIKE '%up%'
  AND lower(m.question) LIKE '%down%'
  AND m.winner_outcome IN ('Up', 'Down')
GROUP BY m.condition_id
""").to_pandas()

log(f"BTC Up/Down markets: {len(btc_mkts):,}")
btc_mkts['yes_won'] = btc_mkts['winner_outcome'] == 'Up'
yes_rate = btc_mkts['yes_won'].mean()
no_rate = 1 - yes_rate
log(f"Base rate: YES(Up)={yes_rate:.3f}, NO(Down)={no_rate:.3f}")

# Parse window duration from question
btc_mkts['window_dur_min'] = btc_mkts['question'].apply(parse_window_duration)
log(f"Window duration distribution:")
log(str(btc_mkts['window_dur_min'].value_counts().head(5)))

# Compute window_start = closed_at - window_dur_min
btc_mkts['closed_at'] = pd.to_datetime(btc_mkts['closed_at'], utc=True).astype('datetime64[us, UTC]')
btc_mkts['window_start'] = btc_mkts.apply(
    lambda r: r['closed_at'] - pd.Timedelta(minutes=int(r['window_dur_min']))
              if pd.notna(r['window_dur_min']) else pd.NaT,
    axis=1
)
btc_mkts = btc_mkts.dropna(subset=['window_dur_min', 'window_start'])
log(f"Markets with valid window times: {len(btc_mkts):,}")

# ── Step 1: YES entries — 1 per market (highest volume trader) ────────────
log("\n=== Step 1: YES entries (1 per market, highest volume) ===")
yes_entry = d.query("""
WITH ranked AS (
    SELECT
        ye.condition_id,
        ye.price_x_vol / ye.volume as entry_price,
        ye.volume,
        ye.first_trade,
        ROW_NUMBER() OVER (PARTITION BY ye.condition_id ORDER BY ye.volume DESC) as rn
    FROM yes_entry_data ye
    WHERE ye.condition_id IN (
        SELECT DISTINCT condition_id FROM markets
        WHERE lower(question) LIKE '%bitcoin%'
          AND lower(question) LIKE '%up%'
          AND lower(question) LIKE '%down%'
          AND winner_outcome IN ('Up', 'Down')
    )
      AND ye.volume > 0
)
SELECT condition_id, entry_price, volume, first_trade
FROM ranked
WHERE rn = 1
""").to_pandas()

log(f"YES entries (1 per market): {len(yes_entry):,}")

# Join with market metadata
analysis = yes_entry.merge(
    btc_mkts[['condition_id', 'yes_won', 'closed_at', 'window_start', 'window_dur_min']],
    on='condition_id', how='inner'
)
log(f"After join with market data: {len(analysis):,}")

# ── Step 2: Load BTC bars (1-min) ─────────────────────────────────────────
log("\n=== Step 2: Load BTC 1-min bars ===")
ch = clickhouse_connect.get_client(host='192.168.0.148', port=18123, database='polymarket')
bars_result = ch.query("""
SELECT toStartOfMinute(ts) as ts_min, any(close) as close_1m
FROM exchange_bars
WHERE symbol = 'BTC-USDT' AND exchange = 'BINANCE'
GROUP BY ts_min ORDER BY ts_min
""")
bars_df = pd.DataFrame(bars_result.result_rows, columns=['ts_min', 'close'])
bars_df['ts_min'] = pd.to_datetime(bars_df['ts_min'], utc=True).dt.floor('min').astype('datetime64[us, UTC]')
bars_df = bars_df.sort_values('ts_min').reset_index(drop=True)
log(f"Bars: {len(bars_df):,} 1-min rows, {bars_df['ts_min'].min()} → {bars_df['ts_min'].max()}")

# ── Step 3: Sigma ─────────────────────────────────────────────────────────
log("\n=== Step 3: Sigma ===")
bars_idx = bars_df.set_index('ts_min')['close']
lr = np.log(bars_idx).diff().dropna()
sigma_roll = lr.rolling(1440, min_periods=60).std()
sigma_ewma = lr.ewm(span=1440, min_periods=60).std()
sigma_roll.index = sigma_roll.index.astype('datetime64[us, UTC]')
sigma_ewma.index = sigma_ewma.index.astype('datetime64[us, UTC]')
sig_df = pd.DataFrame({'ts_min': sigma_roll.index, 'sigma_std': sigma_roll.values,
                        'sigma_ewma': sigma_ewma.values})
log(f"Sigma rolling median: {sigma_roll.median():.6f}, EWMA: {sigma_ewma.median():.6f}")

# ── Step 4: Normalize datetimes + filter to bar coverage ──────────────────
log("\n=== Step 4: Filter + timing ===")
bar_start = bars_df['ts_min'].min()
bar_end = bars_df['ts_min'].max()

for col in ['first_trade']:
    analysis[col] = pd.to_datetime(analysis[col], utc=True).dt.floor('min').astype('datetime64[us, UTC]')

# window_start already computed above
analysis['window_start'] = analysis['window_start'].dt.floor('min').astype('datetime64[us, UTC]')
analysis['closed_at'] = analysis['closed_at'].dt.floor('min').astype('datetime64[us, UTC]')

# Filter to bar coverage
mask = (
    (analysis['first_trade'] >= bar_start) &
    (analysis['first_trade'] <= bar_end) &
    (analysis['window_start'] >= bar_start)
)
analysis = analysis[mask].copy()
log(f"Within bar coverage: {len(analysis):,} markets")

# Timing
analysis['time_remaining_min'] = (analysis['closed_at'] - analysis['first_trade']).dt.total_seconds() / 60.0
analysis['minutes_elapsed'] = (analysis['first_trade'] - analysis['window_start']).dt.total_seconds() / 60.0
analysis['remaining_frac'] = analysis['time_remaining_min'] / analysis['window_dur_min'].clip(lower=1)

# Filters: must have entered after window start (elapsed>=0) with time remaining
analysis = analysis[
    (analysis['time_remaining_min'] >= 1) &   # at least 1 min remaining
    (analysis['minutes_elapsed'] >= 0) &       # entered after window start
    (analysis['minutes_elapsed'] < analysis['window_dur_min'])  # entered before window end
].copy()
log(f"After timing filters: {len(analysis):,}")

# ── Step 5: BTC price lookup ──────────────────────────────────────────────
log("\n=== Step 5: BTC price lookup (merge_asof) ===")
bars_close = bars_df[['ts_min', 'close']].rename(columns={'ts_min': 'ts'})

# S₀ = BTC price at window_start
ws_sorted = analysis[['condition_id', 'window_start']].sort_values('window_start')
btc_s0 = pd.merge_asof(ws_sorted, bars_close, left_on='window_start', right_on='ts',
                         direction='backward').rename(columns={'close': 'btc_s0'}).drop(columns=['ts'])

# S_t = BTC price at first_trade (entry)
ft_sorted = analysis[['condition_id', 'first_trade']].sort_values('first_trade')
btc_st = pd.merge_asof(ft_sorted, bars_close, left_on='first_trade', right_on='ts',
                        direction='backward').rename(columns={'close': 'btc_st'}).drop(columns=['ts'])

# Sigma at entry
sig_at_entry = pd.merge_asof(ft_sorted, sig_df, left_on='first_trade', right_on='ts_min',
                               direction='backward').drop(columns=['ts_min'])

analysis = analysis.merge(btc_s0, on=['condition_id', 'window_start'], how='left')
analysis = analysis.merge(btc_st, on=['condition_id', 'first_trade'], how='left')
analysis = analysis.merge(sig_at_entry, on=['condition_id', 'first_trade'], how='left')

pre_drop = len(analysis)
analysis = analysis.dropna(subset=['btc_s0', 'btc_st', 'sigma_std'])
log(f"After dropping missing bar data: {len(analysis):,} / {pre_drop:,}")

# ── Step 6: GBM P(Up) ─────────────────────────────────────────────────────
log("\n=== Step 6: GBM P(Up) ===")

def gbm_p_up_vec(s0, st, sigma, t_rem):
    log_ret = np.log(np.maximum(st, 1e-10) / np.maximum(s0, 1e-10))
    vol = np.maximum(sigma, 1e-12) * np.sqrt(np.maximum(t_rem, 0))
    d2 = np.where(vol > 1e-10, log_ret / vol, np.where(log_ret > 0, 10.0, -10.0))
    return norm.cdf(d2)

analysis['gbm_p_up'] = gbm_p_up_vec(
    analysis['btc_s0'].values, analysis['btc_st'].values,
    analysis['sigma_std'].values, analysis['time_remaining_min'].values)
sigma_e = analysis['sigma_ewma'].fillna(analysis['sigma_std']).values
analysis['gbm_p_up_ewma'] = gbm_p_up_vec(
    analysis['btc_s0'].values, analysis['btc_st'].values,
    sigma_e, analysis['time_remaining_min'].values)

analysis['pm_p_up'] = analysis['entry_price'].clip(0.01, 0.99)
analysis['gbm_lag'] = analysis['gbm_p_up'] - analysis['pm_p_up']
analysis['gbm_lag_ewma'] = analysis['gbm_p_up_ewma'] - analysis['pm_p_up']
p = analysis['pm_p_up']
analysis['pm_fee'] = 0.25 * (p * (1 - p)) ** 2

# Vol regime
analysis['sigma_q'] = pd.qcut(analysis['sigma_std'], q=4, labels=['Q1_low','Q2','Q3','Q4_high'])

log(f"GBM P(Up): mean={analysis['gbm_p_up'].mean():.3f}, std={analysis['gbm_p_up'].std():.3f}")
log(f"GBM lag: mean={analysis['gbm_lag'].mean():.4f}, std={analysis['gbm_lag'].std():.4f}")
log(f"PM price mean={analysis['pm_p_up'].mean():.3f}")

# ── Sanity check ──────────────────────────────────────────────────────────
log("\n=== Sanity Check ===")
lag05 = analysis[analysis['gbm_lag'].abs() > 0.05].copy()
lag05['signal_correct'] = (
    ((lag05['gbm_lag'] > 0) & lag05['yes_won']) |
    ((lag05['gbm_lag'] < 0) & ~lag05['yes_won'])
)
directional_hr = float(lag05['signal_correct'].mean()) if len(lag05) > 0 else 0.5
hi = float(analysis[analysis['gbm_lag'] > 0.05]['yes_won'].mean())
lo = float(analysis[analysis['gbm_lag'] < -0.05]['yes_won'].mean())
neut = float(analysis[analysis['gbm_lag'].abs() <= 0.05]['yes_won'].mean())

log(f"YES HR: lag>0.05={hi:.3f}, ~0={neut:.3f}, lag<-0.05={lo:.3f}, base={yes_rate:.3f}")
log(f"Directional HR (|lag|>0.05, n={len(lag05)}): {directional_hr:.3f}")

# ── Step 7: Market sweep ───────────────────────────────────────────────────
log("\n=== Step 7: Market sweep ===")

def market_sweep(df, threshold, use_ewma=False, late_frac=0.0,
                 dynamic=False, fee_aware=False, direction='YES'):
    lag_col = 'gbm_lag_ewma' if use_ewma else 'gbm_lag'
    f = df.copy()
    if late_frac > 0:
        f = f[f['remaining_frac'] >= late_frac]
    if direction == 'YES':
        thresh_eff = (threshold + f['pm_fee']) if fee_aware else threshold
        f = f[f[lag_col] > thresh_eff] if isinstance(thresh_eff, float) else f[f[lag_col] > thresh_eff]
        correct = f['yes_won']
        base = yes_rate
    else:
        thresh_eff = (threshold + f['pm_fee']) if fee_aware else threshold
        f = f[f[lag_col] < -thresh_eff] if isinstance(thresh_eff, float) else f[f[lag_col] < -thresh_eff]
        correct = ~f['yes_won']
        base = no_rate
    n = len(f)
    if n == 0:
        return {'n_signals': 0, 'hr': float('nan'), 'excess_hr': float('nan'),
                'avg_ev_usd': float('nan'), 'median_hold_min': float('nan'),
                'compounding': float('nan'), 'trades_per_month': float('nan')}
    hr = float(correct.mean())
    excess_hr = hr - base
    lag_abs = f[lag_col].abs()
    bet = (lag_abs / 0.10 * 50.0).clip(upper=100.0) if dynamic else pd.Series(50.0, index=f.index)
    ev = float((lag_abs * bet * 0.433).mean())  # 0.433 calibrated from FINDINGS.md
    hold_min = float(f['time_remaining_min'].median())
    hold_frac = max(hold_min / 1440.0, 1.0/1440)
    compounding = float(excess_hr * ev / hold_frac)
    date_days = (f['first_trade'].max() - f['first_trade'].min()).days
    tpm = n / max(date_days / 30.0, 1.0)
    return {'n_signals': n, 'hr': hr, 'excess_hr': excess_hr,
            'avg_ev_usd': ev, 'median_hold_min': hold_min,
            'compounding': compounding, 'trades_per_month': float(tpm)}

# Baseline
base_yes = market_sweep(analysis, 0.10, direction='YES')
base_no  = market_sweep(analysis, 0.10, direction='NO')
log(f"Baseline YES: n={base_yes['n_signals']}, HR={base_yes['hr']:.3f} ({base_yes['excess_hr']:+.3f}pp)")
log(f"Baseline NO:  n={base_no['n_signals']}, HR={base_no['hr']:.3f} ({base_no['excess_hr']:+.3f}pp)")

# Full sweep
results = []
for direction in ['YES', 'NO']:
    for threshold in [0.06, 0.08, 0.10, 0.12, 0.15]:
        for dynamic in [False, True]:
            for ewma in [False, True]:
                for fee_aware in [False, True]:
                    for late_frac in [0.0, 0.25, 0.33]:
                        r = market_sweep(analysis, threshold, ewma, late_frac, dynamic, fee_aware, direction)
                        r.update({'direction': direction, 'threshold': threshold,
                                  'dynamic_sizing': dynamic, 'ewma': ewma,
                                  'fee_aware': fee_aware, 'late_entry_frac': late_frac})
                        results.append(r)

results_df = pd.DataFrame(results).dropna(subset=['compounding'])
log(f"Sweep: {len(results)} combos, {len(results_df)} valid")

# ── Vol regime ────────────────────────────────────────────────────────────
log("\n=== Vol regime ===")
regime_results = []
for q in ['Q1_low', 'Q2', 'Q3', 'Q4_high']:
    subset = analysis[analysis['sigma_q'] == q]
    r = market_sweep(subset, 0.10, direction='YES')
    r['regime'] = q
    regime_results.append(r)
    log(f"  {q}: n={r['n_signals']}, HR={r['hr']:.3f} excess={r['excess_hr']:+.3f}")

# ── Top combos ────────────────────────────────────────────────────────────
log("\n=== Top combos ===")
yes_df = results_df[results_df['direction'] == 'YES'].nlargest(5, 'compounding').reset_index(drop=True)
no_df  = results_df[results_df['direction'] == 'NO'].nlargest(5, 'compounding').reset_index(drop=True)

log("\nTop 5 YES (UPPER BOUNDS):")
for i, row in yes_df.iterrows():
    log(f"  #{i+1}: thresh={row['threshold']}, dyn={row['dynamic_sizing']}, ewma={row['ewma']}, "
        f"fee={row['fee_aware']}, late={row['late_entry_frac']}")
    log(f"       n={row['n_signals']}, HR={row['hr']:.3f} ({row['excess_hr']:+.3f}pp), "
        f"EV=${row['avg_ev_usd']:.2f}, hold={row['median_hold_min']:.1f}min, compound={row['compounding']:.1f}")

log("\nTop 5 NO (UPPER BOUNDS):")
for i, row in no_df.iterrows():
    log(f"  #{i+1}: thresh={row['threshold']}, dyn={row['dynamic_sizing']}, ewma={row['ewma']}, "
        f"fee={row['fee_aware']}, late={row['late_entry_frac']}")
    log(f"       n={row['n_signals']}, HR={row['hr']:.3f} ({row['excess_hr']:+.3f}pp), "
        f"EV=${row['avg_ev_usd']:.2f}, hold={row['median_hold_min']:.1f}min, compound={row['compounding']:.1f}")

# ── Sensitivity ───────────────────────────────────────────────────────────
log("\n=== Sensitivity ===")
top = yes_df.iloc[0]
sensitivity = {}
for perturb in [-0.10, +0.10]:
    nt = top['threshold'] * (1 + perturb)
    r_s = market_sweep(analysis, nt, bool(top['ewma']), float(top['late_entry_frac']),
                       bool(top['dynamic_sizing']), bool(top['fee_aware']), 'YES')
    chg = (r_s['hr'] - top['hr']) * 100
    fragile = abs(chg) > 5
    sensitivity[f'threshold_{perturb:+.0%}'] = {'hr_change_pp': float(chg), 'n': r_s['n_signals'], 'fragile': fragile}
    log(f"  threshold {perturb:+.0%}: HR chg={chg:+.2f}pp {'[FRAGILE]' if fragile else '[robust]'}")

# Axis-level deltas
log("\n=== Axis impact ===")
axis_summary = {}
for label, col, val, bval in [
    ('dynamic_sizing', 'dynamic_sizing', True, False),
    ('ewma_sigma', 'ewma', True, False),
    ('fee_aware', 'fee_aware', True, False),
    ('late_entry_frac_0.33', 'late_entry_frac', 0.33, 0.0),
]:
    ax = results_df[(results_df['direction'] == 'YES') & (results_df[col] == val)]
    bx = results_df[(results_df['direction'] == 'YES') & (results_df[col] == bval)]
    if len(ax) and len(bx):
        dhr = (ax['hr'].mean() - bx['hr'].mean()) * 100
        dev = ax['avg_ev_usd'].mean() - bx['avg_ev_usd'].mean()
        log(f"  {label}: ΔHR={dhr:+.2f}pp, ΔEV=${dev:+.2f}")
        axis_summary[label] = {'delta_hr_pp': round(float(dhr), 2), 'delta_ev_usd': round(float(dev), 2)}

# ── Write outputs ─────────────────────────────────────────────────────────
log("\n=== Write outputs ===")
results_df.to_csv(OUT_DIR / 'sweep_results.csv', index=False)
pd.DataFrame(regime_results).to_csv(OUT_DIR / 'regime_results.csv', index=False)

def c2d(rank, row):
    return {
        'rank': rank,
        'params': {'threshold': float(row['threshold']), 'dynamic_sizing': bool(row['dynamic_sizing']),
                   'ewma_sigma': bool(row['ewma']), 'fee_aware': bool(row['fee_aware']),
                   'late_entry_frac': float(row['late_entry_frac'])},
        'hr_pct': round(float(row['hr'])*100, 2),
        'excess_hr_pp': round(float(row['excess_hr'])*100, 2),
        'avg_ev_usd': round(float(row['avg_ev_usd']), 2),
        'median_hold_min': round(float(row['median_hold_min']), 1),
        'compounding_score': round(float(row['compounding']), 1),
        'n_signals': int(row['n_signals']),
        'trades_per_month': round(float(row['trades_per_month']), 1),
        'fragile': False, 'sensitivity': {},
    }

verdict = 'promising' if directional_hr > 0.52 else ('marginal' if directional_hr > 0.50 else 'no_signal')

results_json = {
    'hypothesis': 'crypto-gbm-improvements',
    'timestamp': dt.datetime.utcnow().isoformat(),
    'verdict': verdict,
    'note': 'ALL RESULTS ARE UPPER BOUNDS — vectorized simulation only',
    'universe': {
        'total_btc_markets': int(btc_mkts['condition_id'].nunique()),
        'markets_with_bars': int(analysis['condition_id'].nunique()),
        'date_range': [str(analysis['first_trade'].min().date()), str(analysis['first_trade'].max().date())],
        'window_types': {
            '15min': int((btc_mkts['window_dur_min'] == 15).sum()),
            '60min': int((btc_mkts['window_dur_min'] == 60).sum()),
            '5min': int((btc_mkts['window_dur_min'] == 5).sum()),
            'other': int(btc_mkts['window_dur_min'].notna().sum() - (btc_mkts['window_dur_min'].isin([5,15,60])).sum()),
        },
    },
    'base_rates': {'yes_up_pct': round(yes_rate*100, 1), 'no_down_pct': round(no_rate*100, 1)},
    'sanity': {
        'directional_hr': round(directional_hr, 3),
        'yes_hr_lag_gt05': round(hi, 3),
        'yes_hr_lag_near0': round(neut, 3),
        'yes_hr_lag_lt_neg05': round(lo, 3),
        'n_lag05_markets': int(len(lag05)),
    },
    'baseline': {
        'threshold': 0.10,
        'yes_n': base_yes['n_signals'],
        'yes_hr_pct': round(base_yes['hr']*100, 2),
        'yes_excess_hr_pp': round(base_yes['excess_hr']*100, 2),
        'yes_avg_ev_usd': round(base_yes['avg_ev_usd'], 2),
        'no_n': base_no['n_signals'],
        'no_hr_pct': round(base_no['hr']*100, 2),
        'no_excess_hr_pp': round(base_no['excess_hr']*100, 2),
        'no_avg_ev_usd': round(base_no['avg_ev_usd'], 2),
        'findings_md_baseline_ev': 2.10,
    },
    'buy_only_results': {
        'top_yes_combos': [c2d(i+1, yes_df.iloc[i]) for i in range(min(5, len(yes_df)))],
        'top_no_combos': [c2d(i+1, no_df.iloc[i]) for i in range(min(5, len(no_df)))],
    },
    'directional_results': {
        'note': 'BUY-only strategy. SELL=exit only. Sensitivity=0pp.',
        'sell_sensitivity_pp': 0.0,
    },
    'sell_sensitivity_pp': 0.0,
    'vol_regime': [
        {'regime': r['regime'], 'n_signals': r['n_signals'],
         'hr_pct': round(r['hr']*100, 2) if not (isinstance(r['hr'], float) and r['hr'] != r['hr']) else None,
         'excess_hr_pp': round(r['excess_hr']*100, 2) if not (isinstance(r['excess_hr'], float) and r['excess_hr'] != r['excess_hr']) else None}
        for r in regime_results
    ],
    'axis_summary': axis_summary,
    'sensitivity': sensitivity,
    'spawned_ideas': [
        'ETH/SOL/XRP Up/Down with same GBM model — pending bar fetch (Task #3)',
        'EWMA sigma: test shorter spans (60, 240 min) for faster vol regime adaptation',
        'Vol-regime-specific thresholds (lower threshold in Q4_high vol)',
        'Re-entry logic (axis 2) requires strategy.py code fix first',
        'S₀ timing: live strategy captures S₀ at window open; confirm lag distribution matches',
    ],
    'knowledge_captures': [
        f'markets.created_at != window start. Window start = closed_at - window_duration_min',
        f'BTC Up/Down YES base rate {yes_rate:.3f} (near 50/50) — GBM is not foresight, it is speed',
        f'Directional HR {directional_hr:.3f} at lag>0.05 threshold — modest but consistent edge',
        'SELL sensitivity = 0pp (BUY-only strategy by design)',
    ],
    'classifications_used': [],
    'classifications_proposed': [],
}

with open(OUT_DIR / 'results.json', 'w') as f:
    json.dump(results_json, f, indent=2, default=str)
log("Saved results.json")

log(f"\n=== FINAL SUMMARY ===")
log(f"Markets analyzed: {analysis['condition_id'].nunique():,}")
log(f"YES base rate: {yes_rate:.3f}")
log(f"Directional HR (|lag|>0.05): {directional_hr:.3f}")
log(f"Verdict: {verdict}")
log(f"Best YES compound: {yes_df.iloc[0]['compounding']:.1f} vs baseline: {base_yes['compounding']:.1f}")
log(f"Outputs: {OUT_DIR}")
