# Composite Strategy: 4 Sub-Strategies & Next Steps

**Date**: 2026-02-19
**Based on**: Insights #01-#14 + StratRegfinement + Fav-Longshot research
**Capital**: $1,500 deployed

---

## 3 Active Sub-Strategies + 1 Killed

### S1. Proportional Copy of Longshot YES Specialists (PRIMARY) — HIGH confidence

**Signal**: Sizing alpha from graded trader pool
**Evidence**: 9/9 win months walk-forward, Spearman r=+0.58

| Parameter | Value | Source |
|-----------|-------|--------|
| Pool | 9m consistent, pure_taker, entry<=0.90 | #02, #03, #12 |
| Grade filter | longshot_yes_fraction > 15% | #14 |
| Allocation | Equal-weight (1/N) | #09 (beats Kelly) |
| Contradictions | Skip contradicted markets | #10 (+22%) |
| Compounding | Yes | #11 (+413% vs +188% flat) |
| Lockup | Median 21 hours, P75 = 5 days | Lockup analysis |
| Expected (upper bound) | ~26%/mo, apply 50% haircut → ~13%/mo | #14 |
| Capital allocation | $1,000 of $1,500 | Primary strategy |

### S2. Favorite-Longshot NO on "Will" Binary Questions (STRUCTURAL) — HIGH confidence

**Signal**: Market structure — "Will X happen?" is overpriced on YES
**Evidence**: 13-month walk-forward, 11/13 profitable, Sharpe 2.43

| Parameter | Value | Source |
|-----------|-------|--------|
| Filter | Binary "Will" question, YES price 15-40% | #17 (optimized from 10-50%) |
| Direction | Always NO (dual: buy NO + sell YES) | #18, #20 |
| Bet size | $50-100/side, dual-sided | #20 |
| Fast filter | <3d lockup, "above"/"below" keywords, vol <$5K | #17 |
| Lockup | Median 1 day (fast filter) | #17 |
| Expected | ~$690/mo at $300, ~$3,150/mo at $2K, ceiling ~$6,700/mo at $10K | #20 |
| Capital allocation | $300 initial → scale to $2K-5K as S1 compounds | #20 |

Fully orthogonal to S1 — uses no trader data. Dual-sided (buy NO + sell YES) accesses 2x the liquidity per market. Capacity ceiling ~$6,700/mo at ~$10K capital; saturates beyond that. See insights #17-#20.

### S3. Consensus NO (Fixed Bets, Direction Signal) — MEDIUM confidence

**Signal**: When >=5 pure_taker traders agree NO, bet NO at fixed $100
**Evidence**: Sharpe 3.5-5.0, but only 2 holdout windows

| Parameter | Value | Source |
|-----------|-------|--------|
| Pool | 6-9m consistent, pure_taker | #06 |
| Min traders | 5-7, agreement >= 60% | #06 top configs |
| Direction | NO-only | #06 (YES is anti-predictive) |
| Delay | 60s | #07 (signal improves) |
| Price band | Wide [0.05, 0.95] | #06 |
| Capital allocation | $200 (2 concurrent $100 bets) | Building validation |

### ~~S4. Anti-Consensus YES → Bet NO~~ — KILLED

**Status**: Investigated and killed. See insight #16.
**Reason**: Signal is statistically real (+34pp edge above base) but fires 0-2 times/month with $6.50 payoff per bet. Total edge: ~$58 over 13 months. The pool is too small for YES consensus to form. Capital reallocated to S1.

---

## How They Interact

| | S1 Proportional | S2 Fav-Longshot | S3 Consensus NO | S4 Anti-YES |
|---|:---:|:---:|:---:|:---:|
| Signal source | Individual trader ROI | Market structure | Crowd NO direction | Inverse crowd YES |
| Bet type | Proportional size | Fixed small | Fixed $100 | Fixed $100 |
| Direction | Follows trader | Always NO | NO consensus | NO (inverse YES) |
| Markets | All pool trades | "Will" binary only | Consensus-NO mkts | Consensus-YES mkts |
| Overlap | LOW | NONE | LOW | LOW |

S1 and S2 are fully independent. S3 and S4 cover non-overlapping markets (NO consensus vs YES consensus). S1 may occasionally overlap with S3/S4 on the same market but with different sizing.

---

## Capital Allocation ($1,500)

| Priority | Strategy | Capital | Rationale |
|:--------:|----------|--------:|-----------|
| 1 | S1: Proportional copy | $1,000 | Highest confidence, compounds |
| 2 | S2: Fav-longshot NO | $300 | Independent edge, fast rotation |
| 3 | S3: Consensus NO | $200 | Builds validation data |
| ~~4~~ | ~~S4: Anti-YES~~ | ~~$0~~ | KILLED — zero capacity (#16) |

Scale S2 and S3 as S1 compounds and bankroll grows past $3K.

---

## Validation Backlog

### Immediate (blocking deployment)
1. ~~**S4 base-rate control**~~: DONE — signal is real (+34pp) but zero capacity. S4 killed. See #16.

### Before scaling
3. **S1+S2 overlap**: When S1's pool trades in "Will" binary markets, does S2 add independent edge?
4. **S3 extended holdout**: Validate across 6+ windows as data grows (currently only 2)
5. **Combined equity curve**: Simulate S1+S2+S3 running simultaneously with proper capital partitioning

### Live execution prep
6. **Execution price validation**: Compare backtest entry prices vs achievable prices via CLOB API
7. **Trader detection latency**: How fast can we detect a pool trader's new position? (WebSocket vs polling)
8. **Market identification for S2**: Automate "Will" question detection from market text
9. **Capacity testing**: Does $1,500 move prices in the markets S1 trades?
