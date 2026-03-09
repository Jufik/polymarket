# Entry Price vs Resolution: Market Calibration and Structural Alpha

> **TL;DR**: Polymarket is well-calibrated across the price spectrum with a systematic pattern: slight underpricing of certainty (0.99 entry = 99.46% HR, +0.46pp alpha) and overpricing of long-shots (0.00-0.30 entry = 3.2% HR, -3.4pp vs break-even). This is the favorite-longshot bias, well-known in betting markets.

> [!CRITICAL]
> Entry price ≈ resolution probability across the full range. Any strategy showing high HR at high entry prices is capturing market structure, not alpha. Always compute excess HR over the price-level-specific base rate, not a global base rate. A 95% HR at 0.95 entry is ZERO alpha.

> [!WARNING]
> The 0.99 entry bucket shows +0.46pp structural alpha for ANY trader — not just elites. This is exploitable as a structural play (volume game, ~$0.76 avg profit per $100 position, 2.4h median hold) but fragile to slippage. Even 1-2 cents of execution cost wipes out the edge.

## January 2026 Price-Level Base Rates (Non-Gambling, YES Positions)

| Entry Price | Positions | Population HR | Break-Even | Alpha |
|-------------|-----------|---------------|------------|-------|
| 0.99 | 4,410 | 99.46% | 99.0% | +0.46pp |
| 0.98-0.99 | 1,945 | 97.9% | 98.5% | -0.6pp |
| 0.97-0.98 | 1,853 | 97.4% | 97.5% | -0.1pp |
| 0.96-0.97 | 1,584 | 96.9% | 96.5% | +0.4pp |
| 0.95-0.96 | 1,533 | 96.7% | 95.5% | +1.3pp |
| 0.90-0.95 | 7,547 | 95.9% | 92.5% | +3.4pp |
| 0.85-0.90 | 8,367 | 93.7% | 87.4% | +6.3pp |
| 0.70-0.85 | 19,578 | 84.7% | 79.2% | +5.4pp |
| 0.50-0.70 | 24,266 | 50.8% | 58.7% | -7.9pp |
| 0.30-0.50 | 29,602 | 28.4% | 40.1% | -11.7pp |
| 0.00-0.30 | 188,458 | 3.2% | 6.6% | -3.4pp |

## Favorite-Longshot Bias

The market systematically:
- **Underprices favorites** (0.85-0.95): +3 to +6pp alpha. Buyers of near-certainties get slightly better odds than implied.
- **Overprices long-shots** (0.30-0.70): -8 to -12pp alpha. Buyers of unlikely outcomes pay too much.
- **At the extreme** (0.99): +0.46pp structural edge — certainty is slightly underpriced.

This matches the classic favorite-longshot bias from horse racing and sports betting literature.

## Implications for Strategy Design

1. **Composite scorecard strategies** (0.30-0.70 entry): must demonstrate genuine trader selection alpha to overcome the -8 to -12pp structural headwind at these prices.
2. **High-price copy strategies** (0.85+): structural tailwind of +3-6pp. Even mediocre trader selection produces positive PnL.
3. **0.99 volume play**: structural +0.46pp is exploitable but execution-sensitive. No trader selection needed.
4. **Long-shot strategies** (<0.30): population loses money (-3.4pp). Only elite traders with demonstrated alpha (+10pp over population) can profit here.

## Evidence

DuckDB query over maker_positions + yes_entry_data, January 2026, non-gambling markets.

## Related

- `signals/entry_price_quality.md` — calibration_gap and bucket_excess_hr
- `signals/in_play_elite_traders.md` — elite vs population comparison at each price level
- `pitfalls/excess_hr_vs_absolute_hr.md` — must use price-adjusted base rates

## Tags

`calibration`, `base-rates`, `favorite-longshot-bias`, `price-level`, `structural-alpha`, `market-microstructure`
