# Edge-Weighted Skill: Bucket Excess Hit Rate (BEH)

> **TL;DR**: `bucket_excess_hr` (BEH) is a skill refinement that controls for the trivial effect of high-priced positions always resolving correctly. It is powerful as a QUALIFICATION GATE (remove near-certainty bettors) but does NOT re-rank top traders differently from composite scoring — HR-primary and Edge-primary top-100 lists have 100% Jaccard overlap when using volume-weighting. Use BEH >= 0.02 as a pre-filter before composite scoring.

> [!CRITICAL]
> 51% of qualified traders are NO-skilled (BEH >= 0.02, >=10 NO positions) vs only 12.6% YES-skilled. Direction decomposition is asymmetric: treating all traders as undirected (YES+NO combined) wastes 88% of potential signal. Always build separate YES and NO pools.

> [!WARNING]
> Walk-forward edge-primary scoring is REJECTED. When tested against HR-primary scoring in walk-forward stability, edge-primary does not improve out-of-sample IC. Composite weights (excess_hr×0.45 + consistency_sharpe×0.25 + avg_edge_usd×0.15 + bucket_excess_hr×0.15) remain optimal. Do not increase BEH weight above 0.15 in the composite.

## What Is bucket_excess_hr (BEH)?

A position at 0.90 YES price that wins is not evidence of skill — it's a near-certainty. BEH controls for this by comparing each trader's HR within a price bucket (0.10-wide) to the population HR in that same bucket:

```
BEH = weighted_avg(trader_hr_in_bucket - population_hr_in_bucket)
     over all buckets where trader has positions
```

Example (Politics YES, 0.90-1.00 price bucket):
- Population HR in this bucket: 96.0%
- A trader with 95% HR here has BEH = -1.0pp (below population — unskilled at this price)
- A trader with 70% HR in the 0.35-0.45 bucket (where population HR = 42%) has BEH = +28pp (genuine edge)

## BEH as Filter vs Ranker

**As a gate**: BEH >= 0.02 removes traders whose skill is purely near-certainty bets. Applied BEFORE composite scoring, it is highly effective — removes 26% of the Crypto pool (near-certainty bettors) and prevents pool pollution.

**As a ranker**: At the top of the distribution, BEH and excess_hr rank traders identically. The Jaccard overlap between HR-primary and Edge-primary top-100 is:
- Unweighted: 100% overlap (identical lists)
- Volume-weighted: 40.8% overlap (diverges — but HR-VW and Edge-VW remain 100% overlapping)

The reason: traders with genuine skill at difficult prices (BEH >> 0) also have high excess_hr by definition. BEH is not an independent signal at the top — it's a proxy for the same underlying quality.

**Recommendation**: Keep BEH weight at 0.15 in composite. Use BEH >= 0.02 as a hard gate before composite scoring. Do not raise BEH weight to 0.45 ("edge-primary") — walk-forward stability is no better, and unweighted edge-primary selects small-N perfect traders over high-volume consistent ones.

## Direction Decomposition

This is the most important finding from the edge-weighted analysis:

| Population | Count | % of qualified |
|------------|-------|----------------|
| Total qualified traders (>=20 positions) | 29,252 | 100% |
| YES-skilled (BEH >= 0.02, >=10 YES pos) | 3,677 | 12.6% |
| NO-skilled (BEH >= 0.02, >=10 NO pos) | 14,915 | 51.0% |
| Dual-skilled (both) | 964 | 3.3% |

The 4:1 ratio of NO-to-YES specialists reflects market structure: Polymarket has strong NO bias in most informational tags. It is easier to accumulate NO positions across markets (more markets resolve NO), creating more NO specialists by raw count.

### Per-Tag Direction Profile

| Tag | YES Signal | NO Signal | Interpretation |
|-----|-----------|-----------|----------------|
| Sports | Strong (avg BEH=0.55) | Moderate (avg BEH=0.44) | Both viable, YES slightly stronger |
| Politics | Strong YES (0.61) + Moderate NO (0.26) | Both viable, tick-validated |
| Crypto | Moderate YES (0.43) | Weak NO (0.24) | YES-only; Crypto NO is structural (price drift) |
| Esports | Near-zero YES (0.027) | Strong NO (0.45) | Pure NO signal — confirmed at pool level |
| NFL | Near-zero YES (0.076) | Weak NO (0.12) | Underpowered in both directions |

**Practical consequence**:
- Sports YES, Politics YES, Politics NO: build pools and deploy
- Crypto YES: deploy (verify with max_price gate)
- Esports NO: promising but thin market count, needs volume gate
- NFL: skip until more data

## Walk-Forward: Edge-Primary Rejected

Tested: does flipping weights from `(excess_hr=0.45, BEH=0.15)` to `(BEH=0.45, excess_hr=0.15)` improve walk-forward stability?

Result: **No improvement**. Edge-primary selects identical top-100 as HR-primary (Jaccard=1.0 unweighted). With volume-weighting, both HR-VW and Edge-VW still produce 100% overlap. The methods are empirically equivalent at selecting top traders. Since they perform identically and composite scoring is better understood/tested, keep composite as the standard.

## Price-Level Base Rate Grid

The 2D base rate grid (position direction × price bucket × tag) is the underlying computation:

```sql
-- Population base rate per (tag, direction, price_bucket)
WITH price_data AS (
    SELECT
        p.trader, p.condition_id,
        CAST(p.yes_won AS DOUBLE) AS correct,  -- for YES positions
        floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS price_bucket
    FROM maker_positions p
    JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
    WHERE p.position = 'YES'
      AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
),
bucket_pop AS (
    SELECT price_bucket, avg(correct) AS pop_hr
    FROM price_data
    GROUP BY price_bucket HAVING count(*) >= 10
)
```

Key insight from grid: at YES price 0.90-1.00, population HR is ~96%. A "skilled" trader with 90% HR here has NEGATIVE BEH. The grid shows that perceived skill at the trader level is heavily confounded by price-level effects without this correction.

## Crypto Pool: BEH Gate Impact

In the Crypto tag specifically, 26% of traders who pass raw excess_hr > 0 are removed by the BEH gate. These are traders who:
- Buy into Crypto markets after the price has moved to 0.80-0.95+
- Win 85-95% of the time (slightly below population base rate)
- Show positive excess_hr because they occasionally call close calls right

After BEH filtering, the remaining Crypto pool is smaller but meaningfully more skilled.

## Implementation in Pool Builder

```python
# BEH computation (Step 3 in build_pools_v3.py)
# For YES pool (uses yes_entry_data for entry price):
CREATE OR REPLACE TABLE _v3_yes_bucket_{tag} AS
WITH price_data AS (
    SELECT p.trader, p.condition_id,
           CAST(p.yes_won AS DOUBLE) AS correct,
           CAST(floor(yed.price_x_vol / yed.volume * 10) * 0.10 AS DOUBLE) AS price_bucket
    FROM maker_positions p
    JOIN yes_entry_data yed ON p.trader = yed.trader AND p.condition_id = yed.condition_id
    WHERE p.position = 'YES' AND yed.price_x_vol / yed.volume BETWEEN 0.05 AND 0.95
),
bucket_pop AS (
    SELECT price_bucket, avg(correct) AS pop_hr
    FROM price_data GROUP BY price_bucket HAVING count(*) >= 10
),
trader_bucket AS (
    SELECT pd.trader, pd.price_bucket, avg(pd.correct) AS bucket_hr, bp.pop_hr
    FROM price_data pd JOIN bucket_pop bp ON pd.price_bucket = bp.price_bucket
    GROUP BY pd.trader, pd.price_bucket, bp.pop_hr
)
SELECT trader, avg(bucket_hr - pop_hr) AS bucket_excess_hr
FROM trader_bucket GROUP BY trader HAVING count(*) >= 2;

# BEH gate applied in composite step:
WHERE coalesce(b.bucket_excess_hr, 0.0) >= 0.02
```

Full implementation: `research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py`

## Related

- `signals/composite_scorecard.md` — full composite scoring system (BEH is component + gate)
- `signals/no_direction_consensus.md` — first validated NO-direction strategy using BEH-gated pool
- `signals/entry_price_quality.md` — original calibration_gap exploration that motivated BEH
- `pitfalls/direction_decomposition.md` — YES/NO direction asymmetry by tag
- `data/tag_base_rates.md` — tag base rates (needed for excess_hr computation)

## Tags

`beh`, `bucket-excess-hr`, `edge-weighted`, `direction-decomposition`, `filter`, `pool-building`, `no-direction`, `walk-forward`
