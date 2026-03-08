# Strategy 1: Tag-Expert Consensus — Analysis Report

> **Status**: Vectorized Discovery Complete. Tick-by-tick validation REQUIRED before deployment.
> **All results are UPPER BOUNDS** — vectorized backtests are 20-40pp optimistic vs tick-by-tick.

## Hypothesis

Traders are tag specialists. Build tag-specific qualified pools using scorecard metrics (excess HR
weighted by experience), then trade when N tag-experts converge on a market in the same direction.

## Methodology

### Data Setup
- **DB**: DuckDB over Parquet snapshot (~29.9M maker positions)
- **Train period**: before 2025-12-05
- **Test period**: 2025-12-05 onwards (~3 months)
- **Gambling exclusion**: `slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'`
- **Market-maker exclusion**: `avg(abs(net_usd)/volume) >= 0.90`
- **Minimum activity**: `n_positions >= 20` per trader per tag (eliminates 5/5=100% HR noise)

### Tag Assignment
Each market assigned ONE canonical tag using priority ordering. 23 canonical tags spanning:
Elections, Crypto, NBA, NFL, Soccer, Tennis, MLB, NHL, Golf/PGA, NCAA Basketball,
counter strike 2, Dota 2, League of Legends, Valorant, Sports, Esports,
Politics, Finance, Weather, Culture, Tech, Science, Geopolitics, AI, Economy.

### Scorecard Ranking
Traders ranked by **composite score = excess_hr × ln(n_positions + 1)**
- Penalizes traders with few positions (avoids small-sample 100% HR)
- Rewards both skill and experience

### Consensus Signal
- `N` unique qualified traders (from top-K pool for that tag) enter the same market, same direction
- Signal entry time = `max(first_trade)` across consensus traders (true consensus trigger time)
- One signal per (condition_id, tag, position) — correct market-level aggregation

### Critical Hold Filter
**All sweep results include >= 4-hour hold filter** to remove in-play sports markets
(markets resolving within minutes of last expert entry — uncopyable in real-time).

## Training Data: Tag Base Rates

| Tag | Markets | YES Rate |
|-----|---------|---------|
| Sports | 20,719 | 30.9% |
| Crypto | 20,429 | 26.5% |
| Soccer | 13,664 | 29.2% |
| Politics | 13,332 | 27.9% |
| NBA | 7,592 | 35.0% |
| NFL | 6,548 | 29.7% |
| Culture | 5,231 | 18.5% |
| NCAA Basketball | 5,176 | 35.3% |
| Weather | 4,659 | 19.9% |
| MLB | 4,499 | 31.8% |
| Tennis | 3,345 | 39.9% |
| NHL | 2,928 | 34.8% |
| Finance | 2,024 | 41.1% |
| counter strike 2 | 1,638 | 51.3% |
| Elections | 1,513 | 31.1% |
| Esports | 1,207 | 48.6% |
| Dota 2 | 896 | 59.4% |

Note: Elections base rate (31%) differs from knowledge base (9%) — the knowledge base measures
single-winner election markets while this includes all election market types.

## Trader Scorecard (Training Period)

| Tag | Traders | Avg HR | Avg Excess | Avg Stability |
|-----|---------|--------|------------|--------------|
| Politics | 5,023 | 47.3% | +19.4pp | 0.82 |
| NBA | 3,066 | 54.4% | +19.4pp | 0.55 |
| Crypto | 2,780 | 56.9% | +30.5pp | 1.35 |
| Sports | 2,110 | 50.2% | +19.3pp | 0.99 |
| MLB | 2,104 | 51.9% | +20.1pp | 0.85 |
| Soccer | 1,591 | 53.2% | +24.0pp | 0.77 |
| Weather | 584 | 46.4% | +26.5pp | 2.48 |
| Elections | 584 | 54.5% | +23.5pp | 1.61 |
| Economy | 193 | 56.4% | +31.0pp | 1.79 |
| Crypto (top) | 2,780 | 97-99% | +70pp+ | - |

**Key observation**: With min 20 positions and composite ranking, the scorecard correctly
identifies high-experience, high-skill traders. Crypto and Economy show highest excess HR.

## Critical Discovery: In-Play Signal Problem

> [!CRITICAL]
> The dominant signal source is in-play sports markets where experts enter minutes before
> resolution. These have 99%+ HR but are **completely uncopyable in real-time**.

### Evidence
```
K=50, N=3 — HR by hold duration:
  0h (<1h)    : 486 signals,   HR = 99.8%  ← in-play, uncopyable
  1-4h        : 5,113 signals, HR = 97.0%  ← likely still in-play (soccer, short games)
  4-24h       : 2,180 signals, HR = 76.7%  ← genuine predictions
  1-3d        : 902 signals,   HR = 80.6%  ← genuine predictions
  3-7d        : 596 signals,   HR = 77.4%  ← genuine predictions
  1-4wk       : 448 signals,   HR = 82.4%  ← genuine predictions
  >4wk        : 42 signals,    HR = 83.3%  ← genuine predictions
```

**Pattern**: HR drops sharply from 99% (in-play) to 77-83% for genuine predictions.
This is the key vectorized→tick gap for this strategy: not the consensus mechanism, but the
in-play trader detection.

## Parameter Sweep Results (Test Period: Dec 2025 – Mar 2026)

### Overall Performance (K=50, N=3, >=4h hold filter)

| Metric | Value |
|--------|-------|
| Total signals | ~2,400 |
| Overall HR | 83-86% |
| Dominant tag | Politics NO (335 sigs/mo, HR=92%) |
| Best excess HR | Soccer YES: +70.8pp (HR=100% but likely still in-play given 0.2d hold) |

### Per-Tag Best Configurations (test period, >=4h hold)

| Tag | Position | Best K | Best N | Signals | HR | Excess HR | Hold | Notes |
|-----|---------|--------|--------|---------|-----|-----------|------|-------|
| Politics | NO | 20 | 5 | 467 | 92.7% | +20.6pp | 1.5d | **Best volume + quality** |
| Politics | YES | 50 | 4 | 4 | 100% | +72.1pp | 0.6d | Very thin |
| Elections | NO | 100 | 4 | 220 | 78.6% | +9.7pp | 2.2d | Stable volume |
| Soccer | YES | 10 | 5 | 9 | 100% | +70.8pp | 0.2d | Hold = 5h, borderline |
| Soccer | NO | 50 | 2 | 303 | 97.0% | +26.3pp | 0.2d | Hold = 5h, borderline |
| Crypto | NO | 20 | 4 | 10 | 100% | +26.5pp | 0.2d | Small N |
| Crypto | YES | 30 | 4 | 3 | 100% | +73.5pp | 0.2d | Very thin |
| NBA | NO | 50 | 3 | 13 | 100% | +35.0pp | 0.2d | Short hold |
| Finance | YES | 50 | 4 | 5 | 100% | +58.9pp | 0.6d | Thin |
| Tech | NO | 100 | 2 | 257 | 88.3% | +13.3pp | 5.0d | Good volume, longer hold |
| NCAA Basketball | NO | 50 | 3+ | 22-96 | 95.7-100% | +31-35pp | 0.2d | Short hold |
| Weather | NO | 10 | 2 | 15 | 100% | +19.9pp | 0.2d | Very short hold |

### Monthly Signal Frequency (K=50, N=3, >=4h hold)

| Tag | Position | Avg Sigs/Month | Avg HR | Excess |
|-----|---------|----------------|--------|--------|
| Politics | NO | 335.8 | 92.0% | +19.9pp |
| Culture | NO | 262.3 | 66.1% | -15.3pp |
| Weather | NO | 51.3 | 71.3% | -8.8pp |
| Tech | NO | 58.0 | 85.1% | +10.1pp |
| NCAA Basketball | HEDGED | 153.7 | 53.0% | -11.7pp |
| Soccer | YES | 37.0 | 100.0% | +70.8pp |
| Soccer | NO | 30.7 | 100.0% | +29.2pp |
| Elections | NO | 87.7 | 79.2% | +10.3pp |
| Crypto | NO | 41.3 | 95.9% | +22.4pp |
| Sports | NO | 45.0 | 80.6% | +11.5pp |

## Key Findings by Tag

### Politics NO — Best Actionable Signal

- **K=20, N=5**: 467 signals over test period (3 months), HR = 92.7%, excess = +20.6pp
- **Hold**: 1.5 days — sufficient lead time for execution
- **Volume**: ~156 signals/month — highest actionable throughput
- **Consistency**: HR 88-93% across different K/N combinations
- **Interpretation**: Political experts converging on NO direction are strongly predictive.
  Elections, referendums, presidential actions — experts who cover these specifically.
- **Caution**: Politics markets are high-variance; needs tick validation

### Tech NO — Best Hold Duration

- **K=100, N=2**: 257 signals, HR = 88.3%, excess = +13.3pp, **hold = 5.0 days**
- Longest meaningful hold duration — good for capital planning
- ~86 signals/month — solid throughput
- Interpretation: Tech industry experts know product launches, regulatory outcomes

### Elections NO — Steady Volume

- **K=100, N=4**: 220 signals, HR = 78.6%, excess = +9.7pp, hold = 2.2d
- Lower HR but very consistent (multi-month track record in training)
- In-sample: 738 signals, HR = 81.7%, excess = +12.8pp

### Soccer YES/NO — Likely Still In-Play

- Despite the 4-hour filter, soccer matches with 90+120 minutes duration have "hold = 0.2d = 5h"
- This means expert entries happen ~5 hours before resolution — during a soccer match
- **NOT genuinely predictive of pre-game outcomes** — these are in-game signals
- Soccer signals with hold >= 24h would be genuine; those don't appear in K=50, N=3 sweep

### Negative Findings

| Tag | Position | HR | Excess | Issue |
|-----|---------|-----|--------|-------|
| Culture | NO | 66.1% | -15.3pp | Experts LESS skilled than random |
| Weather | NO | 71.3% | -8.8pp | Below base rate (19.9% → NO = 80.1%) |
| Economy | NO | 66.5-72.8% | -7.9 to -1.8pp | Negative excess |
| Dota 2 | - | - | -5.9pp | Experts worse than random |

## In-Sample vs Out-of-Sample Comparison

| Tag | IS HR | OOS HR | IS Excess | OOS Excess | Gap |
|-----|-------|--------|-----------|------------|-----|
| Politics NO | 85.7% | 92.0% | +13.6pp | +19.9pp | +6.3pp OOS better |
| Soccer YES | 99.1% | 100.0% | +69.9pp | +70.8pp | stable |
| Crypto NO | 95.7% | 95.9% | +22.1pp | +22.4pp | stable |
| Elections NO | 81.7% | 79.2% | +12.8pp | +10.3pp | -2.5pp (expected) |
| Weather NO | 70.7% | 71.3% | -9.4pp | -8.8pp | stable (bad) |

Good IS/OOS consistency for quality tags. Politics shows better OOS (surprising — may be
December 2025 specific events). Elections shows slight expected degradation.

## Stability Gate Analysis

Filtering pool to stability >= N has minimal effect:

| Stability Threshold | Pool Size | Signals | HR |
|--------------------|-----------|---------|----|
| 0.0 (all) | 1,034 | 9,767 | 89.3% |
| 1.0 | 933 | 8,566 | 88.9% |
| 2.0 | 902 | 8,477 | 88.6% |
| 3.0 | 854 | 8,270 | 89.2% |
| 5.0 | 776 | 7,806 | 90.6% |

Stability >= 5 gives +1.3pp HR at cost of -25% pool size. **Marginal benefit** — consistent
with prior scorecard research showing stability is a risk filter, not primary ranker.

## Top Parameter Configurations (Actionable, >=5 signals, >=4h hold)

| Rank | K | N | Tag | Position | Signals | HR | Excess | Hold | Score |
|------|---|---|-----|---------|---------|-----|--------|------|-------|
| 1 | 20 | 5 | Politics | NO | 467 | 92.7% | +20.6pp | 1.5d | Best volume |
| 2 | 100 | 2 | Politics | NO | 2,158 | 92.0% | +19.9pp | 1.1d | Max volume |
| 3 | 100 | 4 | Elections | NO | 220 | 78.6% | +9.7pp | 2.2d | Steady |
| 4 | 100 | 2 | Tech | NO | 257 | 88.3% | +13.3pp | 5.0d | Longest hold |
| 5 | 50 | 2 | Soccer | NO | 303 | 97.0% | +26.3pp | 0.2d | ⚠ Likely in-play |
| 6 | 50 | 3 | Crypto | NO | 124 | 97.3% | +23.7pp | 0.3d | ⚠ Short hold |
| 7 | 50 | 3 | Crypto | YES | 82 | 99.7% | +73.2pp | 0.4d | ⚠ Short hold |

## Compounding Score Analysis

Using the formula: `excess_hr × |median_pnl| / median_hold_days`

Note: realized_pnl in maker_positions is very small ($0.01-0.50 range) due to per-position
accounting. The compounding score is directionally correct but absolute values need calibration
with realistic $50-$100 entry sizes.

**Top compounding candidates (directionally)**:
1. Politics NO (high volume, decent hold, solid excess)
2. Elections NO (longer hold, moderate excess, steady)
3. Tech NO (longest hold, moderate excess)

## Critical Caveats

### 1. Soccer/NBA/Sports "Signals" Are In-Play
Hold = 0.2d = 5h for soccer. This means experts enter during a match, not before it.
The 100% HR is trivially explained by match state visibility. **Cannot copy in practice.**

### 2. Vectorized → Tick Gap Expected
Expected 20-40pp HR degradation in tick-by-tick validation:
- Pool consensus requires all N traders to enter before the strategy fires
- In practice, the strategy may fire before all qualified traders are counted
- Market prices move between expert entries (adverse selection)

### 3. Test Period Is Only 3 Months
Dec 2025 - Feb 2026 is a specific news cycle. Politics NO performing at 92% may be
election-year specific or post-2025 political environment specific.

### 4. Hold = 0.2d for Most Sports
Most sports signals (NBA, NFL, NHL, Tennis, CS2, Dota 2, Valorant) have holds < 6 hours.
These are all likely in-play signals despite the >=4h filter.
**Genuine multi-day sports predictions are rare at the consensus threshold tested.**

## Recommendations

### For Tick-by-Tick Validation

**Priority 1**: Politics NO with K=20-50, N=3-5
- Why: Highest volume, genuine 1-3 day holds, strong excess HR (+20pp)
- Universe: Markets tagged Politics, slug not matching gambling patterns
- Entry: Fire signal when 3rd (or Nth) qualified trader enters in test window

**Priority 2**: Elections NO with K=50-100, N=3-5
- Why: Steady volume (~88/mo), longer holds (2.2d), stable OOS
- Universe: Markets tagged Elections

**Priority 3**: Tech NO with K=50-100, N=2-3
- Why: Longest holds (5 days), good for capital planning, 88% HR
- Universe: Markets tagged Tech

**Avoid for now**: Soccer, Sports, NBA, NHL, CS2, Dota 2 (all likely in-play).

### Pool Construction for Deployment

```python
# Recommended scorecard parameters:
# - min_positions >= 20 per trader per tag
# - conviction_ratio >= 0.90 (exclude market makers)
# - ranking: excess_hr * ln(n_positions + 1)
# - top K=30-50 per tag

# Signal parameters:
# - N = 3-5 unique qualified traders
# - hold_hours >= 24h before firing (stricter than the 4h sweep filter)
# - direction: majority position among qualified traders
```

## SQL Templates

### Pool Construction
```sql
-- Build top-K pool per tag (run on training data)
WITH overall AS (
    SELECT p.trader, mt.canonical_tag AS tag,
           count(*) AS n_positions,
           avg(CAST(p.correct AS DOUBLE)) AS hit_rate,
           avg(abs(p.net_usd) / NULLIF(p.volume, 0)) AS conviction_ratio
    FROM maker_positions p
    JOIN market_tag mt ON p.condition_id = mt.condition_id
    WHERE CAST(p.resolved_at AS DATE) < '{train_end}'
    GROUP BY p.trader, mt.canonical_tag HAVING count(*) >= 20
),
scored AS (
    SELECT trader, tag, hit_rate, conviction_ratio,
           (hit_rate - {tag_base_rate}) * ln(n_positions + 1.0) AS composite_score
    FROM overall WHERE conviction_ratio >= 0.90
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY tag ORDER BY composite_score DESC) AS rk
    FROM scored WHERE composite_score > 0
)
SELECT trader, tag FROM ranked WHERE rk <= {K}
```

### Consensus Signal Detection
```sql
-- Aggregate to MARKET level (not trader level)
SELECT condition_id, tag, position,
       count(DISTINCT trader) AS n_qualified,
       max(first_trade) AS signal_entry,    -- consensus trigger time
       first(resolved_at) AS resolved_at,
       first(correct) AS market_correct
FROM test_positions t JOIN pool p ON t.trader = p.trader AND t.tag = p.tag
GROUP BY condition_id, tag, position
HAVING count(DISTINCT trader) >= {N}
   AND date_diff('hour', max(first_trade), first(resolved_at)) >= 24  -- genuine predictions only
```

## Artifact Files

- `tmp/strategy1_v5_results.json` — full sweep results with all K×N×tag combinations
- `tmp/s1_v5_log.txt` — detailed per-run logs
- `tmp/strategy1_v4.py` — analysis script (v4 without hold filter, shows in-play effect)
- `tmp/strategy1_v5_holdfilter.py` — analysis script (v5, production version)

## Conclusion

**Signal exists, but is dominated by an in-play artifact.** The genuine prediction signal
(hold >= 4h, excluding in-play markers) shows:
- Politics NO: +20.6pp excess HR, 92.7%, 1.5d hold → **VALIDATE with tick-by-tick**
- Elections NO: +9.7pp excess HR, 78.6%, 2.2d hold → **VALIDATE with tick-by-tick**
- Tech NO: +13.3pp excess HR, 88.3%, 5.0d hold → **VALIDATE with tick-by-tick**

Sports tags (Soccer, NBA, NFL, NHL, CS2, Dota 2) appear highly predictive but are
almost entirely in-play signals that cannot be copied in real-time.

**Expected post-validation**: 20-40pp HR drop from vectorized numbers. For Politics NO,
this means real HR likely 52-72% vs the vectorized 92.7%. With base rate 27.9% (NO),
excess HR still +24 to +44pp — potentially viable but needs confirmation.

**Next step**: Tick-by-tick replay on Politics NO and Elections NO using SyncReplayRunner.
Expected signal frequency: 50-150/month. Capital requirement: $50-200 per signal.
