# Crypto OTM NO Strategy: Selling Lottery Tickets on Price Markets

**Date**: 2026-02-20
**Method**: FIFO capital-constrained simulation on crypto "above/below" markets, post-Aug 2025
**Universe**: 1,822 OTM crypto markets (YES 5-30%), BTC/ETH/SOL/XRP
**Capital**: $300-$5,000, $50-$200/bet

---

## TL;DR

**This is the best strategy we've found.** Buying NO on out-of-the-money crypto price markets (YES trading at 5-25%) produces 98.9% hit rate, $12.72/bet at $100 stakes, and $802/month at $1,000 capital. The edge is structural: crypto doesn't move 10-25% in 4 hours very often, and YES buyers pay a lottery premium. Max drawdown: $298. Zero losing months at $1,000 capital across 7 months of data.

---

## The Market Structure

Polymarket launched 4-hour crypto checkpoint markets in August 2025. Each checkpoint (every 4 hours at 12AM/4AM/8AM/12PM/4PM/8PM ET) spawns 6-15 markets per asset at different strike prices:

```
"Bitcoin above $116.5K on August 25 at 4PM ET?"   YES: 25%
"Bitcoin above $117K on August 25 at 4PM ET?"      YES: 18%
"Bitcoin above $118K on August 25 at 4PM ET?"      YES: 10%
"Bitcoin above $120K on August 25 at 4PM ET?"      YES: 5%
```

The OTM strikes (YES < 25%) are the target. These resolve within 4-8 hours.

### Why NO wins

For a "Bitcoin above $120K at 4PM" market when BTC is at $116K:
- BTC needs to rally 3.4% in 4 hours for YES to win
- Historical 4-hour crypto moves exceed 3% roughly 1-2% of the time
- The market correctly prices this low probability (YES = 5%)
- But the market **slightly overprices YES** — lottery ticket premium

### Data coverage

| Period | Crypto above/below markets | With trade data | OTM (YES 5-30%) |
|--------|:---:|:---:|:---:|
| Pre-Aug 2025 | ~400 (weekly/daily) | ~270 | ~100 |
| Aug 2025 | 1,046 | 832 | 198 |
| Sep 2025 | 8,083 | 5,286 | 941 |
| Oct 2025+ | ~9,121 | ~5,745 | ~683 |
| **Total** | **18,757** | **12,272** | **1,822** |

---

## Results by YES Price Band

At $100/bet, $1,000 capital, 10 concurrent slots, min $50 volume:

| YES Band | #Markets | Bets | HR | Total PnL | $/bet | $/month |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|
| 5-15% (safest) | 1,167 | 412 | 99.3% | $3,884 | $9.43 | $555 |
| **5-25% (recommended)** | **1,641** | **441** | **98.9%** | **$5,612** | **$12.72** | **$802** |
| 10-30% (balanced) | 1,082 | 439 | 98.2% | $8,742 | $19.91 | $1,249 |
| 5-30% (wide) | 1,822 | 458 | 98.5% | $6,760 | $14.76 | $966 |
| 15-40% (aggressive) | 907 | 427 | 95.1% | $12,208 | $28.59 | $1,744 |

The **10-30% band** is the EV-maximizing sweet spot: $19.91/bet with 98.2% HR. The 15-40% band has highest total PnL ($1,744/month) but drops HR to 95.1% — one loss in twenty rather than one in a hundred.

---

## Capital Scaling

Post-Aug 2025, YES 5-25%, min $50 volume:

| Capital | Bet Size | Slots | Bets/7mo | HR | Total PnL | $/month |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| $300 | $50 | 6 | 264 | 98.1% | $1,581 | **$226** |
| $500 | $50 | 10 | 441 | 98.9% | $2,806 | **$401** |
| $1,000 | $100 | 10 | 441 | 98.9% | $5,612 | **$802** |
| $2,000 | $100 | 20 | 820 | 99.1% | $10,768 | **$1,538** |
| $5,000 | $200 | 25 | 994 | 99.3% | $26,246 | **$3,749** |

Capital efficiency is excellent. Doubling capital roughly doubles PnL because the constraint is slot count, not market availability. At $5,000/25 slots, we still skip 516 eligible markets — there's capacity for more.

---

## Fast Markets (<24h) vs All

| Filter | Bets | HR | PnL | $/bet | $/month |
|--------|:---:|:---:|:---:|:---:|:---:|
| All markets | 441 | 98.9% | $5,612 | $12.72 | $802 |
| **Fast (<24h) only** | **516** | **98.8%** | **$7,168** | **$13.89** | **$1,024** |

Fast markets are better because 4-hour lockup allows 6x daily capital rotation vs weekly markets. Same HR, more bets, higher total PnL.

---

## Monthly Detail ($1,000 capital, $100/bet, YES 5-25%)

| Month | Bets | Wins | HR | PnL | $/bet |
|-------|:---:|:---:|:---:|:---:|:---:|
| Aug 2025 | 66 | 66 | 100.0% | $1,015 | $15.37 |
| Sep 2025 | 171 | 167 | 97.7% | $2,034 | $11.89 |
| Oct 2025 | 48 | 48 | 100.0% | $696 | $14.50 |
| Nov 2025 | 45 | 45 | 100.0% | $602 | $13.37 |
| Dec 2025 | 54 | 54 | 100.0% | $712 | $13.18 |
| Jan 2026 | 42 | 41 | 97.6% | $341 | $8.12 |
| Feb 2026 | 15 | 15 | 100.0% | $212 | $14.15 |

**Zero losing months.** September 2025 was the "worst" month (97.7% HR) due to a crypto rally — still massively profitable at $2,034. The $341 Jan dip reflects fewer market launches, not signal failure.

---

## Risk Analysis

### Loss inventory

Only 26 losses out of 1,822 OTM markets (1.4%):

| Asset | Markets | Losses | Loss Rate |
|-------|:---:|:---:|:---:|
| Bitcoin | 552 | 3 | **0.5%** |
| Ethereum | 570 | 7 | 1.2% |
| XRP | 327 | 6 | 1.8% |
| Solana | 370 | 10 | **2.7%** |

Solana is the riskiest asset (highest volatility → more OTM strikes hit). Bitcoin is safest.

### Loss clustering

- **22 of 26 losses occurred in September 2025** — a strong crypto rally month
- Losses cluster during rapid directional moves (SOL +30%, XRP +20% in single days)
- Max consecutive losses: **2** (never 3+ in a row)
- Max drawdown: **$298** (without capital constraints)

### What kills you

The only scenario that produces losses: **a sudden 5-25% directional move within a 4-hour window**. This happens during:
1. Major crypto pumps (BTC breaks round number, alt rally)
2. Flash crashes (rare for "above" markets — these are bullish bets losing)
3. News-driven moves (regulatory, ETF, macro)

With 98.9% HR and $12.72/bet EV, a single loss ($100) is recovered in 8 winning bets. At 66+ bets/month, recovery takes ~4 days.

---

## Comparison with Other Strategies

| Strategy | HR | $/bet | $/month ($1K) | Data period | Stability |
|----------|:---:|:---:|:---:|:---:|:---:|
| **S2 Crypto OTM NO (this)** | **98.9%** | **$12.72** | **$802** | **7 months** | **Very high** |
| S2 General "Will" NO | 85.5% | $5.38 | $968 | 14 months | High |
| S1 Consistency Copy | ~55% | ~$2-5 | ~$50-100 | 12 months | Low |
| S5 Informed MM | ~49% | ~$1.83 | Negative | 12 months | Very low |

The crypto OTM strategy has the **highest HR** (98.9% vs 85.5%), **second-highest $/month**, and the **shortest lockup** (4-8h vs days). It generates fewer bets than the general "Will" strategy but each bet is safer.

### vs S2 General "Will" NO

| Aspect | S2 General | S2 Crypto OTM |
|--------|:---:|:---:|
| Universe | 26K "Will" binary | 1,800 crypto price |
| HR | 85.5% | 98.9% |
| Avg lockup | 6.5 days | 4-8 hours |
| Capital rotation | ~5x/month | ~6x/day |
| Market identification | Keyword filter | Keyword + price band |
| Risk of ruin | ~0% | ~0% |

The crypto OTM strategy is a **strict subset** of S2 with tighter filters and much faster rotation.

---

## How to Execute Live

### Market identification

```
Filter: question contains "above" or "below"
  AND question contains AM/PM time checkpoint
  AND asset is BTC, ETH, SOL, or XRP
  AND YES price between 5-25%
  AND market volume > $50
  AND lockup < 24 hours
```

### Execution flow

1. **Scan**: Every 4 hours, new checkpoint markets appear. Scan for OTM strikes.
2. **Filter**: YES price 5-25%. Lower = safer but less profit. Higher = more profit but more risk.
3. **Enter**: Buy NO at market (taker) or place NO limit order 1-2c inside spread (maker).
4. **Wait**: 4-8 hours for resolution.
5. **Collect**: NO wins → collect payout. YES wins → lose bet.
6. **Rotate**: Free capital immediately available for next checkpoint.

### Timing

- 6 checkpoints/day (every 4 hours ET)
- 4 assets × 3-5 OTM strikes per checkpoint = 12-20 opportunities per checkpoint
- At 10 slots: fill all slots at each checkpoint, rotate every 4 hours
- Active monitoring needed at checkpoint times only

---

## Haircuts and Realistic Expectations

These are FIFO upper bounds. Apply haircuts for reality:

| Factor | Impact | Haircut |
|--------|--------|:---:|
| Market identification delay | Miss first 5-10 min of trading | -5% |
| Execution slippage | Price moves 0.5-1c between scan and fill | -10% |
| Unfilled orders (maker) | Limit orders may not fill | -15% (maker only) |
| Market not always available | Some checkpoints have fewer OTM strikes | -10% |
| Adverse selection | Fills concentrate on losing markets | -5% |

**Realistic ranges:**

| Capital | Upper bound | 70% haircut | 50% haircut |
|:---:|:---:|:---:|:---:|
| $300 | $226/mo | **$158/mo** | $113/mo |
| $1,000 | $802/mo | **$561/mo** | $401/mo |
| $5,000 | $3,749/mo | **$2,624/mo** | $1,875/mo |

Even at 50% haircut, $1,000 capital generates $401/month with near-zero risk of ruin.

---

## Limitations

- **7-month window only**: These 4-hour markets started Aug 2025. No data before that. Could be a "new market" arbitrage that decays as more participants enter.
- **Entry price approximation**: Used volume-weighted average entry across all traders. Actual entry as a late NO buyer may be worse (market moves toward fair value over time).
- **No fee modeling**: Polymarket charges fees on winnings. At 2% fee on the smaller side, this reduces $/bet by ~$0.20-$0.50.
- **Single-regime test**: All 7 months were in a crypto bull market. A prolonged bear or high-volatility regime (e.g., 2022 crash) would produce more OTM strikes being hit.
- **Liquidity risk**: Some OTM markets have < $100 volume. At $100/bet, these may not fill or may move the price significantly.
- **Correlated losses**: All 4 assets move together during macro events. A broad crypto rally can trigger losses across BTC, ETH, SOL, and XRP simultaneously (as seen in Sep 2025 with 22 losses in one month).

---

## Recommendation

**Deploy this as the primary live strategy.** It dominates all other strategies on risk-adjusted returns:

1. **Start with $300-500, $50/bet, fast (<24h) markets only.** This is the safest configuration with $226-401/month expected (before haircut).

2. **Target YES 5-25% initially, expand to 5-30% after 1 month** of live data confirms the pattern.

3. **Prefer BTC markets** (0.5% loss rate vs 2.7% for SOL). Add ETH/XRP after building confidence.

4. **Use taker execution first** — simpler infrastructure, nearly identical PnL. Switch to maker (limit orders) only if scaling beyond $2K where liquidity becomes the constraint.

5. **Monitor for regime change**: If weekly loss rate exceeds 5%, reduce position size or pause. The Sep 2025 rally produced 22 losses in one month but was still profitable. A truly adverse regime would show > 10% loss rate.

6. **Combine with S2 general "Will" NO** for diversification. Run crypto OTM for fast rotation + general "Will" for the larger universe. The two strategies share the same structural edge (NO bias) but on different timescales.
