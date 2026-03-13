"""
Vectorized discovery: crypto GBM improvement axes.

Data pipeline:
  1. Parse window start/end from question text → window_start = closed_at - duration
  2. Load BTC 1-min bars from ClickHouse exchange_bars (181 days, Binance)
  3. Compute rolling sigma (24h) and EWMA sigma (halflife variants)
  4. For each market: get S0 (BTC price at window_start) and S_t per minute
  5. Get PM price per minute from `trades` parquet (last trade price before each minute mark)
  6. Compute GBM P(up) at each minute; simulate entries when |GBM-PM| > threshold
  7. Track convergence, EV, time-stop rate, re-entry opportunities

UPPER BOUNDS — vectorized simulation, not tick-by-tick.

Runs in ~3-5 minutes.
"""

import sys, re, json, datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd
import clickhouse_connect
from scipy.stats import norm

sys.path.insert(0, '/mnt/nvme/git/polymarket/polymarket')

OUT_DIR = Path('research/hypotheses/crypto-gbm-improvements/discovery')
LOG = OUT_DIR / 'analysis.log'
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG.write_text('')

def log(msg):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f: f.write(line + '\n')

log("=" * 70)
log("CRYPTO GBM IMPROVEMENTS — VECTORIZED ANALYSIS")
log("ALL RESULTS ARE UPPER BOUNDS")
log("=" * 70)

# ── Window parser ──────────────────────────────────────────────────────────
def parse_window_dur(question: str) -> int | None:
    m = re.search(r'(\d+):(\d+)(AM|PM)-(\d+):(\d+)(AM|PM)\s+ET', question)
    if m:
        h1,m1,mer1,h2,m2,mer2 = m.groups()
        h1,m1,h2,m2 = int(h1),int(m1),int(h2),int(m2)
        if mer1=='PM' and h1!=12: h1+=12
        if mer1=='AM' and h1==12: h1=0
        if mer2=='PM' and h2!=12: h2+=12
        if mer2=='AM' and h2==12: h2=0
        dur = (h2*60+m2)-(h1*60+m1)
        return int(dur) if dur > 0 else int(dur+1440)
    if re.search(r'\d+(?::\d+)?(AM|PM)\s+ET', question):
        return 60
    return None

# ── GBM function ───────────────────────────────────────────────────────────
def gbm_p_up(s0, st, sigma, t_rem_min):
    lr = np.log(np.maximum(st,1e-10) / np.maximum(s0,1e-10))
    vol = np.maximum(sigma,1e-12) * np.sqrt(np.maximum(t_rem_min,0))
    d2 = np.where(vol>1e-10, lr/vol, np.where(lr>0, 10., -10.))
    return norm.cdf(d2)

# ── Load DuckDB ────────────────────────────────────────────────────────────
from research.db import db
d = db()

# ── Step 1: BTC up/down markets ────────────────────────────────────────────
log("\n=== Step 1: Markets ===")
mkts = d.query("""
SELECT
    condition_id,
    first(question)       as question,
    first(winner_outcome) as winner_outcome,
    first(token_yes)      as token_yes,
    first(closed_at)      as closed_at
FROM markets
WHERE lower(question) LIKE '%bitcoin%'
  AND lower(question) LIKE '%up%'
  AND lower(question) LIKE '%down%'
  AND winner_outcome IN ('Up','Down')
  AND closed_at >= '2025-09-10'
GROUP BY condition_id
""").to_pandas()

mkts['yes_won']        = mkts['winner_outcome'] == 'Up'
mkts['window_dur_min'] = mkts['question'].apply(parse_window_dur)
mkts = mkts.dropna(subset=['window_dur_min'])
mkts['window_dur_min'] = mkts['window_dur_min'].astype(int)
mkts['closed_at']      = pd.to_datetime(mkts['closed_at'], utc=True).dt.floor('s').astype('datetime64[us, UTC]')
mkts['window_start']   = mkts['closed_at'] - pd.to_timedelta(mkts['window_dur_min'], unit='m')

# Limit to 5-min and 15-min windows for this analysis (4-hour/1-hour are separate)
mkts_5m  = mkts[mkts['window_dur_min']==5].copy()
mkts_15m = mkts[mkts['window_dur_min']==15].copy()
log(f"Markets: 5-min={len(mkts_5m):,}, 15-min={len(mkts_15m):,}")
yes_rate_5m  = mkts_5m['yes_won'].mean()
yes_rate_15m = mkts_15m['yes_won'].mean()
log(f"YES base rate: 5-min={yes_rate_5m:.3f}, 15-min={yes_rate_15m:.3f}")

# ── Step 2: BTC 1-min bars from ClickHouse ─────────────────────────────────
log("\n=== Step 2: BTC 1-min bars ===")
ch = clickhouse_connect.get_client(host='192.168.0.148', port=18123, database='polymarket')
res = ch.query("""
    SELECT toStartOfMinute(ts) as ts_min, any(close) as close
    FROM exchange_bars
    WHERE symbol='BTC-USDT' AND exchange='BINANCE'
    GROUP BY ts_min ORDER BY ts_min
""")
bars = pd.DataFrame(res.result_rows, columns=['ts','close'])
bars['ts'] = pd.to_datetime(bars['ts'], utc=True).dt.floor('min').astype('datetime64[us, UTC]')
bars = bars.sort_values('ts').set_index('ts')
log(f"BTC bars: {len(bars):,} 1-min rows, {bars.index.min()} → {bars.index.max()}")

# ── Step 3: Sigma variants ─────────────────────────────────────────────────
log("\n=== Step 3: Sigma ===")
lr_1m = np.log(bars['close']).diff().dropna()
sigma_rolling = lr_1m.rolling(1440, min_periods=60).std().rename('sigma_roll')
# EWMA variants with different halflives
sigma_hl60  = lr_1m.ewm(halflife=60,  min_periods=30).std().rename('sigma_hl60')
sigma_hl180 = lr_1m.ewm(halflife=180, min_periods=60).std().rename('sigma_hl180')
sigma_hl360 = lr_1m.ewm(halflife=360, min_periods=60).std().rename('sigma_hl360')
sigma_df = pd.concat([sigma_rolling, sigma_hl60, sigma_hl180, sigma_hl360], axis=1).reset_index()
sigma_df.columns = ['ts','sigma_roll','sigma_hl60','sigma_hl180','sigma_hl360']
sigma_df['ts'] = sigma_df['ts'].astype('datetime64[us, UTC]')
log(f"Sigma (rolling median): {sigma_rolling.median():.6f}")
log(f"Sigma (hl60  median):   {sigma_hl60.median():.6f}")
log(f"Sigma (hl180 median):   {sigma_hl180.median():.6f}")

# ── Step 4: Per-minute PM price from trades ────────────────────────────────
log("\n=== Step 4: PM price from trades (per-minute last trade) ===")
# Load trades for BTC up/down condition_ids in bar window
# Use token_yes to filter to YES-side prices (gives P_yes = P_up)
btc_cids = mkts[mkts['window_dur_min'].isin([5,15])]['condition_id'].tolist()
token_yes_set = mkts[mkts['window_dur_min'].isin([5,15])]['token_yes'].dropna().tolist()

log(f"Loading trades for {len(btc_cids):,} BTC up/down markets...")
# Load all trades for these markets from DuckDB parquet
# Filter to YES-token trades (asset_id = token_yes) to get P(up)
cid_str = "'" + "','".join(btc_cids[:5000]) + "'"  # first 5k for memory
trades_raw = d.query(f"""
SELECT t.condition_id, t.asset_id, t.price, t.timestamp
FROM trades t
WHERE t.condition_id IN ({cid_str})
ORDER BY t.condition_id, t.timestamp
""").to_pandas()
log(f"Trades loaded (first 5k markets): {len(trades_raw):,}")

# Also load remaining markets if needed
remaining_cids = btc_cids[5000:]
if remaining_cids:
    cid_str2 = "'" + "','".join(remaining_cids) + "'"
    trades_raw2 = d.query(f"""
    SELECT t.condition_id, t.asset_id, t.price, t.timestamp
    FROM trades t
    WHERE t.condition_id IN ({cid_str2})
    ORDER BY t.condition_id, t.timestamp
    """).to_pandas()
    log(f"Trades loaded (remaining {len(remaining_cids):,} markets): {len(trades_raw2):,}")
    trades_raw = pd.concat([trades_raw, trades_raw2], ignore_index=True)

log(f"Total trades: {len(trades_raw):,}")

# Join token_yes to filter
token_yes_map = mkts.set_index('condition_id')['token_yes'].to_dict()
trades_raw['is_yes'] = trades_raw['asset_id'] == trades_raw['condition_id'].map(token_yes_map)
trades_yes = trades_raw[trades_raw['is_yes']].copy()
trades_yes['timestamp'] = pd.to_datetime(trades_yes['timestamp'], utc=True).astype('datetime64[us, UTC]')
trades_yes['ts_min'] = trades_yes['timestamp'].dt.floor('min')
log(f"YES-token trades: {len(trades_yes):,}")

# Per-minute last trade price (= PM price at that minute)
pm_prices = (
    trades_yes
    .sort_values('timestamp')
    .groupby(['condition_id','ts_min'])['price']
    .last()
    .reset_index()
    .rename(columns={'ts_min':'ts','price':'pm_price'})
)
log(f"PM price observations: {len(pm_prices):,} (condition_id × minute)")

# Pre-index pm_prices by condition_id for O(1) lookup per market
pm_by_cid = {cid: grp.sort_values('ts').reset_index(drop=True)
             for cid, grp in pm_prices.groupby('condition_id')}
log(f"PM price index built for {len(pm_by_cid):,} markets")

# Pre-index sigma_df for fast lookups
sigma_df_sorted = sigma_df.sort_values('ts').reset_index(drop=True)

# ── Step 5: Core simulation function ──────────────────────────────────────
log("\n=== Step 5: Simulate entries per market ===")

def simulate_markets(mkt_df, threshold=0.10, sigma_col='sigma_roll',
                     dynamic_sizing=False, base_bet=50.0,
                     late_entry_cutoff_frac=None, check_reentry=False,
                     exit_threshold=0.02, no_entry_within_s=90.0):
    """
    For each market, simulate GBM entries at 1-minute resolution.

    Returns list of entry records:
      condition_id, entry_min, lag, pm_price, gbm_p, yes_won,
      converged, time_stopped, hold_min, ev_usd, reentry_num
    """
    entries = []
    n_total = len(mkt_df)

    for i, (_, mkt) in enumerate(mkt_df.iterrows()):
        if i > 0 and i % 1000 == 0:
            log(f"  Progress: {i}/{n_total} markets, {len(entries)} entries so far")
        cid        = mkt['condition_id']
        ws         = pd.Timestamp(mkt['window_start']).tz_convert('UTC')
        we         = pd.Timestamp(mkt['closed_at']).tz_convert('UTC')
        dur        = mkt['window_dur_min']
        yes_won    = mkt['yes_won']
        no_entry_s = no_entry_within_s

        # BTC price at S0 (window open)
        ws_min = ws.floor('min')
        try:
            s0 = bars.at[ws_min, 'close']
        except KeyError:
            continue
        if s0 <= 0:
            continue

        # Get sigma at window open — use searchsorted for O(log n) lookup
        idx = sigma_df_sorted['ts'].searchsorted(ws_min, side='right') - 1
        if idx < 0 or pd.isna(sigma_df_sorted.loc[idx, sigma_col]):
            continue
        sigma = float(sigma_df_sorted.loc[idx, sigma_col])
        if sigma <= 1e-7:
            continue

        # PM prices for this market during the window
        if cid not in pm_by_cid:
            continue
        mkt_pm_all = pm_by_cid[cid]
        we_min = we.floor('min')
        # Slice to window range using searchsorted
        lo = mkt_pm_all['ts'].searchsorted(ws_min, side='left')
        hi = mkt_pm_all['ts'].searchsorted(we_min, side='right')
        mkt_pm = mkt_pm_all.iloc[lo:hi]

        if len(mkt_pm) == 0:
            continue

        # Skip minute zero (first minute of window)
        skip_until = ws_min + pd.Timedelta(minutes=1)

        signaled = False
        reentry_count = 0
        position_direction = None
        position_entry_min = None

        for _, pm_row in mkt_pm.iterrows():
            t_min = pm_row['ts']
            pm_p  = float(pm_row['pm_price'])
            if pm_p <= 0 or pm_p >= 1:
                continue
            if t_min < skip_until:
                continue

            t_rem = (we - t_min).total_seconds() / 60.0
            if t_rem <= 0:
                continue
            # No entry within no_entry_within_s of window end
            if t_rem * 60 < no_entry_s:
                continue

            # Late entry cutoff
            elapsed_frac = (t_min - ws_min).total_seconds() / (dur * 60)
            if late_entry_cutoff_frac is not None and elapsed_frac > late_entry_cutoff_frac:
                continue

            # BTC price at this minute
            try:
                st = bars.at[t_min, 'close']
            except KeyError:
                continue

            # GBM P(up)
            p_up = float(gbm_p_up(s0, st, sigma, t_rem))
            lag  = p_up - pm_p

            # Check if already in position
            if (signaled and not check_reentry) or (reentry_count >= 1 and check_reentry):
                continue

            # Direction signal
            if abs(lag) < threshold:
                # Check if convergence happened (for previous position tracking)
                if signaled and position_direction is not None:
                    # Once lag drops below exit_threshold, convergence happened
                    if abs(lag) <= exit_threshold:
                        signaled = False  # allow reentry
                continue

            direction = 'YES' if lag > 0 else 'NO'

            # Bet sizing
            if dynamic_sizing:
                bet = min(base_bet * (abs(lag) / threshold), base_bet * 2.0)
            else:
                bet = base_bet

            # Simulate outcome: does PM converge to GBM?
            # Convergence = at a later minute, |GBM-PM| < exit_threshold
            # Time-stop = window ends without convergence
            converged = False
            convergence_pm = None
            hold_min = t_rem  # max hold (will be overwritten if convergence found)

            # Look ahead in PM prices for convergence
            future_start = mkt_pm['ts'].searchsorted(t_min, side='right')
            future_pm = mkt_pm.iloc[future_start:]
            for _, fp in future_pm.iterrows():
                fp_t   = fp['ts']
                fp_pm  = float(fp['pm_price'])
                fp_rem = (we - fp_t).total_seconds() / 60.0
                try:
                    fp_st = bars.at[fp_t, 'close']
                except KeyError:
                    continue
                fp_gbm = float(gbm_p_up(s0, fp_st, sigma, max(fp_rem,0)))
                fp_lag = fp_gbm - fp_pm

                # Convergence: GBM-PM gap closed
                if abs(fp_lag) <= exit_threshold:
                    converged = True
                    convergence_pm = fp_pm
                    hold_min = (fp_t - t_min).total_seconds() / 60.0
                    break
                # GBM flip stop-loss (if GBM reverses against our position)
                if direction=='YES' and fp_gbm < 0.35:
                    hold_min = (fp_t - t_min).total_seconds() / 60.0
                    break
                if direction=='NO' and (1-fp_gbm) < 0.35:
                    hold_min = (fp_t - t_min).total_seconds() / 60.0
                    break

            if not converged:
                hold_min = t_rem  # held to time-stop

            # EV model: convergence = exit near GBM price
            # At convergence, PM moves from pm_p to ~(pm_p + lag * convergence_fraction)
            # We capture that as: EV = convergence_fraction * lag * bet * 0.97 (fee)
            if converged and convergence_pm is not None:
                if direction == 'YES':
                    price_gain = convergence_pm - pm_p  # PM rose to meet GBM
                else:
                    price_gain = pm_p - convergence_pm  # PM fell to meet GBM (NO gained)
                ev_usd = max(price_gain, 0) * (bet / pm_p) * 0.97
            else:
                # Time-stop: we exit at market price, typically a loss
                # From FINDINGS.md: -28.75% median PnL on time-stops
                ev_usd = -0.2875 * bet

            entries.append({
                'condition_id': cid,
                'window_dur_min': dur,
                'entry_min': t_min,
                'elapsed_frac': elapsed_frac,
                't_rem': t_rem,
                'lag': lag,
                'lag_abs': abs(lag),
                'pm_price': pm_p,
                'gbm_p': p_up,
                'direction': direction,
                'yes_won': yes_won,
                'bet_usd': bet,
                'converged': converged,
                'hold_min': hold_min,
                'ev_usd': ev_usd,
                'reentry_num': reentry_count,
                'sigma': sigma,
            })

            signaled = True
            position_direction = direction
            position_entry_min = t_min
            if check_reentry:
                reentry_count += 1

    return pd.DataFrame(entries)

# ── Step 6: Run baseline (5-min and 15-min) ───────────────────────────────
log("\n=== Step 6: Baseline simulation ===")

log("Running 5-min markets (baseline)...")
df_5m_base = simulate_markets(mkts_5m, threshold=0.10)
log(f"5-min entries: {len(df_5m_base):,} in {df_5m_base['condition_id'].nunique() if len(df_5m_base) else 0} markets")

log("Running 15-min markets (baseline)...")
df_15m_base = simulate_markets(mkts_15m, threshold=0.10)
log(f"15-min entries: {len(df_15m_base):,} in {df_15m_base['condition_id'].nunique() if len(df_15m_base) else 0} markets")

def summarize(df, label):
    if df.empty or len(df) == 0:
        log(f"  {label}: NO DATA")
        return {}
    n = len(df)
    conv_rate = df['converged'].mean()
    tstop_rate = 1 - conv_rate
    avg_ev = df['ev_usd'].mean()
    med_hold = df['hold_min'].median()
    # HR = did we pick the right direction?
    hr = ((df['direction']=='YES') & df['yes_won'] | (df['direction']=='NO') & ~df['yes_won']).mean()
    excess_hr = hr - 0.5  # base rate is ~50/50
    log(f"  {label}: n={n}, conv={conv_rate:.1%}, tstop={tstop_rate:.1%}, "
        f"avg_ev=${avg_ev:.2f}, med_hold={med_hold:.1f}min, HR={hr:.3f} ({excess_hr:+.3f}pp)")
    return {'n': n, 'conv_rate': float(conv_rate), 'tstop_rate': float(tstop_rate),
            'avg_ev_usd': float(avg_ev), 'median_hold_min': float(med_hold),
            'hr': float(hr), 'excess_hr': float(excess_hr)}

log("Baseline results:")
b5  = summarize(df_5m_base, "5-min baseline")
b15 = summarize(df_15m_base, "15-min baseline")

# ── Step 7: Axis sweeps ────────────────────────────────────────────────────
log("\n=== Step 7: Axis sweeps ===")
results = {'baseline_5min': b5, 'baseline_15min': b15, 'axes': {}}

# Axis 1: Dynamic sizing
log("\n--- Axis 1: Dynamic sizing ---")
for wtype, mkt_df in [('5min', mkts_5m), ('15min', mkts_15m)]:
    df = simulate_markets(mkt_df, threshold=0.10, dynamic_sizing=True)
    s = summarize(df, f"dyn_sizing {wtype}")
    results['axes'].setdefault('dynamic_sizing', {})[wtype] = s
    if not df.empty:
        log(f"  Avg bet (dyn): ${df['bet_usd'].mean():.1f}, Max: ${df['bet_usd'].max():.1f}")
        log(f"  EV by lag bucket:")
        df['lag_bucket'] = pd.cut(df['lag_abs'], bins=[0.10,0.12,0.15,0.20,0.30,1.0])
        for bkt, grp in df.groupby('lag_bucket'):
            log(f"    lag {bkt}: n={len(grp)}, avg_ev=${grp['ev_usd'].mean():.2f}, "
                f"avg_bet=${grp['bet_usd'].mean():.1f}")

# Axis 2: Re-entry
log("\n--- Axis 2: Re-entry ---")
for wtype, mkt_df in [('5min', mkts_5m), ('15min', mkts_15m)]:
    df = simulate_markets(mkt_df, threshold=0.10, check_reentry=True)
    s = summarize(df, f"reentry {wtype}")
    results['axes'].setdefault('reentry', {})[wtype] = s
    if not df.empty:
        log(f"  1st entries: {(df['reentry_num']==0).sum()}, "
            f"Re-entries: {(df['reentry_num']>=1).sum()}")
        first = df[df['reentry_num']==0]
        reentry = df[df['reentry_num']>=1]
        if len(first) and len(reentry):
            log(f"  1st conv={first['converged'].mean():.1%} ev=${first['ev_usd'].mean():.2f}")
            log(f"  Re-entry conv={reentry['converged'].mean():.1%} ev=${reentry['ev_usd'].mean():.2f}")

# Axis 4: Late-entry size reduction (measure by entry minute, no cutoff)
log("\n--- Axis 4: Late-entry analysis ---")
for wtype, mkt_df, base_df in [('5min', mkts_5m, df_5m_base), ('15min', mkts_15m, df_15m_base)]:
    if base_df.empty:
        continue
    log(f"  {wtype} — convergence rate by elapsed fraction:")
    base_df['elapsed_bucket'] = pd.cut(base_df['elapsed_frac'],
                                        bins=[0, 0.33, 0.67, 1.0],
                                        labels=['early','mid','late'])
    axis4 = {}
    for bkt, grp in base_df.groupby('elapsed_bucket'):
        log(f"    {bkt}: n={len(grp)}, conv={grp['converged'].mean():.1%}, "
            f"tstop={1-grp['converged'].mean():.1%}, ev=${grp['ev_usd'].mean():.2f}")
        axis4[str(bkt)] = {'n': len(grp), 'conv': float(grp['converged'].mean()),
                           'ev': float(grp['ev_usd'].mean())}
    results['axes'].setdefault('late_entry', {})[wtype] = axis4

    # Simulate with late-entry cutoff at 0.67 (no entry in final 1/3 of window)
    df_cut = simulate_markets(mkt_df, threshold=0.10, late_entry_cutoff_frac=0.67)
    s = summarize(df_cut, f"late_cutoff_0.67 {wtype}")
    results['axes']['late_entry'].setdefault(f'{wtype}_cutoff', s)

# Axis 5: EWMA sigma variants
log("\n--- Axis 5: EWMA sigma variants ---")
for wtype, mkt_df in [('5min', mkts_5m), ('15min', mkts_15m)]:
    axis5 = {}
    for sigma_col, label in [('sigma_roll','rolling_24h'), ('sigma_hl60','ewma_hl60'),
                               ('sigma_hl180','ewma_hl180'), ('sigma_hl360','ewma_hl360')]:
        df = simulate_markets(mkt_df, threshold=0.10, sigma_col=sigma_col)
        s = summarize(df, f"{label} {wtype}")
        axis5[label] = s
    results['axes'].setdefault('ewma_sigma', {})[wtype] = axis5

# Axis 3: Fee-aware threshold (analytical)
log("\n--- Axis 3: Fee-aware threshold (analytical) ---")
# PM fee = 0.25*(p*(1-p))^2. At p=0.50: 1.56%
# Effective threshold = 0.10 + 0.0156 = 0.1156 at p=0.50
# Impact: ~5-10% reduction in signal count, minimal HR change
for wtype, mkt_df in [('5min', mkts_5m), ('15min', mkts_15m)]:
    df_fee = simulate_markets(mkt_df, threshold=0.10)
    if not df_fee.empty:
        df_fee['pm_fee'] = 0.25 * (df_fee['pm_price'] * (1-df_fee['pm_price']))**2
        df_fee_filtered = df_fee[df_fee['lag_abs'] > (0.10 + df_fee['pm_fee'])]
        log(f"  {wtype}: fee-aware n={len(df_fee_filtered)} vs flat n={len(df_fee)} "
            f"({len(df_fee_filtered)/len(df_fee):.1%} kept)")
        conv_delta = df_fee_filtered['converged'].mean() - df_fee['converged'].mean()
        log(f"    convergence delta: {conv_delta:+.3f}")
        results['axes'].setdefault('fee_aware', {})[wtype] = {
            'n_flat': len(df_fee), 'n_fee_aware': len(df_fee_filtered),
            'pct_kept': float(len(df_fee_filtered)/max(len(df_fee),1)),
            'conv_delta': float(conv_delta)
        }

# ── Step 8: Vol regime stratification ─────────────────────────────────────
log("\n=== Step 8: Vol regime ===")
if not df_15m_base.empty:
    df_15m_base['sigma_q'] = pd.qcut(df_15m_base['sigma'], q=4,
                                      labels=['Q1_low','Q2','Q3','Q4_high'])
    for q in ['Q1_low','Q2','Q3','Q4_high']:
        grp = df_15m_base[df_15m_base['sigma_q']==q]
        if len(grp):
            log(f"  15-min {q}: n={len(grp)}, conv={grp['converged'].mean():.1%}, "
                f"ev=${grp['ev_usd'].mean():.2f}, HR={((grp['direction']=='YES')&grp['yes_won']|(grp['direction']=='NO')&~grp['yes_won']).mean():.3f}")

# ── Step 9: Best combination ──────────────────────────────────────────────
log("\n=== Step 9: Best combination ===")
# Best: dynamic sizing + EWMA hl180 + late entry cutoff 0.67
for wtype, mkt_df in [('5min', mkts_5m), ('15min', mkts_15m)]:
    df_combo = simulate_markets(mkt_df, threshold=0.10, sigma_col='sigma_hl180',
                                 dynamic_sizing=True, late_entry_cutoff_frac=0.67)
    s = summarize(df_combo, f"BEST_COMBO {wtype}")
    results['axes'].setdefault('best_combo', {})[wtype] = s

# ── Step 10: Write results ─────────────────────────────────────────────────
log("\n=== Step 10: Write results ===")

# Structured summary
results['metadata'] = {
    'timestamp': dt.datetime.now(dt.UTC).isoformat(),
    'note': 'ALL RESULTS ARE UPPER BOUNDS',
    'universe': {
        'btc_5min_markets': len(mkts_5m),
        'btc_15min_markets': len(mkts_15m),
        'bar_coverage': '181 days (2025-09-10 to now)',
        'bar_source': 'exchange_bars, BTC-USDT, BINANCE, 1-min resampled',
        'pm_price_source': 'trades parquet, YES-token last price per minute',
    },
    'base_rates': {
        '5min_yes_rate': round(float(yes_rate_5m), 3),
        '15min_yes_rate': round(float(yes_rate_15m), 3),
    },
    'findings_md_baseline': '+$2.10/trade at $50 notional (tick-by-tick)',
    'sell_handling': 'BUY-only strategy. SELL=exit only. sell_sensitivity_pp=0.',
}

with open(OUT_DIR / 'results.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
log(f"Saved results.json")

# Save entry-level data for notebook
if not df_15m_base.empty:
    df_15m_base.to_parquet(OUT_DIR / 'entries_15min_baseline.parquet', index=False)
if not df_5m_base.empty:
    df_5m_base.to_parquet(OUT_DIR / 'entries_5min_baseline.parquet', index=False)
log("Saved entry parquets")

log("\n=== DONE ===")
log(f"Outputs: {OUT_DIR}")
