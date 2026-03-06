# Challenger Review: tag-hr-copy (Round 1, R3 artifacts)

## Compounding Score Assessment

| Tag | Excess HR | Median PnL | Median Hold | CS | vs 0.5 target |
|-----|-----------|------------|-------------|-----|----------------|
| Esports BUY | +35.7pp | $8.13 | 0.083 days | 34.9 | 70x above |
| 1H BUY | +27.3pp | $4.01 | 0.055 days | 19.7 | 39x above |
| Tennis BUY | +33.6pp | $2.40 | 0.083 days | 9.7 | 19x above |

All three tags have exceptional compounding scores on paper. The ultra-short hold times (1.33h-2h) are the engine: capital turns over roughly 12x per day, which amplifies even modest edge dramatically. These are among the highest CS values the framework would ever see. This also means tick-by-tick degradation matters enormously — a 20pp HR drop at Esports would cut CS from 34.9 to ~11.5, and a 40pp drop would eliminate the edge entirely.

## Hold Time Analysis

### Esports BUY
- Median: 2.0h (0.083 days)
- Typical esports market resolution: same-day (match outcome)
- Capital turns: ~12x/day theoretical, ~360x/month
- Distribution concern: avg_hold will differ from median if some markets stall pre-resolution. Need 90th pct.

### 1H BUY
- Median: 1.33h (0.055 days)
- These are "will BTC/ETH go up in 1 hour" markets — resolution is deterministic at the close
- Capital turns: ~18x/day theoretical
- Distribution: almost certainly tight — the resolution timestamp is fixed at market creation

### Tennis BUY
- Median: 2.0h (0.083 days)
- Tennis matches vary: 90 min to 4+ hours. Rain delays, retirements, walkthrough to finals
- Capital turns: ~12x/day theoretical
- Distribution concern: tail risk from long matches or multi-day tournaments

**Hold time verdict**: All three are structurally short-dated — this is real, not a data artifact. No tighter exit criteria needed for median case. The tail risk (90th pct) is the open question.

## Capital Efficiency: The 1H Crypto Problem

**This is the most important scrutiny point.**

1H crypto markets ("will BTC/ETH go up or down in 1 hour") have a structural issue that makes the 78% HR suspicious:

1. **Base rate is 49.7% YES** — nearly 50/50. The market is correctly priced by participants.
2. **BUY-only consensus at 78% HR** means the strategy is betting YES on markets where 50 qualified traders have already pushed consensus to YES. This is copying momentum on a coin-flip outcome.
3. **The mechanism question**: Are these traders genuinely skilled, or are they systematically betting YES on crypto (directional bias) and getting credited when crypto happens to go up? The directional variant (DIR) shows only 4.43pp excess HR with CS=0.06 — essentially noise. This is a red flag: if the skill were genuine price discovery, the directional signal would be stronger, not weaker.
4. **Momentum exploitation hypothesis**: Traders who bet YES on 1H BTC in a bull run accumulate trades fast (50 trades/month of 1H markets = ~2 trades/day), qualify quickly, and their recent trailing window looks skilled. This is regime-dependent performance that will collapse when crypto trend reverses.
5. **Fill latency**: 1H markets expire in 60 minutes. If consensus forms at T=30min and the strategy enters at T=32min with realistic network latency + orderbook spread, there are only 28 minutes of hold. The vectorized model uses avg_hold=1.33h which suggests markets are being entered earlier — but the entry timing relative to consensus formation is not characterized. If entry is typically at 50% through the market life, fills become harder as the market approaches resolution (thin liquidity near 1.0).

**Recommendation**: Before promoting 1H to tick-by-tick validation, require the researcher to characterize:
- Distribution of entry time relative to market open and close
- Whether the 50-trader consensus requirement means entry is typically late (>30min into the hour)
- CS under the directional constraint (why is DIR CS only 0.06 when BUY is 19.7?)

The 78% HR on a near-50/50 market, combined with near-zero directional edge, smells like a latent regime variable (crypto bull market) rather than genuine skill.

## Capital Deployment: 15,503 Signals/Month

With 4,769 + 5,009 + 5,725 = 15,503 signals/month across all three tags:

**Scenario 1 — Small position sizing ($50/trade):**
- Monthly capital required: 15,503 x $50 = $775,150
- But with 2h average hold, concurrent open positions = ~15,503 / (30 days x 12 turns/day) = ~43 simultaneous positions
- At $50/position: $2,150 peak concurrent exposure — very manageable

**Scenario 2 — Meaningful position sizing ($500/trade):**
- ~$21,500 peak concurrent exposure across 43 positions
- At this scale, Tennis's $2.40 median PnL per trade (0.5% edge on $500) becomes relevant

**The throughput is an asset, not a problem.** 15,503 signals/month means the law of large numbers works in the strategy's favor — variance smooths out quickly. However, this also means execution quality is critical: if slippage averages $1/trade, it wipes 42% of Tennis edge.

## Tennis Median PnL $2.40 — Slippage Viability

Tennis BUY has $2.40 median PnL. What does this mean in practice?

**Minimum viable edge calculation:**
- Esports confirmed spread ~$0.02-0.05 per market (thin orderbooks, niche category)
- Tennis is higher volume (5,725 signals/month vs 4,769 Esports), suggesting better liquidity
- CLOB half-spread for YES tokens near 0.75 ceiling is typically $0.01-0.03
- Round-trip slippage estimate: $0.03-0.10 per trade at small size (<$200)

At $100/trade and $2.40 median PnL: slippage of $0.05 = 2% drag. Edge survives.
At $500/trade and $2.40 median PnL: price impact becomes relevant. Edge may compress to $1.50-2.00.

**Tennis is viable if position size stays below ~$300/trade.** Above that, price impact into thin esports-adjacent orderbooks will erode the edge. This is a capacity constraint, not a disqualifier.

**Absolute floor**: If tick-by-tick replay shows 20pp HR degradation (50% degradation scenario), Tennis drops to HR=52.4%, excess=13.6pp, CS=3.9. Still above 0.5 but thin. Tennis is the most fragile of the three under degradation.

## Combined Portfolio: Diversification Assessment

**Time-of-day complementarity:**
- Esports: concentrated in afternoon/evening (match schedules), major volume in EU/NA hours
- 1H crypto: 24/7, evenly distributed across all hours
- Tennis: morning through afternoon, dominated by tournament schedules (Grand Slams, ATP/WTA)

This is genuine diversification — the three categories have low temporal overlap. Peak concurrent position count across all three is manageable.

**Category correlation:**
- Esports and Tennis: low correlation (different sports, different trader bases)
- 1H crypto and either sports category: near-zero correlation
- Portfolio HR variance reduction: significant, assuming independence holds

**Risk concentration point**: All three use the same qualified-trader consensus mechanism. If the mechanism breaks (e.g., qualified traders start losing their edge post-regime change), all three fail simultaneously. The portfolio is diversified across categories but concentrated on a single alpha source.

## Capital Efficiency Suggestions

1. **Characterize 1H entry timing before promoting.** If consensus typically forms in the last 20 minutes of a 60-minute market, fill probability is low and the market-level HR is not achievable in practice. This could explain why BUY CS=19.7 but DIR CS=0.06 — the BUY consensus is heavily biased to late entries where the outcome is nearly certain, not skill.

2. **Set a position-size cap on Tennis at $200-300/trade** based on expected liquidity. Do not attempt to deploy full budget at Tennis — the median PnL only works at small size.

3. **For Esports, test entry timing constraint** (spawned idea: esports-entry-timing). If the 35.7pp excess HR is concentrated in the first 15 minutes of consensus formation, add a recency filter. This trades signal volume for entry timing that allows better fill prices.

4. **Validate 1H under regime split** — separate the backfold performance by BTC trend direction (up-trending months vs flat/down months). If HR collapses in flat/down months, the signal is regime-conditional and should not be promoted without a regime filter.

5. **Sensitivity re-run needed for R3.** The sensitivity.json in this folder is R2-era (pre-first_trade fix). The mt-20% perturbation on 1H_mt50 shows HR=0.6682 — a 28pp drop — which is alarming. This needs re-validation at R3 before tick-by-tick is scheduled.

## Sensitivity Red Flag: 1H at mt=50

From sensitivity.json (R2, partially applicable):

```
1H_mt50_ep15_pc0.75: fragile=true, max_hr_drop=0.2831
  mt-20% (mt=40): HR drops from 0.8372 to 0.6682 (-16.9pp)
  ep-20% (ep=12): HR drops from 0.8372 to 0.6916 (-14.6pp)
  pc+5% (pc=0.79): HR drops from 0.8372 to 0.5541 (-28.3pp)
```

The pc+5% drop from 78% to 55% HR is severe. This suggests the 0.75 price ceiling is not a smooth filter but a cliff: markets just above 0.75 are fundamentally different in nature (near-certain outcomes included in 0.75 but excluded at 0.80). If the edge comes from copying near-certain markets (price 0.70-0.75), the strategy is entering near-resolution, which amplifies fill risk at those prices.

Contrast with 1H_mt30 (fragile=false, max_hr_drop=0.022) — the mt=30 variant is dramatically more stable. The researcher should consider **demoting from mt=50 to mt=30** for 1H. CS drops from 19.7 to 19.4 (negligible) but the sensitivity profile improves from fragile to stable.

## Category Recommendation

- **Esports**: correct category. 2h avg resolution, sports-like speed. Capital efficiency is genuine.
- **1H crypto**: technically the fastest-resolving category on the platform (<1 hour). If the edge is real, CS=19.7 is correct. If it is regime-dependent, this is the highest-risk promotion.
- **Tennis**: correct category. Similar to other sports (~2h per market, not the 8-day sports category average, because we're copying within active match markets).

No category reassignment needed — all three are already in the fastest-resolving segments.

## Risk Caveat

If tighter exits or size caps are enforced:
- Tennis at $200/trade cap limits total monthly PnL contribution. At 5,725 signals/month with 72.4% HR, the strategy still generates substantial return, but capital constraints mean not all signals can be taken.
- 1H momentum filter (regime split) could cut signal count by 30-50% in flat crypto months, reducing the throughput advantage.
- Esports entry timing filter (15-minute window) could cut signals from 4,769 to ~2,000/month if consensus formation is spread across the match duration.

Do not apply all constraints simultaneously — they compound. Apply one at a time in validation.

## Summary

The hold-time profile of all three tags is exceptional: sub-2-hour resolution with 12-18 capital turns per day puts these among the highest compounding scores theoretically achievable on Polymarket. Capital efficiency is not the bottleneck here — execution fidelity and signal authenticity are.

The primary challenge is that Esports and 1H face significant degradation risk. Esports lost 52.7% of its CS from R2 to R3 on a single bug fix; it could lose another 40-60% in tick-by-tick if fill timing is unfavorable. 1H shows alarming sensitivity to parameter perturbation (28pp HR drop at pc+5%) and its near-zero directional CS raises questions about whether the BUY consensus is measuring skill or regime-conditional momentum. Tennis is the cleanest signal of the three — lower CS but more stable, with a directional variant (CS=6.8) as a potential upgrade path.

**Promotion recommendation:**
- 1H: require regime-split analysis and sensitivity re-run at R3 parameters before tick-by-tick. Flag as conditional.
- Esports: proceed to tick-by-tick, but validate entry-timing hypothesis in the same run (is the edge concentrated in early consensus entries?).
- Tennis: promote to tick-by-tick as the lowest-risk validation. Size cap $200-300 upfront.
