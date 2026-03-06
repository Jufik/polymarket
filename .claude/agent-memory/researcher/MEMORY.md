# Researcher Agent Memory

## User Preferences

- **Write/Edit permissions**: Always granted — never ask for confirmation before writing or editing files.

## Shell / Environment

- **Use `./tmp/` not `/tmp/`** for all scratch files (absolute: `/Users/kiefferjulien/git/polymarket/tmp/`). `/tmp/` is not reliably accessible from the Bash tool in this environment.
- Background task outputs go to `/private/tmp/claude-501/-Users-kiefferjulien-git-polymarket/tasks/<id>.output` — poll via `uv run python3 -c "open('./tmp/out.txt','w').write(open('<path>').read())"` then Read the file.
- `uv run python3 -c "..."` works for inline scripts. Multi-line scripts must be written to `./tmp/script.py` first, then run with `uv run python3 ./tmp/script.py`.
- Always write progress to a log file in `./tmp/` when running background sweeps — stdout capture from background tasks is unreliable.

## ClickHouse SQL Gotchas (v24.8)

- **CTE alias in JOIN ON clause fails**: `FROM cte1 AS a INNER JOIN cte2 AS b ON a.col = b.col` then `SELECT a.col` FAILS with UNKNOWN_IDENTIFIER. Must reference columns without table alias prefix in SELECT.
- **Workaround**: Join CTEs at the top-level FROM with INNER JOIN (not subquery JOIN), columns resolve without alias.
- **Double-subquery JOIN also fails**: even `SELECT j.col FROM (subq) AS j INNER JOIN (subq2) AS q ON j.x = q.x` — `j.col` is UNKNOWN_IDENTIFIER.
- **Working pattern**: CTEs joined via `FROM (SELECT * FROM table) AS p INNER JOIN named_cte AS ts ON p.col = ts.col` where column names are unambiguous.
- **has() in JOIN ON across two tables requires**: `SET allow_experimental_join_condition = 1` — avoid.
- **Materialized temp tables** (Memory engine) are the fastest workaround for complex multi-CTE sweeps.

## Critical Sweep Pitfall: first_trade Filter in Test Window

**ALWAYS add `AND toDate(t.first_trade) >= '{test_start}'` in the market-level aggregation table.**

Without it, a trader who entered a market during training counts as a test signal when the market resolves in the test window. That trade is NOT copyable — it happened before the test period. This inflates signal counts by ~32% and HR by 12+ pp in some tags.

Confirmed magnitude: 31.9% of test-window positions had `first_trade` before test_start (2025-07 fold, all tags, YES positions: 69,477 / 217,895).

Pattern:
```sql
CREATE OR REPLACE TABLE _tmp_thr_mkt_buy ENGINE = Memory AS
SELECT t.condition_id, any(t.yes_won) AS yes_won,
       dateDiff('hour', max(t.first_trade), any(t.resolved_at)) AS hold_hours,
       ...
FROM maker_positions_resolved_corrected t
JOIN _tmp_thr_qual_buy q ON t.trader = q.trader
WHERE t.condition_id IN (SELECT condition_id FROM _tmp_thr_tag_mkts)
  AND t.position = 'YES'
  AND toDate(t.resolved_at) >= '{test_start}' AND toDate(t.resolved_at) < '{test_end}'
  AND toDate(t.first_trade) >= '{test_start}'   -- CRITICAL: only copyable test-period entries
GROUP BY t.condition_id
HAVING hold_hours >= 0 AND hold_hours <= 48
```

## event_tags Schema

Table has only `(event_id INT32, tag_id INT32)` — no `created_at`. Tag assignments are static (append-only). No point-in-time tagging risk. The tag universe is correctly time-stable.

## Hit-Rate Copy Hypothesis Key Results (tag-hr-copy — REJECTED)

**Vectorized R3 (UB)**:
- Esports BUY (mt=50, ep=15, pc=0.75): HR=67.2%, excess=+35.7pp, CS=34.87
- 1H BUY (mt=50, ep=15, pc=0.75): HR=78.0%, excess=+27.3pp, CS=19.71
- Tennis BUY (mt=20, ep=15, pc=0.80): HR=72.4%, excess=+33.6pp, CS=9.67
- Tennis DIR (mt=20, ep=10, pc=0.75): HR=72.4%, excess=+30.5pp, CS=6.84

**Tick-by-tick (2026-03)**:
- Esports: HR=45.8% (+10.9pp excess), PnL=-$102.50, verdict: marginal
- 1H: HR=49.8% (+2.5pp excess), PnL=-$102.50, verdict: DEAD (gambling)
- Tennis: HR=40.6% (+10.5pp excess), PnL=-$102.50, verdict: marginal

**Root cause**: Vectorized measured N-trader consensus; tick fired on individual trades.
Consensus gap = primary degradation mechanism (22-32pp). See pitfalls/individual_vs_consensus_signal.md.

**Spawned**: tag-hr-consensus [HIGH] — N-trader convergence trigger (expected 300-800 sigs/yr)

## Data Patterns

- `realized_pnl` in `trader_positions_resolved` is in raw USDC (not scaled). Median ~$2-10, avg $50-400+ due to skew.
- `hold_days = dateDiff('day', first_trade, resolved_at)` — can be 0 for same-day resolution.
- Bot guard: `count(*) < 10000` per (trader, tag) in training period filters bot accounts.
- `any(t.label)` for primary_tag assignment is non-deterministic — use priority-ordered tag lists or `argMin(t.label, t.id)` for stability.
- Always use `median_pnl` (not avg) in compounding score — avg_pnl is 2-100x skewed by whale positions.

## Reusable Temp Tables Pattern

```sql
-- Step 1: market primary tag (fast lookup)
CREATE OR REPLACE TABLE _tmp_hrc_market_tags ENGINE = Memory AS ...

-- Step 2: resolved positions with tag (single join, cached)
CREATE OR REPLACE TABLE _tmp_hrc_positions ENGINE = Memory AS
SELECT lower(p.trader), p.condition_id, mt.primary_tag AS tag, p.position, p.correct,
       p.realized_pnl, toDate(p.resolved_at) AS resolved_date,
       dateDiff('day', p.first_trade, p.resolved_at) AS hold_days
FROM (SELECT * FROM trader_positions_resolved) AS p
INNER JOIN _tmp_hrc_market_tags AS mt ON p.condition_id = mt.condition_id
WHERE ...

-- Step 3: add MVF
CREATE OR REPLACE TABLE _tmp_hrc_positions_mvf ENGINE = Memory AS
SELECT p.*, round(tv.maker_vol / greatest(tv.maker_vol + tv.taker_vol, 1), 3) AS mvf
FROM _tmp_hrc_positions AS p
LEFT JOIN (SELECT * FROM trader_volumes FINAL) AS tv ON p.trader = tv.trader
```
