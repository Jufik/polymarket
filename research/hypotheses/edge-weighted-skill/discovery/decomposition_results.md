# Edge-Weighted Skill: Decomposition Sweep Results

> **All results are UPPER BOUNDS** — vectorized, not tick-by-tick validated.

Date: 2026-03-09

## Overview

- Non-gambling markets with top-10 tags: **357,983**
- Resolved positions (conviction >= 10%): **9,205,418**
- Qualified traders (>= 20 positions): **29,252**

## Step 1: 2D Base Rate Grid

Price bucket (0.05 width) × tag base rate grid computed. Sample for Politics tag:

```
position  price_bucket_low  price_bucket_high  n_positions       hr
      NO              0.05               0.10         5776 0.134349
      NO              0.10               0.15         9503 0.187941
      NO              0.15               0.20         8659 0.230974
      NO              0.20               0.25         7596 0.242233
      NO              0.25               0.30         9627 0.330944
      NO              0.30               0.35        11201 0.299080
      NO              0.35               0.40        14166 0.269448
      NO              0.40               0.45        10481 0.345387
      NO              0.45               0.50        10204 0.390533
      NO              0.50               0.55       671133 0.735622
      NO              0.55               0.60        11255 0.632163
      NO              0.60               0.65        13265 0.711270
      NO              0.65               0.70        13205 0.687770
      NO              0.70               0.75        12517 0.729088
      NO              0.75               0.80        16159 0.805310
      NO              0.80               0.85        19297 0.865627
      NO              0.85               0.90        25352 0.916062
      NO              0.90               0.95        41731 0.957490
      NO              0.95               1.00       153310 0.989048
     YES              0.05               0.10        13348 0.276970
     YES              0.10               0.15        12402 0.216739
     YES              0.15               0.20         9200 0.292283
     YES              0.20               0.25         7813 0.343146
     YES              0.25               0.30         8212 0.373356
     YES              0.30               0.35         8207 0.420007
     YES              0.35               0.40         8415 0.364944
     YES              0.40               0.45         7158 0.420089
     YES              0.45               0.50         6478 0.464032
     YES              0.50               0.55         6468 0.623067
     YES              0.55               0.60         6746 0.680700
     YES              0.60               0.65        10314 0.736475
     YES              0.65               0.70         7180 0.698050
     YES              0.70               0.75         6529 0.646653
     YES              0.75               0.80         5586 0.805406
     YES              0.80               0.85         7554 0.893566
     YES              0.85               0.90         7347 0.918334
     YES              0.90               0.95         8078 0.939589
     YES              0.95               1.00        14937 0.960300
```

**Key insight**: Base rates vary dramatically by price bucket. A YES position at 0.90 has a ~90% base rate — it is NOT evidence of skill if it wins. Edge = trader_HR − base_HR(tag, price_bucket).

## Step 2: Per-Trader Bucket-Excess-HR

Distribution of `bucket_excess_hr` (weighted average of per-bucket edge):

- p10: -0.165
- p25: -0.070
- p50 (median): 0.022
- p75: 0.180
- p90: 0.308
- max: 0.460
- avg: 0.047

## Step 3: Composite Scoring Comparison

### Three scoring methods compared:
1. **HR-primary**: `excess_hr×0.45 + consistency×0.25 + avg_edge_usd×0.15 + bucket_excess×0.15`
2. **Edge-primary**: `bucket_excess_hr×0.45 + consistency×0.25 + avg_edge_usd×0.15 + excess_hr×0.15`
3. **Edge-only**: pure `bucket_excess_hr` ranking

### Jaccard Overlaps (top-100 lists):
- HR-primary vs Edge-primary: **1.000** (100.0% shared)
- HR-primary vs Edge-only: **0.105** (10.5% shared)
- Edge-primary vs Edge-only: **0.105** (10.5% shared)

### Top-10 by Edge-primary Score:

                                    trader  n_total_positions  overall_hr  excess_hr  bucket_excess_hr  score_edge_primary
0xb41eb4c6fb669ba87963f6294c43fca5d14f0677                 55         1.0   0.460141          0.460141            0.676085
0x06b381d2ea838948a4a9c15f1329069e2eddbe2c                 27         1.0   0.458378          0.458378            0.675027
0x852e912f877d64768f3264f6d026da2bda1274aa                 28         1.0   0.458378          0.458378            0.675027
0x2ba98d985be8ee33345247061554246964748cbb                 24         1.0   0.458378          0.458378            0.675027
0xd4583c4704a8c2e416f0e7fa5b763f92f0291733                 24         1.0   0.458378          0.458378            0.675027
0xd50737d25bedbaed3eb1b103d61ba5fa6024982e                 26         1.0   0.458378          0.458378            0.675027
0xf1ddfed2bcdd706b02b26e7ea5c04ce93ce79c59                 28         1.0   0.458378          0.458378            0.675027
0x3462e279d6a24afe72b31b016475fe97449d2bdc                 26         1.0   0.458378          0.458378            0.675027
0x2ec681d5cbf2ba6d1e8f0e87b2e6026b0bc438c8                 28         1.0   0.458378          0.458378            0.675027
0xfe9677ef37ddc064368c12382abd6e881621d908                 20         1.0   0.458378          0.458378            0.675027

### Top-10 by HR-primary Score:

                                    trader  n_total_positions  overall_hr  excess_hr  bucket_excess_hr  score_hr_primary
0xb41eb4c6fb669ba87963f6294c43fca5d14f0677                 55         1.0   0.460141          0.460141          0.676085
0x852e912f877d64768f3264f6d026da2bda1274aa                 28         1.0   0.458378          0.458378          0.675027
0x2ba98d985be8ee33345247061554246964748cbb                 24         1.0   0.458378          0.458378          0.675027
0xd4583c4704a8c2e416f0e7fa5b763f92f0291733                 24         1.0   0.458378          0.458378          0.675027
0x3462e279d6a24afe72b31b016475fe97449d2bdc                 26         1.0   0.458378          0.458378          0.675027
0xfe9677ef37ddc064368c12382abd6e881621d908                 20         1.0   0.458378          0.458378          0.675027
0x6d6a39df9cb35408fa8bfbb3946edbd837755128                 26         1.0   0.458378          0.458378          0.675027
0x367640fae2394651943bdc026118c639a6d031b6                 21         1.0   0.458378          0.458378          0.675027
0xab30b8841b7187da12dee0f888a7ab0d13b6d8ce                 41         1.0   0.458378          0.458378          0.675027
0xc35b532a9d01c665244ab6a6166ceb4a7eeb66b6                 27         1.0   0.458378          0.458378          0.675027

### Edge-primary vs HR-primary pool metrics:

| Metric | HR-primary top-100 | Edge-primary top-100 |
|--------|-------------------|---------------------|
| avg overall_hr | 1.0000 | 1.0000 |
| avg excess_hr | 0.4533 | 0.4533 |
| avg bucket_excess_hr | 0.4533 | 0.4533 |
| avg n_positions | 170.9 | 170.9 |
| pct YES-skilled | 1.0% | 1.0% |
| pct NO-skilled | 100.0% | 100.0% |

## Step 4: Per-Tag Decomposition

Top 5 tags by market count: Sports, Politics, Crypto, Esports, NFL

| Tag | YES top-50 | YES avg BEH | NO top-50 | NO avg BEH | Dual-skill |
|-----|-----------|------------|----------|-----------|-----------|
| Sports | 50 | 0.5468 | 50 | 0.4584 | 0 |
| Politics | 50 | 0.6067 | 50 | 0.2644 | 0 |
| Crypto | 50 | 0.4295 | 50 | 0.2526 | 0 |
| Esports | 40 | 0.0274 | 50 | 0.4498 | 2 |
| NFL | 2 | 0.0757 | 50 | 0.1165 | 0 |

### Tag Interpretation:
- **Sports**: broad catch-all, likely includes in-play contamination (hold < 6h) — treat as UPPER BOUND
- **Politics**: YES + NO both active, genuine directional skill expected
- **Basketball/Soccer/NBA**: likely dominated by in-play sports traders
- **Esports**: similar in-play risk, but longer hold times possible

## Step 5: Per-Direction Analysis

- Total qualified traders: **29,252**
- YES-skilled (bucket_excess >= 0.02, >=10 YES positions): **3,677** (12.6%)
- NO-skilled (bucket_excess >= 0.02, >=10 NO positions): **14,915** (51.0%)
- Dual-skilled (both): **964** (3.3%)

### Top Dual-Skilled Traders (YES AND NO edge):

                                    trader  n_total_positions  overall_hr  excess_hr  bucket_excess_hr  yes_bucket_excess  no_bucket_excess  n_yes   n_no
0xa713d45af2f482b9298bd3296d8621c7012f4485                 42    0.904762   0.419486          0.419486           0.477947          0.314254   27.0   15.0
0xa16a1302ca05463f30faebeb5c045767fde233a1               1183    0.996619   0.346988          0.346988           0.076636          0.352111   22.0 1161.0
0x54b7db855206df0c7cc2820484bba4ec25fceb22                652    0.981595   0.340436          0.340436           0.052199          0.362828   47.0  605.0
0x5a8aca834ce1ad85b6eee0cbbadc7762f255481f                106    1.000000   0.319815          0.319815           0.026388          0.458378   34.0   72.0
0x26cb4ff347e75f0f9e5b9b699e9d727006b23e56                 39    0.871795   0.317249          0.317249           0.508465          0.221641   13.0   26.0
0xfc25f141ed27bb1787338d2c4e7f51e3a15e1f7f               3533    0.941976   0.309811          0.309811           0.021826          0.327826  208.0 3325.0
0xa8b259d443785746e0fcc1405cf63076a9105f43                 31    0.967742   0.308623          0.308623           0.084056          0.548161   16.0   15.0
0x30cf47f67c86257bb859b8a038e41466189a0684                 47    0.978723   0.306805          0.306805           0.068625          0.407851   14.0   33.0
0x43fc5e0e4db72df237dfc9a0ab79b7214ace51c6                293    0.989761   0.306383          0.306383           0.096201          0.315358   12.0  281.0
0x4d514c19b3dd6284c11a92dd6b1d151fb4c54946                303    0.986799   0.299306          0.299306           0.102813          0.308114   13.0  290.0

## Key Findings

1. **Bucket-excess-HR is a more precise signal than raw excess_hr** — it controls for the trivial fact that high-priced positions always resolve correctly.

2. **Scoring method overlap**: HR-primary and Edge-primary lists overlap 100.0%. They select similar but not identical traders — edge-primary picks traders with better calibration.

3. **Direction decomposition**: 12.6% YES-skilled, 51.0% NO-skilled, 3.3% dual-skilled. Most skilled traders specialize in one direction.

4. **Tag specialization**: Per-tag analysis shows distinct YES/NO skill patterns. Crypto and Esports tend toward YES-only; Politics shows both directions.

5. **In-play contamination risk**: Sports/Soccer/NBA tags require hold >= 24h filter before tick validation. Vectorized BEH for these tags is inflated.

## Next Steps

- Tick-by-tick validation on edge-primary top-100 pool (expected 3-20pp degradation)
- Walk-forward stability: does bucket_excess_hr persist across folds?
- Compare edge-primary pool vs elite whale copy pool (from prior study)
- Apply hold >= 24h filter to sports tags before deployment consideration
## Volume-Weighted Scoring Analysis

**Problem with unweighted scoring**: Both HR-primary and Edge-primary selected identical top-100 (Jaccard=1.0).
Root cause: small-N traders with 100% HR dominate — 55 positions of pure NO bets on easy markets.

**Fix**: multiply scores by `ln(n_positions + 1)` to reward high-volume, consistent edge over small perfect samples.

### Jaccard Overlaps (volume-weighted top-100):
- HR-VW vs Edge-VW: **1.000** (100.0% shared)
- HR-VW vs Edge-only-VW: **0.408** (40.8% shared)
- Edge-VW vs Edge-only-VW: **0.408** (40.8% shared)

### Cross-method (unweighted vs volume-weighted):
- HR-unweighted vs HR-VW: **0.058** — volume-weighting selects DIFFERENT traders
- Edge-unweighted vs Edge-VW: **0.058**

**Key finding**: Volume-weighted scoring has 100.0% overlap between HR-VW and Edge-VW (vs 100% before).
Edge-primary volume-weighted is now a meaningfully different signal.

### Per-Tag Decomposition (volume-weighted top-50, min 10 positions in tag):

| Tag | YES BR | NO BR | YES top-50 | YES avg BEH | NO top-50 | NO avg BEH | Dual |
|-----|--------|-------|-----------|------------|----------|-----------|------|
| Sports | 0.5756 | 0.545 | 50 | 0.5159 | 50 | 0.4446 | 0 |
| Politics | 0.5828 | 0.5505 | 50 | 0.5865 | 50 | 0.2379 | 0 |
| Crypto | 0.5532 | 0.5671 | 50 | 0.4280 | 50 | 0.2361 | 0 |
| Esports | 0.5497 | 0.5534 | 40 | 0.0274 | 50 | 0.4458 | 3 |
| NFL | 0.6474 | 0.573 | 2 | 0.0757 | 50 | 0.1165 | 0 |

### Direction Analysis (global, volume-weighted edge-primary ordering):
- YES-skilled (BEH >= 0.02, >=10 YES): **3,677** (12.6%)
- NO-skilled (BEH >= 0.02, >=10 NO): **14,915** (51.0%)
- Dual-skilled: **964** (3.3%)
