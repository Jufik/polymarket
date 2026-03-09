# Politics NO Active Exit — Capital Recycling via Early Profit-Taking

> **TL;DR**: Exiting Politics NO positions at 50% of max payout transforms a capital-constrained
> strategy into a throughput engine. At P=20: +148% PnL ($31,368 vs $12,646), +130 fills,
> median hold drops from 4.7d to 0.9d. Portfolio Sharpe improves from 4.28 to 5.66 (politics track).

> [!CRITICAL]
> Active exit does NOT improve per-position PnL. Unconstrained, Exit@50% = $34,639 vs
> Hold = $33,942 (negligible difference). The entire benefit comes from CAPITAL RECYCLING:
> freeing slots faster allows accepting 66% more signals (327 vs 197 at P=20).

> [!WARNING]
> This analysis uses the trade tape as a price oracle for exit triggers. In production,
> the strategy needs real-time NO price monitoring via CLOB WS orderbook feed.
> Execution slippage is estimated at ~$0.10/sell (MAC half-spread 0.001 for politics),
> totaling ~$29 on 291 exits — negligible vs $18,722 PnL improvement.

## 1. Hypothesis

**Signal**: Active exit on favorable price movement for Politics NO positions.
**Thesis**: Politics NO has 7.5d median hold and 43% signal rejection at P=20 due to capital tie-up.
Early profit-taking frees capital faster, enabling more signals to be accepted.
**Null**: Early exits sacrifice more PnL than they gain from additional fills.
**Success**: Net PnL improvement > 20% at P=20, positive ROC/day improvement.

## 2. Ledger Analysis (346 resolved positions)

### Fill Price Distribution

| Bucket     | N   | Win Rate | Total PnL  | Med Hold | Capital-days | ROC/day  |
|------------|-----|----------|------------|----------|-------------|----------|
| <0.50      | 60  | 73.3%    | $35,854    | 5.0d     | $254,153    | $0.1411  |
| 0.50-0.70  | 12  | 50.0%    | -$162      | 50.6d    | $147,831    | -$0.0011 |
| 0.70-0.80  | 31  | 67.7%    | -$314      | 17.3d    | $245,111    | -$0.0013 |
| 0.80-0.90  | 53  | 84.9%    | -$68       | 12.3d    | $532,673    | -$0.0001 |
| 0.90+      | 190 | 90.0%    | -$1,368    | 5.7d     | $706,401    | -$0.0019 |

**Key finding**: Only the <0.50 bucket (longshots) has positive PnL. The 190 positions at 0.90+
have 90% win rate but NEGATIVE total PnL (-$1,368) because the 10% losses ($100 each) exceed
the tiny payouts ($2-$11 per win). Breakeven HR at 0.93 fill price = 93%.

### PnL is asymmetric and dominated by longshots

- Total PnL: $33,942 across 346 positions
- <0.50 bucket: $35,854 (106% of total PnL from 17% of positions)
- Everything 0.50+: -$1,912 (net negative)
- Median PnL/fill: $3.09 (most positions contribute tiny amounts)
- Max PnL single position: $4,067 (fill at 0.024)

## 3. Price Trajectory Analysis

From the trade tape (3.55M trades across 346 condition_ids):

### WON Positions (287)
| Target         | % Reached | Median Time |
|----------------|-----------|-------------|
| 25% of payout  | 100%      | 10.9h       |
| 50% of payout  | 100%      | 23.0h       |
| 75% of payout  | 100%      | 51.4h       |
| 90% of payout  | 100%      | 101.5h      |

100% of winning positions reach 90% of max payout. The price trajectory is monotonically
approaching resolution, giving ample exit opportunities.

### LOST Positions (59)
| Target         | % Reached | Median Time |
|----------------|-----------|-------------|
| 25% of payout  | 49%       | 8.5h        |
| 50% of payout  | 34%       | 85.2h       |
| 75% of payout  | 24%       | 64.0h       |
| 90% of payout  | 12%       | 42.6h       |

Only 49% of losing positions ever reach 25% of max payout. For 0.90+ losses specifically,
only 1 of 19 LOST positions escaped via 50% exit. Losses at high fill prices are virtually
inescapable — the market moves against the position and never recovers.

## 4. Exit Strategy Comparison (Unconstrained)

| Strategy       | PnL      | HR    | Med Hold | ROC/day  | % Early Exit |
|----------------|----------|-------|----------|----------|-------------|
| Hold to Res    | $33,942  | 82.9% | 7.5d     | $0.0180  | 0%          |
| Exit@25%       | $35,381  | 91.3% | 0.5d     | $0.0935  | 91.3%       |
| Exit@50%       | $34,638  | 88.7% | 1.1d     | $0.0565  | 88.7%       |
| Exit@75%       | $35,139  | 87.0% | 2.3d     | $0.0334  | 87.0%       |
| Exit@90%       | $35,357  | 85.0% | 4.1d     | $0.0254  | 85.0%       |

Unconstrained, active exit adds $700-$1,400 in PnL (negligible). The real benefit
emerges only under capital constraints.

## 5. Constrained Simulation (P=20, time-ordered)

| Strategy    | Fills | Rejected | PnL      | Avg PnL | HR    | Med Hold | ROC/day  | vs Hold   |
|-------------|-------|----------|----------|---------|-------|----------|----------|-----------|
| Hold        | 197   | 149      | $12,646  | $64.2   | 78.7% | 4.7d     | $0.0153  | baseline  |
| Exit@25%    | 341   | 5        | $35,412  | $103.8  | 91.5% | 0.5d     | $0.0994  | +$22,766  |
| Exit@50%    | 327   | 19       | $31,368  | $95.9   | 88.4% | 0.9d     | $0.0664  | +$18,722  |
| Exit@75%    | 289   | 57       | $29,495  | $102.1  | 85.1% | 1.6d     | $0.0476  | +$16,849  |
| Exit@90%    | 245   | 101      | $21,859  | $89.2   | 81.2% | 2.7d     | $0.0302  | +$9,213   |

**Exit@50% is the recommended target**: +148% PnL improvement, accepting 94% of signals
(327/346) instead of 57% (197/346).

### PnL Decomposition (Exit@50% vs Hold at P=20)

- Shared positions (197): Exit PnL $14,157 vs Hold PnL $12,646 → +$1,511
  (exit captures MORE on shared positions because it avoids some tail losses)
- Extra positions (130): PnL $17,211, avg $132.4/fill, 92.3% win rate
- 169 of 197 shared positions exit early (86%)

### Monthly Comparison (P=20)

| Month    | Hold PnL | Exit@50% PnL | Delta     |
|----------|----------|-------------|-----------|
| 2025-07  | $3,590   | $6,908      | +$3,318   |
| 2025-08  | -$365    | $557        | +$921     |
| 2025-09  | -$84     | $1,010      | +$1,094   |
| 2025-10  | -$48     | $1,202      | +$1,251   |
| 2025-11  | $3,901   | $4,239      | +$338     |
| 2025-12  | $3        | -$183       | -$186     |
| 2026-01  | $4,997   | $15,896     | +$10,899  |
| 2026-02  | -$437    | $310        | +$747     |
| **Total**| **$12,646** | **$31,368** | **+$18,722** |

Exit@50% underperforms in only 3 months (2024-11, 2024-12, 2025-12) by trivial amounts
(-$14, -$131, -$186). The dominant month (Jan 2026: +$10,899) alone exceeds total deficit.

## 6. Hybrid Strategies

| Strategy      | Fills | PnL      | Description                          |
|---------------|-------|----------|--------------------------------------|
| exit50_all    | 327   | $31,368  | 50% target for all positions         |
| exit50_ge80   | 307   | $29,613  | 50% only for fill >= 0.80            |
| exit50_ge70   | 313   | $30,475  | 50% only for fill >= 0.70            |
| adaptive      | 319   | $30,789  | hold <0.50, 50% mid, 25% high       |
| adaptive2     | 311   | $29,990  | hold <0.50, 75% mid, 50% high       |

**Uniform Exit@50% wins**. Hybrid strategies add complexity without PnL benefit because
longshot positions (<0.50) exit naturally fast (median 5.0d hold, mostly resolved quickly).
The 50% target on longshots still captures half the massive upside ($200 on a $0.20 fill
vs $400 for hold-to-resolution) while freeing the slot 4.9 days sooner.

## 7. Capital Efficiency

### Concurrent Positions
| Strategy      | Avg Open | Max Open |
|---------------|----------|----------|
| Hold P=20     | 17.6     | 20       |
| Exit@50% P=20 | 10.0     | 20       |

Active exit reduces average concurrent positions from 17.6 to 10.0 (43% less capital usage).

### Cross-P Comparison
| Config          | Fills | PnL     | Capital |
|-----------------|-------|---------|---------|
| Hold P=20       | 197   | $12,646 | $2,000  |
| Exit@50% P=10   | 236   | $26,834 | $1,000  |
| Exit@50% P=20   | 327   | $31,368 | $2,000  |
| Hold P=45       | 346   | $31,403 | $4,500  |

**Exit@50% at P=10 ($1K) outperforms Hold at P=20 ($2K)**: 2.1x PnL with half the capital.
To match Exit@50% at P=20 via Hold alone requires P=45 ($4,500 capital) — 2.25x more.

### Portfolio Impact
| Portfolio Config        | PnL       | Sharpe | MaxDD   |
|-------------------------|-----------|--------|---------|
| Sports + Pol Hold P=20  | $174,803  | 8.07   | $4,338  |
| Sports + Pol Exit P=20  | $193,525  | 7.93   | $3,924  |
| Sports only             | $162,157  | 7.66   | $4,369  |

Active exit adds $18,722 to portfolio PnL with lower max drawdown (-$414).
Portfolio Sharpe drops slightly from 8.07 to 7.93 because the additional fills spread PnL
across more days (273 vs 244 active days), reducing daily variance.

### Politics Track Sharpe
| Config           | PnL     | Sharpe | MaxDD   |
|------------------|---------|--------|---------|
| Hold P=20        | $12,646 | 4.28   | $1,568  |
| Exit@50% P=20    | $31,368 | 5.66   | $900    |

Politics-only Sharpe improves 32% (4.28 to 5.66), MaxDD drops 43%.

## 8. Slippage and Execution Feasibility

- **MAC half-spread** (politics): ~0.001 (from microstructure calibration)
- **Slippage per $100 sell**: ~$0.10
- **Total slippage** (291 early exits): ~$29
- **vs PnL gain**: $18,722 → slippage = 0.16% of benefit

**Liquidity**: 40% of market-days have <$200 volume, but exit triggers fire on trades
(meaning counterparties exist at that moment). $100 sells represent <20% of median daily
volume ($437).

**Implementation**: Requires real-time NO price monitoring via CLOB WS orderbook feed.
Exit is triggered when best bid for NO token >= target price. Can use limit order
(maker) for better execution.

## 9. Risks and Caveats

1. **Look-ahead bias**: Exit prices come from the trade tape, which shows trades that
   actually occurred. In production, the price may not reach the target if our exit order
   itself moves the market. Mitigated by small size ($100) relative to daily volume.

2. **Regime change**: The 10-month backtest window may not represent future politics markets.
   However, the mechanism (capital recycling, not per-position alpha) is structural.

3. **Compounding assumption**: Extra fills are assumed to have similar quality to original
   signals. Verified: extra fills show 92.3% win rate, $132.4 avg PnL (vs 78.7%/$64.2 for
   hold-constrained fills). This is expected — constrained filling biases toward earlier
   (possibly lower-quality) signals.

4. **Execution complexity**: Active exit requires monitoring positions and submitting sell
   orders, vs passive hold-to-resolution. Adds operational risk.

## 10. Recommendation

**Deploy Exit@50% on Politics NO with P=20**.

Implementation:
1. After buying NO at price `p`, set exit target = `p + 0.50 * (1.0 - p)`
2. Monitor NO token price via CLOB WS orderbook
3. When best bid >= target: sell at market (or use limit order for maker rebate)
4. If price never reaches target: hold to resolution (current behavior)

Expected annual performance (extrapolated from 10mo):
- PnL: ~$37,600/yr (vs ~$15,200 hold-to-resolution)
- Fills: ~392/yr (vs ~236)
- Median hold: 0.9d (vs 4.7d)
- Capital required: $2,000 at P=20 (or $1,000 at P=10 for similar PnL)

Alternative: reduce P to 10 ($1K capital) and use freed $1K for Esports YES track.
Combined Politics Exit@50% P=10 + Esports YES: ~$41,800/yr from $2K allocation.

## Related

- `research/hypotheses/portfolio-three-tracks/discovery/portfolio_analysis.md`
- `research/knowledge/execution/spread_microstructure.md`
- `research/knowledge/execution/live_infrastructure.md`
- `research/hypotheses/tag-hr-consensus/discovery/analysis.md` (Esports track)

## Tags

`politics`, `active-exit`, `capital-efficiency`, `profit-taking`, `position-management`
