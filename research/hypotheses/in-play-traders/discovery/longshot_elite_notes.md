# Long-Shot Elite Strategy — Discovery Notes

**Date**: 2026-03-08
**Status**: NO SIGNAL (as configured) — Modifications required

---

## What We Found

### Pool Characteristics
- 3,613 traders in training data beat the 3.2% population base rate at <0.30 YES entries
- Training HR range: 4-55%, median 8.6%, P90 18.8%
- Top traders by CopyScore have very high HR but tiny N (10-12 positions) — overfitting risk
- Specialists are a DISTINCT population from general elite in-play pool (2% overlap)

### Persistence
- Rank ordering preserved OOS (Q4 > Q3 > Q2 > Q1)
- Absolute regression severe: top-train 22.9% → test 15.2% (-7.7pp)
- Only 50% of top-50 train specialists active in test period

### Tick Validation
- K=25, N=1: 506 fills, HR=16.0%, PnL=-$13,398
- K=50, N=1: 831 fills, HR=14.4%, PnL=-$17,030
- All variants negative PnL

### Root Cause of Negative PnL
**The fill price problem**: Strategy fills at `trigger_price + 0.02`, but trigger prices are distributed up to 0.28. The cap at 0.29 means many fills happen at 0.27-0.29. Average fill price = **0.207**, implying break-even HR = **20.7%**. Actual HR = 16.0%. Every price bucket is below break-even.

### Best Signal Is at 20-30% Price Band
Vectorized analysis of top-50 pool shows:
- 0-10%: 0.0% HR — DEAD, pure gambling
- 10-20%: 8-16% HR — below break-even
- 20-30%: 30-35% HR — +7pp above break-even (at ~0.25 avg entry)

This is the key refinement: restrict to 20-30% price band.

---

## SELL Handling
- BUY-only vs directional (BUY + SELL_NO): 2.2pp difference (MODERATE)
- Directional slightly better HR (18.3% vs 16.0%) and slightly better PnL (-$8,803 vs -$13,398)
- Neither variant is profitable

---

## Surprising Findings

### 1. Population HR is 4.53%, Not 3.2%
The knowledge base documents 3.2% population HR at <0.30. Jan 2026 shows 4.53%. The discrepancy may be due to different time periods or the use of `yes_entry_data` join (which excludes split-route traders).

### 2. Elite In-Play Pool Has Almost No Overlap with Long-Shot Specialists
Only 1/50 top long-shot specialists is also in the elite in-play pool. These are completely different trader phenotypes:
- Elite in-play: very high frequency, sports/crypto, near-certainties (99%+ HR)
- Long-shot specialists: lower frequency, multi-day hold, cheaper markets

### 3. Consensus (N=2) Hurts More Than Helps
N=2 has LOWER HR than N=1 in most configurations. In long-shot markets, the signal is often a unique insight by a single specialist. Waiting for a second confirming trader causes adverse selection — only noisier markets get consensus.

### 4. Deep Underdogs (<10%) Are Untradeably Noisy
Both population and specialists have ~0% HR at <10% entry. Even the "best" specialists in this band have 0% HR in January 2026. This is pure noise — markets priced at 2-5% resolve YES extremely rarely.

---

## Parameter Sensitivity Analysis

| Parameter | Tested | HR Change | Fragile? |
|-----------|--------|-----------|---------|
| K (25 vs 50 vs 100) | Yes | -1.7 to +1.3pp | No — HR fairly stable |
| N (1 vs 2) | Yes | -2.5pp (N=2 worse) | Yes |
| price_max (0.30) | Not varied | N/A | YES — break-even critical |
| min_positions (10 vs 20) | Yes (poolsize) | -11pp | Yes — small min = overfitting |

---

## Proposed Classifications

### `longshot_specialist` (trader)
```sql
-- Rule: >=10 YES positions with avg entry price < 0.30, HR > population base rate
-- Score: excess_hr * ln(n_positions + 1)
SELECT trader,
       avg(yes_won) AS hr,
       (avg(yes_won) - 0.032) * ln(count(*) + 1) AS copy_score
FROM maker_positions mp
JOIN yes_entry_data yed USING (trader, condition_id)
WHERE mp.position = 'YES'
  AND mp.first_trade < '{cutoff}'
  AND yed.price_x_vol / yed.volume < 0.30
  AND yed.volume > 0
GROUP BY trader
HAVING count(*) >= 10 AND avg(yes_won) > 0.032
```

---

## Spawned Ideas (for backlog)

1. **longshot-narrowband** [MEDIUM]: Restrict to 20-30% price band where +7pp alpha exists. Test with K=25, fill cap 0.29.
2. **longshot-top10-hyperfiltered** [LOW]: K=10 with very strict thresholds (HR > 40%, N >= 50).
3. **longshot-20-30-consensus** [LOW]: N=2 consensus specifically in 20-30% band — may concentrate the real alpha.

---

## Recommendation to Lead

**REJECT** current hypothesis configuration. The long-shot specialist concept is real (persistence confirmed, distinct from elite in-play), but the signal as implemented is below break-even due to fill price dynamics.

**Conditional GO** on narrowband variant (20-30% entry price): vectorized +7pp alpha. Estimated improvement if we restrict to 0.20-0.29 band: smaller universe (57 vectorized markets in Jan 2026) but potentially positive EV.

Required validation: run `longshot_narrowband` with max_price=0.29, min_price=0.20. Expected fills ~150/month at K=25.
