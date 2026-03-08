# Walk-Forward Fold: January 2026 — Politics YES Composite

**Run date**: 2026-03-07
**Script**: `research/hypotheses/scorecard-v2-strategies/scripts/walkforward_2026_politics.py`
**Ledger**: `research/output/ledger_politics_wf_2026jan.parquet`

## Setup

| Parameter | Value |
|-----------|-------|
| Strategy | Politics YES Composite (TokenMapStrategy) |
| Pool | K=100 (composite score: excess_hr 45%, consistency_sharpe 25%, avg_edge 15%, bucket_excess_hr 15%) |
| Train cutoff | < 2026-01-01 (all data before January) |
| Test window | 2026-01-01 to 2026-02-01 (resolved_at in January) |
| direction_filter | YES only |
| n_threshold | N=5 |
| max_price | 0.80 |
| size_usd | $100 per signal |

## Test Universe

- January 2026 Politics markets (resolved in Jan): **2,605 markets**
- Pool traders: **100**
- Gambling markets excluded: **167,645**

## Results

| Metric | Value |
|--------|-------|
| Fills | 94 |
| Wins / Losses | 63 / 31 |
| Hit rate | **67.0%** |
| January YES base rate (market-level) | **19.7%** (509 won / 2,589 markets) |
| Excess HR | **+47.3pp** (67.0% - 19.7%) |
| Net PnL | **-$1,525** |
| Avg fill price | $0.80 (all fills at max_price cap) |
| Avg hold | 31.7h median (p25=3.5h, p75=682h) |
| Sharpe | -0.83 |

## PnL Explanation

All 94 signals fired at the max_price cap of $0.80. The PnL model at this price is:

- Win payoff: `(1.0 - 0.80) × (100/0.80)` = **+$25.00** per win
- Loss payoff: **-$100.00** per loss
- Break-even HR: **80.0%** (`100 / (100 + 25)`)

The strategy's 67.0% HR is below the 80% break-even at fill_price=0.80, so negative PnL is **mechanically correct** — not a model failure.

Arithmetic check: `63 × $25 + 31 × (-$100) = $1,575 - $3,100 = -$1,525` (matches exactly).

## Comparison to Full-Period Result

| Metric | Full period (2025-07+) | January 2026 fold |
|--------|------------------------|-------------------|
| Fills | 64 | 94 |
| Hit rate | 71.9% | 67.0% |
| Break-even HR at 0.80 | 80.0% | 80.0% |
| Above break-even? | No (71.9% < 80%) | No (67.0% < 80%) |
| Excess HR | ~+52pp | +47.3pp |

**Key observation**: The full-period 64-fill result at 71.9% HR was ALSO below the 80% break-even for max_price=0.80. The high excess HR over the market-level base rate is real signal, but the fill price ceiling dominates the PnL.

## Interpretation

### Signal is real
- 67.0% HR against a 19.7% base rate = **+47.3pp excess** is a strong directional signal.
- The pool correctly identifies traders who pick YES winners at high rates.
- Hold time distribution is reasonable: 46.8% resolve within 24h, 67% within 1 week.

### max_price=0.80 is the problem
- The pool consensus tends to fire on markets that are already priced near 0.80 (high-confidence markets).
- At fill=0.80 you need 80% HR to break even. No Politics fold reaches this threshold.
- **Recommended fix**: lower max_price to 0.65 or 0.70, where break-even drops to 74% or 77%.
  - At max_price=0.65: break-even = 65/(65+35) = **65.0%** — achievable.
  - At max_price=0.70: break-even = 70/(70+30) = **70.0%** — borderline.

### Walk-forward consistency
- 94 fills in January alone vs 64 fills in the full 6-month period is surprisingly high.
- Suggests the larger training window (train < 2026-01-01 vs train < 2025-07-01) produces a more active pool in January 2026 markets.
- HR dropped from 71.9% → 67.0% (-4.9pp) moving from full-period to January fold — a modest degradation, not a collapse.

## Recommendation

The HR signal is consistent across both periods. The strategy is **not profitable at max_price=0.80** in either the full-period or the January fold. Next step: re-run with max_price=0.65 to test whether the break-even constraint can be satisfied.
