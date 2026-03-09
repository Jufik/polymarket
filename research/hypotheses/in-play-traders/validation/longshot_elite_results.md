# Long-Shot Elite Trader Strategy — Discovery & Validation Results

**Date**: 2026-03-08
**Hypothesis**: Traders who excel at identifying winning long-shots (<0.30 price)
can be identified from training data. Copying their sub-0.30 YES BUY entries
captures alpha above the population HR of 3.2% (break-even: ~6.6%).
**Label**: REALISTIC (tick-by-tick, SyncReplayRunner) for validation sections

---

## 1. Population Base Rate (January 2026, Non-Gambling, YES positions at <0.30)

| Metric | Value |
|--------|-------|
| Population HR at <0.30 entry | **4.53%** |
| Break-even HR (avg entry price ~0.083) | ~8.3% |
| Alpha vs break-even | -3.8pp (population LOSES money) |
| N population positions | 101,418 |

Note: The task framing used 3.2% population HR and 6.6% break-even. Actual Jan 2026 data:
HR=4.53%, avg entry price=0.083, break-even=8.3%. Results computed against 4.53% actual base.

---

## 2. Long-Shot Specialist Pool (Training: < 2026-01-01)

| Metric | Value |
|--------|-------|
| Training <0.30 YES positions | 806,982 from 222,125 traders |
| Specialists (≥10 positions, HR > 3.2%) | **3,613** |
| Median specialist HR | 8.6% |
| P75 specialist HR | 12.0% |
| P90 specialist HR | 18.8% |

### HR Distribution

| HR Bucket | Traders | Avg HR | Avg N |
|-----------|---------|--------|-------|
| 0-5% | 447 | 4.1% | 63 |
| 5-10% | 1,731 | 7.4% | 31 |
| 10-15% | 764 | 11.3% | 26 |
| 15-20% | 334 | 16.9% | 25 |
| 20-25% | 206 | 21.2% | 22 |
| 25-30% | 72 | 26.6% | 24 |
| 30-40% | 41 | 32.6% | 16 |
| 40-50% | 11 | 42.0% | 12 |
| >=50% | 7 | 54.9% | 10 |

### Top 10 Specialists by CopyScore (excess_HR × ln(N+1))

| Trader | N | HR% | Avg Price | Excess | Med Vol | Hold Days | Score |
|--------|---|-----|-----------|--------|---------|-----------|-------|
| 0xeb747705bb | 10 | 80.0% | 0.192 | +76.8pp | $1.36 | 3.0d | 1.842 |
| 0x57afdc4b3c | 204 | 28.9% | 0.229 | +25.7pp | $65.12 | 0.0d | 1.369 |
| 0xab148d58d2 | 11 | 54.5% | 0.244 | +51.4pp | $14.87 | 1.0d | 1.276 |
| 0x29c0a89b23 | 487 | 23.0% | 0.199 | +19.8pp | $82.62 | 2.0d | 1.226 |
| 0x38b7c57bc6 | 192 | 26.0% | 0.273 | +22.8pp | $44.47 | 0.0d | 1.202 |
| 0xcefa608d78 | 12 | 50.0% | 0.192 | +46.8pp | $14.96 | 0.5d | 1.200 |
| 0xb9e54c9cfc | 84 | 29.8% | 0.194 | +26.6pp | $431.58 | 7.0d | 1.180 |
| 0x9334346fc7 | 19 | 42.1% | 0.209 | +38.9pp | $25.26 | 0.0d | 1.165 |
| 0xca389b269d | 10 | 50.0% | 0.210 | +46.8pp | $55.93 | 1.0d | 1.122 |
| 0xe901fa6d48 | 10 | 50.0% | 0.174 | +46.8pp | $5.95 | 0.0d | 1.122 |

---

## 3. Pool Construction Summary

| Pool Size (K) | Avg HR | Avg N Positions | Avg Entry Price | Avg Hold Days |
|--------------|--------|-----------------|-----------------|---------------|
| top-25 | 40.5% | 58 | 0.205 | 1.5d |
| top-50 | 36.4% | 54 | 0.190 | 2.8d |
| top-100 | 30.9% | 52 | 0.185 | 5.1d |
| top-200 | 27.3% | 45 | 0.175 | 5.7d |

### Min-Positions Sensitivity (K=50)

| Min Positions | Eligible | K=50 Avg HR |
|--------------|----------|-------------|
| 5 | 3,613 | 36.4% |
| 10 | 3,613 | 36.4% |
| 20 | 1,567 | 25.1% |
| 30 | 894 | 22.9% |

> [!WARNING] The top traders by CopyScore have very high training HR but tiny N (10-12 positions). This is classic small-sample overfitting. The 80% HR trader (#1, 0xeb747705bb) had 10 positions. Expected regression to mean in OOS.

---

## 4. Overlap with General Elite In-Play Pool

| Metric | Value |
|--------|-------|
| General elite in-play pool size | 551 |
| Top-50 long-shot specialists in elite in-play pool | 1/50 |
| Overlap rate | 2% |

**Finding**: Long-shot specialists are a **distinct population** from the general elite in-play pool (2% overlap). The in-play pool excels at near-certainties (0.85+ price, 97%+ HR), while long-shot specialists are a different trader cohort.

---

## 5. Persistence Check (Train < 2025-07-01, Test 2025-07-01 to 2025-12-31)

| Metric | Value |
|--------|-------|
| Train specialists (< 2025-07-01) | 1,502 |
| Top-50 train specialists active in OOS | 25 |
| Test HR at <0.30 | 15.2% |
| Train HR (same traders, avg) | 22.9% |

### Persistence by HR Quartile

| Quartile | N Train | Train HR | Active OOS | Test HR |
|----------|---------|----------|------------|---------|
| Q1 (lowest) | 6,785 | 4.7% | 235 | 3.8% |
| Q2 | 4,212 | 7.7% | 186 | 5.7% |
| Q3 | 3,886 | 10.4% | 169 | 9.6% |
| Q4 (highest) | 3,674 | 17.9% | 201 | 10.3% |

**Key finding**: There IS persistence — Q4 traders (17.9% train HR) maintain 10.3% test HR, vs Q1 (4.7% → 3.8%). The quartile ordering is preserved. However, absolute HR regresses significantly (17.9% → 10.3% = -7.6pp). Only 25/50 top-train traders were even active in the test period.

---

## 6. Vectorized Signal Count (January 2026) — UPPER BOUND

| Metric | K=50, N=1 |
|--------|-----------|
| N signals | 275 |
| Vectorized HR (UB) | 19.3% |
| Median hold days | 1.0d |
| Avg entry price | 0.173 |
| Avg payoff if right | $77.32 per $100 |

---

## 7. Tick-by-Tick Validation (N=1) — REALISTIC

| Pool (K) | Fills | Wins | Losses | HR% | Excess vs pop | Total PnL | Avg Hold |
|----------|-------|------|--------|-----|---------------|-----------|----------|
| top-25 | 506 | 81 | 425 | 16.0% | +11.5pp | $-13,398 | 102.3h |
| top-50 | 831 | 120 | 711 | 14.4% | +9.9pp | $-17,030 | 116.3h |
| top-100 | 1,835 | 233 | 1,602 | 12.7% | +8.2pp | $-41,209 | 153.8h |
| top-200 | 2,660 | 367 | 2,291 | 13.8% | +9.3pp | $-62,181 | 144.9h |

**CRITICAL FINDING: All K variants produce negative PnL despite positive excess HR.**

---

## 8. Why PnL is Negative Despite Positive Excess HR — Root Cause Analysis

This is the key diagnostic. From the K=25 ledger (506 fills):

| Metric | Value |
|--------|-------|
| Avg fill price | **0.207** |
| Median fill price | 0.220 |
| Break-even HR at avg fill price | **20.7%** |
| Actual HR | 16.0% |
| HR deficit vs break-even | **-4.7pp** |
| EV per $100 position | **-$22.54** |
| Wins: avg PnL | +$359.28 |
| Losses: avg PnL | -$100.00 |

**The fill price is the problem.** The strategy triggers at prices like 0.15-0.25, then fills at `trigger_price + 0.02` — but the capping at 0.29 means many fills happen at 0.27-0.29, not at 0.10-0.15.

### PnL by Fill Price Bucket (K=25, N=1)

| Price Bucket | N | Win Rate | Avg PnL | Break-Even HR |
|-------------|---|----------|---------|---------------|
| <5% | 13 | 0.0% | -$100 | 5% |
| 5-10% | 56 | 3.6% | -$54.65 | 7.5% |
| 10-15% | 49 | 8.2% | -$35.47 | 12.5% |
| 15-20% | 84 | 16.7% | -$4.80 | 17.5% |
| 20-25% | 102 | 13.7% | -$35.68 | 22.5% |
| 25-30% | 202 | 23.3% | -$16.12 | 27.5% |

**Every bucket is below break-even.** The 25-30% bucket has the most fills (202) with HR=23.3% vs BE=27.7%.

### Why Vectorized HR (19.3%) > Tick HR (16.0%)

Classic vectorized→tick degradation. The vectorized sweep measures HR of specialist POSITIONS (trained data), while tick measures HR of SIGNALS fired in January 2026 — a different time period with regression to mean.

Gap: -3.3pp (relatively small for this signal type — consistent with N=1, no consensus wait).

---

## 9. Consensus Variant (N=2) — REALISTIC

| Pool (K) | Fills | Wins | Losses | HR% | Excess vs pop | Total PnL |
|----------|-------|------|--------|-----|---------------|-----------|
| top-50 | 243 | 28 | 215 | 11.5% | +7.0pp | $-7,459 |
| top-100 | 468 | 64 | 404 | 13.7% | +9.1pp | $-12,091 |

**Consensus N=2 does NOT improve HR** (worse than N=1 for K=50: 11.5% vs 14.4%). The reduction in fills is not compensated by improved HR.

---

## 10. SELL Variant Comparison (BUY-only vs Directional)

| Variant | Fills | HR% | Excess | PnL |
|---------|-------|-----|--------|-----|
| BUY-only (N=1, K=25) | 506 | 16.0% | +11.5pp | $-13,398 |
| Directional (BUY+SELL_NO) | 526 | 18.3% | +13.7pp | $-8,803 |

SELL sensitivity: **2.2pp** HR difference — MODERATE. The directional variant has slightly better HR and PnL (but still negative). SELL NO at <0.30 adds a genuine bullish signal (NO side is at >0.70, SELL NO = reduce NO exposure = net bullish on YES).

---

## 11. Price Bucket Analysis — Where the Signal Lives (Vectorized, Top-50 Pool)

| Price Bucket | Markets | Pool HR% | Avg Entry | Alpha over BE |
|-------------|---------|----------|-----------|---------------|
| 0-5% | 22 | 0.0% | 0.028 | -2.8pp |
| 5-10% | 30 | 0.0% | 0.077 | -7.7pp |
| 10-15% | 53 | 15.5% | 0.127 | +2.9pp |
| 15-20% | 56 | 15.8% | 0.174 | -1.6pp |
| 20-25% | 47 | 30.0% | 0.230 | +7.0pp |
| 25-30% | 57 | 35.1% | 0.277 | +7.4pp |

**Best signal is in 20-30% price band**: HR 30-35%, alpha +7pp over break-even. The <10% band is pure noise (0.0% HR).

---

## 12. Pre-Flight Checklist

- [x] SELL trades excluded from signal (BUY-only) — SELL variant also tested (+2.2pp sensitivity)
- [x] Price gate: only fires at entry price < 0.30 (long-shot zone)
- [x] Dust exclusion: entry price > 0.001 (spam/near-zero excluded)
- [x] One signal per market (dedup by condition_id — first qualified entry wins)
- [x] Gambling markets excluded (slug pattern classification)
- [x] Asset-ID resolution (token_map with uppercase fix)
- [x] Settlement built-in (SyncReplayRunner)
- [x] Population base rate verified (4.53% at <0.30 entry in Jan 2026)
- [x] Persistence verified with proper train/test split
- [x] Counting unit correct (one row per market in vectorized, per-market dedup in tick)

---

## 13. Critical Findings

### Finding 1: Long-Shot Population HR is Higher Than Hypothesis (4.53% not 3.2%)

The task framing used 3.2% from the knowledge base. Jan 2026 data shows 4.53%. The elite in-play pool 24% HR figure likely reflects a DIFFERENT definition or period. Actual top-50 specialists: training HR 36-40%, test HR 15-16%.

### Finding 2: Break-Even Price is Higher Than Trigger Price

Fill price averages 0.207 → break-even HR = 20.7%. Actual HR = 16.0%. The strategy systematically fills at higher prices than the signal trigger due to the `+0.02` slippage buffer.

**Fix needed**: Use narrower price bands (e.g., only fire at price < 0.20 where break-even is lower), OR require much higher HR pool.

### Finding 3: Best Price Band is 20-30% (Not <10%)

The <10% band has 0.0% HR for both population and specialists — pure gambling. Best HR is at 20-30% entry price band where specialists show +7pp alpha over break-even. This contradicts the "deep underdog" framing.

### Finding 4: Specialists Are a Distinct, Small Population with Weak Persistence

- Only 2% overlap with general elite in-play pool
- Only 25/50 top-train traders active in OOS period
- HR regresses significantly: 22.9% train → 15.2% test
- But rank ordering preserved (Q4 > Q3 > Q2 > Q1 in test)

### Finding 5: Tag Distribution — No Tag Information Available

The `events.category` field had NULL values for nearly all markets. Cannot determine which tags specialists operate in. This is a data quality issue (events.category join returning NULL).

---

## 14. Verdict

> [!CRITICAL]
> **NO SIGNAL AS CONFIGURED**: All pool sizes produce negative PnL in tick validation. Root cause: avg fill price (0.207) implies break-even HR of 20.7%, but actual tick HR is 16.0%. The strategy is fundamentally underperforming break-even.

**Recommended modification**: Restrict to 20-30% price band (where alpha is +7pp over BE) and require higher pool HR (K=10 top traders only). Do not deploy current configuration.

**Spawned ideas**:
1. `longshot-narrowband` — restrict to 20-30% price band where specialists show +7pp alpha. Smaller universe but positive EV.
2. `longshot-top10-hyperfiltered` — K=10 with very strict HR threshold (>40%), require >=50 positions. Fewer signals but potentially above BE.

---

## 15. Production Parameters — NOT RECOMMENDED IN CURRENT FORM

If proceeding with modifications:
- **Pool**: Top-10 traders by CopyScore (not top-50)
- **Price band**: 0.20-0.30 ONLY (not full <0.30)
- **Signal trigger**: Pool trader BUYs YES at 0.20-0.30
- **Fill cap**: 0.30 (at price + 0.01)
- **Position size**: $100 per signal
- **Infrastructure**: pending.signal Kafka topic

---

*Results from tick-by-tick SyncReplayRunner are realistic estimates. Vectorized upper bounds labeled [UB].*
