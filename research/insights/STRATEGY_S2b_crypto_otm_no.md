# S2b: Crypto OTM NO — Selling Lottery Tickets on Price Markets

**Status**: Active, HIGH confidence (best risk-adjusted strategy found)
**Capital allocation**: Scale with S1 compounding, $300-$5,000
**Direction**: Always NO on out-of-the-money crypto checkpoint markets
**Source insights**: 21_crypto_otm_no_strategy.md

---

## Edge Summary

Buying NO on out-of-the-money crypto price markets (YES 5-25%) produces **98.9% hit rate**, $12.72/bet at $100 stakes, and $802/month at $1,000 capital. The edge is structural: crypto doesn't move 10-25% in 4 hours very often, and YES buyers pay a lottery premium. Zero losing months at $1K over 7 months. Max drawdown: $298.

This is the best risk-adjusted strategy discovered. A strict subset of S2a with tighter filters and much faster rotation (4-8 hours vs days).

---

## Market Structure

4-hour crypto checkpoint markets (launched Aug 2025). Each checkpoint spawns 6-15 markets per asset at different strikes:

```
"Bitcoin above $116.5K on August 25 at 4PM ET?"   YES: 25%
"Bitcoin above $117K on August 25 at 4PM ET?"      YES: 18%
"Bitcoin above $118K on August 25 at 4PM ET?"      YES: 10%
"Bitcoin above $120K on August 25 at 4PM ET?"      YES: 5%
```

**Why NO wins**: For "Bitcoin above $120K" when BTC is at $116K, BTC needs to rally 3.4% in 4 hours. Historical 4-hour moves exceed 3% roughly 1-2% of the time, but the market prices YES at 5% — a lottery ticket premium.

---

## Results by YES Price Band ($100/bet, $1K capital, 10 slots)

| YES Band | Bets | HR | $/bet | $/month |
|----------|:---:|:---:|:---:|:---:|
| 5-15% (safest) | 412 | 99.3% | $9.43 | $555 |
| **5-25% (recommended)** | **441** | **98.9%** | **$12.72** | **$802** |
| 10-30% (EV-maximizing) | 439 | 98.2% | $19.91 | $1,249 |
| 15-40% (aggressive) | 427 | 95.1% | $28.59 | $1,744 |

The **10-30% band** maximizes EV. The **5-25% band** maximizes safety.

---

## Monthly Detail ($1K capital, $100/bet, YES 5-25%)

| Month | Bets | HR | PnL |
|-------|:---:|:---:|:---:|
| Aug 2025 | 66 | 100.0% | $1,015 |
| Sep 2025 | 171 | 97.7% | $2,034 |
| Oct 2025 | 48 | 100.0% | $696 |
| Nov 2025 | 45 | 100.0% | $602 |
| Dec 2025 | 54 | 100.0% | $712 |
| Jan 2026 | 42 | 97.6% | $341 |
| Feb 2026 | 15 | 100.0% | $212 |

**Zero losing months.** September "worst" at 97.7% — still massively profitable.

---

## Capital Scaling

| Capital | Bet Size | Slots | HR | $/month |
|:---:|:---:|:---:|:---:|:---:|
| $300 | $50 | 6 | 98.1% | **$226** |
| $500 | $50 | 10 | 98.9% | **$401** |
| $1,000 | $100 | 10 | 98.9% | **$802** |
| $2,000 | $100 | 20 | 99.1% | **$1,538** |
| $5,000 | $200 | 25 | 99.3% | **$3,749** |

Doubling capital roughly doubles PnL. At $5K/25 slots, 516 eligible markets still skipped — capacity available.

---

## Risk Analysis

### Loss Inventory (26 losses out of 1,822 OTM markets = 1.4%)

| Asset | Markets | Losses | Loss Rate |
|-------|:---:|:---:|:---:|
| **Bitcoin** | 552 | 3 | **0.5%** |
| Ethereum | 570 | 7 | 1.2% |
| XRP | 327 | 6 | 1.8% |
| **Solana** | 370 | 10 | **2.7%** |

**Bitcoin safest, Solana riskiest.** 22 of 26 losses in September 2025 (crypto rally month). Max consecutive losses: 2. Max drawdown: $298.

### What Kills You

Only scenario: sudden 5-25% directional move within 4 hours (major pump, flash crash, news-driven). At 98.9% HR with $12.72/bet EV, one $100 loss recovered in 8 winning bets (~4 days).

---

## Execution

### Market Identification Filter

```
question contains "above" or "below"
  AND question contains AM/PM time checkpoint
  AND asset is BTC, ETH, SOL, or XRP
  AND YES price 5-25%
  AND market volume > $50
  AND lockup < 24 hours
```

### Execution Flow

1. **Scan**: Every 4 hours, new checkpoints appear
2. **Filter**: YES 5-25%, prefer BTC (0.5% loss rate)
3. **Enter**: Buy NO at market (taker), or limit order 1-2c inside spread (maker)
4. **Wait**: 4-8 hours for resolution
5. **Collect**: NO wins -> payout. YES wins -> lose bet.
6. **Rotate**: Free capital for next checkpoint

6 checkpoints/day x 4 assets x 3-5 OTM strikes = 12-20 opportunities per checkpoint.

---

## Realistic Expectations (with haircuts)

| Capital | Upper bound | 70% haircut | 50% haircut |
|:---:|:---:|:---:|:---:|
| $300 | $226/mo | **$158/mo** | $113/mo |
| $1,000 | $802/mo | **$561/mo** | $401/mo |
| $5,000 | $3,749/mo | **$2,624/mo** | $1,875/mo |

Even at 50% haircut, $1K generates $401/month with near-zero risk of ruin.

---

## Limitations

- **7-month data window**: Markets started Aug 2025. Could be new-market arbitrage that decays.
- **Single regime**: All data in crypto bull market. Prolonged bear/high-vol regime untested.
- **Correlated losses**: All 4 assets move together during macro events (Sep 2025: 22 losses in one month, still profitable).
- **Liquidity risk**: Some OTM markets have <$100 volume.
- **No fee modeling**: 2% fee on winnings reduces $/bet by $0.20-$0.50.
