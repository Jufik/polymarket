# Entry Price Filter Inverts Between Vectorized and Tick-by-Tick

> **TL;DR**: An entry price filter (< 0.65) improves vectorized HR by +2.7pp but DECREASES tick-by-tick HR by 9pp. The filter's effect flips because tick-by-tick enters at the specific trade price while vectorized uses a blended average.

> [!WARNING]
> Do not assume vectorized-optimal parameters transfer to tick-by-tick. Price filters in particular can invert because the entry mechanism differs fundamentally between the two modes.

## Finding

In vectorized backtests, filtering to positions with avg_yes_price < 0.65 improved HR from 83.2% to 85.9% (+2.7pp) and PnL/pos from $45 to $72. In tick-by-tick, the same filter REDUCED HR from 64.4% to 57.3% (-7.1pp), though PnL was 6x higher ($784K vs $131K).

The mechanism:
1. **Vectorized** uses avg_yes_price (blended across all trades). Filtering < 0.65 removes positions where insiders ultimately averaged in at high prices -- these tend to be less profitable.
2. **Tick-by-tick** enters at the specific trade price where consensus triggers. The < 0.65 filter rejects the trade if THAT SPECIFIC trade's price is >= 0.65, even if many other insiders already entered at lower prices. This rejects ~50% of signals that would have been profitable.
3. The filtered tick-by-tick entries are at very low prices (mean 0.22) vs unfiltered (mean 0.35). Low entries have enormous per-position upside (PnL = (1 - entry) * qty) but fewer of them, reducing overall HR.

| Mode | No Filter | Price < 0.65 | Delta |
|------|-----------|-------------|-------|
| Vectorized HR | 83.2% | 85.9% | +2.7pp |
| Tick-by-tick HR | 64.4% | 57.3% | -7.1pp |
| Tick-by-tick PnL | $131K | $784K | +6x |

## Evidence

Tick-by-tick validation script: `research/scripts/s2_tick_validation.py`
Output: `research/output/s2_tick/monthly_consensus3_price*.parquet`

## Impact

- **Parameter optimization**: always validate filters in tick-by-tick before trusting vectorized results
- **Price filter tradeoff**: the filter creates a HR-vs-PnL tradeoff that doesn't exist in vectorized
- **General principle**: any filter that depends on per-trade price vs per-position average will behave differently between vectorized and tick-by-tick
- **Deployment choice**: use price filter for max PnL (high-risk, high-reward), remove for max HR (stable)

## Related

- `pitfalls/vectorized_vs_tick.md` -- this is a new Gap #10 (parameter inversion)
- `signals/insider_copy.md` -- full validation results
- `data/period_base_rate_variance.md` -- base rate variance amplifies the inversion

## Tags

`price-filter`, `vectorized-vs-tick`, `parameter-inversion`, `entry-price`, `simulation-gap`
