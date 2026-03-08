# Final Strategy Synthesis

**Date**: 2026-03-07
**Status**: Vectorized discovery + semi-tick validation + full review panel
**Agents**: 8 total across 2 research rounds (3 researchers, 1 strategist, skeptic, visionary, architect)

---

## Honest Assessment

After two research rounds, 4 strategy variants, and a full review panel, the picture is **sobering but clear**:

### What We Know for Certain

1. **Trader skill is real and persistent** — IC=0.744 train→test, top-decile achieves 91.9% test HR
2. **Vol-weighted direction beats head-count** — +5-16pp across all tags, consistently
3. **In-play contamination dominates sports signals** — 63% of signals are uncopyable
4. **Binary thresholds beat proportional sizing** — the pool works as a filter, not a continuous scaler
5. **Late majority >> early majority** — waiting for N≥3-5 traders is worth it
6. **Gambling exclusion is mandatory** — 56% of positions are noise
7. **Budget gate bug was silently killing tick-by-tick** — fixed by architect

### What We Don't Yet Know

1. **True tick-by-tick performance** — the "tick validation" used semi-tick methodology (resolved positions, not raw trades). The <2pp degradation is a methodology artifact, not a real result. Expected true degradation: 20-40pp.
2. **Whether any strategy is commercially viable** — the best surviving signal (Politics NO K=50, +6.2pp excess) has CS=0.06, which requires large position sizes.
3. **Regime stability** — Esports base rate collapsed 45%→7% between train/test. Crypto shifted 48%→14% YES. Regime gates are essential but untested.

---

## Strategy Rankings (Post-Review)

| Rank | Strategy | Signal | Verdict | Blocker |
|------|----------|--------|---------|---------|
| 1 | **Politics NO, K=50, N=3, ≥24h hold** | +6.2pp excess (semi-tick) | Most credible survivor | Thin edge, needs real tick validation |
| 2 | **Elite-gate + Smart Pool** (visionary combo) | Not yet tested | Highest theoretical potential | Needs implementation + validation |
| 3 | **Sports, N≥5, vol_conf≥0.70, ≥4h hold** | +19-25pp (vectorized UB) | Strong signal if in-play filtered | Needs real tick validation |
| 4 | **Crypto, N=2-3, vol_conf≥0.70** | Killed by direction decomposition | Dead — pure structural NO bias | Regime shift makes it unviable |
| 5 | **Esports K=50** | 4 signals in test period | Dead — insufficient data | Base rate regime collapse |
| 6 | **Elite Copy (individual)** | +21pp (semi-tick, hold≥1d) | Marginal, as expected | Same-day bias, thin edge |

---

## Critical Blockers (from Skeptic)

### BLOCKER 1: Semi-Tick ≠ Real Tick
The "tick validation" used `maker_positions` (resolved position aggregates), not chronological raw trades. It bypasses:
- Capital constraints (no position limits)
- Fill friction (no slippage/impact)
- Temporal ordering within markets

**Action required**: Implement strategies as proper `Strategy` protocol objects and run through `SyncReplayRunner` with the architect's budget gate fix applied. This is the ONLY way to get trustworthy degradation numbers.

### BLOCKER 2: Direction Decomposition Required
Crypto's 74.5% HR was an illusion — decomposed into YES signals (-2.2pp below base) and NO signals (-6.8pp below base). The headline HR was pure structural NO bias.

**Rule**: Every strategy must report YES and NO HR separately against their respective base rates. Aggregate HR is meaningless without this decomposition.

### BLOCKER 3: Regime Gates Not Implemented
Train→test base rate shifts: Esports -38pp, Crypto -34pp. Without regime detection, strategies trained on one regime fail catastrophically in another.

**Action required**: Implement rolling 30-day base rate monitor. Suspend signals when deviation > 12pp from 6-month average (visionary recommendation).

---

## What Survived the Gauntlet

**Politics NO, K=50 elite pool** is the only candidate that:
- Has genuine excess HR (+6.2pp above NO base rate in semi-tick)
- Was not killed by direction decomposition
- Has sufficient signal volume (1,563 semi-tick signals)
- Has reasonable hold time (6.5 days median)

But it's **fragile**: expanding from K=50 to the full pool drops excess to -0.6pp. The edge is concentrated in exactly 50 elite traders.

**Compounding score**: 0.06 — commercially viable only at large position sizes ($500+/trade) or as part of a multi-strategy portfolio.

---

## Concrete Next Steps

### Immediate (this week)

1. **Real tick-by-tick validation of Politics NO K=50**
   - Implement as Strategy protocol object
   - Run through SyncReplayRunner WITH the budget gate fix
   - Apply direction decomposition (YES/NO separate)
   - Report with confidence intervals

2. **Direction decomposition on all vectorized results**
   - Re-run Strategy 1 (tag-expert) and Strategy 2 (smart pool) results
   - Decompose every headline HR into YES excess and NO excess
   - Kill any signal that's pure structural bias

### Short-term (next 2 weeks)

3. **Implement regime gate**
   - Rolling 30-day base rate monitor per tag
   - Suspend signals when deviation > 12pp
   - Backtest with walk-forward (train window slides, not fixed)

4. **Test elite-gate + smart pool combo** (visionary idea)
   - Use elite participation (+22.7pp market quality lift) as market filter
   - Apply vol-weighted smart pool consensus for direction
   - Could combine the strongest findings from Strategies 2 and 3

5. **Walk-forward parameter validation**
   - Current K=50, N=3 was optimized on a single test window
   - Walk-forward with 3+ folds to confirm parameter stability

### Medium-term

6. **Multi-tag portfolio**
   - Combine Politics NO + Sports (filtered) + future candidates
   - Diversification may make thin individual edges viable

7. **Paper trading pilot**
   - Only after real tick validation confirms ≥ +5pp excess
   - Start with Politics NO at small sizes ($50/trade)

---

## Knowledge Captured (This Research Round)

| Entry | Category | Key Finding |
|-------|----------|-------------|
| `pitfalls/in_play_contamination.md` | CRITICAL | 63% sports signals uncopyable |
| `signals/vol_weighted_direction.md` | TIP | Vol beats head-count by 5-16pp |
| `signals/hr_persistence.md` | CRITICAL | Naive HR beats all λ variants |
| `signals/stability_bonus.md` | WARNING | Strong anti-luck gate |
| `signals/pnl_ic_near_zero.md` | WARNING | HR ≠ PnL |
| `data/gambling_market_taxonomy.md` | CRITICAL | 29.4% markets are gambling |

### New Knowledge to Capture

| Finding | Proposed Entry |
|---------|---------------|
| Semi-tick ≠ real tick | `pitfalls/semi_tick_methodology.md` |
| Direction decomposition required | `pitfalls/direction_decomposition.md` |
| Binary beats proportional for pool signals | `signals/binary_vs_proportional.md` |
| Budget gate bug in run_fast_backtest | `pitfalls/budget_gate_cumulative.md` |
| Train→test regime shifts (Esports -38pp) | Update `data/period_base_rate_variance.md` |

---

## Artifacts (Complete Inventory)

### Scorecard Research (Round 1)
```
research/hypotheses/trader-scorecard/discovery/
├── hr_conviction_analysis.md      — λ sweep, train/test IC, conviction
├── striking_stability_analysis.md — entry price, inverted-U, stability deciles
├── gambling_market_taxonomy.md    — slug patterns, tag filters, trader crossover
├── scorecard_framework.md         — composition, normalization, failure modes
└── synthesis.md                   — round 1 summary
```

### Strategy Research (Round 2)
```
research/hypotheses/scorecard-strategies/
├── strategy1_tag_consensus.md     — per-tag consensus, in-play discovery
├── strategy2_smart_pool.md        — vol-weighted, confidence sweeps
├── strategy3_elite_copy.md        — elite pool, market selector pivot
├── strategy4_smart_pool_pm.md     — position management, binary > proportional
├── tick_validation_results.md     — semi-tick validation (methodology caveat)
├── skeptic_review.md              — 3 critical blockers identified
├── visionary_ideas.md             — elite-gate combo, regime gate, 10 new ideas
├── architect_audit.md             — budget gate fix, harness improvements
├── synthesis.md                   — round 2 summary
└── final_synthesis.md             — this document
```

### Knowledge Base
```
research/knowledge/
├── signals/hr_persistence.md
├── signals/stability_bonus.md
├── signals/pnl_ic_near_zero.md
├── signals/vol_weighted_direction.md
├── data/gambling_market_taxonomy.md
└── pitfalls/in_play_contamination.md
```
