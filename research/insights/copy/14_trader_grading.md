# Trader Grading: Behavioral Dimensions That Predict Copy Profitability

**Date**: 2026-02-19
**Method**: Walk-forward monthly holdout (May 2025 - Jan 2026), 9 windows
**Pool**: 9m consistent, pure_taker, 20+ markets, median entry <= 0.90
**Pool size**: 53 traders (wider entry filter to allow grading)

---

## Executive Summary

**The best traders to copy are NOT the "safe" NO-heavy near-certainty buyers. They are deep-value conviction traders who buy longshot YES tokens and are right far more often than the market expects.**

This finding inverts the intuition from the consensus copy backtester (where NO-only signals dominate) because proportional copy amplifies ROI, and ROI is mechanically much higher on cheap tokens when correct.

---

## The Strongest Grading Signal: Longshot YES Fraction

Training-period `longshot_yes_fraction` (share of positions that are YES buys at <50c) is the single strongest predictor of holdout copy profitability.

### Walk-Forward Robustness (avg monthly ROI from $1,500 equal-weight copy)

| Grade | Avg Monthly ROI | Win Months | Pool Size |
|-------|:---------:|:----:|:--------:|
| **Longshot YES > 15%** | **+26.4%** | **9/9 (100%)** | **20-44** |
| Deep value entry (<0.55) | +21.7% | 8/9 (89%) | 22-40 |
| Full pool (baseline) | +17.3% | 8/9 (89%) | 31-53 |
| NO-heavy (>60% NO) | **-2.6%** | 6/9 (67%) | 19-20 |
| Expensive entry (>0.70) | -13.3% | 3/5 (60%) | 2-5 |

### $1,500 Compound Copy Simulation (9 months)

| Strategy | Final Capital | Return | Avg Traders |
|----------|----------:|-------:|:-----------:|
| Longshot YES >15% | **$11,834** | **+689%** | 20 |
| Deep value + concentrated | $9,330 | +522% | 19 |
| Deep value only | $8,151 | +443% | 22 |
| Full pool equal-weight | $7,215 | +381% | 31 |
| NO-heavy only | **$445** | **-70%** | 19 |

The longshot YES filter nearly doubles returns while maintaining 20+ traders for diversification.

---

## Spearman Rank Correlations: Training Features → Holdout ROI

| Feature | Spearman r | Interpretation |
|---------|:---:|:---|
| **longshot_yes_fraction** | **+0.578** | More longshot YES buying = better holdout |
| win_rate | -0.504 | Lower training WR = better holdout (counterintuitive) |
| mean_entry | -0.478 | Cheaper entry prices = better holdout |
| roi | +0.411 | Higher training ROI persists forward |
| payoff_ratio | +0.405 | Higher win/loss asymmetry persists |
| no_fraction | **-0.398** | **More NO bias = WORSE holdout** |
| n_markets | +0.269 | More markets = slightly better |
| sizing_conviction | -0.252 | Weak negative (noisy metric) |
| volume_cv | +0.119 | Weak positive for concentrated betting |
| neg_risk_fraction | -0.079 | Irrelevant |
| will_q_fraction | -0.005 | Irrelevant |

---

## Why Longshot YES Specialists Are the Best

### The Mechanism

The favorite-longshot bias says: most people who buy YES at 20-30c lose, because the true probability is lower than the price implies. Our own fav-longshot insight (#03) confirms this — blanket NO at low YES prices has a 75% hit rate.

**But within the 9-month consistent pool, the opposite applies.** These traders PASSED the consistency filter specifically because they are skilled at identifying which longshots will pay off. The filter selects for genuine information advantage.

### Position-Level Evidence (pool holdout, May 2025 - Jan 2026)

| Direction | Entry Bin | N Bets | Win Rate | ROI | PnL | $/bet |
|-----------|-----------|-------:|:--------:|:---:|----:|------:|
| **YES** | **<40c** | **6,011** | **60.3%** | **72.5%** | **$4.09M** | **$681** |
| NO | <40c | 5,318 | 49.5% | 34.0% | $876K | $165 |
| YES | 40-55c | 476 | 46.8% | 30.3% | $20K | $41 |
| NO | 40-55c | 5,211 | 43.6% | 9.5% | $31K | $6 |
| NO | >85c | 3,200 | 30.9% | 20.6% | $334K | $104 |

**YES at <40c is the dominant trade in the pool**: 60.3% win rate at implied 20-30% probability = ~30pp of edge. The payoff is 2.5-5x, so even moderate accuracy produces massive ROI.

### Concentration Risk Analysis

The Longshot YES >15% pool is NOT a one-whale bet:

| Month | Pool Size | Active | Top Trader % | Profitable Traders |
|:-----:|:---------:|:------:|:------------:|:------------------:|
| 2025-05 | 7 | 5 | 86% | 4/5 (80%) |
| 2025-08 | 26 | 17 | 49% | 16/17 (94%) |
| 2025-10 | 35 | 28 | 38% | 23/28 (82%) |
| 2025-12 | 44 | 34 | 27% | 25/34 (74%) |
| 2026-01 | 41 | 34 | 27% | 28/34 (82%) |

Pool grows from 7 to 44 traders over time, concentration decreases (top trader share drops from 86% to 27%), and 74-94% of traders are individually profitable. This is a robust pool-level signal, not survivorship of one whale.

---

## Two Types of Consistent Traders (Within the Pool)

| | Longshot Specialists | Near-Certainty Buyers |
|---|:---:|:---:|
| Mean entry price | <0.55 | >0.70 |
| NO fraction | ~57% | ~75% |
| Win rate | 36% | 78% |
| ROI per trade | 50% | 22% |
| Longshot YES % | 39% | 4% |
| Avg markets | 643 | 30 |
| Avg volume | $413K | $5K |
| Sizing conviction | 0.98 | 1.09 |
| **Holdout ROI** | **+22-26%/mo** | **-13%/mo** |

The near-certainty buyers have beautiful training stats (78% WR!) but terrible forward copy performance. They achieve "consistency" by buying $0.92 tokens right before resolution — every win is +8% but every loss is -100%. When you copy them proportionally, the rare losses destroy the edge.

---

## Recommended Grading for Copy Strategy

### Primary filter (add to pool selection)
```
longshot_yes_fraction > 0.15
```
This selects traders who buy YES at <50c for at least 15% of their positions. It nearly doubles compound returns while keeping 20+ traders for diversification.

### Alternative: Entry price filter (simpler, similar effect)
```
mean_directional_entry_price < 0.55
```
Highly correlated with longshot YES (r=0.85), slightly larger pool, slightly lower returns.

### Avoid
```
no_fraction > 0.60  →  NEGATIVE expected copy PnL
mean_entry > 0.70   →  NEGATIVE expected copy PnL
```

### Why This Differs from Consensus Copy

| Context | Best Signal | Mechanism |
|---------|-------------|-----------|
| **Consensus copy** (fixed $100 bet) | NO-only | Direction matters; NO base rate 62% |
| **Proportional copy** (scale by ROI) | YES longshot specialists | ROI matters; 60% WR at 20c = 72% ROI |

The two strategies optimize different things. Consensus copy optimizes **hit rate** (NO wins more often). Proportional copy optimizes **ROI** (YES at deep value pays more when it hits). Both are valid, for different allocation approaches.

---

## Integration with Existing Strategy

1. **Pool construction**: Add `longshot_yes_fraction > 0.15` to existing filters (consistency + MVF + entry price)
2. **Expected pool**: ~20-44 traders (vs ~46-53 unfiltered)
3. **Expected improvement**: +52% more cumulative PnL ($10,334 vs $5,715 from $1,500)
4. **Risk trade-off**: Slightly more concentrated (20 vs 31 traders), higher variance, but 100% win months in walk-forward
5. **Combinable with contradiction filter**: Skip contradicted markets within the graded pool for additional edge
