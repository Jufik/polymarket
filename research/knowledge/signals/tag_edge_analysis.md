# Tag-Specific Edge Analysis

> [!CRITICAL] Most tag+direction combos have NEGATIVE PnL despite positive excess HR.
> "Beating the base rate" ≠ "making money". The market prices in tag-specific base rates,
> so excess HR must overcome the spread/slippage tax.

## Key Finding: Excess HR ≠ Edge

Almost every tag shows traders with positive excess HR (beating the tag base rate) but
**negative average PnL**. The market is efficiently priced at the tag level — knowing the
base rate isn't enough; you need to beat the base rate BY MORE than the spread costs.

## Profitable Tag+Direction Combos (avg_pnl > 0)

Only a handful of combos have positive PnL:

| Tag | Dir | Excess HR | Avg PnL | Hold | Compound | Note |
|-----|-----|-----------|---------|------|----------|------|
| **Tariffs** | YES | +9.6pp | **$99.95** | 6d | 1.60 | Small (5K positions), high conviction |
| **Courts** | YES | +17.4pp | **$45.88** | 21d | 0.38 | Long hold kills compounding |
| **Geopolitics** | YES | +7.5pp | **$4.95** | 4d | 0.09 | Marginal edge |
| **Trump** | YES | +7.9pp | **$1.12** | 2d | 0.04 | Fast cycling but tiny edge |
| **Elections** | NO | -18.4pp | **$23.81** | 11d | -0.40 | NEGATIVE excess but positive PnL! |
| **Music** | NO | -18.0pp | **$10.04** | 5d | -0.36 | Same paradox |
| **Movies** | NO | -18.4pp | **$11.51** | 4d | -0.53 | Same paradox |
| **Culture** | NO | -9.9pp | **$12.44** | 3d | -0.41 | Same paradox |
| **Finance** | NO | -4.0pp | **$11.74** | 4d | -0.12 | Moderate |
| **Economy** | NO | -4.3pp | **$7.63** | 8d | -0.04 | Long hold |

## The NO Paradox

> [!WARNING] In extreme-NO-bias tags (Music, Movies, Elections, Culture), NO traders have
> NEGATIVE excess HR but POSITIVE PnL. This means the market OVERPRICES the NO outcome.
> Traders buying NO at typical prices profit because the market gives them too much edge
> even though their HR is below the base rate.

This is because:
- NO base rate in these tags is 88-91%
- Market prices NO at ~85-90% (not 91%)
- The 1-5pp gap × many positions = positive total PnL
- But individual excess HR is negative because almost everyone gets these right

## The YES Paradox

In the same tags, YES traders have HIGH excess HR (+15-27pp) but NEGATIVE PnL:
- Elections YES: +26.8pp excess but -$29.27/position
- AI YES: +38.8pp excess but -$4.74/position
- Culture YES: +9.8pp excess but -$12.53/position

The market overprices YES outcomes in NO-biased tags (YES tokens too expensive relative
to their ~10% resolution rate), so even traders who beat the base rate lose money.

## Earnings Edge

Earnings shows a nuanced picture by entry price:
| Entry Price | HR | Avg PnL | Total PnL | Hold |
|-------------|------|---------|-----------|------|
| <0.50 | 24.4% | -$12.38 | -$31,868 | 1d |
| 0.50-0.60 | 54.2% | +$5.04 | +$3,448 | 3d |
| 0.60-0.70 | 57.4% | +$3.33 | +$3,905 | 3d |
| 0.70-0.80 | 57.6% | -$31.66 | -$73,442 | 3d |
| 0.80-0.90 | 84.6% | +$0.58 | +$2,727 | 1d |
| 0.90+ | 95.2% | +$6.19 | +$29,331 | 0d |

The sweet spot is **0.50-0.70**: ~55% HR with positive PnL. Below 0.50 is contrarian
losing bets. 0.70-0.80 is the danger zone (high HR but huge losses on misses).

## Compounding Ranking (top positive)

| Tag+Dir | Compound Score | Assessment |
|---------|---------------|------------|
| Sports YES | 2.15 | MISLEADING — negative PnL, compound from abs values |
| Tariffs YES | 1.60 | Genuine but tiny sample (5K) |
| Courts YES | 0.38 | Genuine but 21d hold |
| Geopolitics YES | 0.09 | Marginal |

## Tag-Aware Skilled Traders (excess >= 10pp above tag base, positive PnL, 2025)

These traders beat their tag-specific base rate by >= 10pp AND have positive realized PnL.
This is the pool a tag-aware copy strategy would target.

| Tag | Dir | Traders | Excess HR | Avg PnL | Aggregate PnL | Avg Positions |
|-----|-----|---------|-----------|---------|---------------|---------------|
| **Politics YES** | YES | 959 | +41.9pp | $345 | **$15.6M** | 63 |
| Politics NO | NO | 597 | +18.5pp | $155 | $4.6M | 69 |
| **Crypto YES** | YES | 441 | +48.5pp | $207 | **$4.3M** | 58 |
| Crypto NO | NO | 1,267 | +26.2pp | $52 | $4.2M | 46 |
| **Trump YES** | YES | 387 | +37.3pp | $142 | **$2.6M** | 57 |
| Culture YES | YES | 410 | +36.7pp | $103 | $2.2M | 64 |
| Geopolitics YES | YES | 248 | +40.1pp | $203 | **$2.1M** | 47 |
| Trump NO | NO | 322 | +22.9pp | $127 | $1.7M | 61 |
| Geopolitics NO | NO | 284 | +20.3pp | $181 | $1.6M | 44 |
| Culture NO | NO | 112 | +11.6pp | $471 | $1.1M | 49 |

**Total aggregate PnL from tag-aware skilled traders: ~$40M+ (2025)**

## Strategy Implications

1. **Tag-aware base rates are necessary but not sufficient** — the market already prices them in
2. **The real edge is in mispricing, not base rate deviation** — look for where market price ≠ true probability
3. **NO direction in extreme-NO tags** has a structural PnL advantage despite negative excess HR
4. **Tariffs YES** is the strongest signal but too small for a standalone strategy
5. **Earnings 0.50-0.70** entry is a viable niche strategy
6. **Politics YES skilled traders** are the single largest PnL source ($15.6M from 959 traders)
7. **Crypto YES** is second-largest ($4.3M from 441 traders with +48.5pp excess)
8. **Tag-aware copy strategy** targeting these pools would have ~3,500 traders generating ~$40M/yr

## Related
- `data/tag_base_rates.md` — tag-specific base rates
- `pitfalls/no_hr_collapse_tick.md` — NO HR collapse in tick-by-tick
- `signals/no_pool_contamination.md` — NO pool contamination

## Tags
`tags`, `edge`, `pnl`, `direction`, `critical`
