# Graduated Sizing & Signal Quality Research Plan

**Date**: 2026-03-06
**Parent hypothesis**: tag-hr-consensus
**Status**: active (R5 validation complete, Tracks 3-4 confirmed, Track 5 in exploration)

---

## Motivation

The base consensus strategy uses flat sizing: every signal gets the same $10 position.
Three dimensions can improve capital efficiency:

1. **Time-to-live**: Some markets resolve in 30 minutes, others in 48 hours. Capital locked in slow markets has opportunity cost.
2. **Trader quality**: Not all qualified traders are equal. A consensus of 5 traders with 90% HR is worth more than 5 traders at 60% HR.
3. **Contradictory signals**: When qualified traders disagree (some YES, some NO), the signal is weaker. Flat sizing ignores this.

All three are **position sizing** problems, not signal generation problems. They build on top of the consensus trigger (N qualified traders converge), not replace it.

---

## Track 1: Time-to-Live Sizing

### Concept

Estimate expected hold time at signal time, then size inversely:
- Fast-resolving market (est. 1h hold) → full size
- Slow-resolving market (est. 24h hold) → reduced size (capital opportunity cost)

The sizing function: `size = base_size * (target_hold / max(estimated_hold, target_hold))`

Where `target_hold` is the sweet spot (e.g., 2h for Esports).

### How to estimate hold time at signal time

Available features at consensus trigger:
- **Tag**: Esports resolves faster than Tennis on average
- **Market age**: time since market creation → older markets are closer to resolution
- **Event metadata**: scheduled event time (if available in Gamma API data)
- **Price level**: markets near 0.90+ are closer to resolution
- **Historical median hold for this tag**: simple baseline

Approach: build a hold-time regression from historical data (features → actual hold time). Even a simple bucket model (tag × price_bucket → median hold) would work.

### DuckDB query sketch

```sql
-- Hold time distribution by tag and entry price bucket
SELECT
    et.label AS tag,
    CASE
        WHEN avg_ep < 0.30 THEN 'low'
        WHEN avg_ep < 0.60 THEN 'mid'
        ELSE 'high'
    END AS price_bucket,
    median(hold_hours) AS median_hold_h,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY hold_hours) AS p75_hold_h,
    count() AS n
FROM mkt_consensus_stats s
JOIN event_tags et ON s.event_id = et.event_id
GROUP BY 1, 2
```

### Sizing formula options

| Formula | Behavior | Pros | Cons |
|---------|----------|------|------|
| `base / sqrt(est_hold_h)` | Gentle reduction | Doesn't kill slow markets | Still allocates to 48h holds |
| `base * min(1, target_h / est_hold_h)` | Linear cap | Intuitive | Discontinuous at target |
| Kelly-adjusted: `kelly_frac * (1 - hold_cost)` | Theoretically optimal | Accounts for opportunity cost | Needs edge estimate per market |

**Recommended**: Start with the linear cap. `target_h = 4` (2x median Esports hold). Markets with est_hold > 4h get proportionally less capital.

### Validation approach

1. Run tick-by-tick with flat sizing → baseline PnL and Sharpe
2. Rerun with hold-time sizing → compare PnL, Sharpe, capital utilization
3. Metric: **capital-adjusted return** = total PnL / (avg_capital_locked * avg_hold_time)

---

## Track 2: Per-Trader PnL Profiling

### Concept

Each qualified trader has a profile:
- **Excess HR**: how much they beat the base rate
- **Avg edge per trade**: median PnL on correct predictions
- **Consistency**: HR variance across time windows
- **Tag specialization**: some traders are only good in specific games/events

A consensus of 5 traders each with 30pp excess HR is a much stronger signal than 5 traders each at 11pp excess (just barely qualifying at the 10pp threshold).

### Signal quality score

At consensus trigger time, compute:

```
signal_quality = sum(trader_i.excess_hr * trader_i.avg_edge) / N
```

Or simpler: `signal_quality = mean(trader_i.excess_hr)` across the N consensus traders.

### Sizing with quality

```
size = base_size * min(quality_multiplier, max_multiplier)
```

Where `quality_multiplier = signal_quality / baseline_quality`.

Example: if baseline quality (median across all consensus signals) is 0.20 excess HR:
- 5 traders averaging 0.35 excess → multiplier = 1.75 → $17.50 position
- 5 traders averaging 0.12 excess → multiplier = 0.60 → $6.00 position

Cap at 3x to limit single-position risk.

### Data available at signal time

From the qualified pool (built during training):
- `trader_i.excess_hr` — known from pool qualification
- `trader_i.n_markets` — sample size (confidence weight)
- `trader_i.avg_pnl` — available from maker_positions in training

From the live trade stream:
- `trader_i.entry_price` in this specific market — the price they paid
- `trader_i.entry_time` — when they entered

### DuckDB exploration query

```sql
-- Per-trader profile in qualified pool
SELECT
    trader,
    count() AS n_mkts,
    avg(correct) AS hr,
    avg(correct) - {base_rate} AS excess_hr,
    median(realized_pnl) AS med_pnl,
    stddev(correct) AS hr_std,  -- consistency
    avg(CASE WHEN correct = 1 THEN realized_pnl ELSE 0 END) AS avg_win_pnl,
    avg(CASE WHEN correct = 0 THEN realized_pnl ELSE 0 END) AS avg_loss_pnl
FROM maker_positions
WHERE condition_id IN (SELECT condition_id FROM tag_mkts)
  AND position = 'YES'
  AND CAST(resolved_at AS DATE) >= '{train_start}'
  AND CAST(resolved_at AS DATE) < '{train_end}'
GROUP BY trader
HAVING n_mkts >= 5
  AND excess_hr >= 0.10
```

### Validation approach

1. Backtest: for each consensus signal, compute signal_quality from the N traders
2. Split signals into quality terciles (low/mid/high)
3. Compare HR across terciles — if HR correlates with quality, sizing is justified
4. Rerun tick-by-tick with quality-weighted sizing → compare Sharpe to flat

---

## Track 3: Contradictory Signal Handling

### Concept

Current strategy only counts YES consensus. But qualified traders may also take NO positions in the same market. Three scenarios:

| YES traders | NO traders | Current behavior | Proposed behavior |
|-------------|------------|-----------------|-------------------|
| 5 | 0 | Fire YES | Fire YES (full size) |
| 5 | 3 | Fire YES | Fire YES (reduced size) |
| 3 | 5 | Fire YES | **Don't fire** (net NO) |
| 5 | 5 | Fire YES | **Don't fire** (ambiguous) |

### Metrics to compute

**Dissent ratio**: `n_qual_yes / (n_qual_yes + n_qual_no)`

| Dissent ratio | Interpretation | Proposed action |
|---------------|----------------|-----------------|
| >= 0.90 | Strong consensus | Full size |
| 0.70 - 0.89 | Moderate consensus | Half size |
| 0.50 - 0.69 | Split | No entry |
| < 0.50 | Contrarian NO | Flip to NO signal? |

### DuckDB exploration query

```sql
-- Dissent analysis: YES vs NO qualified traders per market
WITH qual_positions AS (
    SELECT
        p.condition_id,
        p.trader,
        p.position,  -- 'YES' or 'NO'
        p.correct,
        p.yes_won
    FROM maker_positions p
    JOIN qual_pool q ON p.trader = q.trader
    WHERE p.condition_id IN (SELECT condition_id FROM tag_mkts)
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.resolved_at AS DATE) < '{test_end}'
      AND CAST(p.first_trade AS DATE) >= '{test_start}'
)
SELECT
    condition_id,
    first(yes_won) AS yes_won,
    sum(CASE WHEN position = 'YES' THEN 1 ELSE 0 END) AS n_yes,
    sum(CASE WHEN position = 'NO' THEN 1 ELSE 0 END) AS n_no,
    n_yes::DOUBLE / (n_yes + n_no) AS dissent_ratio
FROM qual_positions
GROUP BY condition_id
HAVING n_yes + n_no >= 2
```

Then: `SELECT dissent_bucket, avg(yes_won), count() FROM ... GROUP BY dissent_bucket`

### Key question

Does the presence of qualified NO traders PREDICT that the YES consensus is wrong?

If yes → dissent ratio is a valuable filter (reduce size or skip).
If no → the NO traders are noise (different thesis, hedging, etc.) and can be ignored.

### Validation approach

1. Vectorized first: compute dissent ratio for all consensus markets, compare HR by bucket
2. If signal exists: add dissent filter to tick-by-tick strategy
3. Sizing: `size = base * dissent_ratio` (continuous) or bucket-based (discrete)

---

## Track 4: Signal-Time Volume (fixing the look-ahead)

### Concept

The discovery sweep found volume is the dominant HR predictor (+45pp uplift). But it was
computed at resolution time (look-ahead bias). The question is: **does volume observable at
signal time still predict HR?**

At the moment the Nth qualified trader enters, we know:
- Volume from the first N qualified traders' positions (their `net_usd`)
- Total market volume up to that timestamp (from the trade stream)
- Orderbook depth (from CLOB WS, in live deployment)

We do NOT know:
- Final total market volume at resolution
- Whether more qualified traders will enter after the Nth

### Signal-time volume definition

```
signal_time_vol = sum(abs(net_usd)) for qualified traders 1..N
                  WHERE first_trade <= signal_timestamp
```

This is strictly causal — only uses data available when the strategy fires.

### DuckDB exploration query

```sql
-- Compute signal-time volume (only first N traders' positions)
WITH ranked_entries AS (
    SELECT
        p.condition_id,
        p.trader,
        p.first_trade,
        abs(p.net_usd) AS trader_vol,
        ROW_NUMBER() OVER (
            PARTITION BY p.condition_id
            ORDER BY p.first_trade
        ) AS entry_rank
    FROM test_positions p
),
signal_points AS (
    SELECT
        condition_id,
        max(first_trade) AS signal_time,   -- Nth trader's entry
        sum(trader_vol) AS signal_vol       -- volume from traders 1..N only
    FROM ranked_entries
    WHERE entry_rank <= {N}
    GROUP BY condition_id
    HAVING count(DISTINCT trader) >= {N}
)
SELECT
    CASE
        WHEN s.signal_vol < 100 THEN 'micro'
        WHEN s.signal_vol < 500 THEN 'small'
        WHEN s.signal_vol < 1000 THEN 'medium'
        ELSE 'large'
    END AS vol_bucket,
    count() AS n_markets,
    avg(m.yes_won::INT) AS hr,
    avg(m.yes_won::INT) - {base_rate} AS excess_hr
FROM signal_points s
JOIN mkt_stats m ON s.condition_id = m.condition_id
GROUP BY 1
ORDER BY 1
```

### Key questions

1. Does signal-time volume (from N traders only) still predict HR?
2. What's the minimum signal-time volume threshold that works?
3. How much of the +45pp uplift survives when computed causally?

If signal-time vol still predicts HR (even at +15-20pp instead of +45pp), it becomes a
powerful causal sizing/filter signal. If it doesn't, the volume finding was entirely
look-ahead and we rely on pure consensus count only.

### Integration

Signal-time volume is observable in live deployment:
- From the trade stream: sum the position sizes of the N qualifying traders
- No external data needed beyond what the strategy already processes
- Can be used as a filter (skip if signal_vol < $X) or as a sizing factor

### Validation approach

1. DuckDB vectorized: compute signal-time vol per consensus market, bucket by vol, compare HR
2. If signal exists: add `min_signal_vol` as a parameter in tick-by-tick replay
3. Compare: signal-time vol filter HR vs resolution-time vol filter HR (quantify look-ahead bias)

---

## Track 5: Sustainability Tiers (Trader Consistency Grading)

### Concept

Not all high-HR traders are equal. A trader with 70% HR in every 2-month sub-window is
fundamentally more reliable than one who had 95% HR in one period and 30% in another.
The aggregate excess_hr is the same, but the signal quality is different.

**Sustainability = temporal consistency of edge.**

### Tier definitions

Split training window (6 months) into 3 x 2-month sub-windows. For each trader:

| Tier | Criteria | Interpretation | Sizing weight |
|------|----------|----------------|---------------|
| 1 | Positive excess in ALL 3 sub-windows, low variance | Rock-solid | 2.0x |
| 2 | Positive excess in ALL 3 sub-windows, any variance | Reliable | 1.5x |
| 3 | Positive excess in 2/3 sub-windows | Seasonal | 1.0x |
| 4 | Positive excess in 1/3 sub-windows | Noisy | 0.5x |
| 5 | Positive aggregate but 0/3 sub-windows positive | Lucky run | 0.25x |

### Dynamic conviction adjustment

As trade flow arrives after the initial consensus trigger:

```python
# At N-th trader entry (consensus trigger):
initial_quality = mean(tier_weight[t.tier] for t in traders[:N])
position_size = base_size * initial_quality

# As N+1, N+2 traders enter:
if new_trader.tier <= 2:
    add_to_position(base_size * 0.5)  # Tier 1-2 confirmation
elif new_trader is on NO side and new_trader.tier == 1:
    reduce_position(base_size * 0.3)  # Tier 1 dissent = strong warning

# Controversial position handling:
yes_quality = sum(tier_weight[t.tier] for t in yes_traders)
no_quality = sum(tier_weight[t.tier] for t in no_traders)
if no_quality > yes_quality * 0.5:
    skip_or_reduce()  # Quality-weighted dissent
```

### Key advantage

This solves the base rate non-stationarity problem. Traders who maintain excess HR across
different base rate regimes (low in sub-window 1, high in sub-window 2) are genuinely skilled.
Traders who only beat a 65% base rate by 5pp are not. The tier system separates them
automatically without needing an explicit regime gate.

### Validation approach

1. DuckDB vectorized: compute tier distributions, check if tiers predict out-of-sample HR
2. If tiers predict HR: add tier-weighted sizing to tick-by-tick (compare flat vs weighted PnL)
3. If Tier 1-2 traders dominate successful consensus: use tier as hard gate (require >= 1 Tier 1-2)

---

## Implementation Priority

| Track | Priority | Status | Expected impact |
|-------|----------|--------|-----------------|
| 4. Signal-time volume | DONE | R5 tick validated | Causal gate, +20-45pp (hard gate at $200) |
| 3. Contradictory signals | DONE | R5 tick validated | Dissent >=0.70 as gate, kills count if too strict |
| 5. Sustainability tiers | IN PROGRESS | DuckDB exploration | Quality-weighted sizing, regime adaptation |
| 2. Trader profiling | SUBSUMED by Track 5 | — | Track 5 is the evolved version |
| 1. Time-to-live | LOW | Not started | Capital efficiency (post-profit) |

Track 5 (sustainability tiers) subsumes Track 2 (trader profiling). Track 2 was about
aggregate per-trader quality; Track 5 adds the temporal dimension.

---

## Integration with base strategy

These tracks change HOW MUCH we invest. The base consensus trigger (N qualified traders
from sharp pool converge on YES) remains the entry signal. The sizing layers stack:

```python
# Gate 1: signal-time volume (Track 4)
if signal_time_vol < min_signal_vol:
    return None

# Gate 2: price ceiling
if signal_price > price_ceil:
    return None

# Sizing
base = $100

# Factor 1: Tier-weighted quality (Track 5)
quality = mean(tier_weight[t.tier] for t in consensus_traders)
size = base * quality  # Tier 1 consensus = $200, Tier 5 = $25

# Factor 2: Quality-weighted dissent (Track 3 + Track 5)
yes_quality = sum(tier_weight[t.tier] for t in yes_traders)
no_quality = sum(tier_weight[t.tier] for t in no_traders)
dissent_factor = yes_quality / (yes_quality + no_quality)
size *= dissent_factor

# Factor 3: Hold time adjustment (Track 1, future)
# size *= hold_time_factor(estimated_hold)

# Cap
size = min(size, max_position_usd)
```

### Graduated entry (dynamic conviction)
```python
# Enter small at N=2 (with high-tier traders):
if n_yes >= 2 and mean_tier <= 2:
    enter(size * 0.3)

# Add at N=3:
if n_yes >= 3:
    add(size * 0.3)

# Full conviction at N=4+:
if n_yes >= 4:
    add(size * 0.4)
```

Each factor is independently testable and can be enabled/disabled.
