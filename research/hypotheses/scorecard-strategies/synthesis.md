# Strategy Research Synthesis

**Date**: 2026-03-07
**Status**: Vectorized discovery complete — all results are UPPER BOUNDS
**Team**: 3 researchers (tag-consensus, smart-pool, elite-copy)

---

## Executive Summary

Three strategies were explored using the trader scorecard. A **critical new pitfall** emerged: in-play/same-day sports signals dominate all strategies and are uncopyable. After filtering, the strategies rank:

| Rank | Strategy | Best Config | Excess HR (UB) | Signals/mo | CS (UB) | Validate? |
|------|----------|-------------|----------------|------------|---------|-----------|
| 1 | **Smart Money Pool** | Esports K=50, N=3, conf≥0.80 | +44pp | 200 | 628 | YES — top priority |
| 2 | **Smart Money Pool** | Crypto, N=5, conf≥0.90 | +39pp | 229 | 187 | YES |
| 3 | **Tag-Expert Consensus** | Politics NO, K=50, N=3, ≥4h | +19.9pp | 336 | ~40 | YES |
| 4 | **Elite Market Selector** | Elite participation filter | +22.7pp | ~6K mkts | ~15 | YES |
| 5 | **Elite Copy** | N≥2, hold≥1d | +21pp | 606 | ~3-9 | Maybe |

### Critical New Pitfall: In-Play Signal Contamination

> [!CRITICAL]
> 63% of "elite" sports signals and a large fraction of consensus signals resolve same-day.
> These are traders entering AFTER the outcome is effectively known (live-score watchers).
> Any vectorized result without a ≥4h or ≥1d hold filter is inflated by 15-30pp.
> ALL future vectorized sweeps MUST include hold-time filtering.

---

## Strategy Comparison

### Strategy 1: Tag-Expert Consensus

**What it does**: Per-tag qualified pools (top-K by excess_hr × log(n_positions)), consensus when N agree.

**Strengths**:
- Clean separation of in-play vs genuine signals via hold filter
- Politics NO and Elections NO show genuine signal after filtering (80-92% HR vectorized)
- 23 canonical tags with tag-specific base rates

**Weaknesses**:
- Sports signals are almost entirely in-play (uncopyable)
- Stability gate adds only +1.3pp at -25% pool — minimal effect
- After hold filtering, signal volume drops dramatically

**Best configs** (≥4h hold, test period):
- Politics NO: 92.0% HR, 336 sigs/mo, +19.9pp excess
- Tech NO: 88.3% HR, 58 sigs/mo, +13.3pp excess
- Elections NO: 79.2% HR, 88 sigs/mo, +9.7pp excess

**Expected post-tick-by-tick**: 52-72% real HR for Politics NO.

### Strategy 2: Smart Money Pool

**What it does**: All qualified traders vote on direction; trade when confidence ≥ threshold.

**Strengths**:
- **Vol-weighted direction beats head-count by 5-16pp** — the strongest finding
- Esports K=50 pool: 100% HR on 403 test signals (vectorized, will degrade)
- High signal volume across multiple tags
- Near-unanimous (0.9-1.0 confidence) strongest bucket for Crypto and Elections

**Weaknesses**:
- Esports 100% HR will degrade heavily in tick-by-tick (expect 60-75%)
- Very large qualified pools (3K-21K traders) — may include low-quality traders
- Sports may still contain in-play contamination

**Best configs**:
- Esports K=50, N=3, conf≥0.80: CS=628 (UB)
- Sports K=All, N=3, conf≥0.90: 1,811 signals, CS=205
- Crypto K=All, N=5, conf≥0.90: 687 signals, CS=187

### Strategy 3: Elite Copy

**What it does**: Ultra-strict scorecard (517 traders) → copy individual entries.

**Strengths**:
- Elite Market Selector (Pivot C) is strong: +22.7pp YES win rate lift on 17.8K markets
- Clear identification of same-day resolution bias
- Small, interpretable pool

**Weaknesses**:
- Pure copy after hold≥1d filtering: only 51.4% HR (+21pp excess but marginal absolute)
- Crypto/Esports pools too small (2-28 traders)
- Entry price ceiling INVERTS signal (higher price = higher HR — resolution anchor)

**Best use**: Elite participation as market filter, not individual copy signal.

---

## Production Recommendations

### Tier 1: Validate First (tick-by-tick)

1. **Smart Money Esports K=50**: Highest CS but 100% HR is suspicious. Tick validation will reveal true signal.
2. **Tag-Expert Politics NO**: Strong excess HR, genuine predictions (≥4h hold), 336 sigs/mo.
3. **Smart Money Crypto N=5 conf≥0.90**: High excess HR, decent volume.

### Tier 2: Explore Further

4. **Elite Market Selector + entry rule**: Use elite participation to SELECT markets, then add a separate entry criterion (e.g., price in [0.20, 0.70]).
5. **Multi-tag portfolio**: Combine top configs across tags for diversification.

### Tier 3: Likely Dead Ends

6. **Pure elite copy (N=1)**: Individual copy is too noisy, confirmed again.
7. **Sports consensus without hold filter**: Dominated by in-play signals.

### Key Production Decisions

| Decision | Recommendation | Evidence |
|----------|---------------|----------|
| Direction weighting | **Vol-weighted** (not head-count) | +5-16pp across all tags |
| Hold filter | **≥4h minimum** for sports, ≥1d for conservative | In-play contamination |
| Lambda for HR | **Naive (λ=0)** or ≤0.003 | IC peaks at no weighting |
| Pool ranking | **excess_hr × log(n_positions)** | Balances skill + experience |
| Stability gate | **Optional** (adds +1.3pp, costs -25% pool) | Minimal marginal value |
| Gambling exclusion | **Mandatory** (updown + up-or-down slugs) | 56% of positions are noise |
| Market-maker gate | **avg(abs(net_usd)/vol) ≥ 0.90** | MM HR = 0.27 |

---

## New Knowledge Captured

| Entry | Category | Finding |
|-------|----------|---------|
| `pitfalls/in_play_contamination.md` | CRITICAL | 63% of sports signals are in-play (uncopyable), ≥4h hold filter required |
| `signals/vol_weighted_direction.md` | TIP | Vol-weighted consensus beats head-count by 5-16pp |

---

## Open Questions

1. **Tick-by-tick validation**: Which strategies survive the 20-40pp degradation?
2. **Hold filter calibration**: Is 4h enough or should it be 24h for sports?
3. **Multi-tag portfolio**: Can we combine Esports + Crypto + Politics for diversified signal?
4. **Entry timing**: When exactly do elite/qualified traders enter relative to market creation?
5. **Capital efficiency**: With 50 position slots, which strategy maximizes throughput?

---

## Artifacts

| File | Strategy |
|------|----------|
| `strategy1_tag_consensus.md` | Tag-Expert Consensus |
| `strategy2_smart_pool.md` | Smart Money Pool |
| `strategy3_elite_copy.md` | Elite Copy + Pivots |
| `synthesis.md` | This document |
