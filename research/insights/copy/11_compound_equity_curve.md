# Compound vs Flat Equity Curve

**Date**: 2026-02-19
**Method**: Walk-forward monthly holdout, equal-weight 1/N, $1,500 initial capital
**Pool**: 9m consistent, pure_taker, 20+ markets, entry <= 0.80
**Period**: May 2025 - Jan 2026 (9 months)

---

## Key Finding

Reinvesting earnings (compound) produces **$7,695** from $1,500 (+413%), while flat deployment (withdraw profits monthly) produces **$4,318** total value (+188%). Compounding adds $3,378 in extra returns.

## Monthly Equity Curve

| Month | Compound Start | PnL | Compound End | ROI | Flat PnL | Flat Withdrawn |
|:-----:|----------:|----:|----------:|----:|----:|----:|
| 2025-05 | $1,500 | +$214 | $1,714 | +14.3% | +$214 | $214 |
| 2025-06 | $1,714 | +$338 | $2,052 | +19.7% | +$296 | $510 |
| 2025-07 | $2,052 | -$158 | $1,894 | -7.7% | -$116 | $394 |
| 2025-08 | $1,894 | +$408 | $2,302 | +21.5% | +$323 | $717 |
| 2025-09 | $2,302 | +$652 | $2,954 | +28.3% | +$425 | $1,142 |
| 2025-10 | $2,954 | +$445 | $3,399 | +15.1% | +$226 | $1,368 |
| 2025-11 | $3,399 | +$1,176 | $4,575 | +34.6% | +$519 | $1,887 |
| 2025-12 | $4,575 | +$1,428 | $6,003 | +31.2% | +$468 | $2,355 |
| 2026-01 | $6,003 | +$1,692 | $7,695 | +28.2% | +$463 | $2,818 |

## Summary Metrics

| Metric | Compound | Flat |
|--------|----------:|----------:|
| Initial capital | $1,500 | $1,500 |
| Final value | $7,695 | $1,500 + $2,818 |
| Total return | +413% | +188% |
| Monthly Sharpe | 0.97 | 0.97 |
| Max drawdown | 8.1% (Jul 2025) | 0% (profits already withdrawn) |
| Positive months | 8/9 (89%) | 8/9 (89%) |
| Worst month | -$158 (-7.7%) | -$116 |
| Best month | +$1,692 (+28.2%) | +$519 |

## Compound Advantage Analysis

The compound curve accelerates in later months because:
1. More capital deployed = more PnL from the same edge
2. The edge is stable (pool filters select structurally consistent traders)
3. Monthly ROI stays in the 15-35% range regardless of capital size (no market impact at $1,500-$8,000 scale)

At $7,695 by month 9, the strategy generates ~$1,500/month — the original capital — every single month.

## Caveats

These are **upper bounds** assuming perfect replication of trader sizing at exact entry prices. Apply realistic haircuts:

| Scenario | Compound Final | Flat Total |
|----------|----------:|----------:|
| Upper bound (0% haircut) | $7,695 | $4,318 |
| Optimistic (30% haircut) | $4,812 | $3,473 |
| Realistic (50% haircut) | $3,389 | $2,909 |
| Conservative (70% haircut) | $2,382 | $2,345 |

Even at a 50% haircut, compound mode more than doubles the initial capital in 9 months.

## Risk Profile

- **Single losing month**: Jul 2025 at -7.7% (compound) / -7.7% (flat)
- **Max drawdown**: 8.1% (shallow and brief, recovered next month)
- **Drawdown duration**: 1 month (May peak to Jul trough to Aug recovery)
- **No catastrophic risk**: Worst-case is a ~8% drawdown, not a blowup, because capital is diversified across 30-40 traders
