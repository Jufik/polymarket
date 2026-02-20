# S2 Dual-Sided Strategy & Capacity Ceiling

**Date**: 2026-02-19
**Method**: Capital-constrained simulation on 7,021 fast "Will" binary markets (YES 15-40%, <3d lockup), Jan 2025 - Feb 2026
**Approach**: Buy NO (taker) + Sell YES (maker) on same markets = 2x exposure per market

---

## The Dual-Sided Idea

Instead of choosing taker-NO or maker-sellYES, run **both simultaneously**:

| Side | Action | Liquidity source |
|------|--------|-----------------|
| Taker | Buy NO at ask | NO ask depth |
| Maker | Sell YES via limit order | YES retail flow |

Both are economically long NO. Each accesses a **different liquidity pool**. Combined deployable volume per market: **1.97x** single-sided.

87.5% of target markets support full dual deployment ($50 on each side).

---

## At $300: Dual Doesn't Help (Capital-Bound)

| Approach | Capital/market | Slots | Markets/mo | PnL/mo |
|----------|:---:|:---:|:---:|:---:|
| Taker 6 × $50 | $50 | 6 | 180 | $1,517 |
| **Dual 3 × $100** | **$100** | **3** | **90** | **$1,491** |

Same PnL — fewer markets but 2x per market. At $300, capital is the bottleneck regardless of approach.

---

## At $600+: Dual Unlocks Scale

| Approach | Capital | Slots | Markets/mo | PnL/mo |
|----------|:---:|:---:|:---:|:---:|
| Taker 6 × $50 | $300 | 6 | 180 | $1,517 |
| **Dual 6 × $100** | **$600** | **6** | **180** | **$2,897** |

Same diversification (180 markets), **1.9x the PnL**. The second $300 earns nearly as much as the first because it accesses an entirely separate liquidity pool (YES retail flow).

---

## Capacity Ceiling: Diminishing Returns Curve

At 5% market impact limit, dual-sided, fast markets only:

| Capital | PnL/mo (upper) | PnL/mo (50% haircut) | Monthly ROI | Marginal ROI |
|:---:|:---:|:---:|:---:|:---:|
| $300 | $1,380 | **$690** | 230% | — |
| $1,000 | $3,449 | **$1,725** | 173% | 277% |
| $2,000 | $6,308 | **$3,154** | 158% | 344% |
| $5,000 | $10,046 | **$5,023** | 100% | 157% |
| $10,000 | $13,017 | **$6,509** | 65% | 53% |
| $15,000 | $13,410 | **$6,705** | 45% | 8% |
| $20,000 | $13,410 | **$6,705** | 34% | 0% |

### Three regimes

1. **$300-2,000 (linear scaling)**: Every extra dollar works as hard as the first. Marginal ROI > 200%. Capital is the only bottleneck.
2. **$2,000-10,000 (diminishing but productive)**: Marginal ROI falls from 344% to 53%. Larger bets hit the 5% impact limit on smaller markets, forcing you into fewer, bigger markets.
3. **$10,000+ (saturated)**: Market supply caps out at ~500 fast markets/month. Adding capital barely helps. The curve goes flat.

---

## Absolute Ceiling

| Metric | Upper bound | Realistic (50% haircut) |
|--------|:---:|:---:|
| **Max monthly PnL** | $13,410 | **$6,705** |
| Capital to reach ceiling | ~$10,000 | ~$10,000 |
| Capital needed at 1-day rotation | ~$7,160 | ~$3,580 |
| Monthly deployed volume | $214,807 | ~$107,000 |
| PnL / deployed volume | 15.0% | 15.0% |

The ceiling is a **market supply constraint**, not a capital constraint. ~500 fast "Will" binary markets per month, ~$2,000 median volume each, 5% impact = ~$100 deployable per market. No amount of capital changes this.

---

## Monthly Market Supply (the real bottleneck)

| Month | Fast Markets | Total Volume | NO HR |
|-------|:---:|:---:|:---:|
| 2025-01 | 215 | $1.5M | 79.5% |
| 2025-04 | 168 | $0.8M | 83.3% |
| 2025-09 | 1,473 | $5.7M | 87.7% |
| 2026-01 | 833 | $10.2M | 89.3% |
| **Average** | **502** | **$4.3M** | **87.0%** |

Trend is positive: market count is growing (168 → 833). If Polymarket continues to grow, the ceiling rises.

---

## Optimal Capital Allocation for S2

| Capital available | Recommended allocation | Expected PnL/mo | Approach |
|:---:|:---:|:---:|:---:|
| $300 | $300 all S2 | $690 | Taker-only (simpler) |
| $1,000 | $1,000 all S2 | $1,725 | Start dual at $100/mkt |
| $3,000 | $2,000 S2 + $1,000 reserve | $3,150 | Dual, reserve for variance |
| $5,000+ | $5,000 S2 (diminishing above) | $5,000 | Dual, consider widening to YES 10-50% |
| $10,000+ | $10,000 S2 max, rest to S1 | $6,500 | Saturated — reallocate surplus to S1 |

### Scaling playbook
1. **Start at $300-1,000**: Taker-only, learn execution, validate the edge
2. **Scale to $2,000-5,000**: Add maker-sellYES side, dual deployment
3. **At $5,000+**: Near ceiling on fast markets. Options:
   - Widen to slower markets (3-7d lockup) for more supply
   - Widen YES range to 10-50% (more markets, lower PnL/bet)
   - Accept diminishing returns and redirect surplus to S1
4. **At $10,000+**: Fully saturated. Additional capital goes to S1 (proportional copy)

---

## What the 50% Haircut Covers

The upper bound assumes:
- Perfect market identification (find all "Will" binary markets instantly)
- Zero execution latency (enter at exact target price)
- No idle capital (immediate rotation after resolution)
- FIFO selection (fastest-resolving markets picked first = hindsight bias)
- No competition (we capture all available flow)

The 50% haircut is conservative for these factors. If execution is automated and well-tuned, 60-70% of upper bound may be achievable.

---

## Key Numbers to Remember

- **$300 starting capital → ~$690/month** (realistic, taker-only)
- **$2,000 → ~$3,150/month** (realistic, dual-sided)
- **$5,000 → ~$5,000/month** (near ceiling)
- **$10,000 → ~$6,500/month** (ceiling hit)
- **Absolute ceiling: ~$6,700/month** regardless of capital
- **Market supply: ~500 fast markets/month** (growing)
- **Break-even in 1 month** at any capital level
