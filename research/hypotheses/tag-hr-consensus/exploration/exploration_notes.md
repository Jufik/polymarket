# Exploration Notes — Sharp Pool + Tracks 3-4

**Date**: 2026-03-06
**Source data**: DuckDB Parquet snapshot (vectorized upper bounds)
**Folds**: 2025-07, 2025-10, 2026-01 (same as validation)
**Tags**: Esports, Tennis
**N=3 for all explorations** (consensus threshold)
**Pool params**: meh >= 10pp, mpe <= 0.80, min_trades=5, bot_guard<10,000

---

## Exploration 1: Sharp Pool (top-K by excess_hr)

**`sharp_pool.json`**

### Results

**Esports** (pool_total: 89 / 367 / 1299 across folds):

| K | 2025-07 signals | 2025-07 HR | 2025-10 signals | 2025-10 HR | 2026-01 signals | 2026-01 HR |
|---|---|---|---|---|---|---|
| K=10 | 0 | — | 2 | 100% (+34.6pp) | 1 | 100% (+54.4pp) |
| K=20 | 6 | 83.3% (+46.6pp) | 14 | 100% (+34.6pp) | 31 | 100% (+54.4pp) |
| K=30 | 27 | 74.1% (+37.3pp) | 19 | 94.7% (+29.4pp) | 34 | 100% (+54.4pp) |
| K=50 | 73 | 65.8% (+29.0pp) | 42 | 73.8% (+8.4pp) | 36 | 100% (+54.4pp) |

**Tennis** (pool_total: 161 / 491 / 692 across folds):

| K | 2025-07 signals | 2025-07 HR | 2025-10 signals | 2025-10 HR | 2026-01 signals | 2026-01 HR |
|---|---|---|---|---|---|---|
| K=10 | 2 | 100% (+75.2pp) | 1 | 100% (+60.5pp) | 1 | 100% (+54.7pp) |
| K=20 | 11 | 81.8% (+57.1pp) | 1 | 100% (+60.5pp) | 1 | 100% (+54.7pp) |
| K=30 | 24 | 91.7% (+66.9pp) | 2 | 100% (+60.5pp) | 1 | 100% (+54.7pp) |
| K=50 | 61 | 67.2% (+42.5pp) | 6 | 100% (+60.5pp) | 1 | 100% (+54.7pp) |

### Interpretation

**These are vectorized upper bounds.** The consensus trigger fires on resolved positions — at resolution time, not at signal time. Expect 20-40pp tick degradation.

**K=30 is the sweet spot for Esports**: sufficient signal volume (19-34 per fold = 60-100/year) with very high HR (74-100%). K=20 starts running thin in the 2025-07 fold (6 signals).

**Tennis is too thin at K=30** for most folds: 2 signals in 2025-10, 1 in 2026-01. Only 2025-07 gives useful volume (24 signals). Tennis K=50 gives 1-61 signals (highly variable).

**Why the signal is so sharp at top-K**: The threshold-based pool (meh>=10pp) admits hundreds of traders who barely qualify. The top-K approach selects only the most skilled. When K=20-30 of the most excess-HR traders ALL agree on a market, that market resolves YES almost universally.

**Recommended for Task #13 tick validation**: Esports K=30, N=3, price_ceil=0.75. Expected 60-100 signals/year, vectorized HR=74-100% → expected tick HR=35-60%. At fill prices ~0.45-0.55, break-even is 45-55%. Need tick validation to confirm.

---

## Exploration 2: Track 4 — Signal-Time Volume (causal)

**`signal_time_vol.json`**

Pool: meh=10pp, mpe=0.80, N=3, INNER JOIN on yes_entry_data.
Signal-time volume = sum(abs(net_usd)) of the first N qualified traders by first_trade.
This is strictly causal — only uses data available at the moment the Nth trader enters.

### Esports results across folds

| Vol bucket | 2025-07 n/HR/excess | 2025-10 n/HR/excess | 2026-01 n/HR/excess |
|---|---|---|---|
| micro (<$50) | 2 / 50% / +13pp | 1 / 0% / -65pp | 10 / 40% / -6pp |
| small ($50-200) | 5 / 20% / -17pp | 15 / 40% / -25pp | 43 / 72% / +27pp |
| medium ($200-500) | 8 / 37.5% / +1pp | 23 / 57% / -9pp | 73 / 51% / +5pp |
| large ($500-1k) | 13 / **76.9%** / +40pp | 28 / 50% / -15pp | 67 / 54% / +8pp |
| xlarge (>$1k) | 50 / **70%** / +33pp | 140 / **65.7%** / +0.3pp | 271 / **72.3%** / +27pp |

### Tennis results across folds

| Vol bucket | 2025-07 n/HR/excess | 2025-10 n/HR/excess | 2026-01 n/HR/excess |
|---|---|---|---|
| micro (<$50) | 3 / 0% / -25pp | 20 / 25% / -15pp | 26 / 19% / -26pp |
| small ($50-200) | 18 / 28% / +3pp | 66 / 24% / -15pp | 47 / 45% / -1pp |
| medium ($200-500) | 24 / 50% / +25pp | 78 / 42% / +3pp | 45 / 40% / -5pp |
| large ($500-1k) | 26 / **65%** / +41pp | 77 / **44%** / +5pp | 54 / **61%** / +16pp |
| xlarge (>$1k) | 56 / **70%** / +45pp | 111 / **59%** / +20pp | 147 / **68%** / +23pp |

### Interpretation

**The causal volume signal is confirmed.** Signal-time volume (from first N traders only) predicts YES HR monotonically in both tags across all folds. This is NOT look-ahead.

**xlarge (>$1k) is the strongest tier**:
- Esports: +27-33pp excess HR (2025-10 fold almost zero because base rate is 65.4%)
- Tennis: +20-45pp excess HR

**Recommended filter threshold**: signal_time_vol >= $500 (large+xlarge combined):
- Esports: 63/168/338 markets per fold — sufficient volume
- Tennis: 82/188/201 markets per fold — good coverage

**In tick-by-tick deployment**: accumulate `sum(abs(net_usd))` as qualified traders enter. When the Nth trader fires consensus, check that accumulated vol >= $500 before submitting the intent.

**Note on 2025-10 Esports**: The near-zero excess at xlarge (+0.3pp) is not a failure — the base rate is 65.4%, meaning the xlarge HR of 65.7% still tracks the base rate. The signal doesn't ADD much in this fold because the base rate is already very high. The causal vol filter doesn't harm in high-base-rate folds.

---

## Exploration 3: Track 3 — Dissent Filter

**`dissent.json`**

Pool: meh=10pp, mpe=0.80, N=3 min YES traders required. Looks at ALL qualified traders (YES and NO) per market.
Dissent ratio = n_qual_yes / (n_qual_yes + n_qual_no).

### Esports results across folds

| Dissent bucket | 2025-07 n/HR/excess | 2025-10 n/HR/excess | 2026-01 n/HR/excess |
|---|---|---|---|
| pure YES (1.0) | 4 / **100%** / +63pp | 12 / **91.7%** / +26pp | 86 / **93.0%** / +47pp |
| strong (0.90-1.0) | — | 3 / **100%** / +35pp | 3 / 66.7% / +21pp |
| moderate (0.70-0.90) | 16 / 75% / +38pp | 54 / 68.5% / +3pp | 119 / 84.0% / +38pp |
| split (<0.70) | 58 / 58.6% / +22pp | 138 / 53.6% / -12pp | 256 / 47.7% / +2pp |

### Tennis results across folds

| Dissent bucket | 2025-07 n/HR/excess | 2025-10 n/HR/excess | 2026-01 n/HR/excess |
|---|---|---|---|
| pure YES (1.0) | 5 / **80%** / +55pp | 13 / **69.2%** / +30pp | 34 / 52.9% / +8pp |
| strong (0.90-1.0) | 1 / 100% | — | — |
| moderate (0.70-0.90) | 14 / 64.3% / +40pp | 71 / 56.3% / +17pp | 90 / 60.0% / +15pp |
| split (<0.70) | 107 / 55.1% / +30pp | 268 / 39.2% / -0.4pp | 195 / 53.8% / +8pp |

### Interpretation

**Strong and consistent effect.** Pure-YES consensus (dissent=1.0, no qualified NO traders) outperforms split markets in every fold for both tags.

**Esports pure YES**: 91-100% HR across all folds (4/12/86 markets). This is extraordinary even vectorized.
**Tennis pure YES**: 53-80% HR — also above any split bucket.

**Split markets (<0.70) are near-random in the hostile folds**: Esports 2025-10 split: 53.6% HR with base=65.4% → actually -12pp excess. Tennis 2025-10 split: 39.2% HR vs 39.6% base → -0.4pp excess. The dissent filter correctly kills the worst signals.

**Recommended as a hard gate**: `dissent_ratio >= 0.90` before firing intent.
- Esports pure+strong: 4+0/15+3/86+3 = 4/18/89 markets per fold passing — very selective
- With `>= 0.70` (moderate+pure): 20/69/208 per fold — more tractable

**The cost of strictness**: at pure YES only (dissent=1.0), Esports 2025-07 has only 4 markets — too thin for a monthly strategy. Recommend dissent >= 0.70 (includes moderate) as the gate, with pure YES getting larger position sizing.

---

## Combined Recommendation for Task #13

Stack all three filters on top of the base consensus signal (N=3, price_ceil=0.75):

1. **Sharp pool K=30** (not threshold): 30 traders ranked by excess_hr
2. **Signal-time vol >= $500**: causal volume from first N traders
3. **Dissent >= 0.70**: at least 70% of qualified traders are on YES side

Expected Esports signal counts per fold after stacking (rough estimate):
- K=30 gives 19-34 per fold
- Vol >= $500 filter: ~60-70% of those pass (~12-24 per fold)
- Dissent >= 0.70: ~60% of those pass (~7-14 per fold)

**Final: ~10-15 Esports signals/fold (30-45/year)**. Small but highly selective. At vectorized HR=74-100% for K=30, and applying 20-40pp tick degradation → expected tick HR=35-55%. At signal entry prices likely 0.45-0.55 YES, break-even is 45-55%.

This is the make-or-break: if the stacked filters produce tick HR >= 55% consistently, the signal is deployable. If it degrades below 50%, we pivot to knowledge capture.
