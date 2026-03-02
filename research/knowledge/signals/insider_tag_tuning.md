# Per-Tag Parameter Tuning for Insider Copy Strategy

> **TL;DR**: Tick-by-tick VALIDATED (2026-03-02). Sports is the standout: 74.3% HR, +13.5pp NO excess, 4-8pp vectorized gap (smallest of any category). Politics/culture/other/weather all profitable but with negative excess HR against their high NO base rates. Crypto confirmed NO-GO. Esports marginal. Entry price filter confirmed suboptimal.

> [!TIP]
> TICK-BY-TICK VALIDATED. Sports C>=4 is the champion: 74.3% HR, only -4.1pp from vectorized, positive excess HR (+13.5pp NO). Deploy immediately.

> [!WARNING]
> Culture/weather have 70%+ absolute HR but NEGATIVE excess HR (-17pp) because their NO base rates are 86-89%. The 70% HR looks good but is actually 17pp below naive NO-only.

> [!WARNING]
> Entry price filter < 0.65 CONFIRMED suboptimal in tick-by-tick for all categories. Higher PnL but lower HR. Use no-filter for stability.

> [!WARNING]
> Hold times are MUCH longer in tick-by-tick than vectorized: sports 33d vs 1d, politics 25d vs 7d. Vectorized median hold was severely underestimated. Capital recycling is slower than expected.

## Finding

Walk-forward analysis across 5 OOS months (Jan25, Apr25, Jul25, Oct25, Jan26) with 12-month training windows. Strict tier insiders (train_hr >= 0.75, high_pct >= 0.20, train_hr < 0.99).

### Per-Category Base Rates (susceptible markets, excl gambling)

| Category | YES Base | NO Base | N Resolved |
|----------|----------|---------|------------|
| politics | 24.5% | 75.5% | 23,462 |
| sports | 38.7% | 61.3% | 189,701 |
| esports | 45.6% | 54.4% | 46,608 |
| culture | 11.4% | 88.6% | 17,394 |
| finance | 37.7% | 62.3% | 4,416 |
| weather | 14.1% | 85.9% | 11,342 |
| crypto | 23.4% | 76.6% | 1,799 |
| other | 29.6% | 70.4% | 8,276 |

Culture and weather have extremely high NO base rates (89%, 86%). Any strategy in these categories must clear a very high bar on the NO side.

### Hold Time Distributions

| Category | Median | P90 | Avg |
|----------|--------|-----|-----|
| esports | 0d | 6d | 1.9d |
| sports | 1d | 59d | 15.7d |
| weather | 1d | 4d | 3.4d |
| culture | 5d | 55d | 18.9d |
| crypto | 6d | 64d | 21.5d |
| politics | 7d | 71d | 25.0d |
| other | 14d | 76d | 32.3d |
| finance | 18d | 93d | 37.7d |

### Best Per-Category Parameters (vectorized upper bounds)

| Category | Best Cons | MaxPx | N | HR | Excess HR | $/pos | Med Hold | Comp |
|----------|-----------|-------|---|---|-----------|-------|----------|------|
| sports | >= 4 | none | 2,404 | 78.4% | +23.7pp | $78.69 | 1d | 1,867 |
| politics | >= 5 | none | 4,044 | 83.8% | +19.3pp | $46.86 | 5d | 181 |
| other | >= 3 | none | 1,537 | 80.0% | +20.4pp | $64.94 | 11d | 121 |
| culture | >= 5 | none | 1,404 | 80.8% | +6.1pp | $26.50 | 4d | 40 |
| weather | >= 3 | none | 1,150 | 83.0% | +4.0pp | $8.59 | 1d | 35 |
| finance | >= 2 | none | 693 | 79.2% | +25.3pp | $1.84 | 8d | 6 |
| crypto | ANY | ANY | - | ~79-85% | +14-20pp | **-$28 to -$84** | 4-9d | **NEGATIVE** |
| esports | ANY | ANY | - | ~71-85% | +20-31pp | **-$185 to -$1238** | 0d | **NEGATIVE** |

### Max Hold Time Impact

Sports: 7-day cap captures 83% of total PnL ($4.08M of $4.92M) with 66% of positions.
Politics: 30-day cap captures 44% of PnL ($4.08M of $9.20M). Long-hold politics positions are MORE profitable per position.

### Per-Tag vs Global Improvement

Direct compounding improvement: ~6% (weighted average).
Main benefits: (1) excluding crypto/esports saves ~$115K negative drag, (2) adding sports provides 10x capital efficiency, (3) higher consensus for politics improves per-position quality.

## Tick-by-Tick Validation Results (2026-03-02)

Validated using `research/scripts/s2_tick_tag_validation.py`. Walk-forward: train 12mo, test 1mo.
Test months: 2025-07, 2025-10, 2026-01. Capital: $5,000/pool, $50/position, max 50 open.
RealisticFillSimulator with fallback spreads, asset_id-based resolution, mid-replay settlement, unique-trader consensus.

### Best No-Filter Configs (p<1.00) -- Aggregated across 3 test months

| Category | Best Cons | Fills | Resolved | HR | NO Count | NO HR | NO ExHR | PnL | $/pos | Vec HR | Gap |
|----------|-----------|-------|----------|-----|---------|-------|---------|-----|-------|--------|-----|
| **sports** | **>= 4** | **323** | **257** | **74.3%** | **254** | **74.8%** | **+13.5pp** | **$100K** | **$391** | **78.4%** | **-4.1pp** |
| culture | >= 4 | 431 | 396 | 70.5% | 386 | 71.2% | -17.4pp | $173K | $436 | 80.8% | -10.3pp |
| weather | >= 2 | 725 | 701 | 71.6% | 687 | 72.3% | -13.6pp | $172K | $245 | 83.0% | -11.4pp |
| other | >= 2 | 263 | 225 | 69.8% | 216 | 71.8% | +1.4pp | $183K | $813 | 80.0% | -10.2pp |
| politics | >= 3 | 374 | 313 | 69.3% | 304 | 70.4% | -5.1pp | $136K | $435 | 83.8% | -14.5pp |
| finance | >= 3 | 185 | 135 | 66.7% | 133 | 67.7% | +5.4pp | $15K | $113 | 79.2% | -12.5pp |
| crypto | >= 5 | 209 | 167 | 55.7% | 155 | 56.1% | -20.5pp | $14K | $86 | 82.0% | -26.3pp |
| esports | >= 3 | 346 | 324 | 54.3% | 303 | 54.5% | +0.1pp | $5K | $16 | 78.0% | -23.7pp |

### Key Finding: Vectorized-to-Tick Degradation by Category

| Degradation Range | Categories | Interpretation |
|-------------------|------------|----------------|
| **4-7pp (excellent)** | **sports** | Vectorized holds up well; entry timing aligns with trade prices |
| 10-12pp (good) | culture, weather, other | Moderate degradation, still profitable |
| 12-16pp (expected) | politics, finance | Within normal range |
| 24-29pp (high) | esports, crypto | Severe degradation; vectorized is misleading for these |

### Why Sports Has Smallest Degradation

1. **Short-resolution markets**: sports events resolve quickly (games end same day), so entry price at consensus trigger is close to the blended vectorized average
2. **NO excess is genuinely positive**: +13.5pp above 61.3% NO base rate -- this is real alpha, not base-rate artifact
3. **Capital recycling**: even though tick hold times are longer than vectorized (33d vs 1d), sports still settles faster than politics (25d) or culture (23d)
4. **Fewer capital rejections**: sports C>=4 has 323 fills vs politics C>=3 with 374 -- similar throughput

### Why Culture/Weather Look Good but Have Negative Excess

Culture NO base = 88.6%, weather NO base = 85.9%. A strategy needs NO HR above these thresholds to show genuine alpha. Culture tick NO HR is 71.2% -- that is 17.4pp BELOW the base rate. The 70.5% absolute HR is misleading: a naive "always bet NO in culture markets" would get 88.6%.

### Entry Price Filter Inversion (confirmed per-category)

| Category | No Filter HR | Filter HR (p<0.65) | Delta | No Filter PnL | Filter PnL |
|----------|-------------|-------------------|---------|--------------|-----------|
| sports | 74.3% | 66.4% | -7.9pp | $100K | $392K |
| politics | 69.3% | 61.5% | -7.8pp | $136K | $664K |
| culture | 70.5% | 60.6% | -9.9pp | $173K | $1,204K |
| weather | 71.6% | 61.6% | -10.0pp | $172K | $466K |

The price filter consistently LOWERS HR by 8-10pp but RAISES PnL 3-7x across ALL categories. This is the same mechanism as the global inversion: low-price entries have enormous per-position upside on wins.

### Hold Time: Vectorized vs Tick-by-Tick

| Category | Vec Med Hold | Tick Avg Hold | Tick Med Hold |
|----------|-------------|---------------|---------------|
| sports | 1d | 33d | 8d |
| politics | 7d | 25d | 8-10d |
| culture | 5d | 24d | 5-6d |
| weather | 1d | 5d | 1d |
| esports | 0d | 3d | 0d |

Weather and esports have similar hold times. Sports and politics are MUCH longer in tick-by-tick -- the vectorized estimate used resolved positions (which settled), while tick-by-tick includes positions that wait months for resolution.

### Go/No-Go Decisions (TICK-BY-TICK VALIDATED)

| Category | Decision | Rationale |
|----------|----------|-----------|
| **sports** | **GO** | 74.3% HR, +13.5pp NO excess, $100K PnL, smallest degradation |
| politics | GO (conditional) | 69.3% HR, positive PnL but negative NO excess (-5.1pp). Profitable due to asymmetric payoffs, not true alpha. |
| culture | GO (conditional) | 70.5% HR, positive PnL but deeply negative NO excess (-17.4pp). Monitor closely. |
| other | GO (conditional) | 69.8% HR, marginal NO excess (+1.4pp). Good PnL but thin alpha. |
| weather | GO (conditional) | 71.6% HR, high throughput (701 res), but NO excess -13.6pp. Volume champion, thin alpha. |
| finance | GO (conditional) | 66.7% HR, small sample (135 res), NO excess +5.4pp. Too few positions for confidence. |
| **crypto** | **NO-GO** | 55.7% HR, NO excess -20.5pp, near-zero PnL. Confirmed negative alpha. |
| esports | MARGINAL | 54.3% HR, near-zero PnL ($5K total), NO excess +0.1pp. Not worth the complexity. |

## Evidence

Walk-forward SQL: adapted from `queries/s2_category_tags.sql` with per-category consensus counts.
Notebook: `research/notebooks/S2_tag_tuning.py`
Parameter sweep script: `/tmp/s2_tag_sweep_fix.py` (212,970 enriched positions, 36,649 unique market-sides after dedup)
Hold time script: `/tmp/s2_tag_holdtime.py`

**Tick-by-tick validation:**
Per-tag validation script: `research/scripts/s2_tick_tag_validation.py`
Output: `research/output/s2_tick_tag/per_tag_all.parquet` + per-category parquet files
SQL queries: `research/knowledge/queries/s2_tick_insider_pool.sql`, `s2_tick_resolutions.sql`

## Impact

### Updated Recommendations (tick-by-tick validated)

1. **DEPLOY SPORTS FIRST**: C>=4, no price filter. Only category with genuinely positive excess HR in tick-by-tick. 74.3% HR, $391/pos.
2. **EXCLUDE CRYPTO**: Confirmed NO-GO at tick level. 55.7% HR, -20.5pp NO excess.
3. **EXCLUDE ESPORTS**: Marginal at best. 54.3% HR, near-zero PnL. Not worth operational complexity.
4. **CONDITIONAL DEPLOY** politics/culture/other/weather: positive PnL but negative or marginal NO excess. These profit from asymmetric payoffs at low entry prices, not from genuine alpha over base rates.
5. **REMOVE ENTRY PRICE FILTER**: Confirmed suboptimal at tick level for ALL categories (8-10pp HR reduction).
6. **RECALIBRATE HOLD TIME EXPECTATIONS**: vectorized drastically underestimates hold times. Budget for 8-33d holds, not 1-7d.
7. **LOWER CONSENSUS FOR POLITICS**: C>=3 beats C>=5 in tick (69.3% vs 67.4% HR, similar PnL). The vectorized preference for C>=5 does not survive tick validation.

### Revised Pool Structure (tick-by-tick validated)

| Pool | Categories | Consensus | MaxPx | Priority | Confidence |
|------|-----------|-----------|-------|----------|------------|
| s2_sports | sports | >= 4 | none | HIGHEST | HIGH (positive excess HR) |
| s2_politics | politics, other | >= 3 | none | MEDIUM | CONDITIONAL (negative excess, positive PnL) |
| s2_misc | culture, weather, finance | >= 2 | none | LOW | CONDITIONAL (negative excess, positive PnL) |
| EXCLUDED | crypto, esports | - | - | - | NO-GO |

### Key Insight: Absolute HR vs Excess HR Discrepancy

Most categories show "high HR" (65-72%) that is actually BELOW their NO base rates. This means the strategy is less accurate than naive "always NO" in those categories. The positive PnL comes from entry at very low prices (mean 0.22-0.35), creating asymmetric payoffs: wins pay $100-500 on $50 bets while losses cap at $50. This is a DIFFERENT thesis than "insiders know the outcome" -- it is closer to "insiders enter cheap markets with positive expected value even at below-random accuracy."

Only sports and finance show genuinely positive NO excess HR in tick-by-tick. These are the only categories where insiders demonstrably predict outcomes better than base rates after accounting for the direction mix.

## Related

- `signals/insider_copy.md` -- base S2 strategy findings and global tick validation
- `pitfalls/entry_price_filter_inversion.md` -- price filter inversion (confirmed per-category)
- `execution/hold_time_capital.md` -- hold time drives capital efficiency
- `data/market_base_rates.md` -- per-category base rates used for excess HR
- `pitfalls/category_column_null.md` -- must use tag chain, not m.category

## Tags

`insider`, `per-category`, `parameter-tuning`, `tag-based`, `compounding`, `vectorized`, `tick-by-tick`, `validated`, `walk-forward`, `hold-time`, `consensus`, `entry-price`, `excess-hr`, `base-rate`
