# Absolute HR Is Misleading Without Base Rate Context

> **TL;DR**: A 70% tick-by-tick HR looks strong but may actually be BELOW the NO base rate for categories like culture (88.3%) and weather (85.9%). Always compute excess HR per category.

> [!TIP]
> For each category, compute `excess_hr = (yes_frac * (yes_hr - yes_base)) + (no_frac * (no_hr - no_base))`. If negative, the strategy is underperforming a naive direction-only baseline.

## Finding

Positive PnL despite negative excess HR can come from entry at very low prices (mean 0.20-0.35), creating asymmetric payoffs. A $50 bet at price 0.22 pays $177 on a win vs losing $50. This asymmetry means a strategy can be profitable even at BELOW-RANDOM accuracy.

## Evidence

Compute per-tag tick-by-tick validation with tag-specific base rates from `data/tag_base_rates.md`. Compare strategy HR against the tag's base rate, not the global 38/62.

## Impact

- **Strategy evaluation**: ALWAYS compute per-category excess HR, not overall HR
- **Category selection**: Only deploy in categories with positive excess HR for genuine alpha
- **PnL source**: for categories with negative excess, PnL comes from entry price asymmetry, not prediction quality
- **Sustainability risk**: asymmetric payoff strategies are more vulnerable to slippage and execution degradation than genuine alpha strategies

## Related

- `data/market_base_rates.md` -- overall base rates
- `data/tag_base_rates.md` -- tag-specific base rates vary 9-73% YES
- `data/period_base_rate_variance.md` -- base rates vary by period
- `pitfalls/vectorized_vs_tick.md` -- vectorized masks the excess HR issue

## Tags

`base-rate`, `excess-hr`, `per-category`, `evaluation`, `pitfall`
