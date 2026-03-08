# Direction Decomposition Required for All Strategy Results

> **TL;DR**: Aggregate HR is meaningless without decomposing into YES-signal HR and NO-signal HR against their respective base rates. Crypto's "74.5% HR" was pure structural NO bias — both directions underperformed their base rates.

> [!CRITICAL]
> Every strategy result MUST report YES and NO signals separately: YES HR vs YES base rate, NO HR vs NO base rate. Aggregate HR conflates direction quality with direction mix. A strategy that only bets NO in a 85% NO-win period looks great at aggregate but has zero edge.

## Finding

Smart Money Pool Crypto tick validation:
- Aggregate: 74.5% HR, +60pp above YES base rate (14.2%) — looks amazing
- YES signals: N=50, HR=12.0% vs 14.2% base → **-2.2pp below base**
- NO signals: N=699, HR=79.0% vs 85.8% base → **-6.8pp below base**

Both directions UNDERPERFORM naive betting. The 74.5% aggregate is entirely explained by the 93/7% NO/YES signal mix matching a bearish period.

## Evidence

Direction decomposition script: `research/hypotheses/scorecard-strategies/scripts/tick_direction_decomp.py`

## Impact

- **All prior vectorized results** should be re-checked with direction decomposition
- **Regime-dependent strategies** (trained on 48% YES, tested on 14% YES) will show this pattern
- **Excess HR computation** must be per-direction: `yes_excess = yes_hr - yes_base; no_excess = no_hr - no_base`

## Related

- `data/tag_base_rates.md` — base rates vary by tag
- `data/period_base_rate_variance.md` — base rates vary by period
- `pitfalls/vectorized_vs_tick.md` — another source of inflated results

## Tags

`direction`, `decomposition`, `base-rate`, `critical`, `methodology`
