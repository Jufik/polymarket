# S4 Anti-Consensus YES → Bet NO: KILLED (Zero Capacity)

**Date**: 2026-02-19
**Method**: Walk-forward monthly holdout, Jan 2025 - Jan 2026 (13 windows)
**Pool**: 9m consistent, pure_taker, 20+ markets, entry <= 0.90

---

## Summary

The anti-consensus YES signal (when >=N skilled traders agree YES, bet NO) is statistically real but has **zero practical capacity**. It fires 0-2 times per month with ~$6.50 payoff per correct bet. Total edge over 13 months: ~$20. Not a strategy.

## Evidence

### Bet count is near-zero

| Min Traders | Agreement | Total Bets (13mo) | Bets/Month |
|:-----------:|:---------:|:-----------------:|:----------:|
| 3 | 60% | 26 | ~2 |
| 5 | 60% | 3 | ~0.2 |
| 5 | 80% | 1 | — |
| 7 | any | 0 | — |

The pool (53-77 traders) is too small for >=5 to independently converge on YES in the same market. Even at min_traders=3, only 26 bets fire across 6 active windows.

### Hit rate is high but payoff is tiny

At min_traders=3, agree>=60%: **96.2% NO hit rate** vs 61.9% base (+34.3pp edge). The signal is real.

But the YES consensus forms on ultra-cheap YES tokens (median 6.3c entry). The NO side costs **93.7c**, so a correct NO bet profits only **$6.50 per $100** risked. Even at 96% HR: expected PnL = 0.96 × $6.50 - 0.04 × $100 = **$2.24/bet**.

Total: 26 bets × $2.24 = **$58 over 13 months**.

### Why the original "18.5% YES HR" seemed actionable

Insight #06 reported 18.5% YES HR across ~1,400 configs using wider pools (up to 4,934 traders), looser consistency (6-month), and lower min_markets (10). That pool generates far more consensus signals. Our operational pool (9m, 20+ mkts, pure_taker, entry<=0.90) is 53-77 traders — too small for consensus to form on the YES side.

## Root Cause

The consistent pure_taker pool is **structurally NO-biased** (insight #14 notwithstanding — even the longshot YES specialists buy YES on a minority of markets). Consensus YES requires multiple independently NO-leaning traders to all agree YES on the same market. This is a 3-sigma event in our pool.

## Decision

**S4 is killed.** Capital reallocated to S1 (proportional copy) which generates 200+ positions per month across 20-44 traders.

S3 (consensus NO) has a similar capacity problem (9 bets over 2 windows at min_traders=5) but remains on watch because the payoff per bet is higher (NO side is cheap when consensus is NO).
