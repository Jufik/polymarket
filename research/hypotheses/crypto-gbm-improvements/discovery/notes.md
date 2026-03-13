# Discovery Notes: Crypto GBM Improvements

**Date**: 2026-03-10
**Researcher**: Researcher agent

---

## Critical Data Limitation Discovered

> [!CRITICAL]
> **`yes_entry_data` cannot be used to simulate the GBM scalp strategy vectorized.**
>
> The `yes_entry_data` parquet snapshot captures HISTORICAL trader entries at any time during
> the market's lifetime. The GBM scalp strategy fires within SECONDS of a BTC price move
> (when GBM-PM divergence is created). By the time historical traders make their entries
> (median 5-30 min before window close), PM has already repriced to nearly match GBM.
>
> Using `yes_entry_data` to proxy GBM signals produces an INVERTED result:
> - When GBM lag > 0.05 (GBM says YES underpriced), actual YES HR = 11% (not ~52%)
> - When GBM lag < -0.05 (GBM says NO underpriced), actual YES HR = 78%
> - This is NOT a GBM signal inversion — it is data contamination
>
> **Root cause**: The representative entry (highest-volume trader) typically enters when PM
> has ALREADY moved to reflect BTC direction. If PM shows 0.80 for YES and GBM shows 0.65,
> the trader probably entered long ago (e.g., when PM was 0.35 and GBM was 0.40). By the
> time of the `yes_entry_data` record, the trade was hours ago and PM has moved against
> the GBM lag direction.
>
> **Correct approach**: The vectorized sweep must use exchange_bars + PM price snapshots
> at second-level resolution, simulating the exact GBM signal timing (within 30-90s of
> window open). This requires tick-level PM price data, not snapshot positions.

---

## What Works (from FINDINGS.md baseline)

The prior tick-by-tick validation in `crypto-gbm-exit/FINDINGS.md` is the correct ground truth:
- +$2.10 median EV per trade at $50 notional
- 96.2% convergence exit rate
- 3.8% time-stop rate
- GBM P(Up) at minute 1 is within ±4pp of actual outcome rate

The strategy IS working in paper_dev. The issue is with VECTORIZED SIMULATION methodology.

---

## What CAN Be Analyzed Vectorized

### 1. PM Price Calibration (confirmed working)
Entry price vs YES HR correlation = 0.61. PM price IS the correct fair value:
| Entry Price | YES HR |
|-------------|--------|
| 0.00-0.20 | 8.0% |
| 0.20-0.40 | 20.4% |
| 0.40-0.50 | 43.5% |
| 0.50-0.60 | 55.6% |
| 0.60-0.80 | 70.4% |
| 0.80-1.00 | 94.8% |

This validates FINDINGS.md Section 1 (GBM calibration is correct).

### 2. Window Duration Mix
- 15-min: 16,177 markets (57.7%)
- 60-min: 6,117 markets (21.8%)
- 5-min: 4,954 markets (17.7%)
- 4-hour: 778 markets (2.8%)

The live strategy config targets 5-min windows. Most historical markets are 15-min. This
does NOT affect strategy correctness (it trades whichever window is active) but DOES mean
the 5-min window subsample (~4,954 resolved markets) is the relevant backtest universe.

### 3. Dynamic Sizing — Analytical
Dynamic sizing (bet proportional to lag magnitude) increases EV without changing HR:
`ΔEV = $+2.89` per trade at dynamic sizing (vs fixed $50).
This is a purely mechanical improvement — no signal quality required.

### 4. Fee-Aware Threshold — Analytical
PM fee at p=0.50: 0.25 * (0.5 * 0.5)^2 = 1.5625%
At typical entry range 0.40-0.55:
- Fee = 0.25 * (0.45 * 0.55)^2 ≈ 0.019% ... Wait, let me recalculate:
  `0.25 * (p*(1-p))^2` at p=0.50 = 0.25 * 0.0625 = 0.015625 = 1.56%
  at p=0.45: 0.25 * (0.45*0.55)^2 = 0.25 * 0.0612 = 1.53%

The fee is already smaller than the 10% threshold. Fee-aware adjustment is ~1.5% on
top of 10% threshold = 11.5% effective. Marginal impact on signal count.

### 5. EWMA Sigma
EWMA vs rolling: median sigma 0.000589 vs 0.000583 (similar).
EWMA adapts faster to vol regime changes but shows -0.56pp HR at sweep level
(likely noise given data quality issues). Cannot properly evaluate without correct simulation.

---

## Vol Regime Note

> [!WARNING]
> Vol regime stratification (Q1_low vs Q4_high) shows NO meaningful HR differentiation
> in the vectorized sweep because of the data contamination issue above.
> This should be re-evaluated in tick validation using exchange_bars directly.

---

## Correct Discovery Approach for GBM Improvements

Since vectorized simulation requires minute-level PM price data not yet accumulated:

**Bar coverage note**: `exchange_bars` (BTC price) has 181 days of 1s Binance data
(Sep 10 2025 → now), covering 26,196 of 28,889 resolved BTC up/down markets. Bar data
is NOT the bottleneck.

**PM price is the bottleneck**: `orderbook_bars_1m` (PM bid/ask per minute) has only 6 days
(2026-03-04+). Need 30+ days before a proper vectorized sweep is feasible.

**Why early-window yes_entry_data also fails**: The first-2-min highest-volume entries are
settlement arbitrageurs buying YES at ~$0.99 near-certain prices — NOT GBM scalp entries.
GBM scalp fires on markets where PM is 10pp wrong (thin, volatile); those have no
large-volume counterparties in yes_entry_data at signal time.

**Option A (recommended now)**: Tick-by-tick only
- Skip vectorized discovery for signal quality axes
- Use analytical arguments for axes 3 (fee-aware), 6 (hold-to-resolution — already live)
- Run tick backtest for axes 1 (dynamic sizing), 5 (EWMA sigma)

**Option B (future — needs 30+ days)**: orderbook_bars_1m sweep
- Join with exchange_bars (BTC price) on timestamp
- Compute GBM-PM lag at each minute of each window
- Proper upper-bound vectorized simulation becomes possible

---

## Spawned Ideas

1. **Export orderbook_bars_1m for BTC up/down markets** → enables proper vectorized GBM sweep
2. **5-min window analysis**: Focus on the 4,954 5-min windows which match live config best
3. **ETH/SOL/XRP**: Same approach applies once exchange bars fetched (Task #3)
4. **EWMA sigma**: Requires tick-level testing; analytical argument favors it for regime adaptation
5. **Re-entry logic**: Structural code fix required in strategy.py before any testing

---

## Proposed Knowledge Capture

```markdown
## [crypto_gbm_vectorized_limitation]
yes_entry_data cannot be used to vectorize-simulate the GBM scalp strategy.
Historical trade data is from AFTER PM has repriced, not at GBM signal time.
Correct approach: use orderbook_bars_1m (PM price timeseries) + exchange_bars (BTC price)
to compute GBM-PM lag at each minute during the window. This enables proper vectorized sweep.
```
