# Track C: Scalper/Hedger Alpha Analysis

**Date**: 2026-03-07
**Sample**: Full 2025 (Jan-Dec), raw trades parquet + maker_positions
**Status**: VECTORIZED UPPER BOUND — tick-by-tick validation required

---

## Summary

A "scalper" is defined as a maker who **BUYs** then **SELLs** the same (condition_id, asset_id) within 24 hours.

Key methodology:
- Only the initial **BUY direction** is evaluated for directional alpha. SELLs are used for classification only, not as signals (per sell_is_exit.md).
- YES base rate ~39%, NO base rate ~55-67% (tag-dependent)
- Price gate [0.10, 0.80] applied to exclude near-resolution contamination

---

## 1. Scalper Population (Full 2025)

| n_scalp_positions | n_unique_scalpers | n_unique_markets | median_scalp_hours | median_spread_pct | avg_spread_pct | total_sell_vol_M | median_buy_size | median_scalp_profit | total_est_profit_M |
|---|---|---|---|---|---|---|---|---|---|
| 2,482,231 | 230,693 | 137,201 | 0.72h | 0.51% | 3.09% | $1,140.87M | $28.92 | $0.32 | $37.54M |

**Key observations:**
- 230K unique scalpers in 2025 — a large population
- Median scalp time: 43 minutes (buy-to-sell)
- Median spread: +0.51% — slight positive median, but 39% of scalps are at a loss
- Estimated aggregate profit from spreads: ~$37.5M across 2025

### Spread Distribution (sell_price - buy_price)

| Spread Bucket | n | pct |
|---|---|---|
| negative large (<-10%) | 160,406 | 6.5% |
| negative medium (-10% to -2%) | 179,965 | 7.3% |
| negative small (-2% to 0%) | 331,989 | 13.4% |
| near zero (0% to 2%) | 944,031 | 38.0% |
| small profit (2-5%) | 286,519 | 11.5% |
| medium profit (5-10%) | 205,956 | 8.3% |
| large profit (>10%) | 373,365 | 15.0% |

~39% of scalps result in a loss. ~35% earn >2% profit.

### Scalp Time Distribution

| Time Bucket | n | pct | median_spread_pct | median_vol |
|---|---|---|---|---|
| <15min | 954,855 | 38.5% | 1.33% | $24.50 |
| 15min-1h | 372,624 | 15.0% | 1.00% | $52.01 |
| 1-4h | 504,267 | 20.3% | 0.10% | $27.78 |
| 4-8h | 281,757 | 11.4% | 0.10% | $24.50 |
| 8-24h | 368,728 | 14.9% | 0.25% | $29.73 |

Ultra-short scalps (<15min) have highest spreads. Longer scalps have near-zero spreads — suggesting these are hedges/exits rather than pure spread trades.

---

## 2. Scalper Hit Rate at BUY Entry Direction

The central question: does the BUY direction predict the market outcome?

### Overall

| n_positions | n_scalpers | n_markets | hr_pct | median_buy_vol | median_spread_pct |
|---|---|---|---|---|---|
| 2,344,869 | 224,246 | 133,689 | **53.18%** | $29.63 | 0.69% |

**53.18% overall hit rate — above YES base rate (39%) and slightly above NO base rate (55% raw, ~50% after weighting).**

This is the combined YES+NO rate. Since NO markets win ~62% by default, we must look at each side separately.

### By Position Side

| bought_side | n | hr_pct | median_vol |
|---|---|---|---|
| NO | 1,179,529 | **67.06%** | $36.93 |
| YES | 1,165,340 | **39.12%** | $22.50 |

- **NO scalpers: 67.06% HR** vs NO base rate of ~62% → +5pp excess
- **YES scalpers: 39.12% HR** vs YES base rate of ~39% → near zero excess

**Initial interpretation**: Scalpers buying NO have slight edge (+5pp). Scalpers buying YES are at base rate. The combined 53.18% is largely attributable to the directional asymmetry between YES/NO base rates.

### By Scalp Time Window

| scalp_time | n | hr_pct | median_spread_pct | median_vol |
|---|---|---|---|---|
| <15min | 941,824 | 47.79% | 1.42% | $24.77 |
| 15min-1h | 360,910 | 53.85% | 1.00% | $51.65 |
| 1-4h | 455,232 | **58.16%** | 0.10% | $29.19 |
| 4-8h | 249,502 | **59.46%** | 0.10% | $25.01 |
| 8-24h | 337,401 | 56.12% | 0.30% | $30.47 |

HR increases with scalp time. However, this is the combined YES+NO rate — longer scalps may have more NO positions. Need side-specific breakdown for longer windows.

---

## 3. Serial Scalper Profiles

Top 30 by scalp count (all 2025, >= 10 scalps):

| maker | n_scalps | n_markets | entry_hr_pct | median_scalp_hours | median_spread_pct | total_est_profit | median_vol | total_vol |
|---|---|---|---|---|---|---|---|---|
| 0x4bfb41d... | 44,768 | 1,088 | 52.9% | 0.02h | 3.40% | $6,029.45 | $23.11 | $2,017,898 |
| 0xb32f21c... | 28,990 | 1,066 | 51.5% | 0.02h | 2.10% | $2,065.11 | $19.91 | $1,005,124 |
| 0xc5d563a... | 25,551 | 981 | 54.2% | 0.02h | 3.90% | $2,935.23 | $23.77 | $1,005,124 |
| (27 more below threshold) | ... | ... | ... | ... | ... | ... | ... | ... |

### Activity Tier Distribution

| activity_tier | n_traders | total_scalps | avg_hr_pct | total_vol_M |
|---|---|---|---|---|
| 100+ scalps | 3,467 | 1,154,701 | 54.0% | $476M |
| 50-99 scalps | 3,285 | 227,649 | 52.6% | $84M |
| 20-49 scalps | 11,419 | 341,847 | 55.2% | $116M |
| 10-19 scalps | 17,285 | 240,547 | 58.6% | $68M |
| 1-9 scalps | 195,237 | 517,487 | 53.4% | $397M |

High-activity traders (100+) have lower average HR (54%) than moderate traders (10-19: 58.6%). This is likely because high-frequency scalpers are market makers, not information-traders.

---

## 4. Scalper vs Non-Scalper HR (maker_positions Comparison)

Using the authoritative `maker_positions.correct` field for all resolved positions.

**Note**: `is_scalper=1` means the maker executed a scalp (BUY then SELL within 24h) in that specific (trader, market) combination. `is_scalper=0` includes both: (a) traders who never scalp, and (b) scalper-traders in their non-scalped markets.

| is_scalper | position | n_positions | n_traders | n_markets | hr_pct | median_vol_usd |
|---|---|---|---|---|---|---|
| 0 (not-scalped market) | HEDGED | 4,538,294 | 76,862 | 253,962 | 63.71% | $59.28 |
| 0 (not-scalped market) | NO | 10,482,631 | 736,185 | 336,305 | **55.32%** | $13.26 |
| 0 (not-scalped market) | YES | 10,877,492 | 740,204 | 346,430 | **39.12%** | $10.77 |
| 1 (scalped market) | HEDGED | 316,574 | 14,681 | 89,060 | 56.60% | $389.42 |
| 1 (scalped market) | NO | 457,580 | 84,854 | 91,013 | **65.28%** | $111.86 |
| 1 (scalped market) | YES | 483,496 | 93,714 | 95,300 | **47.80%** | $75.64 |

### Non-Scalper Baseline (traders who never scalp)

| position | n | n_traders | hr_pct | median_vol |
|---|---|---|---|---|
| HEDGED | 3,099,114 | 61,860 | 65.18% | $42.14 |
| NO | 7,174,300 | 676,363 | 55.36% | $10.61 |
| YES | 7,446,947 | 680,710 | 39.64% | $10.00 |

**Critical finding: In scalped markets, HR is elevated:**
- YES in scalped markets: **47.80% HR** vs non-scalped YES of 39.12% → **+8.7pp excess**
- NO in scalped markets: **65.28% HR** vs non-scalped NO of 55.32% → **+9.9pp excess**

The maker_positions `correct` field here includes ALL traders (scalpers and non-scalpers) in that market. Markets that attracted scalpers have higher overall HR — this could mean scalped markets are more "knowable" markets, or scalpers are concentrated in markets they understand better.

**Median volume contrast**: Scalped-market positions: $75-112 per position vs non-scalped: $10-13. Scalpers operate in much higher-volume markets.

---

## 5. Scalp Profitability

| profit_bucket | n | pct | median_vol | median_profit | total_profit |
|---|---|---|---|---|---|
| large profit (>5%) | 503,804 | 21.5% | $18.62 | $1.98 | $41,742,929 |
| small profit (1-5%) | 273,268 | 11.7% | $27.78 | $0.44 | $3,729,044 |
| near breakeven (0-1%) | 744,673 | 31.8% | $30.68 | $0.03 | $1,047,869 |
| small loss (-2% to 0%) | 469,618 | 20.0% | $29.54 | -$0.21 | -$3,047,698 |
| loss (>2%) | 353,506 | 15.1% | $25.01 | -$2.03 | -$9,270,494 |

Total estimated profit: ~$34M from 2.3M resolved scalp positions. 63.2% near-zero or slightly profitable. 36.8% at meaningful loss.

### Monthly Volume

| month | n_scalps | n_scalpers | buy_vol_M | est_profit_K | entry_hr_pct |
|---|---|---|---|---|---|
| 2025-01 | 214,455 | 67,434 | $70.9M | $2,567K | 53.8% |
| 2025-02 | 202,430 | 62,774 | $70.0M | $3,072K | 54.5% |
| 2025-03 | 160,793 | 50,521 | $57.4M | $2,291K | 51.4% |
| 2025-04 | 165,264 | 51,267 | $60.0M | $3,180K | 53.4% |
| 2025-05 | 176,977 | 54,698 | $62.5M | $3,143K | 52.7% |
| 2025-06 | 170,734 | 51,621 | $57.1M | $3,055K | 53.5% |
| 2025-07 | 203,523 | 56,840 | $67.7M | $3,516K | 53.8% |
| 2025-08 | 169,960 | 54,070 | $65.4M | $3,002K | 53.3% |
| 2025-09 | 188,375 | 55,601 | $66.6M | $3,038K | 53.9% |
| 2025-10 | 212,196 | 56,750 | $74.0M | $3,267K | 52.3% |
| 2025-11 | 237,834 | 57,574 | $83.8M | $3,768K | 53.1% |
| 2025-12 | 242,328 | 58,048 | $84.3M | $4,601K | 53.2% |

Consistent activity: ~$60-85M/month in scalper buy volume, steady HR of 51-55%.

---

## 6. Timing: Entry vs Resolution

| time_to_resolution | n | pct | entry_hr_pct | median_spread_pct | median_vol |
|---|---|---|---|---|---|
| <1h to resolution | 205,174 | 8.8% | **69.78%** | 2.29% | $25.00 |
| 1-4h to resolution | 1,161,803 | 49.6% | **52.12%** | 0.36% | $30.38 |
| 4-24h to resolution | 444,025 | 19.0% | **54.84%** | 1.00% | $36.00 |
| 1-3d to resolution | 196,403 | 8.4% | 52.67% | 1.23% | $34.75 |
| >3d to resolution | 337,464 | 14.4% | 44.91% | 1.90% | $19.94 |

**Key finding**: Scalpers who enter within 1h of resolution have 69.78% HR — nearly 30pp above YES base rate! This is the in-play contamination zone (outcome may already be known). The 1-4h window has 52% HR (+13pp for combined YES+NO), and 4-24h has 54.8%.

**Warning**: The <1h HR likely reflects in-play contamination. Must apply timing filter: only copy entries where hold_to_resolution > 4h.

---

## 7. Tag Breakdown

| tag | n_scalps | n_markets | n_scalpers | entry_hr_pct | median_scalp_hours | median_spread_pct | median_vol | total_vol_M | est_profit_K |
|---|---|---|---|---|---|---|---|---|---|
| Crypto | 934,252 | 63,947 | 71,269 | 53.85% | 0.10h | 2.00% | $34.18 | $380.6M | $10,380K |
| Recurring | 899,303 | 67,215 | 53,646 | 53.78% | 0.10h | 2.21% | $31.06 | $276.3M | $8,299K |
| Crypto Prices | 880,563 | 62,573 | 59,730 | 54.14% | 0.10h | 2.01% | $32.76 | $312.6M | $8,424K |
| Up or Down | 707,291 | 51,537 | 18,210 | 53.85% | 0.07h | 3.25% | $30.49 | $143.0M | $5,455K |
| Sports | 586,990 | 40,768 | 105,814 | 51.05% | 1.12h | 0.46% | $37.00 | $522.1M | $16,182K |
| Bitcoin | 520,766 | 19,846 | 41,665 | 55.07% | 0.08h | 2.00% | $38.77 | $207.3M | $6,113K |
| Politics | 453,342 | 11,217 | 113,152 | 55.47% | 3.28h | 0.17% | $25.51 | $419.9M | $5,906K |
| Games | 429,731 | 36,211 | 35,313 | 50.73% | 0.75h | 1.00% | $78.48 | $475.0M | $15,700K |
| 15M | 417,128 | 30,150 | 10,586 | 55.80% | 0.05h | 3.63% | $24.83 | $55.5M | $2,376K |
| 1H | 256,285 | 18,706 | 7,732 | 51.15% | 0.12h | 3.00% | $40.07 | $48.2M | $2,393K |
| Culture | 209,873 | 7,058 | 57,772 | 49.49% | 3.02h | 0.26% | $23.75 | $97.5M | $2,189K |
| Ethereum | 208,826 | 17,601 | 23,354 | 53.79% | 0.12h | 2.00% | $33.46 | $78.8M | $1,770K |
| World | 182,977 | 3,423 | 66,238 | **56.64%** | 3.35h | 0.10% | $26.20 | $160.5M | $2,905K |
| Trump | 167,863 | 5,301 | 56,855 | **56.62%** | 3.35h | 0.20% | $22.07 | $92.9M | $1,545K |
| Geopolitics | 135,551 | 2,704 | 42,357 | 55.82% | 3.23h | 0.30% | $33.88 | $114.6M | $2,038K |
| Trump Presidency | 131,356 | 2,545 | 54,012 | **58.99%** | 3.58h | 0.10% | $20.73 | $67.8M | $931K |
| NBA | 129,377 | 5,755 | 28,655 | 46.33% | 0.67h | 0.79% | $52.99 | $162.6M | $2,863K |
| Soccer | 117,259 | 12,005 | 31,401 | 51.94% | 1.70h | 0.25% | $31.05 | $62.6M | $3,060K |
| Global Elections | 95,725 | 1,749 | 47,540 | 56.26% | 3.38h | 0.10% | $18.40 | $82.8M | $1,422K |
| MLB | 78,702 | 3,085 | 26,610 | **57.29%** | 3.08h | 0.10% | $31.74 | $68.9M | $2,129K |

**Key tag observations:**
- **Crypto/Recurring/Up-or-Down**: High volume, very short scalp times (0.05-0.12h), high spreads — these are market-making activities on predictable price-oscillation markets
- **Politics/World/Trump/Geopolitics**: Longer scalp times (3-4h), near-zero spreads, HIGHER entry HR (55-59%) — more likely to be informed exits/hedges
- **MLB (57.29%)**: Highest HR among sports. Short-duration baseball games with known results.
- **Trump Presidency (58.99%)**: Highest HR overall among major tags
- **NBA (46.33%)**: Below combined base rate — scalpers are poor directional predictors in NBA

---

## 8. Copyability Assessment (Price Gate: 0.10-0.80)

**Note on price gate interpretation**: `avg_buy_price` is the price of the token purchased. For YES tokens, this is the YES probability. For NO tokens, this is the NO token price — a NO token at 0.80 means the YES market is at 0.20 (low-probability). The gate [0.10, 0.80] on token price filters extreme positions for both sides.

| price_gate | bought_side | n | pct_all | hr_pct | median_vol | median_spread_pct |
|---|---|---|---|---|---|---|
| a. below_gate (<0.10 long-shot) | NO | 74,286 | 3.2% | 6.97% | $3.33 | 1.00% |
| a. below_gate (<0.10 long-shot) | YES | 323,805 | 13.8% | 2.73% | $2.10 | 0.10% |
| b. in_gate (0.10-0.80) | NO | 614,743 | 26.2% | **50.64%** | $49.83 | 2.00% |
| b. in_gate (0.10-0.80) | YES | 701,320 | 29.9% | **44.92%** | $42.77 | 1.66% |
| c. above_gate (>0.80 near-resolution) | NO | 490,500 | 20.9% | **96.75%** | $34.56 | 0.10% |
| c. above_gate (>0.80 near-resolution) | YES | 140,215 | 6.0% | **94.20%** | $96.20 | 1.01% |

**Price-gated scalpers (0.10-0.80):**
- **YES in-gate: 44.92% HR** vs YES base rate of 39% → **+5.9pp excess** ✓
- **NO in-gate: 50.64% HR** (BUY-direction HR for NO tokens 0.10-0.80) — above the 50% mid-point

**Above-gate contamination**: NO tokens >0.80 have 96.75% HR (outcome nearly determined — in-play contamination confirmed). YES tokens >0.80 have 94.20% HR — same contamination.

Below-gate long-shots (YES <0.10): 2.73% HR confirms deep underdog entries are worthless.

---

## 9. High-Edge Scalper Pool (HR >= 55%, >=10 scalps)

Top 50 scalpers by entry HR show many with 100% HR on small samples (10-50 scalps). More reliable are those with larger sample sizes.

### Copy Pool Summary (HR>=55%, >=10 scalps, price-gated)

| bought_side | n_signals | n_scalpers | n_markets | hr_pct | median_vol | median_scalp_h |
|---|---|---|---|---|---|---|
| NO | 151,585 | 7,154 | 52,831 | **62.37%** | $33.12 | 0.30h |
| YES | 152,123 | 7,609 | 52,065 | **58.55%** | $29.36 | 0.27h |

**High-edge pool signals per year: ~150K YES + 151K NO = ~300K signals/year from 7K-7.6K traders**

- YES: 58.55% HR vs 39% base rate = **+19.6pp excess (VECTORIZED UB)**
- NO: 62.37% HR vs 62% base rate = **+0.4pp excess** (negligible for NO side)

The **YES signal is the primary alpha** here. High-edge scalpers buying YES hit 58.55% — a substantial excess.

### Profit Quintile vs Entry HR

| profit_quintile | n_traders | avg_entry_hr_pct | avg_spread_pct | avg_n_scalps | avg_med_vol | total_profit |
|---|---|---|---|---|---|---|
| 1 (lowest profit) | 11,340 | 59.79% | -1.15% | 26.1 | $311 | -$9,230,951 |
| 2 | 11,339 | 74.94% | 0.01% | 6.9 | $66 | -$415 |
| 3 | 11,339 | 67.05% | 0.07% | 7.3 | $18 | $3,806 |
| 4 | 11,339 | 43.19% | 2.87% | 13.6 | $73 | $153,816 |
| 5 (highest profit) | 11,339 | 55.22% | 8.71% | 124.2 | $599 | $39,213,586 |

**Counter-intuitive finding**: The most profitable scalpers (quintile 5, 124 scalps avg) have **lower entry HR (55.22%)** than quintile 2 traders (74.94% entry HR). This suggests profitable scalping is an execution/spread skill, NOT a directional skill. The high-HR traders in Q1-Q3 are directionally skilled but not spread-proficient.

---

## 10. Edge Persistence: Scalper Held Positions

Do traders who scalp (quick round-trips) also perform better on their HELD positions?

| position_type | position | n | n_traders | hr_pct | median_vol |
|---|---|---|---|---|---|
| held | HEDGED | 1,442,530 | 16,505 | 60.52% | $121.96 |
| held | NO | 3,352,438 | 65,164 | **55.45%** | $20.20 |
| held | YES | 3,483,678 | 65,356 | **37.93%** | $14.85 |
| scalped | HEDGED | 313,224 | 11,645 | 56.69% | $392.79 |
| scalped | NO | 413,473 | 46,748 | **64.56%** | $121.42 |
| scalped | YES | 430,363 | 47,636 | **49.38%** | $84.82 |

**Note**: "scalped" here means these are the maker_positions for (trader, market) pairs where the trader executed a scalp in that same market. "Held" means positions in OTHER markets where the same trader did NOT scalp.

Scalper-class traders on their HELD YES: **37.93%** vs non-scalper YES: **39.64%** — *slightly below* baseline. This suggests scalpers are not inherently better directional traders on held positions. Their edge comes from the scalp itself (timing and spread capture).

---

## 11. Spread Quintile vs Held HR (Correlation)

Do higher-spread (more profitable) scalpers also make better directional bets when they hold?

| spread_quintile | n_traders | avg_scalp_entry_hr | avg_held_hr | avg_spread_pct | avg_n_scalps |
|---|---|---|---|---|---|
| 1 (lowest spread) | 3,661 | 60.37% | 27.36% | -2.89% | 41.3 |
| 2 | 3,660 | 51.74% | 20.49% | 0.05% | 43.8 |
| 3 | 3,660 | 51.66% | 41.82% | 1.02% | 192.9 |
| 4 | 3,660 | 53.54% | 40.67% | 3.52% | 133.5 |
| 5 (highest spread) | 3,660 | 61.89% | 32.75% | 17.10% | 68.6 |

No clear monotonic relationship. Q3 (1% spread) traders have highest held HR (41.82%). Q1 (loss-making scalpers) have lowest held HR (27.36%). Overall: **spread profitability does not predict held-position directional skill**.

### Entry HR Quintile vs Held HR

| entry_hr_quintile | n_traders | avg_scalp_entry_hr_pct | avg_held_hr_pct | avg_spread_pct | avg_n_scalps |
|---|---|---|---|---|---|
| 1 (lowest HR) | 3,661 | 19.13% | 26.18% | 0.43% | 87.1 |
| 2 | 3,660 | 44.25% | 36.01% | 3.02% | 133.4 |
| 3 | 3,660 | 56.58% | 36.88% | 4.10% | 153.9 |
| 4 | 3,660 | 70.36% | 31.91% | 5.20% | 48.6 |
| 5 (highest HR) | 3,660 | 88.89% | 32.11% | 6.05% | 57.1 |

**Scalp entry HR does NOT predict held-position HR.** Quintile 5 (89% scalp entry HR) has 32% held HR — below the YES base rate of 39%. High-HR scalpers appear to be skilled at specific short-term timing (scalp entry), but this does NOT transfer to their longer-term positions.

**Implication**: The scalper's scalp ENTRY is where the alpha lives, not their broader trading behavior.

---

## 12. Assessment and Conclusions

### Signal Strength

| Signal | HR | Base Rate | Excess | Volume |
|---|---|---|---|---|
| All scalpers (YES) | 39.12% | 39% | +0.1pp | 1.17M positions/year |
| All scalpers (NO) | 67.06% | 62% | +5.1pp | 1.18M positions/year |
| Price-gated YES (token price 0.10-0.80) | 44.92% | 39% | **+5.9pp** | 701K positions/year |
| High-edge pool YES (HR>=55%, >=10 scalps, price-gated) | 58.55% | 39% | **+19.6pp** | 152K positions/year |
| Within 1h of resolution (YES+NO combined) | 69.78% | ~50% | **+20pp** | 205K/year — CONTAMINATED |

### Copyability Assessment: YES

The YES scalper signal shows genuine alpha when filtered:

1. **Price gate [0.10, 0.80]** on token buy price: Removes long-shot and near-resolution contamination. +5.9pp excess.
2. **Pool filter (serial scalpers, HR>=55%, >=10 scalps, price-gated)**: Selects high-skill scalpers. +19.6pp excess.
3. **Timing filter**: Exclude entries within 4h of resolution (in-play contamination).

**Estimated vectorized HR for copy signal: 58.55% (UB)**
**YES base rate: ~39%**
**Excess: +19.6pp (VECTORIZED UPPER BOUND)**

**Important caveat**: The high-edge pool (HR>=55%) is computed on the same data used to identify those traders. This is a vectorized look-ahead bias — in live trading, we'd only know their historical HR, not their 2025 future HR. The true out-of-sample excess will be lower.

### Copyability Assessment: NO

NO scalpers (even price-gated) show 50.64% HR — the directional interpretation for NO tokens in [0.10, 0.80] is not straightforward. A NO token at 0.50 means the market is 50/50. The 50.64% HR is near random. **NO scalper copy is not worth pursuing.**

### Edge Persistence (Key Finding)

From Step 13 (scalper-class traders, >=5 scalps):
- Scalped markets YES: **49.38% HR** (in-market, same as the scalp itself)
- Held markets YES (same trader, different markets): **37.93% HR** — at or below baseline (39%)

Scalper-class traders do **NOT** show persistent directional skill on their held positions. Their edge is specifically in the markets they scalp, not transferable across their broader portfolio. This means:
- **Copy strategy must be reactive**: copy the BUY entry in real-time as it happens, NOT follow the trader's general portfolio
- The signal is the specific scalp entry itself — not the trader type generically

### Strategy Design (if pursuing)

**"Scalper-Copy YES"** strategy outline:
1. **Pool construction (rolling 90d)**: Identify traders with >=10 price-gated scalps (avg_buy_price 0.10-0.80, YES only) + scalp-entry HR >= 55%
2. **Signal trigger**: When a pool-member executes a fresh BUY on a YES token at price 0.10-0.80
3. **Timing gate**: Only fire if market has >4h to resolution (filter in-play contamination)
4. **Entry**: Copy BUY at observed price (live: delay of 5-30 seconds expected from on-chain detection)
5. **Volume filter**: Only fire when buy_amount >= $10 (filter micro-bets)
6. **Hold**: To resolution — do NOT copy their subsequent SELL
7. **No re-entry**: One position per market

### Compounding Score Estimate

- Excess HR at entry: +19.6pp (vectorized UB, same-period pool selection — optimistic)
- Out-of-sample pool HR degradation: expect -5 to -10pp (pool selection bias)
- Tick degradation (signal delay, price impact): -5 to -15pp
- Expected realistic tick-validated excess: **+0 to +10pp**
- Median hold time: ~1 day for scalped markets
- Avg edge per YES position: ~$12-20 (median vol $29, price ~0.45)

**CS estimate (optimistic)**: (0.10) × $15 / 1 = **1.50**
**CS estimate (pessimistic)**: (0.02) × $15 / 1 = **0.30**

This is a moderate to marginal signal. The key risk is that pool selection used same-period data — true OOS degradation could eliminate the excess entirely.

### Alternative Signal: Market-Level Scalper Concentration

The Section 4 finding (scalped markets have +8.7pp YES excess HR) suggests a different approach: **markets that attract scalpers are higher-quality markets**. A consensus-style signal where N scalpers BUY YES in the same market within a short window might show stronger, less biased excess HR.

### Recommendation: CONDITIONAL GO — with critical caveats

1. **Vectorized UB is likely optimistic**: Pool selection used the same 2025 period. True OOS is unknown.
2. **Edge persistence is market-specific, not trader-generic**: The copy must be trade-level, not trader-level.
3. **In-play contamination is severe**: Must apply >4h-to-resolution gate.
4. **The real signal may be market-level**: Scalper concentration in a market (N scalpers BUY YES) rather than individual scalper copying.

**Suggested next step**: Tick-by-tick validation on 2025 H1 universe with OOS test on 2025 H2, using the high-edge pool (HR>=55%, >=10 scalps, rolling 90d window, price 0.10-0.80 YES, hold_to_resolution > 4h). Expected to reveal significant degradation from the +19.6pp vectorized UB.

---

## 13. Artifacts

- Script: `research/hypotheses/in-play-traders/scripts/track_c_scalpers.py`
- Log: `tmp/track_c_scalpers.log`
- Supplementary: `tmp/track_c_supplementary.txt`
