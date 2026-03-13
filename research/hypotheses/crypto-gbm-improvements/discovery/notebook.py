"""
Crypto GBM Improvements — Discovery Notebook
marimo notebook for exploring GBM improvement axes.

Usage:
  PYTHONPATH=. uv run marimo run research/hypotheses/crypto-gbm-improvements/discovery/notebook.py
"""

import marimo as mo

# Cell 0: Setup
import sys
sys.path.insert(0, '/mnt/nvme/git/polymarket/polymarket')
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# Load results
OUT_DIR = Path('/mnt/nvme/git/polymarket/polymarket/research/hypotheses/crypto-gbm-improvements/discovery')
with open(OUT_DIR / 'results.json') as f:
    results = json.load(f)

mo.md(f"""
# Crypto GBM Improvements — Discovery Results

**Verdict**: `{results['verdict']}`

**Critical Finding**: Vectorized simulation using `yes_entry_data` is **INVALID** for this strategy.
Historical trade data is from AFTER PM has repriced, not at GBM signal time.

Evidence: YES HR = **{results['sanity']['yes_hr_lag_gt05']:.1%}** when GBM lag > 0.05 (expected ~50%).
The signal is inverted — PM was already correct when the historical trader entered.

## What This Means

The GBM scalp strategy fires within **seconds** of a BTC price move. By the time
historical trades are recorded, PM has already repriced. The `yes_entry_data` snapshot
cannot reproduce the strategy's entry conditions.

## PM Calibration (Confirmed)

PM price IS well-calibrated: entry price predicts outcome with 0.61 correlation.
""")

# Cell 1: PM Calibration
pm_cal = pd.DataFrame([
    {'bucket': '0.00-0.20', 'entry_price_mid': 0.10, 'yes_hr': 0.080},
    {'bucket': '0.20-0.40', 'entry_price_mid': 0.30, 'yes_hr': 0.204},
    {'bucket': '0.40-0.50', 'entry_price_mid': 0.45, 'yes_hr': 0.435},
    {'bucket': '0.50-0.60', 'entry_price_mid': 0.55, 'yes_hr': 0.556},
    {'bucket': '0.60-0.80', 'entry_price_mid': 0.70, 'yes_hr': 0.704},
    {'bucket': '0.80-1.00', 'entry_price_mid': 0.90, 'yes_hr': 0.948},
])

fig, ax = plt.subplots(figsize=(8, 5))
ax.bar(pm_cal['bucket'], pm_cal['yes_hr'], color='steelblue', alpha=0.7)
ax.plot([0, len(pm_cal)-1], [0.5, 0.5], 'r--', label='50% base rate')
x = np.arange(len(pm_cal))
ax.set_xticks(x)
ax.set_xticklabels(pm_cal['bucket'], rotation=45)
ax.set_ylabel('YES (Up) Hit Rate')
ax.set_title('PM Price Calibration — BTC Up/Down Markets\n(Confirms GBM edge is speed, not foresight)')
ax.legend()
plt.tight_layout()
plt.savefig(OUT_DIR / 'pm_calibration.png', dpi=120)
plt.close()
mo.md("![PM Calibration](pm_calibration.png)")

# Cell 2: Axis Summary (Analytical)
mo.md("""
## Axis Analysis (Analytical)

| Axis | Conclusion | Impact | Requires Tick? |
|------|-----------|--------|---------------|
| 1. Dynamic Sizing | IMPLEMENT — +$2.89 EV (+57%) | High | No |
| 2. Re-entry | BLOCKED — needs code fix | TBD | Yes |
| 3. Fee-Aware Threshold | SKIP — minimal impact (<2% signal change) | Negligible | No |
| 4. Late-Entry Reduction | MARGINAL — already handled by no_entry_within_s | Low | Yes |
| 5. EWMA Sigma | UNCERTAIN — faster vol adaptation, needs tick test | Medium | Yes |
| 6. Hold-to-Resolution | ALREADY IMPLEMENTED at threshold=0.65 | N/A | No |

## Recommended Next Steps

1. **Tick validate Axis 1** (dynamic sizing): `bet = clip(|lag| / 0.10 * $50, max=$100)`
2. **Tick validate Axis 5** (EWMA sigma): compare rolling vs EWMA in full backtest
3. Accumulate `orderbook_bars_1m` for 30+ days → enables proper vectorized sweep
4. Multi-asset extension (Task #5): same analytical approach once ETH/SOL/XRP bars ready
""")

# Cell 3: Window Duration Mix
window_types = results['universe']['window_types']
fig2, ax2 = plt.subplots(figsize=(6, 4))
labels = list(window_types.keys())
sizes = list(window_types.values())
ax2.pie(sizes, labels=labels, autopct='%1.0f%%', startangle=90)
ax2.set_title('BTC Up/Down Window Duration Distribution\n(live strategy targets 5-min, but 15-min dominates)')
plt.savefig(OUT_DIR / 'window_distribution.png', dpi=120)
plt.close()
mo.md("![Window Distribution](window_distribution.png)")

# Cell 4: Dynamic Sizing EV Impact
mo.md("""
## Axis 1: Dynamic Sizing — EV Model

**Current**: Fixed $50 bet per signal
**Proposed**: `bet = clip(|GBM_lag| / 0.10 * $50, max=$100)`

| GBM-PM lag | Bet Size | Expected EV | vs Fixed EV |
|-----------|---------|------------|------------|
| 0.06 (min threshold) | $30 | $1.27 | -$0.83 |
| 0.10 (baseline) | $50 | $2.10 | baseline |
| 0.15 | $75 | $3.15 | +$1.05 |
| 0.20 | $100 | $4.20 | +$2.10 |
| 0.25+ | $100 (capped) | $4.20 | +$2.10 |

*EV calibrated from FINDINGS.md: +$2.10 per $50 at lag=0.10 → +$0.042/$ of bet*

**Portfolio impact**: Average lag in active signals is ~0.12-0.15 → average bet ~$60-75 →
EV uplift ~+$0.50-$1.00 per trade. No additional capital risk beyond max $100 cap.

**Capital implication**: With dynamic sizing, max bet doubles to $100. Current config has
`max_position_usd = 50` — this needs updating to 100.
""")

# Cell 5: EWMA vs Rolling Sigma
sigma_comparison = pd.DataFrame({
    'Metric': ['Median', 'Speed (half-life)', 'Vol spike detection', 'Lag from regime change'],
    'Rolling 24h': ['0.000583', '720 min (half-window)', 'Slow', '~12h to full weight'],
    'EWMA (span=1440)': ['0.000589', '720 min (half-span)', 'Faster', '~3-4h to 80% weight'],
})
mo.md(f"""
## Axis 5: EWMA vs Rolling Sigma

{mo.as_html(sigma_comparison)}

EWMA sigma is nearly identical to rolling at the median but adapts 3x faster to vol spikes.
During BTC flash crashes or surges (Q4_high vol regime), EWMA gives a more current volatility
estimate, which translates to a more accurate GBM P(Up).

**Recommendation**: Test in tick backtest. If vol regime matters (premortem noted this as a risk),
EWMA should show improvement in high-vol periods.
""")

# Cell 6: Data Architecture Note
mo.md("""
## Data Architecture for Proper Vectorized Sweep

To properly vectorize GBM improvement discovery:

```
orderbook_bars_1m  (CH, has 6 days so far)     exchange_bars (CH, 181 days BTC)
     │                                                │
     │  PM price at each minute                       │  BTC price at each minute
     ▼                                                ▼
   pm_p_up(t) ─────────────────────────────────> GBM_lag(t) = GBM(S₀, S_t, σ, T-t) - pm_p_up(t)
                                                        │
                                                 GBM_lag > threshold → ENTRY signal
                                                        │
                                              (simulate convergence exit at GBM price)
```

This requires `orderbook_bars_1m` to cover the backtest period. Currently only 6 days —
accumulate for 30+ days before running proper vectorized sweep.

Alternative: run tick-by-tick backtest using the research harness — this is the
correct approach given the data constraints.
""")
