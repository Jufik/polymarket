# GBM Flip Stop — Baseline False-Stop Analysis

**Date**: 2026-03-10
**Dataset**: BTC 15-min markets, Sep 2025 – Mar 2026
**Note**: UPPER BOUNDS — vectorized simulation using actual PM trade prices (ASOF-joined to exchange bars). No live orderbook simulation.

## Setup

- 16,176 resolved 15-min BTC markets
- Strategy fires on 15,934 markets (98.5% hit rate — most windows trigger a signal)
- Entry: first bar where |GBM P(Up) - PM P(Up)| > effective threshold (time-decaying, fee-aware)
- PM price proxy: most recent trade price at each 1-second bar (ASOF join)
- Production exit logic replicated: time_stop > flip_stop > trailing_stop > convergence

## Baseline (flip_threshold=0.35, delay=1)

| Metric | Value |
|--------|-------|
| Total signals | 15,934 |
| Overall hit rate | 79.9% |
| Avg PnL per trade | +0.1066 (relative) |
| Total PnL | +1,698 |

### Exit type breakdown

| Exit Type | Count | % |
|-----------|-------|---|
| trailing_stop | 12,900 | 81.0% |
| flip_stop | 2,215 | 13.9% |
| hold_resolution | 814 | 5.1% |
| time_stop | 5 | 0.0% |

Key insight: **81% of exits are trailing stops** — the flip stop is a secondary mechanism that affects only 14% of positions. The trailing stop is doing most of the work.

## False Stop Analysis

| Metric | Value |
|--------|-------|
| Flip-stop exits | 2,215 (13.9% of all exits) |
| False stops | 1,302 (58.8% of flip exits) |
| True stops | 913 (41.2% of flip exits) |
| Avg PnL lost per false stop | +0.082 (counterfactual would have gained this) |
| Avg PnL saved per true stop | +0.122 (loss avoided per correct stop) |
| Net flip stop value | +5.04 total PnL units |

**Key finding: 58.8% of flip exits are false stops.** Despite this majority being false, the flip stop still has a small net positive value (+5.04 total) because true stops save more per event (+0.122) than false stops lose (+0.082).

The flip stop is marginally beneficial but barely above its counterfactual. This matches the paper trading observation of 238 flip exits — they're happening too often.

## Sigma Regime Breakdown

| Regime | N | Flip Exits | Flip% | HR_baseline | HR_no_flip | PnL_baseline | PnL_no_flip |
|--------|---|-----------|-------|-------------|------------|--------------|-------------|
| Low (σ < 0.000273) | 5,258 | 602 | 11% | 78.5% | 77.8% | +0.126 | +0.117 |
| Mid (0.000273–0.000489) | 5,418 | 544 | 10% | 82.9% | 85.8% | +0.104 | +0.105 |
| High (σ > 0.000489) | 5,258 | 1,069 | 20% | 78.0% | 87.7% | +0.090 | +0.097 |

**Critical finding: The flip stop HURTS in the mid and high sigma regimes.**

- **High vol**: baseline 78.0% HR vs no-flip 87.7% HR (+9.7pp difference). The flip stop is cutting winners in volatile windows where BTC oscillates below 0.35 then recovers.
- **Mid vol**: baseline 82.9% HR vs no-flip 85.8% HR (+2.9pp difference). Similar but smaller.
- **Low vol**: flip stop helps slightly (78.5% vs 77.8%). In low-vol windows, flips below 0.35 are more likely to be genuine reversals.

The flip stop at 0.35 is calibrated for low-vol behavior but fires too aggressively in high-vol markets.
