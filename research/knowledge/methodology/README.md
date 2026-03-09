# Research Methodology: From Raw Data to Deployed Strategy

This guide walks through the complete pipeline for building and deploying a copy-trading strategy on Polymarket. It is intended for someone technically skilled but new to this codebase.

The pipeline has two phases:
- **Part 1** (Steps 1-5): Pre-strategy work — building the signal by identifying skilled traders and scoring them
- **Part 2** (Steps 6-10): Strategy execution — tick-by-tick validation, simulation, and production deployment

---

## Part 1: Pre-Strategy (Features and Pool Building)

### Step 1: Data Foundation

**What exists and where**

All research uses a Parquet snapshot that was exported from ClickHouse. It lives in `data/research/` (~17.6 GB) and is accessed via a DuckDB singleton:

```python
from research.db import db
d = db()  # 3.4s startup, loads tables into memory
# d.con is the raw DuckDB connection for SQL queries
```

**Key tables loaded in-memory by `db()`:**

| Table | Contents | Key columns |
|-------|----------|-------------|
| `maker_positions` | One row per (trader, condition_id, direction) resolved market | trader, condition_id, position, yes_won, correct, net_usd, volume, resolved_at |
| `maker_positions_resolved_corrected` | Same with split corrections applied (~12% of positions had corrupted PnL) | same as above |
| `markets` | One row per market | condition_id, slug, event_id, neg_risk |
| `markets_resolved` | Markets with resolution info | condition_id, resolved_at, winner_outcome |
| `event_tags` | Tag assignments for events | event_id, tag_id, label |
| `token_market_map` | asset_id ↔ condition_id + outcome mapping | asset_id, condition_id, outcome |
| `trader_volumes` | Trader-level volume aggregates | trader, total_volume_usd |

**External Parquet views (predicate pushdown, not in-memory):**

| View | Contents | Access |
|------|----------|--------|
| `yes_entry_data` | Pre-joined YES entry prices | `data/research/positions/yes_entry_data.parquet` |
| `trades` | Individual trade ticks | `data/research/trades/` partitioned by month |
| `trader_trade_agg` | Aggregated per-trader trade stats | Parquet view |

**How they join:**

```
markets ──(event_id)──> event_tags ──> tag label (e.g., "Sports")
markets ──(condition_id)──> maker_positions ──> trader behavior
maker_positions ──(trader, condition_id)──> yes_entry_data ──> entry prices
markets ──(condition_id)──> token_market_map ──> asset_id (YES/NO tokens)
```

**The `correct` column**: In `maker_positions`, `correct=1` when the position won. For YES positions, `correct=1` when `yes_won=1`. For NO positions, `correct=1` when `yes_won=0`. This means you can use `correct` uniformly regardless of direction.

**Why split corrections matter**: `maker_positions_resolved_corrected` patches positions where CTF splits created artificially negative `net_no` token counts. Affects ~12% of maker positions. Always use the corrected view for any PnL or position-size analysis. See `pitfalls/split_position_blind_spot.md`.

---

### Step 2: Market Classification

Before scoring traders, markets must be filtered and categorized. Two classification tasks:

**Gambling exclusion (critical)**

169K markets (29.4% of all markets) are crypto price-direction gambling products (5m/15m/4h binary bets). They generate 56% of all positions but are noise for copy trading. The slug pattern catches 97% of them:

```sql
-- Gambling exclusion macro (used throughout pool building)
CREATE OR REPLACE MACRO is_gambling_market_v3(slug) AS (
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
    OR (
        (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
             OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
             OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%sol%'
             OR lower(slug) LIKE '%-close-%'
             OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%nvda%')
    )
);
```

Do NOT use `markets.category` — it is NULL in almost all rows. Do NOT use `higher`/`lower` slug patterns — 95%+ false positives. See `data/gambling_market_taxonomy.md`.

**Tag assignment**

Markets are tagged via the `event_tags` chain: `markets → events → event_tags → tags`. Many markets have multiple tags (e.g., "NBA" market also has "Basketball" and "Sports"). Assign a single primary tag using a priority-ordered CASE WHEN:

```sql
CREATE OR REPLACE TABLE _v3_market_tags AS
WITH tag_ranked AS (
    SELECT m.condition_id, m.slug, et.label,
        CASE
            WHEN et.label = 'Politics'    THEN 0
            WHEN et.label = 'Elections'   THEN 1
            WHEN et.label = 'Sports'      THEN 2
            WHEN et.label = 'Basketball'  THEN 3
            WHEN et.label = 'Soccer'      THEN 4
            WHEN et.label = 'Esports'     THEN 5
            WHEN et.label = 'NBA'         THEN 6
            WHEN et.label = 'Crypto'      THEN 7
            ...
            ELSE 999
        END AS tag_priority
    FROM markets m
    JOIN event_tags et ON m.event_id = et.event_id
    WHERE NOT is_gambling_market_v3(m.slug)
      AND m.neg_risk = 0
)
SELECT condition_id, slug, arg_min(label, tag_priority) AS primary_tag
FROM tag_ranked GROUP BY condition_id, slug;
```

**Why priority-ordered, not "most specific"?** Markets should inherit their most informative classification. "NBA" is more specific than "Sports" but both are valid. Priority order reflects research utility — Politics markets contain Elections markets, but for strategy purposes we want the most actionable label.

**Why exclude `neg_risk = 1`?** Negative-risk markets share a liquidity pool across outcomes and have different resolution mechanics. Exclude them to avoid contaminating single-binary analyses.

**Tag-specific base rates (critical)**

The global base rate (38% YES / 62% NO) is wrong for most tags. Tag YES base rates range from 3% (Golf/PGA) to 73% (Earnings). A trader with 50% YES HR is +27pp skilled in Esports (45.8% base) but -23pp unskilled in Earnings (72.9% base). Always use tag-specific base rates for excess HR computation. See `data/tag_base_rates.md`.

---

### Step 3: Trader Scoring

The core question: "Given a trader's history of resolved positions in tag X, how skilled are they?"

**The 4-component composite scorecard:**

```
composite = 0.45 × percentile(excess_hr)
          + 0.25 × percentile(consistency_sharpe)
          + 0.15 × percentile(avg_edge_usd)
          + 0.15 × percentile(bucket_excess_hr)
```

All components are percentile-rank normalized to [0,1] within the tag before weighting. This normalization means a trader at the 90th percentile on excess_hr gets 0.90, regardless of the absolute HR value.

**Component details:**

| Component | What it measures | Why it matters |
|-----------|-----------------|----------------|
| `excess_hr` | HR minus tag base rate | Primary skill signal. IC=0.744 (highly persistent trait). |
| `consistency_sharpe` | Monthly HR mean / (stddev + 0.05) | Anti-luck filter. A trader with 70% HR in 12/12 months is more valuable than one who went 100% then 40%. Prevents pool collapse in walk-forward. |
| `avg_edge_usd` | Median realized PnL per market | Ensures traders make meaningful money, not just technical correct calls on $2 positions. |
| `bucket_excess_hr` | HR − population HR within 0.10-wide price buckets | Controls for near-certainty effect. See Step 3a below. |

**Qualification gates (applied BEFORE scoring):**

```sql
HAVING count(DISTINCT condition_id) >= 20  -- sufficient sample
   AND avg(conviction) >= 0.90             -- not market maker (YES only)
   AND count(*) < 10000                    -- not a bot
   AND (hit_rate - base_rate) > 0          -- positive edge
   AND bucket_excess_hr >= 0.02            -- BEH gate (v3+): removes near-certainty bettors
```

Note: the conviction gate (`avg(abs(net_usd)/volume) >= 0.90`) is applied for YES pools but omitted for NO pools — NO positions rarely have clean conviction signals due to split mechanics.

**Step 3a: The BEH Gate**

A trader who only bets YES when the market is already at 0.90+ achieves 90%+ HR trivially — they're not predicting, they're taking near-certain payoffs. The BEH (bucket excess HR) gate removes these traders:

```
BEH = avg over all price buckets: (trader_HR_in_bucket − population_HR_in_bucket)
```

Gate: `BEH >= 0.02` before composite scoring. This removes ~26% of Crypto pool traders who pass raw excess_hr > 0 but have no genuine predictive skill. See `signals/edge_weighted_skill.md`.

**Why HR alone fails**

HR-only ranking:
- Collapses to 1 signal in fold 3 of walk-forward (unstable)
- Selects traders with low but consistent volume over those with high variance
- Has only 10-21% Jaccard overlap with composite ranking (fundamentally different traders)

Composite-ranked traders gained vs HR-only have 2-5x higher consistency_sharpe and 5-12x higher avg_edge_usd. See `signals/composite_scorecard.md`.

**Direction decomposition (critical)**

Traders specialize by direction. Treating all traders as undirected wastes signal:

- 12.6% of traders are YES-skilled (BEH >= 0.02, >=10 YES positions)
- 51.0% are NO-skilled (BEH >= 0.02, >=10 NO positions)
- 3.3% are dual-skilled

Direction rules by tag:
- **Sports, Crypto**: Build YES-only pools. NO signals have structural bias, not skill.
- **Politics**: Build separate YES and NO pools. Both have genuine specialists.
- **Esports**: Pure NO signal. YES BEH near zero (0.027 average in top-50).

See `signals/edge_weighted_skill.md` for per-tag decomposition details.

---

### Step 4: Pool Building

A "pool" is a set of top-K traders selected by composite score who will collectively generate signals.

**Full pipeline (v3, DuckDB):**

```python
from research.hypotheses.scorecard_v3_strategies.scripts.build_pools_v3 import (
    build_sports_yes_pool_v3,
    build_politics_no_pool,
)

# Returns (pool_set, tag_markets_set, gambling_markets_set)
pool, tag_markets, gambling_markets = build_sports_yes_pool_v3(k=25)
```

Each call executes 4 sequential SQL steps:
1. Base training stats (excess_hr qualification)
2. Consistency sharpe (monthly HR time series)
3. Bucket excess HR (price-level skill)
4. Composite ranking + BEH gate + top-K selection

Takes ~5-15s per pool on DuckDB in-memory.

**Walk-forward discipline (critical)**

Training must use only data before `train_cutoff`. Test must use data from `test_start` onward. The two must be equal (`train_cutoff = test_start = 2025-07-01`). No lookahead:

```python
TRAIN_CUTOFF = "2025-07-01"  # scorer uses: resolved_at < TRAIN_CUTOFF
TEST_START   = "2025-07-01"  # strategy sees: resolved_at >= TEST_START
```

A prior iteration violated this by including positions that resolved in the test window but were entered during training. This inflated test HR by 12+ pp and signal counts by 32%. See `pitfalls/phantom_signals.md` and the MEMORY.md entry on the first_trade filter.

**Pool cap guidance**

| Tag | Direction | Recommended K | Rationale |
|-----|-----------|--------------|-----------|
| Sports | YES | 25 | 2023+ fills/8mo at N=2; enough signal |
| Politics | YES | 100 | Sparse markets (18K total), need larger pool |
| Politics | NO | 100 | Same reasoning; slower capital recycling |
| Crypto | YES | 50 | Balanced between pool quality and size |
| Esports | NO | 50 | Thin market count; larger pool compensates |

Do not set K too large: pool explosion (536-774 traders) causes NO HR collapse (too many noisy traders in pool). See prior tag-hr-consensus findings in MEMORY.md.

---

### Step 5: Vectorized Discovery (Upper Bound Estimation)

Before running tick-by-tick (slow), run vectorized sweeps over the Parquet snapshot to estimate signal quality across parameter combinations.

**What vectorized sweeps measure**

A vectorized sweep aggregates resolved positions: "for each market where N+ pool traders entered on the YES side, what was the outcome?" This is the theoretical upper bound — it assumes:
- Infinite capital (unlimited concurrent positions)
- Entry at the blended average price across all pool traders
- Perfect timing (no execution delay)
- No position already being signaled when N-1 traders are present

**Market-level aggregation rule (critical)**

Every vectorized result must aggregate to market level (1 row per condition_id):

```sql
WITH consensus_markets AS (
    SELECT
        condition_id, position,
        count(DISTINCT trader) AS n_qualified,   -- DISTINCT is mandatory
        max(first_trade) AS signal_entry,         -- Nth trader's entry = signal time
        first(resolved_at) AS resolved_at,
        first(correct) AS market_correct
    FROM maker_positions_resolved_corrected p
    JOIN qualified_pool q ON p.trader = q.trader
    WHERE p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '{test_start}'
      AND CAST(p.first_trade AS DATE) >= '{test_start}'  -- phantom signal filter
    GROUP BY condition_id, position
    HAVING n_qualified >= {consensus}
)
SELECT
    count(*) AS n_signals,
    avg(market_correct) AS hr,
    median(date_diff('day', signal_entry, resolved_at)) AS hold_days
FROM consensus_markets
```

**Phantom signal filter**: `first_trade >= test_start` ensures we only count positions where the entry happened during the test period. Without this, a trader who entered during training and had the market resolve in the test window would count as a test signal — but that entry is not copyable (it happened before the strategy was active). This one filter can inflate signal counts by 32%.

**Sweep template**: `research/hypotheses/tag-hr-copy/scripts/sweep_duckdb.py` — full 3-tag sweep in 46s.

**Interpreting vectorized results**

Always label vectorized results as UPPER BOUNDS. Expected degradation when moving to tick-by-tick:
- Consensus strategies: 20-40pp HR degradation
- In-play strategies: 7-10pp degradation (fill model only — real-time infra has no latency gap)

When tick HR is close to (or exceeds) vectorized UB, it indicates the consensus fires early — a strong positive signal. See `pitfalls/vectorized_vs_tick.md`.

---

## Part 2: Strategy Execution

### Step 6: Strategy Protocol

Strategies implement the `Strategy` protocol in `src/polymarket_pipeline/strategies/protocol.py`. For research, the key entry point is the synchronous hot-path:

**`ConsensusStrategy` in `research/strategies/consensus_v2.py`:**

```python
from research.strategies.consensus_v2 import TokenMapStrategy

strategy = TokenMapStrategy(
    name="sports_yes_k25_n2",
    pool=pool,               # set[str] of lowercase trader addresses
    tag_markets=tag_markets, # set[str] of condition_ids for this tag
    gambling_markets=gambling_markets,  # set[str] to exclude
    n_threshold=2,           # fire when 2 distinct pool traders have entered
    token_map=token_map,     # {condition_id: {"YES": asset_id, "NO": asset_id}}
    direction_filter="YES",  # "YES", "NO", or None (both)
    size_usd=100.0,
)
```

**How it processes each trade tick:**

```
on_trade_sync(tick) → apply 5 gates → update state → check consensus → maybe fire TradeIntent

Gate 1: condition_id in tag_markets?       (correct tag)
Gate 2: condition_id not in gambling?      (not a gambling market)
Gate 3: condition_id not already signaled? (one signal per market)
Gate 4: trade.side == "BUY"?              (SELL is ambiguous — exit or split-entry)
Gate 5: trade.maker in pool?              (pool trader)

State update: accumulate {maker: {YES: usd, NO: usd}} per condition_id

Consensus check: count distinct pool traders by dominant direction
  → if n_traders_in_majority >= n_threshold: fire TradeIntent
```

**SELL exclusion**: SELL trades are excluded because in Polymarket's CTF mechanic, "SELL YES" can be either exiting a YES position or entering a NO position via the split route. The strategy cannot determine which without full position history. Excluding SELLs introduces a modest undercount but avoids directional contamination. See `pitfalls/sell_is_exit.md`.

**Direction resolution**: `asset_id` from the trade tick is looked up in `token_map` to determine YES or NO. `TokenMapStrategy` pre-populates the direction cache at construction time for O(1) lookups.

---

### Step 7: Tick-by-Tick Validation

**Why tick-by-tick validation is required**

Vectorized sweeps answer "did enough pool traders enter this market?". Tick-by-tick replay answers "when the Nth trader entered, was that the moment to copy them, at what price, given capital constraints?" The two are fundamentally different questions. The first is an upper bound; the second is a realistic estimate.

**The simplest entry point:**

```python
from research.harness import run_fast_backtest, print_summary
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

config = StrategyConfig(
    name="sports_yes_k25_n2",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50_000,       # total budget (not per-fill)
    max_position_usd=100,     # per-market position size
    max_open_positions=50,    # concurrent position limit
    cooldown_s=0,
)

result, summary = run_fast_backtest(
    strategy,
    config,
    universe=tag_markets,  # predicate pushdown — only load relevant trades
)
print_summary(summary, "sports_yes_k25_n2")
```

**What `run_fast_backtest` does:**
1. Calls `load_replay_trades(universe=tag_markets)` — Polars scan with predicate pushdown (~1-3s for large universes)
2. Calls `load_replay_resolutions()` — loads token_map and resolution outcomes from Parquet
3. Creates `SyncReplayRunner` with `SimulatedExecutor` (instant fill at trigger price + 0.02)
4. Processes all ticks chronologically; strategy calls `on_trade_sync()` per tick
5. Flushes `ParquetLedger` to `research/output/ledger_{name}.parquet`
6. Computes analytics summary (HR, PnL, Sharpe, drawdown, profit factor)

**Capital constraints in tick-by-tick:**

`max_open_positions=50` limits concurrent active positions. Once the limit is hit, new signals are queued but not filled until existing positions settle (market resolves). Long-dated markets (Politics: 54-day avg hold) block slots for extended periods — this is the primary vectorized-to-tick degradation source for Politics strategies.

**Fill model choices:**

| Executor | Fill price | Use for |
|----------|-----------|---------|
| `SimulatedExecutor` | trigger_price + 0.02 | Research validation (fast) |
| `RealisticFillSimulator` | trigger_price + 0.02 + calibrated slippage | Tighter PnL estimates |

For research, `SimulatedExecutor` is sufficient. The +0.02 slippage already captures most of the fill gap. `RealisticFillSimulator` adds market-impact estimates from calibrated spread data — only needed if your strategy fires in thin markets.

**Expected degradation by strategy type:**

| Strategy type | Expected tick vs vectorized | Reason |
|--------------|-----------------------------|--------|
| Consensus (N≥2) | -20 to -40pp | Entry price gap + capital constraints |
| In-play copy (N=1) | -7 to -10pp | Fill model only; real-time infra eliminates signal delay |
| NO-direction consensus | 0 to +5pp | Consensus fires early; market hasn't priced in outcome yet |

---

### Step 8: Promotion Pipeline

After tick-by-tick validation produces a viable strategy (positive excess HR, positive PnL, Sharpe > 0.5), the path to production is:

```
vectorized sweep
   └─> tick-by-tick validation (SyncReplayRunner)
         └─> paper_dev (live data, simulated fills, loose gates)
               └─> paper_prod (live data, realistic fills, strict gates)
                     └─> live (real money)
```

**Promotion gates** (enforced by `pm-strategy promote`):

```
paper_dev  → paper_prod: min_trades=100, sharpe>0.5, pnl>0, max_drawdown<20%
paper_prod → live:       min_trades=500, sharpe>1.0, runtime>30d
```

**Running in paper mode:**

```bash
# Register strategy in configs/my_strategy.toml
uv run pm-strategy run --config configs/sports_yes.toml

# Check promotion readiness
uv run pm-strategy promote sports_yes_k25_n2 --to paper_prod --config configs/sports_yes.toml
```

**Live infrastructure for strategy execution:**

| Component | Role | Kafka topic |
|-----------|------|-------------|
| RTDS WebSocket | Trade feed (~50ms) | `trades.raw` |
| Pending Block Poller | Pre-confirmation (~1s early) | `pending.signal` |
| CLOB WS Orderbook | Best bid/ask | `orderbooks.raw` |
| Market Events | Resolution signals (5s debounce) | `markets.events` |

The strategy's `on_trade()` async method is called for each event from `trades.raw`. `ExecutionGateway` wraps the executor and enforces capital budgets. `LiveRunner` handles pool refresh via `request_refresh()` when `markets.events` fires a resolution.

**Sub-second delivery**: Elite traders lead by 58 minutes median. WS delivery is ~50ms. The strategy captures the full 58-minute information advantage without any meaningful execution latency penalty. See `execution/live_infrastructure.md`.

---

### Step 9: Key Pitfalls

These are failure modes that destroyed prior research iterations:

**1. Individual vs consensus signal mismatch**

Vectorized sweeps implicitly measure consensus (only count markets where N traders are present). A tick-by-tick strategy that fires on the first individual trader's entry replicates a fundamentally weaker signal. This was the primary cause of the tag-hr-copy failure (67% vectorized → 46% tick, -21pp). Fix: buffer qualified trades per `condition_id`; emit `TradeIntent` only when N distinct qualified traders have entered.

**2. In-play contamination**

Sports experts enter during live matches. HR=99%+ for hold < 4h — but these signals are from during the game, not before. A live copy strategy would fire on a soccer market 5 minutes before the final whistle. Fix: either filter `hold >= 24h` in vectorized sweeps, or build an explicit in-play track that accepts this behavior and sizes appropriately.

**3. Pool explosion**

Setting K too large (K=200+) or using a loose excess_hr threshold floods the pool with marginally skilled traders. When consensus requires N=3 and there are 600 pool traders in a market, you get fake consensus constantly. Fix: composite scoring with tight K (25-100), BEH gate to pre-filter.

**4. Lookahead bias from train/test contamination**

If `train_cutoff != test_start`, or if positions resolved in the test window but entered during training are counted, the result is optimistically biased. A trader's training-period entries don't exist to copy during the test period. Fix: always add `AND CAST(first_trade AS DATE) >= '{test_start}'` to the test-period query.

**5. Phantom signals (resolved in test, entered in training)**

A market entered during training that resolves during the test window appears in test-period signal counts. That trade is not copyable — you would have needed to enter it before the strategy was active. Confirmed magnitude: 31.9% of test-window positions had `first_trade` before `test_start` in the 2025-07 fold. Fix: the phantom signal filter (`first_trade >= test_start`).

**6. Split position blind spot**

Polymarket CTF splits (buy USDC → YES token + NO token → sell unwanted side) create negative `net_no` token counts in raw data. ~12% of maker positions are affected. Using raw `trader_positions_resolved` gives corrupted PnL for these positions. Fix: always use `maker_positions_resolved_corrected` for any PnL analysis.

**7. SELL trade semantics**

SELL is not simply "exit a position." SELL YES = "sell YES tokens" (could be exiting a YES position or selling the YES side from a split to enter NO). Including SELLs as directional signals in consensus logic corrupts signal quality. Fix: strategy should track only BUY trades for consensus counting.

**8. Vectorized counting unit**

Any vectorized sweep that uses `count(*)` over rows rather than `count(DISTINCT trader)` will overcount consensus. One trader making 4 trades into a market does NOT represent 4 distinct consensus voices. Fix: always use `count(DISTINCT trader)` in the HAVING clause.

---

### Step 10: Current Portfolio (As of 2026-03-09)

| Strategy | Config | Tick Excess HR | Fills/8mo | Sharpe | Status |
|----------|--------|---------------|-----------|--------|--------|
| Sports YES v3 | K=25, N=2 | +30.0pp | 2023 | 5.23 | PROMOTE to paper_dev |
| Sports InPlay v3 | K=25, N=1 | +26.9pp | 5936 | 0.27 | VIABLE with position limits |
| Politics NO v3 | K=100, N=2 | +9.3pp | 347 | 0.55 | MARGINAL — monitor |
| Elite Whale Copy | top-100, N=1 | ~3pp degradation | ~200/mo | 0.72 | Separate track — no price gate |

**Capital allocation guidance:**
- Sports YES K=25 N=2: primary allocation (best risk-adjusted return, 6.9h avg hold)
- Sports InPlay K=25 N=1: secondary allocation with hard `max_open_positions=20` cap
- Politics NO: tertiary, small allocation due to 54-day hold and low Sharpe
- Elite Whale Copy: runs independently, not part of consensus framework

---

## Quick Reference

**Start a new research hypothesis:**

```bash
# 1. Create hypothesis folder
mkdir -p research/hypotheses/my-new-idea/{scripts,discovery,validation}

# 2. Run a DuckDB vectorized sweep
PYTHONPATH=. uv run python research/hypotheses/my-new-idea/scripts/sweep.py

# 3. If promising, run tick validation
PYTHONPATH=. uv run python research/hypotheses/my-new-idea/scripts/run_tick.py

# 4. Capture findings
# Edit research/knowledge/ entries
# Update research/ideas.md with verdict
```

**Key imports for research scripts:**

```python
from research.db import db                         # DuckDB singleton
from research.harness import run_fast_backtest, print_summary  # tick validation
from research.strategies.consensus_v2 import TokenMapStrategy  # strategy
from research.hypotheses.scorecard_v3_strategies.scripts.build_pools_v3 import (
    build_sports_yes_pool_v3, build_politics_no_pool,  # pool builders
)
```

**DuckDB syntax reminders** (differs from ClickHouse):
- `first()` not `any()` for arbitrary-element aggregation
- `date_diff('day', ts1, ts2)` with single-quoted date part
- `arg_min(label, priority)` not `argMin(label, priority)`
- `CAST(x AS DATE)` not `toDate(x)`
- No `FINAL` keyword (DuckDB doesn't have MergeTree)
- `percent_rank() OVER (ORDER BY col)` for percentile normalization

---

## Related Knowledge Entries

- `signals/composite_scorecard.md` — full scorecard system with all validated results
- `signals/no_direction_consensus.md` — Politics NO strategy details
- `signals/edge_weighted_skill.md` — BEH gate and direction decomposition
- `signals/hr_persistence.md` — why excess_hr is the primary signal (IC=0.744)
- `data/tag_base_rates.md` — base rates by tag (critical for excess HR computation)
- `data/gambling_market_taxonomy.md` — gambling market exclusion
- `pitfalls/vectorized_vs_tick.md` — simulation gap quantification
- `pitfalls/individual_vs_consensus_signal.md` — the most common research bug
- `pitfalls/vectorized_counting_unit.md` — market-level aggregation rule
- `pitfalls/sell_is_exit.md` — SELL trade semantics
- `pitfalls/split_position_blind_spot.md` — use corrected tables
- `execution/live_infrastructure.md` — sub-second delivery; no latency penalty
- `execution/hold_time_capital.md` — capital constraint modeling
