# Insider Entry Characteristics -- Price and Volume at Entry

> **TL;DR**: Insiders enter at very low prices (mean 0.28, median even lower) and very low market volumes (median $33). They buy early in a market's life before volume accumulates. Filtering to price < 0.65 improves HR from 83.2% to 85.9%.

> [!WARNING]
> Band price filters (0.20-0.80, 0.30-0.70) DESTROY the insider signal.
> The signal IS the low-price positions. Filtering them out removes the edge entirely.
> Only use upper-bound filters (< 0.85, < 0.75, < 0.65).

> [!TIP]
> Volume-at-entry filtering is NOT useful for HR improvement (constant ~83% across
> all thresholds). Most insiders enter at median $33 volume. Filtering by volume
> discards 84% of the profitable universe.

## Finding

Analysis of 388K strict-tier insider positions across 14 OOS months revealed two counter-intuitive facts about when insiders enter markets:

**1. Insiders buy at very low prices**

Mean avg_yes_price = 0.28, which means they are buying YES tokens at ~28 cents or NO tokens at implied ~72 cents. The entry price filter has a monotonically improving effect:
- No filter: 83.2% HR, +$45/pos
- < 0.85: 83.5% HR, +$53/pos (removes 15% of positions)
- < 0.75: 84.6% HR, +$64/pos (removes 18%)
- < 0.65: 85.9% HR, +$72/pos (removes 20%)

The positions removed (price >= 0.65) are disproportionately YES-side positions in already-likely markets. With the filter at < 0.65, YES HR drops to 29.5% but NO HR rises to 90.6%.

**2. Insiders enter very early in a market's life**

Median cumulative market volume at entry = $33. This means 50% of insider positions are taken when the market has seen less than $33 in total trading. 84% of positions have < $500 volume at entry. This explains why volume filtering doesn't help: the insiders ARE the early market-makers.

Volume filtering is not predictive of HR (constant ~83% at all thresholds) but does affect PnL per position non-monotonically. Mid-liquidity ($500-$5K) has NEGATIVE PnL, while high-liquidity (>= $5K) has very high PnL (+$183/pos) but tiny universe (13K positions).

## Evidence

SQL: `research/knowledge/queries/s2_enriched_positions.sql`, `s2_volume_at_entry_batch.sql`
Runner: `research/scripts/s2_enhancements.py`
Output: `research/output/s2_positions_with_volume.parquet`

Key stats from the enriched positions dataset (1.35M rows, 14 months):
- Strict tier: 388K positions, 4,812 unique traders, 76,561 unique markets
- Volume-at-entry distribution: mean=$1,069, median=$33, p25=$0, p75=$278

## Impact

- **Entry price filter < 0.65**: Recommended for all copy strategies. Improves HR +2.7pp and PnL +58%.
- **Band filters**: NEVER use. They contradict the finding that insiders buy LOW.
- **Volume filters**: Not recommended for primary filtering. Can be used for a small "high-conviction" sub-strategy at >= $5K.
- **Execution implication**: Since insiders enter young markets, the copy trader will also be entering illiquid markets. Slippage and spread will be high. This is likely a major source of vectorized-to-tick degradation.

## Related

- `signals/insider_copy.md` -- main strategy findings
- `pitfalls/vectorized_vs_tick.md` -- illiquid entry markets will worsen tick-by-tick degradation
- `execution/hold_time_capital.md` -- young markets may have longer hold times

## Tags

`insider`, `entry-price`, `volume-at-entry`, `market-structure`, `liquidity`, `signal-filter`
