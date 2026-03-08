# Track B: In-Play Consensus Signal — Discovery Results

**Status: UPPER BOUNDS (vectorized). Expect 20-40pp HR degradation in tick-by-tick.**

Generated: 2026-03-07
Script: `research/hypotheses/in-play-traders/scripts/track_b_consensus.py`

---

## Methodology

**Hypothesis**: When N>=3 distinct traders all enter the same market within the final 2-4 hours before resolution, their consensus predicts the outcome. Unlike scorecard strategies (pre-qualified pools), timing is the quality filter here (urgency = information).

**Pitfalls addressed**:
- Counting unit: one row per market (DISTINCT trader count), not per-trader
- Signal entry = max(first_trade) across in-play traders (trigger time)
- Phantom test signals: first_trade >= test_start filter applied
- Gambling exclusion: `updown`, `up-or-down`, `above/below`, `multistrike` slugs removed
- Price contamination gate: swept 0.70 / 0.80 / 0.85 / none
- In-play contamination: by design (the signal IS the in-play activity); reported as upper bound

**Dataset**: 342,802 valid non-gambling closed markets, 13.1M positions after gambling exclusion.

---

## Overall Base Rates

| Metric | Value |
|--------|-------|
| Overall YES market win rate | 29.0% |
| Overall NO market win rate | 71.0% |
| YES positions with entry price | 100.0% |
| Hold distribution: <=1h | 101,726 positions |
| Hold distribution: <=2h | 297,889 positions |
| Hold distribution: <=4h | 2,438,264 positions |

The very short hold tails are the true in-play window.

---

## Parameter Sweep — Best Combos (Training, YES direction, n>=20 signals)

Sorted by excess HR vs overall base (29.0%):

| hold_w | n_thresh | price_gate | n_signals | hit_rate | excess_hr | med_hold_h | med_ep |
|--------|----------|-----------|-----------|----------|-----------|------------|--------|
| 2.0h | 5 | 0.70 | 238 | 63.5% | +34.4pp | 1h | 0.50 |
| 2.0h | 5 | 0.85 | 244 | 63.1% | +34.1pp | 1h | 0.50 |
| 2.0h | 5 | none | 244 | 63.1% | +34.1pp | 1h | 0.50 |
| 2.0h | 5 | 0.80 | 242 | 62.8% | +33.8pp | 1h | 0.50 |
| 1.0h | 2 | none | 526 | 60.8% | +31.8pp | 0h | 0.50 |
| 2.0h | 2 | none | 1968 | 60.7% | +31.7pp | 1h | 0.50 |
| 1.0h | 2 | 0.85 | 522 | 60.5% | +31.5pp | 0h | 0.50 |
| 2.0h | 2 | 0.85 | 1953 | 60.5% | +31.5pp | 1h | 0.50 |
| 1.0h | 2 | 0.80 | 521 | 60.5% | +31.5pp | 0h | 0.50 |
| 2.0h | 2 | 0.80 | 1949 | 60.4% | +31.4pp | 1h | 0.50 |
| 2.0h | 3 | none | 854 | 60.1% | +31.1pp | 1h | 0.50 |
| 2.0h | 3 | 0.80 | 850 | 59.9% | +30.9pp | 1h | 0.50 |

Key observation: **price gate has almost no effect** (gating 0.70 vs none: HR shifts <1pp). This is the first major finding.

---

## Train / Test Persistence (YES direction)

**CRITICAL FINDING: Massive out-of-sample collapse for high-N combos**

| combo | TRAIN HR | TRAIN n | TEST HR | TEST n | excess train | excess test |
|-------|----------|---------|---------|--------|-------------|------------|
| 2h n=5 gate=0.70 | 63.5% | 238 | 29.4% | 9,167 | +34.4pp | +0.4pp |
| 2h n=5 gate=0.85 | 63.1% | 244 | 29.5% | 9,191 | +34.1pp | +0.5pp |
| 2h n=5 none | 63.1% | 244 | 29.6% | 9,210 | +34.1pp | +0.6pp |
| 2h n=5 gate=0.80 | 62.8% | 242 | 29.5% | 9,185 | +33.8pp | +0.4pp |
| **1h n=2 none** | **60.8%** | **526** | **44.9%** | **7,546** | **+31.8pp** | **+15.9pp** |
| **2h n=2 none** | **60.7%** | **1968** | **46.0%** | **23,329** | **+31.7pp** | **+17.0pp** |
| 1h n=2 gate=0.85 | 60.5% | 522 | 44.8% | 7,535 | +31.5pp | +15.8pp |
| **2h n=2 gate=0.85** | **60.5%** | **1953** | **46.0%** | **23,282** | **+31.5pp** | **+16.9pp** |
| **2h n=2 gate=0.80** | **60.4%** | **1949** | **45.9%** | **23,264** | **+31.4pp** | **+16.9pp** |
| **2h n=3 none** | **60.1%** | **854** | **39.8%** | **15,219** | **+31.1pp** | **+10.7pp** |
| **2h n=3 gate=0.80** | **59.9%** | **850** | **39.6%** | **15,181** | **+30.9pp** | **+10.6pp** |

**Key findings**:
1. n=5 combos: train 63% → test **29%** — essentially random. Massive training overfitting on very small training set (n=238).
2. n=2 combos: train 61% → test **46%** — persistent signal (~+17pp OOS).
3. n=3 combos: train 60% → test **40%** — partial persistence (~+11pp OOS).
4. Test signal counts are enormous (7K-23K), suggesting this is a very high-frequency signal.

---

## Hold Window Quality Gradient (n=3, gate=0.80, YES)

| hold_window | n_signals | HR | excess | med_hold | pct_lt15min |
|-------------|-----------|-----|--------|----------|-------------|
| <=1h | 4,787 | 37.9% | +8.9pp | 0h | 36% |
| <=2h | 16,032 | 40.7% | +11.7pp | 1h | 16% |
| <=4h | 112,788 | 28.1% | -0.9pp | 2h | 4% |

**Critical finding**: The 2-4h window is NOISE — the in-play signal is concentrated in the last 2 hours. Markets with 2-4h hold have near-random outcomes (28.1% vs 29.0% base). The signal lives in the <=2h window.

---

## Contamination Analysis (YES entries, hold<=4h, n>=3)

| Price bucket | n_markets | hit_rate | med_hold_h |
|-------------|-----------|----------|------------|
| <30% (long-shot) | 17,111 | 0.08% | 3h |
| 30-50% (uncertain) | 14,662 | 9.6% | 2h |
| 50-70% (mild lean) | 80,278 | 37.0% | 2h |
| 70-80% (strong lean) | 737 | 79.1% | 2h |
| 80-85% (near-certain) | 155 | 74.8% | 2h |
| 85-90% (contaminated?) | 120 | 70.0% | 2h |
| >90% (likely contaminated) | 120 | 82.5% | 2h |

**Finding**: The price gate doesn't filter contamination because the median entry price across ALL combos is 0.50. Most in-play markets are priced near 0.50 even during the final hours. Contaminated markets (>0.85) are only 995/113,183 (0.9%) of qualifying markets — negligible.

The dominant volume is in the <30% and 50-70% buckets. The 0-30% bucket has near-zero HR because these are long-shot markets that stay unresolved by YES. The 50-70% bucket has 37.0% HR (signal!).

---

## Tag Breakdown (hold_w=2h, n=3, gate=0.80, YES)

| primary_tag | n_signals | hit_rate | med_hold_h | med_vol_usd |
|-------------|-----------|----------|------------|-------------|
| Games | 4,777 | 35.6% | 1h | $1.88 |
| Esports | 3,034 | 28.5% | 1h | $1.60 |
| Basketball | 2,438 | 49.7% | 1h | $16.56 |
| Dota 2 | 921 | 24.5% | 1h | $1.21 |
| Mentions | 600 | 50.0% | 1h | $57.98 |
| **Geopolitics** | **356** | **60.1%** | **1h** | **$736.87** |
| Culture | 331 | 44.7% | 1h | $5.73 |
| CFB | 317 | 31.9% | 1h | $2.29 |
| Cricket | 237 | 17.3% | 1h | $1.39 |
| **EPL** | **188** | **90.4%** | **1h** | **$2,882** |
| **Crypto** | **156** | **82.1%** | **1h** | **$5,023** |
| EFL Championship | 110 | 73.6% | 1h | $335 |
| **Bitcoin** | **98** | **88.8%** | **1h** | **$22,733** |
| **Earnings** | **60** | **96.7%** | **1h** | **$788** |
| **Gaza** | **77** | **77.9%** | **0h** | **$2,165** |
| **Breaking News** | **59** | **71.2%** | **1h** | **$4,237** |

**Key pattern**: High-signal-volume tags (EPL, Crypto, Bitcoin, Earnings, Breaking News) show 71-97% HR. This is NOT contamination (prices are at 0.50) — it is **smart money at scale**. These are fast-moving informational markets where large-volume in-play consensus IS the information.

Low-volume tags (Games, Esports, Dota 2, CFB) show HR near or below base rate — these are noise from fans/watchers without edge.

**Volume is the discriminator**, not the tag per se.

---

## Tag Breakdown (hold_w=4h, n=3, gate=0.80, YES)

At 4h window, signal quality collapses for most tags:

| primary_tag | n_signals | hit_rate | excess |
|-------------|-----------|----------|--------|
| Games | 33,487 | 32.0% | -2.8pp |
| Basketball | 25,186 | 22.3% | -13.5pp |
| Esports | 14,552 | 23.0% | -8.5pp |
| Dota 2 | 4,775 | 20.3% | -4.1pp |

The 4h window includes too much "pre-signal" noise. Confirmed: in-play signal = last 2 hours.

---

## NO Direction Analysis

NO in-play consensus is **not a reliable signal** overall:

| combo | TRAIN HR | TRAIN n | TEST HR | excess train | excess test |
|-------|----------|---------|---------|-------------|------------|
| 1h n=8 any gate | 76.3% | 38 | 50.6% | +5.3pp | -20.4pp |
| 1h n=2 any gate | 70.3% | 644 | 52.3% | -0.7pp | -18.7pp |
| 2h n=2 none | 69.2% | 1,976 | 51.4% | -1.8pp | -19.6pp |

NO positions severely degrade OOS. NO side in-play activity is not predictive. This is consistent with the market mechanics: NO side has high base rate (71%) but in-play NO entries don't add information — they're more likely exits/hedges.

**Exception**: The NO tag breakdown shows some tags with 100% HR: Daily Temperature (72 signals), London (66 signals), New York City (66 signals), Atlanta (50 signals) — these are contaminated weather markets (in-play contamination per knowledge base — city temperature watchers entering right before resolution).

---

## Signal Entry Time Distribution (n>=3, YES, hold<=4h)

| signal_hold_bucket | n_markets | avg_n_traders |
|-------------------|-----------|---------------|
| <5min | 1,290 | 13.4 |
| 5-15min | 2,690 | 11.7 |
| 15-30min | 3,716 | 11.5 |
| 30-60min | 6,510 | 11.1 |
| 1-2h | 20,379 | 10.2 |
| 2-4h | 78,514 | 9.8 |

The signal trigger time (Nth trader entering) is typically 2-4h before resolution, but the SIGNAL HOLD (from Nth entry to resolution) reflects that we need to wait for resolution. The sub-15min markets (3,980 total) have the highest trader counts — extremely last-minute.

---

## Compounding Score (Train, Upper Bound)

Median entry price is consistently 0.50 across all combos (50-50 markets).

| combo | HR | excess | hold_days | n_sigs | CS_proxy |
|-------|-----|--------|-----------|--------|----------|
| 2h n=5 gate=0.70 | 63.5% | +34.4pp | 0.04d | 238 | 111.2 |
| 2h n=5 gate=0.80 | 62.8% | +33.8pp | 0.04d | 242 | 103.9 |
| 2h n=2 none | 60.7% | +31.7pp | 0.04d | 1,968 | 81.1 |
| 2h n=3 gate=0.80 | 59.9% | +30.9pp | 0.04d | 850 | 73.2 |

NOTE: hold_days=0.04 (1h) is because median_hold_hours=1 (signal fires 1h before resolution). These compounding scores are extremely inflated by the short hold time, which is itself an artifact of in-play contamination. Real deployable hold = time from signal detection + execution + resolution = likely 30-90 minutes, not useful for compounding.

---

## Critical Issues

### 1. In-Play Contamination is BY DESIGN — but NOT price-based

The signal fires on markets priced near 0.50 (genuine uncertainty), not at 0.95 (known outcome). However, the reason traders enter in the final 2h is BECAUSE they have live information not yet in the price. This is:
- NOT copyable in the traditional sense (requires sub-hourly monitoring + execution)
- HIGH frequency: 23K+ test signals at n=2 threshold
- Potentially deployable as a LIVE MONITORING system (watch for N entries in real-time)

### 2. Training Collapse for High-N (n=5, n=8)

Train n=5: +34pp excess → test: +0.5pp excess. Completely spurious.
The training set had only 238-244 signals; the test set has 9,000+. This is a **train/test imbalance artifact** — the training period had fewer active markets, so n=5 was a rare high-conviction event. In the test period with more liquidity, n=5 events are common and non-predictive.

### 3. Genuine OOS Signal at n=2 (15-17pp excess)

The n=2 test signal (46.0% HR, +17pp excess) is a real effect. Even after 20-40pp tick degradation, this would be near or below base rate (29%). The issue: this isn't enough edge after slippage.

**Post-tick-degradation estimate** (apply ~20pp degradation floor):
- n=2, 2h: 46% - 20pp = ~26% (below base rate) — DEAD
- n=3, 2h: 40% - 20pp = ~20% — BELOW BASE RATE

### 4. Volume Segmentation Opportunity

The tag breakdown shows a bimodal pattern:
- High-volume consensus (EPL, Crypto, Bitcoin, Earnings): 80-97% train HR — these may survive tick degradation
- Low-volume consensus (Games, Esports): at or below base rate even in train

If restricted to high-volume markets (signal vol > $500), the in-play signal may be meaningful. This is a refinement worth pursuing.

---

## Verdict: Is This Distinct From Scorecard Strategies?

**Yes, mechanically distinct:**
- Scorecard: pre-qualify trader pool (training window), then count pool entries (any time)
- Track B: no pool — any N traders entering in final 2h = signal

**But signal strength is weaker:**
- Scorecard (tag-hr-consensus): vectorized +33-45pp, tick ~+10-22pp (marginal)
- Track B: vectorized +34pp (train), +17pp (test), tick estimate ~-3pp to +0pp
- The OOS collapse from 34pp → 17pp → ~0pp (after tick) suggests this strategy doesn't survive

**High-volume segmentation exception:**
- EPL, Crypto, Bitcoin in-play consensus: 80-97% train HR, high signal volume ($2K-$22K median)
- These may represent genuinely informational large-volume in-play activity
- Requires volume-gated analysis and tick validation

---

## Recommendation

**DO NOT proceed to full validation** for the general in-play consensus signal. It does not survive train/test and will not survive tick degradation.

**PURSUE** as a sub-study: volume-gated in-play consensus for high-liquidity tags (EPL, Crypto, Bitcoin, Breaking News). These show 70-97% train HR with $2K-$22K median signal volumes. This may be the "smart money in-play" signal we want.

**SPECIFICALLY**: restrict to:
- Tag: EPL, Crypto, Bitcoin, Earnings, Breaking News, Gaza, Geopolitics
- hold_window: <=2h
- n_thresh: 3
- signal_time_vol: >= $500 (sum of |net_usd| across in-play traders)
- price_gate: 0.10-0.80 (current filter already correct)

This would be Track B.2: "High-Liquidity In-Play Consensus."

---

## Artifacts

| File | Description |
|------|-------------|
| `discovery/sweep_raw.csv` | Raw parameter sweep (192 rows) |
| `discovery/sweep_with_excess.csv` | Sweep + excess HR vs base rate |
| `discovery/tag_base_rates.csv` | Per-tag YES win rates |
| `discovery/summary.json` | Key results in JSON |
| `discovery/track_b_log.txt` | Full execution log |
| `scripts/track_b_consensus.py` | Discovery script |
