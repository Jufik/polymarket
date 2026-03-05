---
name: research-discover
description: "Vectorized signal discovery methodology — CH SQL sweeps, marimo notebooks, compounding score computation. Used by the Researcher agent during Phase 2."
user-invocable: false
---

# Discovery Methodology (Vectorized)

You are performing vectorized signal discovery. All results are UPPER BOUNDS.

## Step 0: Load Knowledge

Before any CH query, load relevant knowledge:

```python
# Read all CRITICAL/WARNING admonitions from research/knowledge/
# Apply each one to your methodology
```

Key pitfalls to address:
- **SELL handling** (`pitfalls/sell_is_exit.md`): SELL is directional (SELL YES = bearish, SELL NO = bullish) but ambiguous (exit vs split-entry). Include/exclude is a research parameter — test both.
- **Consensus dedup** (`pitfalls/consensus_dedup.md`): count unique traders, not trades
- **Resolution** (`data/resolution_mechanics.md`): use asset_id, never string matching
- **Base rates** (`data/market_base_rates.md`): NO wins 62%, YES wins 38%

## Step 1: CH SQL Sweep

Connect to remote ClickHouse: `192.168.0.148:18123`, database `polymarket`.

### COMPOSABLE QUERY PATTERN (MANDATORY)

**NEVER write monolithic 150+ line CTEs.** Build queries by JOINing against existing
classification tables and views. Each query should be <50 lines.

### TEMPORAL CLASSIFICATIONS (MANDATORY)

Classifications are **functions** `f(cutoff) → rows`. Every query MUST use `as_of` filtering
to ensure point-in-time correctness. The `{cutoff}` placeholder is substituted at runtime.

**NEVER query classifications without `as_of`** — that leaks future data into backtests.

#### Classification tables (taxonomy layer):

```sql
-- Exclude bots (point-in-time)
LEFT JOIN (SELECT trader FROM trader_classifications FINAL
           WHERE label = 'bot' AND as_of = toDate('{cutoff}')) bots
    ON t.maker = bots.trader
WHERE bots.trader IS NULL

-- Only insider-susceptible markets (point-in-time)
INNER JOIN (SELECT condition_id FROM market_classifications FINAL
            WHERE label = 'susceptibility' AND tier >= 2
              AND as_of = toDate('{cutoff}')) mkt
    ON t.condition_id = mkt.condition_id

-- Score-based filtering (point-in-time)
INNER JOIN (SELECT trader, score FROM trader_classifications FINAL
            WHERE label = 'insider_score' AND tier <= 2
              AND as_of = toDate('{cutoff}')) insiders
    ON t.maker = insiders.trader
```

#### Creating new classifications (fast iteration):

1. **Write a rule** as a `.sql` file in your hypothesis `classifications/` folder:
   ```sql
   -- file: classifications/trader_sure.sql
   -- label: sure_trader | entity: trader | version: 1
   -- schedule: manual
   -- description: Traders with avg BUY price > 0.90 across > 20 markets before cutoff
   INSERT INTO trader_classifications
   SELECT maker, 'sure_trader', tier, avg_price, toDate('{cutoff}'), 1, now64(3, 'UTC')
   FROM (
       SELECT maker,
              avg(price) AS avg_price,
              if(avg(price) > 0.95, 1, 2) AS tier
       FROM (SELECT * FROM trades_raw FINAL)
       WHERE side = 'BUY' AND timestamp <= toDateTime('{cutoff}')
       GROUP BY maker
       HAVING avg_price > 0.90 AND count(DISTINCT condition_id) > 20
   )
   ```
2. **Populate it** by running the rule with your test cutoff date
3. **Use it** immediately via JOIN with `as_of = toDate('{cutoff}')`
4. **Iterate** — tweak the rule, re-populate, re-test
5. **Promote** — when proven useful, propose for production in `discovery/notes.md`

#### If a classification you need does NOT exist and you don't want to create it yet:

Propose it in `discovery/notes.md` under a `## Proposed Classification` section:
```markdown
## Proposed Classification: sure_trader
Entity: trader
Rule: traders whose BUY trades have avg price > 0.90 across > 20 markets
SQL sketch: SELECT maker AS trader, avg(price) FROM trades_raw FINAL
            WHERE side = 'BUY' GROUP BY maker HAVING avg(price) > 0.90 AND count(DISTINCT condition_id) > 20
Tier mapping: tier=1 (avg > 0.95), tier=2 (avg > 0.90)
Score: avg(price) as continuous score
```

#### Available building blocks:

| Table/View | Type | Use For |
|---|---|---|
| `trades_raw` | ReplacingMergeTree | Raw trades (use FINAL for dedup) |
| `trader_market_positions` | SummingMergeTree | Positions per (trader, condition_id) |
| `markets_resolved` | VIEW | Resolution data (condition_id, asset_id, outcome, token_won) |
| `trader_trade_agg` | SummingMergeTree | Per (trader, condition_id, asset_id) aggregation |
| `trader_volumes` | SummingMergeTree | maker_vol, taker_vol per trader |
| `trader_classifications` | ReplacingMergeTree | Trader taxonomy — temporal, use `as_of` filter |
| `market_classifications` | ReplacingMergeTree | Market taxonomy — temporal, use `as_of` filter |

### SQL conventions:
- Always use `FROM (SELECT * FROM table FINAL) alias` NOT `FROM table FINAL AS alias`
- Compare hit rates against base: NO 62%, YES 38%
- SELL handling is a research parameter — test BUY-only vs directional SELL mapping (see `pitfalls/sell_is_exit.md`)
- **JOIN classification tables with `as_of`** — never re-derive inline, never omit temporal filter
- Keep queries under 50 lines by composing building blocks

## Step 2: Parameter Sweep

Vary signal thresholds systematically. For each combo compute:
- Hit rate (by direction: YES and NO separately)
- Excess HR above base rate
- Average edge per trade (USD)
- Median hold time (days)
- Universe size (trades/month)
- **Compounding score**: `excess_hr x avg_edge_usd / median_hold_days`

Use walk-forward windows when possible:
- Train: 12 months (default from config)
- Test: 1 month (default from config)
- **Re-populate classifications per fold** using `cutoff = fold_train_end`

## Step 3: Create Marimo Notebook

Write to `discovery/notebook.py`. Marimo conventions:

```python
import marimo as mo
# Cell 0: Imports + CH connection
import clickhouse_connect
ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

# Cell 1: Classification population (populate rules with cutoff)
# Cell 2: Universe definition (qualified traders, market filters — via classification JOINs)
# Cell 3: Signal computation SQL
# Cell 4: Parameter sweep results table
# Cell 5: Hit rate vs base rate chart
# Cell 6: Compounding score heatmap
# Cell 7: Hold time distribution
```

## Step 4: Populate config.toml

Fill in `research/hypotheses/{slug}/config.toml` with:
- Strategy name and parameters from best sweep combo
- Provider configuration
- Harness settings (executor=realistic, settlement=true)

## Step 5: Write Notes

Write observations to `discovery/notes.md`:
- What worked, what didn't
- Surprising findings (flag for knowledge capture)
- Spawned ideas (for backlog)
- Parameter sensitivity analysis
- **Classification proposals** for rules that proved useful

## Output Requirements

All discovery results MUST be labeled as **UPPER BOUNDS**.

Return to Lead:
1. Top 5 parameter combos by compounding_score
2. Vectorized HR and PnL (labeled UPPER BOUND)
3. Hold time distribution
4. Universe size (trades/month)
5. Spawned ideas (for backlog)
6. Surprising findings (for knowledge capture)
7. Classification rules created/proposed
