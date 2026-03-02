# Bayesian HR Shrinkage is the Only Universally Positive S2 Improvement

> **TL;DR**: Bayesian HR with alpha+beta=10 improves OOS HR by +1pp and PnL by +6-79% across all test periods while eliminating NO pool contamination.

> [!TIP]
> Use Bayesian HR shrinkage for any trader qualification based on hit rate. It is the single most effective improvement for the S2 hit-rate copy strategy, eliminating contamination without destroying pool size.

## Finding

Testing 4 vectorized improvements to S2 hit-rate copy strategy in isolation:

| Improvement | Apr HR delta | Jul PnL change | Pool change | Verdict |
|------------|-------------|---------------|-------------|---------|
| Entry price (<0.85) | -6.6pp | -$1.87M | -83% | Too aggressive |
| Category exclusion | ~0pp | ~$0 | -0.5% | Broken (m.category NULL) |
| Adaptive base rates | -3.7pp | -$492K | +10% YES, -20% NO | Dilutes quality |
| **Bayesian HR** | **+1.0pp** | **+$602K** | **-5%** | **Best single change** |

Bayesian HR formula: `(alpha + wins) / (alpha + beta + total)` with direction-specific
priors (YES: alpha=3.81, beta=6.19; NO: alpha=6.19, beta=3.81).

Key effects:
- NO contamination: 43.2% -> 0.0% (HR >= 95% traders eliminated)
- YES contamination: 13.3% -> 0.0%
- Pool size: only 5% reduction (removes noise, not signal)
- July 2025 PnL: $758K -> $1.36M (+79%)

The improvements interact DESTRUCTIVELY when combined:
- Entry price + Bayesian double-penalizes small-sample traders
- Adaptive BR + entry price removes most NO traders
- All 4 combined: HR drops 13-21pp, total PnL drops $9.6M

## Evidence

Walk-forward OOS at consensus >= 4, 3 periods:

```
BEFORE:     Apr 80.3%/$3.47M | Jul 78.0%/$758K  | Oct 78.0%/$6.89M
Bayes-only: Apr 81.3%/$3.68M | Jul 79.3%/$1.36M | Oct ~79%/~$7M+
All-AFTER:  Apr 67.4%/$1.75M | Jul 61.4%/-$1.87M| Oct 57.4%/$1.63M
```

## Impact

- **S2 strategy**: Use `use_bayesian_hr=True` as the primary improvement
- **Entry price**: Raise from 0.85 to 0.92+ or disable entirely
- **Adaptive BR**: Keep conceptually but tune interaction with Bayesian
- **General**: For any trader qualification, Bayesian shrinkage > raw HR thresholds

## Related

- `signals/no_pool_contamination.md` -- the problem Bayesian shrinkage solves
- `pitfalls/category_column_null.md` -- why category exclusion does nothing
- `data/period_base_rate_variance.md` -- why adaptive BR matters in principle

## Tags

`bayesian`, `trader-qualification`, `hit-rate`, `S2`, `improvement`, `contamination`
