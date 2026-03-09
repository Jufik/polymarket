# Researcher Agent Memory

## User Preferences

- **Write/Edit permissions**: Always granted — never ask for confirmation before writing or editing files.

## Shell / Environment

- **Use `./tmp/` not `/tmp/`** for scratch files. On this machine (Linux), `/mnt/nvme/git/polymarket/polymarket/tmp/` is the correct absolute path (NOT `/Users/kiefferjulien/...`).
- Background tasks: `/tmp/claude-1000/-mnt-nvme-git-polymarket-polymarket/tasks/<id>.output`
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

## DuckDB Gotchas (Research Sessions)

- **date_diff requires single-quoted date part**: `date_diff('day', ts1, ts2)` — using double-quotes causes `BinderException: Referenced column "day" not found`. In Python f-strings with double-quote outer delimiter, define `DAY = "'day'"` then use `date_diff({DAY}, ...)`.
- **events table primary key is `id` not `event_id`**: join via `m.event_id = e.id`, not `e.event_id`.
- **Temp table names with float lambda**: `str(0.030).replace('.','p')` → `'0p03'` (trailing zero stripped) not `'0p030'`. Always verify table name with `print(lam_tag)` before use.
- **DuckDB in-memory temp tables persist within a Python session** but are lost when the process exits. All temp table creation and querying must be in a single Python process.
- **Run multi-step analysis as a single script** (not multiple `-c` invocations) — temp tables are connection-scoped.

## Hit-Rate Copy Hypothesis Key Results (tag-hr-copy — REJECTED)

Vectorized: Esports/Tennis/1H HR 67-78% UB. Tick: 40-50% (marginal). Root cause: consensus gap 22-32pp.
Spawned: tag-hr-consensus [HIGH].

## Data Patterns

- `realized_pnl` in `trader_positions_resolved` is in raw USDC (not scaled). Median ~$2-10, avg $50-400+ due to skew.
- `hold_days = dateDiff('day', first_trade, resolved_at)` — can be 0 for same-day resolution.
- Bot guard: `count(*) < 10000` per (trader, tag) in training period filters bot accounts.
- `any(t.label)` for primary_tag assignment is non-deterministic — use priority-ordered tag lists or `argMin(t.label, t.id)` for stability.
- Always use `median_pnl` (not avg) in compounding score — avg_pnl is 2-100x skewed by whale positions.

## BEH Gate Domain Specificity (2026-03-09)

- BEH gate (`bucket_excess_hr >= 0.02`) works for Sports/Politics (many deep-uncertainty markets)
- For **Crypto YES max_price≤0.65**: BEH gate over-filters — leaves only 37 traders (vs 50 v2), kills signal at N=2 (vectorized: -9.8pp excess, 19 signals vs v2's +37.4pp tick)
- Root cause: Crypto signals at ≤0.65 are rare events per trader; not enough training data to pass BEH gate
- **Lesson**: Do NOT apply BEH gate to Crypto YES leg. Use v2 HR-only pool for Crypto YES.

## Crypto YES max_price=0.65 Vectorized vs Tick Gap (2026-03-09)

- Vectorized severely undercounts signals for Crypto YES (v2 N=2: 11 vectorized vs 122 tick fills)
- Root cause: `yes_entry_data` INNER JOIN excludes split-route traders; `first_trade >= test_start` date filter cuts more than expected
- **Lesson**: Vectorized result for Crypto YES with price gate is NOT reliable — always tick-validate
- Reference: `research/hypotheses/scorecard-v2-strategies/validation/crypto_maxprice065_results.md`

## tag-hr-consensus Key Findings (2026-03-06)

### Pool Qualification Pattern (DuckDB)
- Use INNER JOIN on yes_entry_data (not LEFT JOIN + coalesce) — excludes split-route traders
- Use TRAINING-window base rate for pool qualification, test-window base rate for reporting excess HR
- Two-step: `_val_ep_tmp` pre-compute avg_ep, then INNER JOIN in HAVING clause

### Price Ceiling Effect
- price_ceil=0.75: strategy fires on YES markets priced 0.40-0.75 (genuine uncertainty zone)
- price_ceil=0.40: fires only on YES < 0.40 (long-shot markets) — INVERTS the signal for Esports
- Break-even formula: HR > fill_price (e.g., fill=0.26 → need HR > 26%)

### Sharp Pool (top-K) — STRONG VECTORIZED SIGNAL
- Top-K traders by excess_hr dramatically outperforms threshold-based pool
- K=30 Esports: 20-34 signals/fold, HR=74-100% (vs pool explosion with threshold)
- K=30 Tennis: 1-24 signals/fold, HR=91-100% (too thin for most folds)
- These are vectorized UBs — expect 20-40pp tick degradation

### Key filters: signal_time_vol >= $500 (causal vol, xlarge bucket: +20-45pp HR). Dissent=1.0 (no qualified NO traders): Esports HR=91-100%, Tennis HR=69-80%.

### load_replay_trades for calibration: pass `as_ticks=False` → DataFrame → `calibrate_spreads(df)`, `calibrate_volumes(df)` → `_df_to_ticks(df)` for runner.

## trader-scorecard: hit_rate_weighted and conviction (2026-03-07)

- Best lambda: naive (0) or 0.003. Never > 0.010. Skill is persistent trait, not hot streak.
- Train/test IC = 0.74; top-decile train HR → 92% test HR. Very strong predictor.
- Token conviction (net_yes/(net_yes+net_no)): degenerate (99.5% = 1.0). Useless.
- USDC conviction (abs(net_usd)/volume): trader-type filter (MM vs directional), not confidence.
  - conviction < 0.10 = market makers: HR=0.267. conviction >= 0.90 = directional: HR=0.492.
  - USDC conviction >= 0.90 to exclude MMs from pool. Adding to HR composite HURTS IC.
- PnL IC near zero: HR does NOT predict PnL. Size drives PnL more than HR.

## trader-scorecard: striking_score and stability_bonus (2026-03-07)

- yes_entry_data: Columns `trader, condition_id, price_x_vol, volume, first_trade`. Entry price = `price_x_vol/volume`.
- net_usd is USDC-scaled; net_yes/net_no are token units. Use yes_entry_data for entry prices.
- striking_score (edge/cross_sectional_std): Q1 HR=23%, Q4 HR=45%. High-striking enter at 0.28-0.57 range.
- stability_bonus: useful as risk filter, not ranker. OOS r=0.434 vs raw HR r=0.782.
- **39% of YES positions priced below 10 cents with 1.6% HR** — deep underdogs are pure noise, exclude always.

## Tag Scan Jan 2026 Results (2026-03-07)

See `agent-memory/researcher/scorecard-jan2026-scan.md` for full details.

**Key findings**:
- Finance: NEW viable tag, +64.8pp tick excess HR but negative PnL → needs max_price=0.75 gate
- Weather: IN-PLAY CONTAMINATED (city temperature watchers) — do not deploy
- Esports: 1,973 training markets now (was 42). Pool buildable. Re-scan April 2026 (off-season in Jan).
- High HR + negative PnL pattern: consensus fires after price already moved to 0.85+. Fix = max_price gate.

## DuckDB Macro Naming
- Always use unique macro names across scripts to avoid collision in same session.
- Pattern: `CREATE OR REPLACE MACRO is_gambling_market_jan(slug)` (suffix distinguishes from earlier scripts).

## In-Play Track A Results (2026-03-07)
See `agent-memory/researcher/in-play-track-a.md` for full details.
- 1,546 elite traders (>=50 in-play pos, >=80% HR, >=$5 med vol); 79% trade non-gambling mkts
- Non-gambling HR: Sports 97.2%, Soccer 97.9%, Basketball 97.6%, NCAA 98.2%, Weather 98.4%
- Persistence: 95% of train-active traders maintain >=70% HR out-of-sample
- CRITICAL: Elite traders lead the market by 58 min median. Only 6.3% of positions are "after pool" — must monitor wallets in real-time, not follow consensus.
- Top copy traders: 0x2c45f2be0c (99.98% HR, 4.4% gambling), 0x336151559e (99.95%, 11.8% gambling)

## strategy1_tag_consensus Key Findings (2026-03-07)

- In-play sports (Soccer, NBA, CS2, etc.): HR=99%+ but hold<6h — UNCOPYABLE. Filter: hold>=24h.
- DuckDB CTE scope bug: `COALESCE(s.stability,...)` fails if `s` not in FROM. Use separate CREATE TABLE.
- Canonical tag: priority CASE WHEN (not fewest-markets). Gambling: slug NOT LIKE '%updown%'.
- Composite rank: `excess_hr * ln(n_markets+1)`, min 20 positions, conviction >= 0.90 (no MMs).
- Actionable (K=50, N=3, >=4h hold): Politics NO +19.9pp, Elections NO +10.3pp, Tech NO +13.3pp.

## Elite Whale Copy Validation (2026-03-08)
See `agent-memory/researcher/elite-whale-copy.md` for full details.
- Top-100 pool (no price gate): 94.2% HR, $52,932 PnL in January 2026, Sharpe=0.72
- CRITICAL: max_price gate DESTROYS signal (68% of fills are at 0.90+ where HR=99.4%)
- Ledger `outcome` column = winning label, NOT "won"/"lost" — use `pnl_net > 0` for wins
- 3pp vectorized→tick degradation (N=1 fires immediately, no consensus wait)
- Verdict: EXPLOITABLE — deploy top-100 pool with NO price gate

## In-Play Tracks B, C and Longshot Elite Summary (2026-03-07/08)

See `agent-memory/researcher/in-play-tracks-bc.md` for Tracks B/C details.

**Track B** (In-play consensus, N>=3 traders in final 2h): REJECTED. OOS collapses.
**Track C** (Scalpers, BUY then SELL within 24h): CONDITIONAL GO. CS 0.30-1.50.

### Longshot Elite (2026-03-08) — REJECTED

3,613 specialists, HR persistent OOS. BUT tick PnL negative for ALL configs (K=25-200, N=1/2).
Root cause: avg fill=0.207, break-even=20.7%, actual tick HR=16.0%. Even narrowband 0.20-0.30 fails (HR=21.4%, BE=25.5%). Hypothesis closed.

**CRITICAL PITFALL DISCOVERED**: Fill price determines break-even, not price ceiling. Always verify `avg(fill_price)` from ledger before declaring above break-even.

## BTC Up/Down Scalp Convergence Key Findings (2026-03-09)

- GBM-PM deviation is structural: median 5.3% across ALL seconds (100% of windows have max >5%)
- Convergence is FAST: 5% threshold → p50=12s, 10% → p50=22s, 15% → p50=42s
- Theoretical net PnL (3% fee/side): 5%→+2.8%, 10%→+8.7%, 15%→+13.1% (UPPER BOUNDS)
- Hit rate ~42-48% (BELOW 50%) — profit from mean-reversion, not GBM directional correctness
- Regime-robust: consistent across all hours, vol quartiles, BTC direction
- CRITICAL: 12s median convergence with ~1.5s execution latency = BORDERLINE feasibility
- 10% threshold with 22s median hold is the best tradeoff
- Data: 12,788 windows resolved in 90 days, 4M per-second PM price snapshots
- Verdict: MARGINAL. Requires websocket infrastructure, $100-300 position size.

## Large Polars Iteration Pitfall (2026-03-09)

- NEVER iterate over Polars groups with `group_by(...).agg()` on 125M+ rows using Polars filter inside loop
- Per-row `.filter(pl.col("timestamp") == pl.col("timestamp").min())` inside agg FAILS silently (OOM/process kill)
- Use DuckDB ASOF JOIN for time-series price lookups at scale (fast, correct, no Python loop)
- For per-second aggregation of 125M trades → reduce to ~4M rows using DuckDB group_by first
- 4M per-second rows with per-window Python loop (~13k windows × 300 rows avg) = 5s — fast enough

## SimulatedExecutor Fill Price Artifact (2026-03-09)

See `agent-memory/researcher/sim-pitfalls.md` for full details.
- SimulatedExecutor fills at `intent.max_price` regardless of actual market price
- When max_price=0.80 is used as filter: break-even HR = 80%, tick PnL structurally negative
- Symptom: all fill prices identical in ledger (= max_price). PnL/Sharpe invalid.
- Fix: use HR/excess HR only; run `realistic_pnl.py` with yes_entry_data for true PnL estimate.

## NO Direction PnL Sign Bug (2026-03-09)

When storing entry/exit prices as YES-equivalent probabilities for BOTH directions:
- **YES position PnL** = `exit_price - entry_price` (YES rose = profit)
- **NO position PnL** = `entry_price - exit_price` (YES fell = NO rose = profit)
- **Fees for NO** must use NO token prices: `FEE_PCT * (1-entry_price) + FEE_PCT * (1-exit_price)`
- Bug symptom: NO direction shows ~10% profitable (should be ~70%). Flips total verdict from NO-GO to GO.
- Reference: `research/hypotheses/crypto-scalp-convergence/validation/notes.md`

## Politics YES v3 (BEH-gated, combined consensus) — GO (2026-03-09)

- Pool: K=100 BEH-gated, combined YES+NO consensus, direction_filter=YES, max_price=0.80.
- Base rate: 18.8%. N=3: 351 fills, +43.5pp. N=4: 176 fills, +43.1pp. N=5: 113 fills, +46.7pp.
- 8/8 months positive excess for all N. Exceeds v2 baseline (+41pp). Tick PnL = artifact.
- Median trigger price 0.54-0.62 → realistic PnL ≈ +$133-178/fill.
- Verdict: GO. Recommended N=3 (volume) or N=5 (quality).
- Reference: `research/hypotheses/scorecard-v3-strategies/validation/politics_yes_v3_results.md`
