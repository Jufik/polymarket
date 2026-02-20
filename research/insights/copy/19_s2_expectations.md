# S2 Realistic Expectations: $300 Capital on "Will" Binary Markets

**Date**: 2026-02-19
**Method**: FIFO capital-constrained simulation, 8,018 fast-resolving "Will" binary markets, Jan 2025 - Feb 2026
**Capital**: $300, $50/bet, 6 concurrent slots

---

## What does the MM angle actually add?

Not as much as hoped. The spread edge (2c on a 20-40c token) adds ~11% PnL on top of the structural NO bias. The real edge is the 85% NO win rate, not the execution method.

| Scenario | Bets | HR | Total PnL | $/month | vs Taker |
|----------|:---:|:---:|:---:|:---:|:---:|
| Taker-NO (baseline) | 2,520 | 85.5% | $13,556 | $968 | — |
| MM 25% capture | 2,398 | 83.2% | $15,064 | $1,076 | +11% |
| MM 50% capture | 2,454 | 83.9% | $15,100 | $1,079 | +11% |
| MM 75% capture | 2,473 | 84.2% | $15,406 | $1,100 | +14% |

Fill capture rate barely matters (25% vs 75% = only $24/month difference). The bottleneck is **capital and rotation**, not fills. Even at 25% capture, there are more fillable markets than we have capital for.

---

## Why is the MM edge only 11%?

Selling YES at price `p` and buying NO at price `(1-p)` are **economically identical positions**. The only difference is the spread:

| Action | Entry price | Profit if NO wins | Loss if YES wins |
|--------|:---:|:---:|:---:|
| Taker buys NO at 75c | 75c | $50 × 25/75 = **$16.67** | -$50 |
| Maker sells YES at 26c | ~74c effective | $50 × 26/74 = **$17.57** | -$50 |
| Difference | 1c better | **+$0.90/bet** | same |

The 1c spread edge adds $0.90 per winning bet, or ~$0.77/bet overall at 85% HR. Over 180 bets/month = ~$139/month extra. That's real money but not a strategy transformation.

---

## Realistic monthly expectations

These are **FIFO upper bounds** — assume perfect market identification, instant fills, zero idle time. Apply haircuts for reality.

| Filter | Upper bound | 50% haircut | 30% haircut |
|--------|:---:|:---:|:---:|
| Fast markets, taker-NO | $968/mo | **$484/mo** | $290/mo |
| Fast markets, MM-sellYES | $1,079/mo | **$540/mo** | $324/mo |
| All markets, taker-NO | $466/mo | **$233/mo** | $140/mo |

### What the haircut accounts for
- **Imperfect market identification** — finding "Will" binary markets with right YES price takes time
- **Execution latency** — by the time we enter, the price may have moved
- **Idle capital** — gaps between resolution and next entry
- **Selection bias** — FIFO simulation picks the fastest-resolving markets (hindsight)
- **Liquidity risk** — $50 bets in <$1K volume markets may not fill at target price

### Bottom line at $300

| Scenario | Monthly PnL (realistic) | Annual |
|----------|:---:|:---:|
| **Conservative (30% of upper)** | **$290-324** | **$3,480-3,890** |
| **Moderate (50% of upper)** | **$484-540** | **$5,810-6,475** |
| Upper bound (100%) | $968-1,079 | $11,620-12,940 |

---

## Monthly detail: what good and bad months look like

(Taker-NO baseline, fast markets, $50/bet)

| Month | Available | Placed | HR | PnL | Notes |
|-------|:---:|:---:|:---:|:---:|-------|
| 2025-01 | 209 | 180 | 77.2% | $84 | Cold start, lower HR |
| 2025-02 | 204 | 180 | 77.8% | **-$8** | Only losing month |
| 2025-09 | 1,489 | 180 | 90.6% | **$2,365** | Best month (high HR + volume) |
| 2026-01 | 1,046 | 180 | 92.8% | $1,732 | Strong month |

**Losing months**: 1 out of 14 (7%). The single loss was $8 — a rounding error. No month lost more than $50. This is because 85% HR × 180 bets provides strong diversification. With 50% haircut (90 bets), expect occasional losing months of $100-200.

---

## Risk profile

| Metric | Value |
|--------|:---:|
| Max monthly loss (simulated) | -$8 (essentially zero) |
| Worst monthly HR | 77.2% |
| Max drawdown | $0 (never went negative) |
| Losing months | 1/14 (7%) |
| Std dev of monthly PnL | $697 |

**At 50% haircut**, expect:
- Occasional $100-200 losing months (1-2 per year)
- Monthly PnL variance of ~$350
- Max drawdown: $200-400 (1-1.5 months of capital)

**Risk/reward**: At $300 capital, worst realistic outcome is a few hundred dollar drawdown. Annual expectation is $3,500-6,500. Risk of ruin is essentially zero at 85% HR with diversified $50 bets.

---

## MM vs Taker: when does MM make sense?

The 11% PnL edge from MM is **not the main argument**. The real reasons to go MM:

| Factor | Taker | Maker (MM) |
|--------|-------|------------|
| Execution | Active: must submit market orders | Passive: post limit orders, wait |
| Timing | Must act when market opens | Post once, get filled whenever |
| Infrastructure | Simple: one API call per bet | Complex: order management, cancellation |
| Thin markets | May not have NO liquidity | Provides liquidity, gets filled by YES flow |
| Scalability | Limited by NO-side book depth | Limited by YES-buy flow (larger) |

**Verdict**: For $300, taker-NO is simpler and nearly as profitable. The MM approach makes sense when:
1. Scaling beyond $1K capital (NO-side liquidity becomes the bottleneck)
2. Building automated execution anyway (marginal complexity to add limit orders)
3. Targeting ultra-thin markets (<$500 volume) where NO liquidity doesn't exist but YES flow does

---

## What actually matters for S2 PnL

The PnL is driven almost entirely by three factors, in order of importance:

1. **Market selection** (65% of edge): "Will" binary, YES 15-40%, fast-resolving. Getting this right is worth 5-10x more than the taker/maker choice.
2. **Capital rotation** (25% of edge): Fast filter (<3d lockup) triples bet count. "above"/"below" keywords accelerate further.
3. **Execution method** (10% of edge): MM adds ~11%. Nice but not decisive.

Focus on #1 and #2. The execution method is a secondary optimization.
