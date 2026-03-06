# Architect Agent Memory

## Owned Files

Harness files I may modify:
- `src/polymarket_pipeline/cli/harness.py` — production replay harness (pm-harness CLI)
- `src/polymarket_pipeline/cli/strategy.py` — strategy/provider registry (try/except around imports)
- `research/hypotheses/*/reviews/validation_audit.md` — per-hypothesis audit logs

Strategy/provider code in `strategies_impl/` is NOT mine to modify unless fixing a harness-interface bug.

## CH SQL Gotchas (v24.8)

- `FROM table FINAL AS alias` is INVALID — use `FROM (SELECT * FROM table FINAL) alias`
- Subquery alias scoping: qualified column refs like `fe.condition_id` fail in outer scope — use Memory temp tables or WITH clauses, and join on unqualified column names
- `published_at` in `trades_raw` is `Float64` (epoch seconds), not DateTime — use `toDate(timestamp)` for date ops
- `timestamp` column in `trades_raw` is `DateTime64(3)` — correct column for date filtering
- Memory table column names inherit source aliases (e.g. `t.condition_id` stored literally as column name if not aliased in CREATE AS SELECT)

## Harness Bugs Fixed (2026-03-06) — cli/harness.py

All 7 bugs were introduced in the initial harness implementation and fixed during tag-hr-copy validation:

1. **Resolution query**: `resolved_epoch` column doesn't exist in `markets_resolved` — use `toUnixTimestamp(resolved_at) AS resolved_epoch`
2. **published_at=0 for backfill trades**: use `if(published_at > 0, published_at, toUnixTimestamp(timestamp)) AS published_at` in trade SELECT
3. **Provider bootstrap ordering**: providers must be bootstrapped (`compute()`) BEFORE trade loading, so `pre_filter_makers` can use the qualified pool
4. **Gateway cumulative budget**: replay capital recycles via position settlement but `ExecutionGateway` tracks cumulative spend — pass `strategy_budgets=None` for replay mode
5. **SELECT * includes _version**: use explicit column list with `_version AS version`
6. **Stale registry imports**: wrap all `_register_strategies()` / `_register_providers()` imports in try/except ImportError
7. **token_won IS NOT NULL**: resolutions query must filter `WHERE token_won IS NOT NULL AND resolved_at IS NOT NULL`

Current harness.py already has all fixes applied.

## ReplayRunner Settlement Requirements

- Settlement fires when `trade.published_at >= resolution.resolved_at` (clock advances per tick)
- `_resolutions` dict must be populated via `load_resolutions_from_rows()` from `markets_resolved`
- `_settle_market()` only fires if position exists in ctx AND resolution in `_resolutions`
- Settlement rate of 99%+ is achievable; <50% indicates a data or timing bug
- `n_settled` counter is the authoritative settlement count (summary.json `settled` field)

## Provider Training Window Pattern

- Provider `compute()` must accept `train_end_date` param for proper walk-forward OOS
- Using `datetime.now()` as train_end is look-ahead contamination for historical replays
- For a replay period START:END, the correct training window is [START - lookback_months, START)
- Current TagHRProvider uses wall-clock now() — correct fix is harness passes replay_start as train_end

## Degradation Monitoring

- Expected: 20-40pp HR degradation from vectorized to tick-by-tick
- tag-hr-copy observed: 22-28pp across Esports/1H/Tennis — in expected band
- No harness fidelity issue when degradation is in band
- Summary.json from intermediate broken run ≠ final results; always check validation_results.json

## Signal Copyability Analysis (1H tag bots, 2026-03-06)

- 76% of 1H qualified traders are bots (>30 TPD); 45% exceed 100 TPD
- "1H" is a crypto tag label, NOT a 1-hour market duration (median market life = 12 hours)
- Bots enter at median 681 minutes after market open (not latency-arb — information accumulation)
- Copyability window: median 70 minutes after bot entry to market close
- Bot signal does NOT survive tick-by-tick: 1H HR=51.1% vs base=50.7% (+0.4pp excess = noise)
- Root cause: bots enter at 57% of market lifetime; price already partially reflects signal by copy time

## tag-hr-copy Validation Results (2026-03-06)

Final results from `research/hypotheses/tag-hr-copy/validation/validation_results.json`:

| Tag | n | HR | Excess | Degradation | Verdict |
|-----|---|----|--------|-------------|---------|
| Esports | 363 | 44.9% | +13.4pp | 22.3pp | marginal |
| 1H | 908 | 51.1% | +0.4pp | 26.9pp | DEAD |
| Tennis | 266 | 44.7% | +5.94pp | 27.7pp | marginal |

Open caveat: provider training window (wall-clock) includes Sep–Dec 2025 signals (42% of total) as partially in-sample. Walk-forward recomputation per fold needed for clean OOS.
