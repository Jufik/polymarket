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

## DuckDB Gotchas (Research Sessions)

- **date_diff requires single-quoted date part**: `date_diff('day', ts1, ts2)` — using double-quotes causes `BinderException: Referenced column "day" not found`. In Python f-strings with double-quote outer delimiter, define `DAY = "'day'"` then use `date_diff({DAY}, ...)`.
- **events table primary key is `id` not `event_id`**: join via `m.event_id = e.id`, not `e.event_id`.
- **Temp table names with float lambda**: `str(0.030).replace('.','p')` → `'0p03'` (trailing zero stripped) not `'0p030'`. Always verify table name with `print(lam_tag)` before use.
- **DuckDB in-memory temp tables persist within a Python session** but are lost when the process exits. All temp table creation and querying must be in a single Python process.
- **Run multi-step analysis as a single script** (not multiple `-c` invocations) — temp tables are connection-scoped.

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

### Track 4: Signal-Time Volume — CONFIRMED CAUSAL
- Causal volume (sum net_usd of first N traders only, up to Nth entry time) predicts HR monotonically
- xlarge (>$1k signal-time vol): Esports HR=65-72% (+20-33pp), Tennis HR=60-70% (+20-45pp)
- Recommended filter: signal_time_vol >= $500 before entering
- The +45pp discovery uplift survives causally as +20-45pp at xlarge volumes

### Track 3: Dissent Filter — STRONG EFFECT
- pure YES (dissent=1.0, no qual NO traders): Esports HR=91-100%, Tennis HR=69-80%
- split (<0.70): Esports HR=48-59%, Tennis HR=39-55%
- Hard gate: skip markets with any qualified NO traders (dissent < 0.90)

### load_replay_trades usage for calibration
```python
# Load as DataFrame for calibrate_spreads/calibrate_volumes
ticks_df = load_replay_trades(universe=universe, start_month=xm, end_month=xm, as_ticks=False)
market_spreads = calibrate_spreads(ticks_df)
market_volumes = calibrate_volumes(ticks_df)
# Then convert to ticks for runner
ticks = _df_to_ticks(ticks_df)
```

## trader-scorecard: hit_rate_weighted and conviction (2026-03-07)

### hit_rate_weighted Lambda Sensitivity (CRITICAL)
- Naive unweighted HR and lambda=0.003 (231d half-life) are nearly identical predictors: IC=0.742 vs 0.744
- IC degrades monotonically: lambda=0.007 → 0.739, lambda=0.014 → 0.722, lambda=0.030 → 0.681
- **Best lambda: naive or 0.003. Never use lambda > 0.010.**
- Interpretation: trader skill is a persistent trait, not a hot streak. Long history > recent history.
- Train/test IC = 0.74; top-decile train HR → 92% test HR. Very strong signal.
- Per-tag IC: Crypto=0.869, Elections=0.874 (high); Sports=0.675 (lowest — more noisy)

### Conviction Metric: Two Approaches, Neither as Intended
- **Token conviction** (net_yes / (net_yes+net_no)): degenerate — 99.5% of YES positions have net_no=0 → conviction=1.0 for all. IC=-0.001. Useless.
- **USDC conviction** (abs(net_usd)/volume): captures market-maker vs directional distinction, NOT confidence
  - Bimodal: 74.9% at conviction=1.0 (pure directional), 25.1% with round-trips
  - conviction < 0.1 = market makers: HR=0.267, high volume, many trades (avg 23 trades/position)
  - conviction >= 0.9 = directional: HR=0.492 (base rate)
  - IC (trader avg conviction → HR) = 0.44 — useful as TRADER-TYPE filter, not confidence signal
- **True conviction requires raw `trades` table** (134M rows, has `side` field). Not computed here.

### Conviction as Pool Filter
- USDC conviction >= 0.90 excludes market makers from copy pool (good — they have HR=0.267)
- Adding conviction filter WITHIN top-decile HR: only +0.016pp test HR at cost of -13% pool size. Marginal.
- Adding conviction to HR composite HURTS IC: 0.8HR+0.2CV = 0.716 vs HR-alone = 0.739

### HEDGED Positions — Anomaly
- 1,650,048 HEDGED positions (both net_yes>0 AND net_no>0) with HR=0.588 — suspiciously high
- Likely a classification artifact in `correct` field. Investigate before using.

### PnL IC Near Zero
- IC (train HR → test PnL) = 0.005. HR does NOT predict PnL.
- PnL driven by position size × markets selected, not HR alone. Must incorporate volume as separate signal.
- Decile 6 (~50% HR, large-volume traders) has highest avg PnL — size dominates.

## trader-scorecard: striking_score and stability_bonus (2026-03-07)

### yes_entry_data Schema
- Columns: `trader, condition_id, price_x_vol, volume, first_trade`
- Entry price proxy: `price_x_vol / volume` = volume-weighted avg YES entry price (range 0-1, median 0.246)
- JOIN on `(trader, condition_id)` to get per-position entry price

### maker_positions net_usd Scaling
- `net_usd` and `volume` are in USDC (already scaled). `net_yes`/`net_no` are in token units (1e3 scale relative to net_usd — e.g., net_usd=-0.09, net_yes=90 means $0.09 USDC for 90 tokens at $0.001/token). Use `yes_entry_data` for entry prices, not net_usd/net_yes ratio.

### striking_score Key Results
- Proxy V1: `edge / cross_sectional_std`, where edge = `abs(yes_won - entry_price)`, vol = stddev of entry prices across traders in same market
- Q1 traders: HR=23%, Q4: HR=45% — strong 22pp monotonic gradient
- High-striking traders enter at 0.28-0.57 price range (NOT extremes). Low-striking cluster near 0 or 1.
- 26% of markets have only 1 YES trader → vol proxy undefined (defaulted 0.10). Primary proxy limitation.
- striking_v2 (edge × log(1+hold_days)): weaker gradient, don't use.

### stability_bonus Key Results
- Eligible pool: 11,588 traders with ≥6 months AND ≥3 markets/month
- Median stability: 2.36. 61% have stability > 2.0. 10.6% have stability > 5.0.
- OOS correlation: raw HR → future HR: r=0.782. Stability → future HR: r=0.434.
- Combined score barely beats raw HR: stability is a **risk filter**, not primary ranker.
- High-HR stable (stab>2.0) vs High-HR streaky: +8.6pp higher future HR, half the monthly variance.
- Best tags for stability: CS2 (19.7), Bundesliga (18.8), Ligue 1 (17.9), SEA esports (15.3) — weekly cadence.

### Surprising Data Characteristic (flag for knowledge)
- 39% of all YES positions are priced below 10 cents, with 1.6% hit rate — deep underdogs dominate volume but have negligible edge. Any copy strategy must exclude or heavily discount these.

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

### In-Play Signal Problem (CRITICAL)
- Most sports signals (Soccer, NBA, NHL, NFL, CS2, Dota2, Valorant) have hold < 6h
- These experts enter DURING live matches, not before. HR=99%+ but UNCOPYABLE in real-time
- Hold-duration HR breakdown (K=50, N=3): 0h=99.8%, 1-4h=97.0%, 4-24h=76.7%, 1-3d=80.6%
- Must filter: `date_diff('hour', max(first_trade), first(resolved_at)) >= 24` for genuine signals
- In-play effect is the dominant vectorized→tick gap for sports consensus strategies

### DuckDB: CTE scoping bug in scorecard
- `COALESCE(s.stability, ...)` inside final SELECT fails if `s` is a CTE not in the FROM clause
- Fix: break into multiple separate `CREATE TABLE` steps (overall, monthly, stability, then JOIN)

### Canonical Tag Assignment (DuckDB)
- Use priority CASE WHEN for assignment, NOT "most specific" (fewest markets) — that picks niche tags
- Pattern: `CASE WHEN et.label = 'Elections' THEN 0 WHEN et.label = 'Crypto' THEN 1 ... ELSE 999 END`
- Gambling exclusion: markets.slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'

### Composite Scorecard Ranking
- Use `excess_hr * ln(n_positions + 1)` not raw hit_rate — avoids small-sample 100% HR traders
- Require min_positions >= 20 to eliminate traders with 5/5=100% but tiny sample
- conviction_ratio = avg(abs(net_usd)/volume) >= 0.90 to exclude market makers

### Actionable Signals (test period Dec 2025 – Feb 2026, K=50, N=3, >=4h hold)
- **Politics NO**: 335 sigs/mo, HR=92%, excess=+19.9pp, hold=1.5d — best volume+quality
- **Elections NO**: 88 sigs/mo, HR=79.2%, excess=+10.3pp, hold=2.2d — stable
- **Tech NO**: 58 sigs/mo, HR=88.3%, excess=+13.3pp, hold=5.0d — longest hold
- Soccer/Sports/NBA/NHL: HR=97-100% but hold<6h → in-play, not copyable
- Stability gate (>=5.0): only +1.3pp HR vs no gate, -25% pool — marginal

### Outcome: Recommend Tick-by-Tick Validation
- Priority 1: Politics NO (K=20-50, N=3-5, hold>=24h)
- Priority 2: Elections NO (K=50-100, N=3-5)
- Priority 3: Tech NO (K=50-100, N=2-3)
- Expected post-validation: 20-40pp HR drop → Politics NO real HR likely 52-72%

## In-Play Tracks B and C Summary (2026-03-07)

See `agent-memory/researcher/in-play-tracks-bc.md` for full details.

**Track B** (In-play consensus, N>=3 traders in final 2h): REJECTED as general signal. OOS collapses. Sub-study Track B.2: restrict to high-volume tags (EPL, Crypto, Bitcoin, Earnings) with signal_time_vol >= $500.
- DuckDB: markets.status = 'closed' (not 'resolved')

**Track C** (Scalper alpha — BUY then SELL within 24h):
- 230K scalpers, 2.48M scalp events/year, median scalp time 43 min
- YES BUY entry HR: at base rate (39%) unfiltered; +5.9pp price-gated; **+19.6pp UB** (high-edge pool, same-period)
- Caveats: pool selection bias (same period), no edge persistence on held positions, in-play gate >4h required
- Verdict: CONDITIONAL GO. Tick validation required. CS 0.30-1.50.
