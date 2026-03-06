---
name: research-discover
description: "Vectorized signal discovery methodology — CH SQL sweeps, marimo notebooks, compounding score computation. Used by the Researcher agent during Phase 2."
user-invocable: false
---

# Discovery Methodology (Vectorized)

You are performing vectorized signal discovery. All results are UPPER BOUNDS.

## Step 0: Verify Knowledge Context

Your dispatch prompt includes a `## Knowledge Context` section with all CRITICAL and WARNING
admonitions collected by the Lead in Phase 0. Verify these are present before proceeding.
If missing, load knowledge from `research/knowledge/` yourself as a fallback.

Key pitfalls to verify are addressed in your methodology:
- **SELL handling** (`pitfalls/sell_is_exit.md`): SELL is directional but ambiguous — test both BUY-only and directional mapping (MANDATORY dual-test, see below)
- **Consensus dedup** (`pitfalls/consensus_dedup.md`): count unique traders, not trades
- **Resolution** (`data/resolution_mechanics.md`): use asset_id, never string matching
- **Base rates** (`data/tag_base_rates.md`): tag-specific rates vary 9-73% YES — never use global 38/62 blindly
- **Split correction** (`pitfalls/split_position_blind_spot.md`): use `maker_positions_resolved_corrected`, NOT `trader_positions_resolved`
- **Counting unit** (`pitfalls/vectorized_counting_unit.md`): aggregate to market-level, not trader-level

## Step 0.5: Sanity Combo (Early Abort)

Before running the full parameter sweep, test ONE sensible default combo:
- Use median parameter values or prior-informed defaults from the hypothesis framing
- Run on a single recent 3-month window (not full walk-forward)
- Compute HR, excess HR above tag-specific base rate, and universe size

**If HR is BELOW the tag-specific base rate for the signal direction: ABORT EARLY.**

If aborting:
- Write `discovery/results.json` with `"verdict": "no_signal"` and the sanity combo results
- Return to Lead with NO-GO recommendation and the sanity results
- Do NOT proceed to full sweep — this saves compute on dead hypotheses

If sanity combo shows signal (HR above base rate by any margin): proceed to Step 1.

## Step 1: CH SQL Sweep

Connect to remote ClickHouse: `192.168.0.148:18123`, database `polymarket`.

### Classification Verification (before sweep)

Before running any sweep SQL:
1. Check which classification labels exist:
   ```sql
   SELECT DISTINCT label, count(*) AS n FROM trader_classifications FINAL GROUP BY label
   SELECT DISTINCT label, count(*) AS n FROM market_classifications FINAL GROUP BY label
   ```
2. Check which labels your hypothesis needs (from the hypothesis framing)
3. If a needed classification is MISSING:
   - Create it using the "Creating new classifications" pattern below
   - OR propose it in `discovery/notes.md` and use an inline approximation
4. Verify `computed_at` is appropriate for your cutoff:
   ```sql
   SELECT label, max(computed_at) FROM trader_classifications FINAL GROUP BY label
   ```
5. Document classification status in `discovery/notes.md`

### COMPOSABLE QUERY PATTERN (MANDATORY)

**NEVER write monolithic 150+ line CTEs.** Build queries by JOINing against existing
classification tables and views. Each query should be <50 lines.

### TEMPORAL CLASSIFICATIONS (MANDATORY)

Classifications are **functions** `f(cutoff) → rows`. The table is a **cache** — repopulated
before each use. Temporal correctness lives in the function, not the table.

**Before any sweep or backtest**, repopulate classifications with the correct cutoff:
```python
populate_all(cutoff=backtest_date)  # or fold_train_end for walk-forward
```

#### Classification tables (taxonomy layer):

```sql
-- Exclude bots
LEFT JOIN (SELECT trader FROM trader_classifications FINAL
           WHERE label = 'bot') bots
    ON t.maker = bots.trader
WHERE bots.trader IS NULL

-- Only insider-susceptible markets
INNER JOIN (SELECT condition_id FROM market_classifications FINAL
            WHERE label = 'susceptibility' AND tier >= 2) mkt
    ON t.condition_id = mkt.condition_id

-- Score-based filtering
INNER JOIN (SELECT trader, score FROM trader_classifications FINAL
            WHERE label = 'insider_score' AND tier <= 2) insiders
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
   SELECT maker, 'sure_trader', tier, avg_price, 1, now64(3, 'UTC')
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
3. **Use it** immediately via JOIN
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
| `trader_classifications` | ReplacingMergeTree | Trader taxonomy — cache, repopulate before use |
| `market_classifications` | ReplacingMergeTree | Market taxonomy — cache, repopulate before use |
| `maker_positions` | ReplacingMergeTree | Maker-only positions per (trader, condition_id) — no taker mixing |
| `split_corrections` | ReplacingMergeTree | Inferred min_splits for positions with negative net (static backfill) |
| `maker_positions_corrected` | VIEW | maker_positions patched with split_corrections |
| `maker_positions_resolved_corrected` | VIEW | Corrected positions + PnL + resolution — **use instead of trader_positions_resolved** |

### SQL conventions:
- Always use `FROM (SELECT * FROM table FINAL) alias` NOT `FROM table FINAL AS alias`
- Compare hit rates against **tag-specific** base rates (not global 38/62)
- **JOIN classification tables** — never re-derive inline; repopulate with correct cutoff before use
- Keep queries under 50 lines by composing building blocks

### MANDATORY: SELL Dual-Test

Every discovery sweep MUST run TWO variants:
1. **BUY-only**: Filter `side = 'BUY'` — excludes all SELLs from signal generation
2. **Directional**: Map SELL YES → bearish signal, SELL NO → bullish signal (include as directional entries)

Report BOTH results side-by-side in the output and in `discovery/results.json`.
The top-5 parameter combos must be reported for EACH variant.

If only one variant is reported, the discovery is **INCOMPLETE** and will be rejected at review.

If the difference between variants is < 2pp HR: note "SELL handling insensitive" and either is acceptable.
If > 5pp difference: this is a finding — capture in knowledge base.

See `research/knowledge/pitfalls/sell_is_exit.md` for the 4 implementation options.

## Step 2: Parameter Sweep

Vary signal thresholds systematically. For each combo compute:
- Hit rate (by direction: YES and NO separately)
- Excess HR above **tag-specific** base rate
- Average edge per trade (USD)
- Median hold time (days)
- Universe size (trades/month)
- **Compounding score**: `excess_hr x avg_edge_usd / median_hold_days`

Use walk-forward windows when possible:
- Train: 12 months (default from config)
- Test: 1 month (default from config)
- **Re-populate classifications per fold** using `cutoff = fold_train_end`

### Step 2b: Sensitivity Analysis (MANDATORY)

After identifying the top-3 parameter combos from the sweep, test their robustness:

For each top combo:
1. Vary each parameter independently by -10% and +10%
2. Re-run the sweep for each perturbation (6 × N_params additional runs)
3. Record HR change for each perturbation

Flag as **FRAGILE** if ANY single perturbation changes HR by > 5pp:

```markdown
> [!WARNING] FRAGILE: Parameter {X} at {value} — HR drops {N}pp with -10% change.
> Strategy is sensitive to this parameter. Consider widening the acceptable range.
```

Include sensitivity results in `discovery/results.json`. Fragile combos should be
deprioritized in the top-5 ranking (prefer robust combos with slightly lower HR).

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
# Cell 4: Parameter sweep results table (BOTH SELL variants)
# Cell 5: Hit rate vs base rate chart (tag-specific base rates)
# Cell 6: Compounding score heatmap
# Cell 7: Hold time distribution
# Cell 8: Sensitivity analysis results
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
- Parameter sensitivity analysis summary
- SELL variant comparison results
- **Classification proposals** for rules that proved useful

## Step 6: Write Structured Output

Write `discovery/results.json` with machine-readable results:

```json
{
  "hypothesis": "{slug}",
  "timestamp": "ISO-8601",
  "verdict": "promising | marginal | no_signal",
  "universe": {
    "total_markets": 0,
    "trades_per_month": 0,
    "date_range": ["YYYY-MM-DD", "YYYY-MM-DD"],
    "tags": ["tag1", "tag2"]
  },
  "base_rates": {
    "tag": "tag_name",
    "yes_pct": 0.0,
    "no_pct": 0.0
  },
  "buy_only_results": {
    "top_combos": [
      {
        "rank": 1,
        "params": {},
        "hr_pct": 0.0,
        "excess_hr_pp": 0.0,
        "avg_edge_usd": 0.0,
        "median_hold_days": 0.0,
        "compounding_score": 0.0,
        "n_signals": 0,
        "trades_per_month": 0.0,
        "fragile": false,
        "sensitivity": {}
      }
    ]
  },
  "directional_results": {
    "top_combos": []
  },
  "sell_sensitivity_pp": 0.0,
  "spawned_ideas": [],
  "knowledge_captures": [],
  "classifications_used": [],
  "classifications_proposed": []
}
```

This file is read by the Lead orchestrator for gate presentation.

## Output Requirements

All discovery results MUST be labeled as **UPPER BOUNDS**.

Return to Lead:
1. Top 5 parameter combos by compounding_score (BOTH SELL variants)
2. Vectorized HR and PnL (labeled UPPER BOUND)
3. Hold time distribution
4. Universe size (trades/month)
5. Sensitivity analysis summary (fragile flags)
6. SELL variant comparison
7. Spawned ideas (for backlog)
8. Surprising findings (for knowledge capture)
9. Classification rules created/proposed
