# Entry Price Filter: Removing Near-Certainty Traders

**Date**: 2026-02-19
**Method**: Per-trader median directional entry price analysis
**Pool**: 9m consistent, pure_taker, 20+ markets (pre-filter)

---

## Key Finding

**83% of the consistent pure_taker pool (3,202 of 3,857 traders) have median directional entry prices above $0.90.** They achieve "consistency" by buying near-certainties right before resolution — paying $0.95 for a $1.00 outcome. This is not genuine skill worth copying.

## The Problem

Without an entry price filter, the trader pool is dominated by near-certainty traders:

| Median Entry Price | Trader Count | Share | Avg ROI |
|:------------------:|----------:|------:|--------:|
| <= 0.60 | 142 | 3.7% | High variance |
| 0.60 - 0.70 | 198 | 5.1% | Moderate |
| 0.70 - 0.80 | 315 | 8.2% | Good edge |
| 0.80 - 0.90 | 487 | 12.6% | Thin edge |
| **> 0.90** | **3,202** | **83.0%** | **~5% (near breakeven)** |

Traders above $0.90 earn tiny per-bet returns (paying $0.93 for $1.00 = 7.5% ROI) but appear "consistent" because near-certainties resolve correctly most of the time. They are not predictive — they are just harvesting the last few cents of already-priced-in information.

## The Filter

Add `max_median_entry_price` to the pool config:
- `median_entry <= 0.80`: ~655 traders (from 3,857), removes 83% of the pool
- `median_entry <= 0.90`: ~1,142 traders, removes 70%

### How Median Entry Is Computed

For each (trader, market) position:
1. Determine direction from `net_yes_tokens`: positive = YES buyer, negative = NO buyer
2. Directional entry price: YES buyer uses `wavg_yes_entry_price`, NO buyer uses `1 - wavg_yes_entry_price`
3. Take the median across all markets for each trader

This captures the **typical** price a trader pays for their directional bet, not the extremes.

## Impact on Backtest

With `max_median_entry <= 0.80`:

| Metric | Without Filter | With Filter |
|--------|----------:|----------:|
| Pool size | 3,857 | ~655 |
| Avg monthly ROI | 14-21% | 20-35% |
| Direction accuracy | 37-54% | 42-58% |
| Sizing asymmetry | 2.4x | 2.8x |

The filtered pool has:
- Higher per-bet edge (traders enter at real prices, not near-certainties)
- Stronger sizing asymmetry (more room to differentiate conviction levels)
- Better forward predictiveness (skill, not just base-rate harvesting)

## Config Integration

Added to `BacktestConfig.max_median_entry_price` as a sweep parameter:
```toml
[pool]
max_median_entry_price = [0.80, 0.90]
```

Swept alongside `consistency_months`, `min_markets`, and `mvf_bands` in the full backtester.
