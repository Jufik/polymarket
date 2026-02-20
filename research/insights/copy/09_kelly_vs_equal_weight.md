# Kelly Criterion vs Equal-Weight Allocation

**Date**: 2026-02-19
**Method**: Walk-forward monthly Kelly simulation, $1,500 capital, compounding
**Pool**: 9m consistent, pure_taker, 20+ markets, entry <= 0.80
**Period**: May 2025 - Jan 2026 (9 months)

---

## Key Finding

**Equal-weight (1/N) allocation beats every Kelly parameterization.** Per-trader Kelly fractions estimated from training-period monthly ROI systematically underperform naive equal allocation.

## Results: Comparison Table

| Config | Final Capital | Total PnL | Return | Monthly Sharpe | Max DD |
|--------|------------:|----------:|-------:|-----:|------:|
| **Equal-Weight (1/N)** | **$7,695** | **+$6,195** | **+413%** | **0.97** | **8.1%** |
| Kelly cap=10%, min_mo=4 | $4,872 | +$3,372 | +225% | 0.81 | 12.3% |
| Kelly cap=25%, min_mo=4 | $4,536 | +$3,036 | +202% | 0.76 | 14.1% |
| Kelly cap=50%, min_mo=4 | $4,231 | +$2,731 | +182% | 0.72 | 15.8% |
| Kelly cap=100%, min_mo=4 | $3,987 | +$2,487 | +166% | 0.68 | 17.2% |
| Kelly cap=10%, min_mo=6 | $4,654 | +$3,154 | +210% | 0.79 | 11.5% |
| Kelly cap=25%, min_mo=6 | $4,398 | +$2,898 | +193% | 0.74 | 13.8% |
| Kelly cap=50%, min_mo=6 | $4,102 | +$2,602 | +173% | 0.70 | 16.1% |
| Kelly cap=100%, min_mo=6 | $3,856 | +$2,356 | +157% | 0.66 | 18.0% |

## Why Kelly Underperforms

1. **Edge is uniformly distributed**: The pool is already filtered to 9-month consistent traders. There is no small subset of "obviously better" traders for Kelly to concentrate on. The consistency filter did the heavy lifting — survivors are equally skilled.

2. **Kelly normalization dominates**: Raw Kelly fractions are large (mu/sigma^2 >> 1.0 for most traders), so the normalization step (`sum(f*) -> 1.0`) dominates. The result is a slightly different weighting scheme that concentrates more on a few traders, losing diversification benefit.

3. **Monthly ROI variance is high**: With only 4-6 monthly data points per trader, sigma estimates are noisy. Kelly's sensitivity to sigma^2 amplifies this estimation error. Underestimating sigma by 20% can double the allocation — dangerous with noisy estimates.

4. **Higher Kelly caps = worse performance**: As kelly_cap increases from 10% to 100%, more concentration is allowed, diversification drops, and returns get worse. This confirms the uniform-edge hypothesis.

## Implication

For a pool of pre-filtered consistent traders:
- **Use equal-weight allocation** — it's simpler and outperforms
- Kelly only helps when you have strong prior knowledge that some traders are significantly better than others
- The consistency + entry price + MVF filters already select for quality; further within-pool differentiation adds noise, not signal
- If you want to deviate from 1/N, use trailing ROI ranking (top-5 by recent performance) rather than Kelly fractions
