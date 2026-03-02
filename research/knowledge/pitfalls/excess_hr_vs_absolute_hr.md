# Absolute HR Is Misleading Without Base Rate Context

> **TL;DR**: A 70% tick-by-tick HR looks strong but is actually 17pp BELOW the NO base rate for categories like culture (88.6%) and weather (85.9%). Always compute excess HR per category.

> [!WARNING]
> Never evaluate a strategy by absolute HR alone. Culture 70.5% HR is WORSE than naive "always bet NO" (88.6%). Only sports (+13.5pp NO excess) and finance (+5.4pp) show genuine tick-by-tick alpha in the insider copy strategy.

> [!TIP]
> For each category, compute `excess_hr = (yes_frac * (yes_hr - yes_base)) + (no_frac * (no_hr - no_base))`. If negative, the strategy is underperforming a naive direction-only baseline.

## Finding

Per-tag tick-by-tick validation of the S2 insider copy strategy revealed a striking pattern: most categories show 65-72% absolute HR (seemingly strong) but NEGATIVE excess HR when measured against category-specific NO base rates.

| Category | Tick HR | NO Base | NO HR | NO Excess | Absolute Looks | Reality |
|----------|---------|---------|-------|-----------|----------------|---------|
| sports | 74.3% | 61.3% | 74.8% | +13.5pp | Great | Genuinely great |
| culture | 70.5% | 88.6% | 71.2% | -17.4pp | Good | Worse than naive NO |
| weather | 71.6% | 85.9% | 72.3% | -13.6pp | Good | Worse than naive NO |
| other | 69.8% | 70.4% | 71.8% | +1.4pp | Good | Barely above baseline |
| politics | 69.3% | 75.5% | 70.4% | -5.1pp | Good | Below baseline |
| finance | 66.7% | 62.3% | 67.7% | +5.4pp | Moderate | Genuinely above baseline |

The positive PnL despite negative excess HR comes from entry at very low prices (mean 0.22-0.35), creating asymmetric payoffs. A $50 bet at price 0.22 pays $177 on a win vs losing $50. This asymmetry means the strategy can be profitable even at BELOW-RANDOM accuracy.

## Evidence

Per-tag tick-by-tick validation: `research/scripts/s2_tick_tag_validation.py`
Output: `research/output/s2_tick_tag/per_tag_all.parquet`

## Impact

- **Strategy evaluation**: ALWAYS compute per-category excess HR, not overall HR
- **Category selection**: Only deploy in categories with positive excess HR for genuine alpha (sports, finance)
- **PnL source**: for categories with negative excess, PnL comes from entry price asymmetry, not prediction quality
- **Sustainability risk**: asymmetric payoff strategies are more vulnerable to slippage and execution degradation than genuine alpha strategies

## Related

- `signals/insider_tag_tuning.md` -- per-tag validation results
- `data/market_base_rates.md` -- overall base rates
- `data/period_base_rate_variance.md` -- base rates vary by period
- `pitfalls/vectorized_vs_tick.md` -- vectorized masks the excess HR issue

## Tags

`base-rate`, `excess-hr`, `per-category`, `evaluation`, `pitfall`, `insider`
