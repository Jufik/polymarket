# NO Direction HR Collapses in Tick-by-Tick Copy Strategies

> **TL;DR**: NO direction shows 8-15pp BELOW the NO base rate in tick-by-tick copy strategies, despite showing 15-20pp ABOVE in vectorized. This is structural, not a bug.

> [!CRITICAL]
> Never assume vectorized NO HR will transfer to tick-by-tick. NO direction is systematically anti-predictive at the trade level. Consider dropping NO direction entirely or using it only as a YES-confirms signal.

> [!WARNING]
> Direction-agnostic consensus (counting ANY qualified trader regardless of their qualified direction) reduces HR by 7pp vs same-direction consensus. Always filter consensus participants by their qualified direction.

## Finding

Across two independent strategies (S1 copy, S2 hit-rate copy), NO direction HR collapses from vectorized to tick-by-tick:

| Strategy | Vectorized NO HR | Tick NO HR | NO Base Rate | Tick NO Excess |
|----------|-----------------|------------|-------------|----------------|
| S1 copy | 82% | 34% | 62% | -28pp |
| S2 HRC (Apr 25) | 89.1% | 56.3% | 71% | -14.7pp |
| S2 HRC (Jul 25) | 87.9% | 58.2% | 74% | -15.8pp |
| S2 HRC (Oct 25) | 89.5% | 54.4% | 63% | -8.6pp |

YES direction behaves much better:

| Strategy | Vectorized YES HR | Tick YES HR | YES Base Rate | Tick YES Excess |
|----------|------------------|-------------|--------------|-----------------|
| S1 copy | 80% | 87% | 38% | +49pp |
| S2 HRC (Apr 25) | 73.6% | 37.0% | 29% | +8.0pp |
| S2 HRC (Jul 25) | 73.5% | 40.1% | 26% | +14.1pp |
| S2 HRC (Oct 25) | 77.4% | 38.2% | 37% | +1.2pp |

## Mechanism

The collapse happens because:

1. **Vectorized uses NET position** (aggregate of 13+ trades). The final directional stance captures the trader's true conviction after averaging in/out. This is naturally smoother and more accurate.

2. **Tick-by-tick enters at the Nth trade** (the consensus-triggering trade). This is a single snapshot price, typically worse than the blended average.

3. **NO is the default outcome** (62-74% base rate). Copying a NO-direction trader's specific BUY of a NO token at a specific price is much less informative than knowing they ended up with a net NO position over many trades.

4. **Many NO "wins" resolve after price is already at 0.90+**. By the time tick-by-tick would enter, the value is already priced in.

5. **Direction-agnostic consensus** amplifies the problem: a YES-qualified trader buying a NO token contributes to NO-direction consensus, but their NO positions are not what made them qualified.

## Evidence

S2 HRC tick-by-tick validation (3 OOS periods, 2025):
- Validation script: `research/scripts/s2_hitrate_tick_validation.py`
- Direction diagnosis: `research/scripts/s2_hitrate_diagnose4_tick.py`

Direction-aware consensus (manual simulation, Apr 2025):
| Variant | HR | YES excess | NO excess |
|---------|-----|-----------|----------|
| Naive (any trader) | 46.1% | +8.0pp | -14.7pp |
| Same-direction | 52.9% | +17.4pp | -7.1pp |
| YES-only directed | 46.4% | +17.4pp | N/A |

## Impact

- **Any copy strategy**: Default to YES-only or require direction-aware consensus
- **Vectorized discovery**: NO HR in vectorized should be DISCOUNTED by 20-30pp for tick-by-tick
- **Strategy design**: Use NO direction only as confirming signal (e.g., "NO consensus AND YES absence"), not as primary entry
- **This applies broadly**: Any signal based on trader historical performance in a specific direction will see the same degradation pattern

## Related

- `pitfalls/vectorized_vs_tick.md` -- original 9 gaps (this is a refinement of Gap #2 and #5)
- `pitfalls/sell_is_exit.md` -- SELL misinterpretation compounds NO HR collapse
- `data/market_base_rates.md` -- NO base rate context (62-74%)
- `pitfalls/entry_price_filter_inversion.md` -- entry price divergence mechanism

## Tags

`no-direction`, `vectorized-vs-tick`, `copy-trading`, `direction-collapse`, `structural-gap`
