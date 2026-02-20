# S2 Market Making Angle: 78% Better Capital Efficiency by Selling YES

**Date**: 2026-02-19
**Method**: 19.2M trades on 26,012 "Will" binary markets (YES 10-50%), Jan 2025 - Jan 2026
**Comparison**: Maker sells YES (MM approach) vs Taker buys NO (current S2)

---

## Core Idea

Current S2 = buy NO at market (taker). Same final position as **selling YES as a limit order** (maker). Both are long NO at resolution. But the maker earns the spread instead of paying it.

---

## Head-to-Head: Maker-sellYES vs Taker-buyNO

| Metric | Taker-NO (S2 now) | Maker-sellYES (MM) | Delta |
|--------|:---:|:---:|:---:|
| Markets traded | 25,639 | 25,629 | ~same |
| Market-level HR | 81.7% | 81.7% | same |
| Total PnL | $86.9M | $104.3M | **+20%** |
| Total volume | $873.8M | $588.7M | -33% |
| **PnL / Volume** | **9.95%** | **17.72%** | **+78%** |
| Avg PnL / market | $3,392 | $4,069 | +20% |

Same hit rate, **78% better capital efficiency**. The maker approach generates 20% more PnL on 33% less volume — the spread works in your favor instead of against you.

---

## Why It Works: YES Takers Are Dumb Money

| YES Price | YES Taker HR | YES Taker PnL/trade | N Trades |
|:---------:|:---:|:---:|:---:|
| 10-20% | **15.0%** | **-$3.26** | 2.2M |
| 20-30% | **21.0%** | **-$4.34** | 1.8M |
| 30-40% | **28.8%** | **-$8.16** | 1.3M |
| 40-50% | **32.5%** | **-$12.15** | 936K |

YES takers lose money at EVERY price bucket from 10-90%. At 10-20% YES, their HR is 15% vs 18.2% base — they're worse than random. These are retail gamblers buying lottery tickets on "Will" questions. Every dollar they spend buying YES is a dollar we earn selling it.

531K unique YES takers across 26K markets — broad, persistent, uninformed flow.

---

## Spread: 2c Median (Free Edge)

| Metric | Value |
|--------|:---:|
| Median bid-ask spread | **2.0c** |
| Mean bid-ask spread | 4.2c |
| P25 | 1.0c |
| P75 | 5.0c |

The 2c spread is consistent across all price buckets (10-50%). On a 20c YES token, that's 10% of the price — significant. And it's on top of the structural NO edge.

---

## Fees: Zero

Polymarket charges zero fees on "Will" binary markets (confirmed: 100% of 19.2M trades have $0 fees). No fee drag for makers or takers.

---

## PnL by YES Price Bucket (Maker Selling YES)

| YES Price | Maker HR | PnL/Volume | Total PnL | Volume |
|:---------:|:---:|:---:|:---:|:---:|
| 0-10% | 88.8% | **-0.92%** | -$992K | $108M |
| **10-20%** | **85.0%** | **1.53%** | $1.3M | $87M |
| **20-30%** | **79.0%** | **3.65%** | $2.6M | $71M |
| **30-40%** | **71.2%** | **7.18%** | $5.6M | $78M |
| **40-50%** | **67.5%** | **13.51%** | $9.5M | $71M |
| 50-60% | 72.9% | 53.24% | $18.4M | $35M |

**0-10% is negative** — when YES is ultra-cheap (1-9c), the rare YES-wins produce 10-100x losses that overwhelm the 89% HR. Confirms insight #17: stay above 10%.

**Sweet spot: 20-40%** — best balance of HR (71-79%), volume ($71-78M each), and PnL/volume (3.65-7.18%). At 40-50%, PnL/vol jumps to 13.5% but HR drops to 68%.

---

## Volume Profile: Plenty of Flow

| Metric | Value |
|--------|:---:|
| Markets/day with ≥$50 YES-buy flow | **184** |
| Median daily YES-buy volume/market | $93 |
| P90 daily volume | $3,475 |
| Median fills/day/market | 8 |

At $50/bet, there are 184 markets per day with enough YES-buying flow to fill our orders. That's not a capacity constraint — it's an abundance of opportunity.

---

## Taker Flow Composition

| YES Price | Taker→YES % | Taker→NO % |
|:---------:|:---:|:---:|
| 10-20% | 35.4% | 64.6% |
| 20-30% | 34.6% | 65.4% |
| 30-40% | 37.3% | 62.7% |
| 40-50% | 41.0% | 59.0% |

~35-40% of taker flow is YES-buying across our target zone. That's the flow we'd sell into. The other 60% is NO-buying (smart money) — we don't interact with that.

---

## S2 Updated Strategy: Maker-sellYES

### What changes
| Aspect | Taker-NO (before) | Maker-sellYES (after) |
|--------|------|------|
| Order type | Market order (buy NO) | Limit order (sell YES) |
| Entry price | Pay the spread | Earn the spread |
| Execution | Immediate | Wait for fill (passive) |
| Capital during wait | Idle until fill | Posted as collateral |
| Capital efficiency | 9.95% PnL/vol | 17.72% PnL/vol |

### Implementation requirements
1. **CLOB API integration**: Post limit orders on the YES side of target markets
2. **Price selection**: Post YES asks at 1-2c above the current best bid (tight spread)
3. **Market selection**: Same S2 filters — "Will" binary, YES 15-40%, volume <$5K, "above"/"below" keywords
4. **Inventory management**: Accumulated short-YES (= long-NO) positions resolve naturally. No rebalancing needed.
5. **Order management**: Cancel stale orders if price moves significantly (>5c)

### What doesn't change
- Same markets (26K "Will" binary)
- Same directional edge (NO wins 82%)
- Same risk (YES wins 18% of the time → full loss)
- Same lockup period (wait for resolution)

---

## Risks

1. **Execution complexity**: Requires automated order management (posting, canceling, monitoring fills). Much more complex than manual market orders.
2. **Adverse selection from NO-side**: When informed traders sell YES to us... wait, that's what WE want. The risk is informed YES-buyers, but we've shown they barely exist (23% HR).
3. **Inventory accumulation**: If we can't get filled (too much competition), capital sits idle. But 184 markets/day suggests this isn't a problem.
4. **Price staleness**: If we post a YES ask at 20c and the true probability drops to 10c, we get filled at a stale price. Need order management to cancel on price moves.
5. **Competition**: Other MMs may compete for the same flow, narrowing spreads. The 2c median spread could compress.

---

## Verdict

The MM angle **strictly dominates** taker-NO for S2:
- Same directional edge
- +78% capital efficiency (17.72% vs 9.95% PnL/volume)
- Zero fees
- 184 markets/day with sufficient flow
- YES takers are reliably dumb money (15-33% HR)

The only cost is execution complexity. If we're building automated execution anyway (for S1), adding limit order management for S2 is incremental work.

**Recommended next step**: Build a CLOB API integration that posts YES limit sells on target "Will" markets. Start with "above"/"below" keyword markets (same-day resolution) to minimize inventory risk.
