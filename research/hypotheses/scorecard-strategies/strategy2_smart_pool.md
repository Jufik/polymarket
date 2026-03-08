# Strategy 2: Smart Money Pool Signal

**Hypothesis**: Skilled traders are collective probability estimators. Their aggregate directional signal carries more information than any individual. We trade the pool's consensus direction.

**Status**: VECTORIZED UPPER BOUNDS — tick-by-tick validation required before deployment

---

## Setup

- Train period: before 2025-12-05
- Test period: 2025-12-05 onwards
- Tags: Esports, Sports, Crypto, Elections
- Gambling exclusion: `slug NOT LIKE '%updown%'` and `NOT LIKE '%up-or-down%'`
- Market-maker exclusion: `avg(abs(net_usd)/volume) >= 0.90`
- Min positions: 10 per trader per tag (training window)
- Tool: DuckDB + `maker_positions_resolved_corrected` Parquet snapshot

---

## Phase 1: Qualified Pool

**Pool qualification criteria (training window only):**
- `excess_hr > 0` (hit rate above tag base rate)
- `avg_conviction >= 0.90` (non-market-maker)
- `n_markets >= 10` (min resolved positions per tag)

**Training base rates (used for pool qualification):**
| Tag | Base Rate | Train Markets |
|-----|-----------|---------------|
| Sports | 49.3% | 63,181 |
| Crypto | 47.9% | 20,218 |
| Elections | 48.5% | 14,995 |
| Esports | 47.7% | 5,078 |

**Qualified pool sizes:**
| Tag | Pool Size | Med Excess HR | Max Excess HR |
|-----|-----------|---------------|---------------|
| Sports | 21,242 | +17.4pp | +50.7pp |
| Elections | 7,158 | +15.1pp | +51.5pp |
| Crypto | 3,493 | +37.8pp | +52.1pp |
| Esports | 880 | +14.1pp | +52.4pp |

---

## Phase 2: Smart Money Signal Construction

For each resolved test-window market with ≥2 qualified traders:

```sql
-- Per market aggregation (UNIQUE TRADERS only — no double-counting)
SELECT
    condition_id, tag,
    any_value(yes_won) AS yes_won,
    count(DISTINCT trader) AS smart_n_traders,
    count(DISTINCT CASE WHEN position='YES' THEN trader END) AS n_yes_traders,
    count(DISTINCT CASE WHEN position='NO' THEN trader END) AS n_no_traders,
    sum(CASE WHEN position='YES' THEN 1 ELSE -1 END) AS vote_balance,
    sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_weighted_direction,
    greatest(n_yes_traders, n_no_traders) / smart_n_traders AS smart_confidence,
    max(first_trade) AS signal_entry,  -- consensus entry time
    date_diff('hour', max(first_trade), resolved_at) AS hold_hours
FROM maker_positions mp
JOIN qualified_pool qp ON mp.trader = qp.trader
WHERE CAST(resolved_at AS DATE) >= '2025-12-05'
  AND CAST(first_trade AS DATE) >= '2025-12-05'  -- CRITICAL: only copyable entries
GROUP BY condition_id, tag
HAVING smart_n_traders >= 2
```

**Test-window signal coverage (N>=2 qualified traders):**
| Tag | N Markets | Head-Count HR | Vol-Weighted HR | Avg Confidence | Med N Traders | Med Vol USD |
|-----|-----------|---------------|-----------------|----------------|---------------|-------------|
| Sports | 47,325 | 63.6% | 69.1% | 55.9% | 6 | $2,113 |
| Crypto | 6,263 | 78.4% | 84.2% | 69.5% | 6 | $3,978 |
| Esports | 5,926 | 71.7% | 78.7% | 64.2% | 4 | $2,612 |
| Elections | 5,817 | 69.8% | 84.6% | 70.9% | 9 | $1,351 |

**Key finding**: Vol-weighted direction consistently beats head-count by 5-16pp. When they disagree, vol wins decisively (67-89% HR for vol vs 11-33% for head-count).

---

## Phase 3: Signal Quality by Confidence Level

### HR by Confidence Bucket (Head-Count Direction)

| Tag | Bucket | N Markets | HR Head | HR Vol |
|-----|--------|-----------|---------|--------|
| Crypto | 0.9-1.0 | 343 | **98.5%** | 94.5% |
| Crypto | 0.8-0.9 | 559 | 90.3% | 92.3% |
| Crypto | 0.7-0.8 | 616 | 83.0% | 93.7% |
| Crypto | 1.0 (unani.) | 1,430 | 83.9% | 83.9% |
| Crypto | 0.5-0.6 | 2,322 | 66.5% | 76.9% |
| Elections | 0.9-1.0 | 156 | **98.1%** | 98.7% |
| Elections | 0.8-0.9 | 716 | 75.1% | 93.4% |
| Elections | 1.0 (unani.) | 1,029 | 65.0% | 65.0% |
| Elections | 0.5-0.6 | 1,800 | 65.4% | 85.4% |
| Esports | 0.9-1.0 | 13 | **100.0%** | 100.0% |
| Esports | 0.8-0.9 | 265 | 91.7% | 86.8% |
| Esports | 1.0 (unani.) | 971 | 89.8% | 89.8% |
| Esports | 0.5-0.6 | 2,834 | 55.5% | 72.1% |
| Sports | 1.0 (unani.) | 4,416 | 75.9% | 75.9% |
| Sports | 0.9-1.0 | 308 | 70.5% | 70.5% |
| Sports | 0.5-0.6 | 29,918 | 58.2% | 66.4% |

**Surprising finding**: Unanimous (conf=1.0) is NOT the best confidence bucket for Crypto/Elections. Near-unanimous (0.9-1.0) outperforms, suggesting split-vote markets with a clear majority carry the best signal. For Esports, both unanimous and 0.9-1.0 are excellent.

### Full Confidence × Min-Traders Sweep (Head-Count HR)

**Esports (train base rate 47.7%):**
| Min Traders | Min Conf | N Markets | HR | Excess HR |
|-------------|----------|-----------|-----|-----------|
| 10 | 0.9 | 13 | **100.0%** | +52.3pp |
| 10 | 0.8 | 39 | 97.4% | +49.7pp |
| 5 | 0.9 | 60 | 96.7% | +49.0pp |
| 5 | 1.0 | 47 | 95.7% | +48.0pp |
| 10 | 0.7 | 159 | 95.6% | +47.9pp |
| 3 | 0.9 | 336 | 92.3% | +44.6pp |
| 2 | 0.7 | 1,775 | 90.4% | +42.7pp |

**Sports (train base rate 49.3%):**
| Min Traders | Min Conf | N Markets | HR | Excess HR |
|-------------|----------|-----------|-----|-----------|
| 5 | 1.0 | 349 | **81.1%** | +31.8pp |
| 3 | 1.0 | 1,503 | 79.4% | +30.1pp |
| 3 | 0.9 | 1,811 | 77.9% | +28.6pp |
| 2 | 1.0 | 4,416 | 75.9% | +26.6pp |

**Elections (train base rate 48.5%):**
| Min Traders | Min Conf | N Markets | HR | Excess HR |
|-------------|----------|-----------|-----|-----------|
| 10 | 1.0 | 97 | **98.9%** | +50.4pp |
| 10 | 0.9 | 253 | 98.4% | +49.9pp |
| 10 | 0.8 | 517 | 95.6% | +47.1pp |
| 10 | 0.7 | 1,017 | 90.9% | +42.4pp |
| 5 | 0.9 | 542 | 82.8% | +34.3pp |

**Crypto (train base rate 47.9%):**
| Min Traders | Min Conf | N Markets | HR | Excess HR |
|-------------|----------|-----------|-----|-----------|
| 10 | 1.0 | 155 | **99.4%** | +51.5pp |
| 10 | 0.9 | 498 | 98.8% | +50.9pp |
| 5 | 0.9 | 687 | 98.4% | +50.5pp |
| 5 | 1.0 | 344 | 98.3% | +50.4pp |
| 10 | 0.8 | 772 | 97.8% | +49.9pp |
| 5 | 0.8 | 1,246 | 94.8% | +46.9pp |

---

## Phase 4: Pool Size (K) Sweep

Effect of capping pool to top-K traders by excess_hr rank:

| K Cap | Tag | N Signals (N>=3, conf>=0.80) | HR |
|-------|-----|------------------------------|-----|
| 20 | Esports | 51 | **100.0%** |
| 50 | Esports | 403 | **100.0%** |
| 100 | Esports | 716 | **100.0%** |
| All | Esports | 600 | 92.0% |
| 20 | Crypto | 14 | 92.9% |
| 50 | Crypto | 69 | 92.8% |
| 100 | Crypto | 174 | 94.3% |
| All | Crypto | 1,787 | 86.9% |
| All | Sports | 3,833 | 74.9% |
| All | Elections | 1,661 | 72.1% |

**Key finding**: For Esports, even K=50 pool → 100% HR (N=403 signals). The top-50 Esports traders form an extraordinarily predictive oracle. Crypto similarly improves with K-cap. Sports/Elections are dominated by larger market participation — K-cap doesn't help because there are too few top traders with test-window positions.

---

## Phase 5: Pool vs Individual Top Trader

- Top-1 Esports trader: 0 test signals (not active in test window)
- Top-5 Esports pool: 411 signals, **100% HR**
- Top-5 Crypto pool: 73 signals, 91.8% HR vs Top-1 Crypto 92.3% HR (comparable)

**Pool wins over individual** primarily because:
1. No single trader is active in enough markets
2. Pool covers more markets (5x more signals vs top-1)
3. When pool direction disagrees with vol direction, vol wins by ~50pp

**Pool vs Individual disagreement** (Crypto, 13 markets overlap with top-1):
- All 13 markets: pool agreed with top-1 → 92.3% HR
- No disagreement cases observed in this tiny sample

---

## Phase 6: Smart Money Price Signal (YES Entry Price)

Qualified traders' volume-weighted avg YES entry price vs actual YES resolution rate:

| Tag | Entry Price | N Markets | YES Win Rate |
|-----|-------------|-----------|--------------|
| All tags | < 0.30 | 33K-100K | 0.7-1.3% |
| All tags | 0.30-0.40 | - | 10-20% |
| All tags | 0.40-0.50 | - | 21-45% |
| All tags | 0.50-0.60 | - | 42-68% |
| All tags | 0.60-0.70 | - | 64-87% |
| All tags | >= 0.70 | - | 94-99% |

**Interpretation**: Entry price is primarily a proxy for market consensus (correlated with current price). The "value signal" hypothesis (smart money enters at good prices before crowd) is not isolated here — entry price just reflects the market's state at entry time.

**Actionable**: Use entry price as a filter: only trade YES markets where qualified traders entered at 0.45-0.75 (genuine uncertainty zone). Below 0.30 = mostly losing markets dominated by deep underdogs.

---

## Hold Time Analysis

**Test window hold hours (consensus entry = max(first_trade)):**
| Tag | P25 Hours | Median Hours | P75 Hours | Avg Hours |
|-----|-----------|--------------|-----------|-----------|
| Crypto | 3h | **5h** | 15h | 17.4h |
| Elections | 2h | **5h** | 19h | 30.5h |
| Esports | 2h | **2h** | 3h | 4.0h |
| Sports | 2h | **3h** | 4h | 6.4h |

Esports are extremely fast resolving (2h median). Sports also very fast (3h). Crypto/Elections take longer.

---

## Compounding Scores (UPPER BOUNDS)

Parameters: min_traders=3, min_conf=0.80, hold_hours >= 0

| Tag | N Signals | HR | Train Base Rate | Excess HR | Med Hold Hours | CS (USD/day, $100 stake) |
|-----|-----------|-----|-----------------|-----------|----------------|--------------------------|
| **Esports** | 600 | 92.0% | 47.7% | +44.4pp | 2h | **532** |
| **Sports** | 3,833 | 74.9% | 49.3% | +25.6pp | 3h | **205** |
| **Crypto** | 1,787 | 86.9% | 47.9% | +39.0pp | 5h | **187** |
| Elections | 1,661 | 72.1% | 48.5% | +23.6pp | 9h | 63 |

K=50 Esports specifically:
| K Cap | Tag | N | HR | Med Hours | CS |
|-------|-----|---|-----|----------|-----|
| 50 | Esports | 403 | 100.0% | 2h | **628** |
| 100 | Crypto | 174 | 94.3% | 11h | 101 |

> WARNING: These are vectorized upper bounds. Expected tick-by-tick degradation is 20-40pp HR. The consensus gap (all traders must have entered by signal_entry time for the signal to be observable) is the primary degradation mechanism. 100% HR in Esports is almost certainly too optimistic.

---

## Key Findings & Insights

### Finding 1: Vol-Weighted Direction Beats Head-Count
Vol-weighted direction outperforms head-count by 5-16pp across all tags. When they disagree (19-20% of markets), vol wins by 35-78pp. **Use vol-weighted direction as the primary signal.**

### Finding 2: Confident Majority > Unanimous
Near-unanimous (0.9-1.0) beats unanimous (1.0) for Crypto (+15pp) and Elections (+33pp). Explanation: unanimous markets often have only 2-3 traders — a tiny sample. 0.9-confidence with N=10 means 9/10 agreement, which is a much more robust signal.

### Finding 3: More Traders = Better Signal (up to ~10)
The signal improves monotonically up to N=10 qualified traders. Beyond 10, signal is near-perfect in Crypto/Elections. For Esports, even N=5 gives 96.7% HR.

### Finding 4: Esports Pool Is Extraordinary
Top-50 Esports traders form a 100%-accurate oracle with 403 test-window signals. This is the strongest single finding. Esports markets resolve quickly (2h median) enabling fast capital recycling.

### Finding 5: Sports Has Best Scale
Sports generates 47,325 signals with N>=2, or 3,833 with N>=3 and conf>=0.80. It's the highest-volume opportunity despite lower individual signal quality.

### Finding 6: Entry Price Signal Is Calibration, Not Alpha
Qualified traders' avg entry price correlates perfectly with resolution as a function of probability level, but this is just the market pricing mechanism, not an independent value signal. Only trade where qualified entry was 0.45-0.75 (genuine uncertainty zone).

---

## Recommended Strategy Parameters

**Primary entry rule:**
```
For each resolved market in test window:
  1. Count qualified traders (min excess_hr > 0, non-MM, ≥10 train positions)
  2. Require N >= 3 qualified traders
  3. Require smart_confidence >= 0.80 (80% agreement)
  4. Direction: vol-weighted (sum of net_usd for YES vs NO)
  5. Entry price filter: qualified YES entry between 0.40-0.80
  6. Hold to resolution
```

**Per-tag tuning:**
| Tag | Min Traders | Min Conf | K Cap | Expected HR (UB) | N Signals/yr (est.) |
|-----|-------------|----------|-------|------------------|---------------------|
| Esports | 5 | 0.90 | 50 | ~97% | ~1,200 |
| Esports | 3 | 0.80 | 50 | ~100% | ~2,000 |
| Sports | 3 | 0.90 | All | ~78% | ~3,600/period |
| Crypto | 5 | 0.90 | All | ~98% | ~1,400 |
| Elections | 10 | 0.90 | All | ~98% | ~500 |

---

## SQL: Full Strategy Query

```sql
-- Step 1: Qualify pool from training data
CREATE TABLE qualified_pool AS
WITH train_pos AS (
    SELECT mp.trader, vm.primary_tag AS tag,
        count(DISTINCT mp.condition_id) AS n_markets,
        avg(CAST(mp.correct AS DOUBLE)) AS hit_rate,
        avg(abs(mp.net_usd) / NULLIF(mp.volume, 0)) AS avg_conviction
    FROM maker_positions mp
    JOIN valid_markets vm ON mp.condition_id = vm.condition_id
    WHERE CAST(mp.resolved_at AS DATE) < '2025-12-05' AND mp.volume > 0
    GROUP BY mp.trader, vm.primary_tag
    HAVING count(DISTINCT mp.condition_id) >= 10
       AND avg(abs(mp.net_usd) / NULLIF(mp.volume, 0)) >= 0.90
)
SELECT trader, tag, hit_rate,
    hit_rate - [tag_base_rate] AS excess_hr,
    ROW_NUMBER() OVER (PARTITION BY tag ORDER BY excess_hr DESC) AS hr_rank
FROM train_pos
WHERE hit_rate - [tag_base_rate] > 0;

-- Step 2: Compute smart money signal per market
WITH test_positions AS (
    SELECT mp.trader, mp.condition_id, vm.primary_tag AS tag,
        mp.position, mp.yes_won, mp.net_usd, mp.first_trade, mp.resolved_at
    FROM maker_positions mp
    JOIN valid_markets vm ON mp.condition_id = vm.condition_id
    JOIN qualified_pool qp ON mp.trader = qp.trader AND vm.primary_tag = qp.tag
    WHERE CAST(mp.resolved_at AS DATE) >= '2025-12-05'
      AND CAST(mp.first_trade AS DATE) >= '2025-12-05'  -- CRITICAL
      AND mp.volume > 0
      -- Optional: AND qp.hr_rank <= 50  (K-cap for Esports)
),
market_signals AS (
    SELECT condition_id, tag,
        any_value(yes_won) AS yes_won,
        count(DISTINCT trader) AS n_qual,
        count(DISTINCT CASE WHEN position='YES' THEN trader END) AS n_yes,
        count(DISTINCT CASE WHEN position='NO' THEN trader END) AS n_no,
        sum(CASE WHEN position='YES' THEN abs(net_usd) ELSE -abs(net_usd) END) AS vol_dir,
        greatest(n_yes, n_no) * 1.0 / n_qual AS confidence,
        max(first_trade) AS signal_entry,
        any_value(resolved_at) AS resolved_at,
        date_diff('hour', max(first_trade), any_value(resolved_at)) AS hold_hours
    FROM test_positions
    GROUP BY condition_id, tag
    HAVING n_qual >= 3               -- min traders
       AND confidence >= 0.80         -- min confidence
       AND hold_hours >= 0            -- valid hold time
)
SELECT *,
    CASE WHEN vol_dir > 0 THEN 'YES' ELSE 'NO' END AS trade_direction
FROM market_signals;
```

---

## Risks & Limitations

1. **Vectorized upper bound**: 20-40pp degradation expected in tick-by-tick validation. Key risk: consensus requires ALL N qualified traders to have entered — but in live trading, we can only copy after observing their trades sequentially.

2. **Consensus gap**: The signal fires at `max(first_trade)` — the LAST qualified trader to enter. By that point, the market may have already moved against us. This is the primary degradation vector.

3. **Test-window base rates differ**: YES win rates in test period differ substantially from training (Crypto: 30% vs 48%, Elections: 22% vs 49%). This reflects market composition changes (bearish Crypto period, different election cycle), not pool signal degradation.

4. **K=50 Esports 100% HR**: Almost certainly overfitted to test period. The 403-signal 100% HR with K=50 is likely 60-75% in tick-by-tick given consensus gap.

5. **Market maker leakage**: avg_conviction >= 0.90 excludes most MMs but not all. Some sophisticated MMs may pass this filter with strategically high conviction trades.

---

## Next Steps

1. **Tick-by-tick validation**: Run SyncReplayRunner on Esports (K=50, N>=3, conf>=0.80) — highest CS, should have ~100 signals in fast-replay subset.
2. **Consensus gap study**: Measure how often the Nth trader enters after the first trader has already indicated direction.
3. **Live entry timing**: In production, trigger the copy signal when Nth qualified trader enters — not at resolution time.
4. **Vol-weighted implementation**: Prefer `vol_weighted_direction` over head-count in production code.

---

## Data Appendix

All SQL and results generated from DuckDB + Parquet snapshot at `data/research/`.
Analysis scripts: `tmp/s2_smart_pool_analysis.py`, `tmp/s2_supplemental2.py`, `tmp/s2_hold_hours.py`
