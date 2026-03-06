# Quant Research Strategist Memory

## Core Principles

### Knowledge Base (ALWAYS CHECK FIRST)
- **Before ANY research**: read `research/knowledge/README.md` and load all entries
- Knowledge entries use admonitions: `> [!CRITICAL]`, `> [!WARNING]`, `> [!TIP]`
- CRITICAL = violating this invalidates results. WARNING = results biased.
- **After research**: capture new findings as knowledge entries
- **SQL snippets**: reuse queries from `research/knowledge/queries/`
- **Idea backlog**: `research/ideas.md` — check before starting, update after finishing

### ClickHouse-First (ALWAYS)
- **ALL heavy computation in ClickHouse SQL** — never pull millions of rows locally
- Remote CH: `192.168.0.148:18123`, database `polymarket`
- Local Docker: `localhost:18123` (may be empty or partial)
- Creating `_tmp_*` tables encouraged — name them `_tmp_{strategy}_{purpose}`
- `FROM table FINAL AS alias` INVALID in CH 24.8 — use subquery

### Research Workflow
- LOAD KNOWLEDGE → FRAME → DISCOVER (vectorized) → MANUAL GATE → VALIDATE (tick-by-tick) → CAPTURE
- Compounding score: `excess_hr × avg_edge_usd / median_hold_days` (higher = faster recycling)
- Vectorized = UPPER BOUND (20-40pp optimistic). Always discount.

## Data Facts (verified)
- trades_raw: 448M rows, 559K markets, Nov 2022 -- Mar 2026, $23B+ volume
- trader_positions_resolved: 35M rows, 1.6M+ traders, 446K resolved markets
- Base rate: 36.3% YES-won, 63.7% NO-won (updated 2026-03-02; use `token_won`, never strings)
- **Monthly base rates vary wildly**: Jul 2025 = 20.4% YES; always compute period-specific
- Susceptible base rate (tag-based): 44.3% overall, 33.2% YES, 57.9% NO
- Address case: always use `lower(trader)` (5.6% duplicates otherwise)
- Gambling filter: use tags ('Up or Down', 'Crypto Prices', '5M', '15M', 'Hit Price', 'Multi Strikes', '4H', '1H')
- Most markets have ZERO trading fees
- MVF finding: pure takers (MVF<0.1) = 25.8% HR worst; makers-who-take (MVF 0.5-0.9) ~45% HR best
- **Susceptibility classification**: use tag chain (markets -> events -> event_tags -> tags), NOT market_categories
- Tag distribution: Sports 267K, Games 234K, Crypto 187K, Crypto Prices 184K, Up or Down 151K, Politics 39K
- trader_positions_resolved columns: use `market_volume` not `volume`; `p.volume` does NOT exist

## Simulation Engine Gaps (for sim-fidelity agent)
See `research/knowledge/` for full details. Key structural weaknesses:
1. No orderbook dynamics (only best bid/ask, no depth beyond L1)
2. No signal aggregation window (each trade triggers immediate decision)
3. Linear market impact (no convexity, no feedback to next trade)
4. No partial fills (all-or-nothing; FillStatus.PARTIAL exists but never returned)
5. Spread calibration from trade prices, not orderbook
6. No latency distribution model (uniform delay_s only)
7. Sharpe annualization assumes constant trade frequency
8. No mark-to-market / unrealized PnL tracking
9. ledger._buffer access violates encapsulation (getattr hack in replay.py)
10. Batch resolution enrichment is O(markets × records), not O(records)

## S2 Insider Copy -- TICK-BY-TICK VALIDATED (2026-03-02)
- Vectorized: 83.2% HR (strict tier), 85.9% HR (with price < 0.65) -- UPPER BOUNDS
- **Tick-by-tick validated**: ALL 8 configs profitable across 3 OOS months (Jul-25, Oct-25, Jan-26)
- Best HR config: C>=2, no filter: 66.8% HR, $254K PnL, compounding 7.01
- Best PnL config: C>=3, p<0.65: 57.3% HR, $784K PnL, compounding 12.37
- Vectorized-to-tick gap: 18-29pp (within expected 20-40pp range)
- **CRITICAL FINDING**: entry price filter INVERTS in tick-by-tick (-7pp HR vs +2.7pp vectorized)
  - Reason: tick-by-tick enters at specific trade price, not blended average
  - Filter creates HR-vs-PnL tradeoff (lower HR but 6x higher PnL)
- Capital constraint is primary bottleneck: ~130 fills/month from 28K-60K signals
- NO direction dominates (95%+ of fills); avg hold 25d; pool ~25K traders
- Validation script: `research/scripts/s2_tick_validation.py`
- Knowledge: `signals/insider_copy.md`, `pitfalls/entry_price_filter_inversion.md`
- **Status**: READY for production implementation in strategies_impl/

## ClickHouse SQL Gotchas
- `FROM table FINAL AS alias` INVALID in CH 24.8 -- use subquery
- CTE aliases (`sm.category`) lost when re-joined in outer query -- join raw table again
- Use tag chain for susceptibility, not `market_categories` (see `research/knowledge/queries/tag_susceptibility.sql`)
- CTE column names after CROSS JOIN: prefix `o.column` fails in CH 24.8. Use short aliases in CTE (e.g., `cid`, `dir`, `rpnl`) and reference without prefix in outer query.
- `markets.category` is 99.3% NULL -- never use for filtering; use tag join chain instead
- `markets_resolved` view exposes `token_won` not `yes_won` -- use `mr.token_won` for base rate computation
- CTE with multi-table JOIN: outer SELECT can't resolve unqualified column names from CTE. Fix: alias all columns in the CTE (e.g., `p.trader AS t_trader`) and reference aliases in outer query.

## S2 Hit-Rate Copy -- REJECTED (tick-by-tick validated 2026-03-02)
- **Vectorized (UB)**: 82.9-85.5% HR, $145-241/pos, comp scores 6.75-11.46
- **Tick-by-tick**: 45.9-50.6% HR, negative PnL (2 of 3 periods), comp ~0
- **Gap**: 33-39pp (within expected 20-40pp range but at upper end)
- **Root causes**: (1) direction-agnostic consensus -7pp, (2) NO HR collapse structural -8 to -15pp below base, (3) UNKNOWN outcomes -50-180 fills, (4) entry price shift -2pp, (5) on_timer() never called
- **Direction diagnosis**: same-dir consensus 50-54% HR (best); YES excess +7-18pp; NO excess -1 to -15pp
- **Verdict**: FAIL. NO direction is anti-predictive in tick-by-tick. YES has genuine excess but low absolute HR.
- **Knowledge**: `pitfalls/no_hr_collapse_tick.md`
- Validation script: `research/scripts/s2_hitrate_tick_validation.py`
- Notebook: `research/notebooks/s2_tick_validation.py`

## Tick-by-Tick Validation Approach (proven pattern)
- **DO NOT** try to construct millions of NormalizedTrade Pydantic objects (too slow/memory)
- **DO** query ClickHouse for only the trades you need (insider BUY trades = ~50K-1M vs 6M-84M total)
- **DO** use lightweight dataclasses (frozen, slots) instead of Pydantic for trade records
- **DO** implement strategy logic directly in Python loop (mirrors on_trade exactly)
- **DO** use pre-sorted resolution timeline for O(1) per-tick settlement scanning
- **DO** track entered_markets set to prevent re-entry after settlement
- `DROP TABLE IF EXISTS ... SYNC` + `CREATE TABLE IF NOT EXISTS` for temp tables (race-safe)
- `TRUNCATE TABLE` before re-populating temp tables

## S2 Per-Tag Parameter Tuning -- TICK-BY-TICK VALIDATED (2026-03-02)
- **Sports is ONLY category with genuine positive excess HR in tick-by-tick**: +13.5pp NO excess, 74.3% HR
- **Vectorized-to-tick gap varies by category**: sports 4pp (best), culture/weather 10-11pp, politics 14pp, crypto/esports 24-29pp
- Most categories have NEGATIVE excess HR despite 65-72% absolute HR (base-rate artifact)
- PnL is positive in all categories due to asymmetric payoffs at low entry prices, NOT prediction quality
- **Entry price filter confirmed suboptimal in tick-by-tick**: -8 to -10pp HR, +3-7x PnL
- **Hold times MUCH longer than vectorized estimated**: sports 33d (vec: 1d), politics 25d (vec: 7d)
- Crypto: 55.7% HR, -20.5pp NO excess -- confirmed NO-GO
- Esports: 54.3% HR, near-zero PnL -- MARGINAL, not worth complexity
- Best configs (no filter): sports C>=4, politics C>=3, culture C>=4, other C>=2, weather C>=2
- Knowledge: `signals/insider_tag_tuning.md`, `pitfalls/excess_hr_vs_absolute_hr.md`
- Validation script: `research/scripts/s2_tick_tag_validation.py`
- Output: `research/output/s2_tick_tag/per_tag_all.parquet`
- **Status**: VALIDATED. Sports ready for deployment. Others conditional.

## Per-Category Base Rates (susceptible markets, verified 2026-03-02)
| politics 24.5/75.5 | sports 38.7/61.3 | esports 45.6/54.4 | culture 11.4/88.6 |
| finance 37.7/62.3 | weather 14.1/85.9 | crypto 23.4/76.6 | other 29.6/70.4 |

## Key Learnings
- **ReplayRunner NOW calls on_timer()**: timer_interval_s=3600 default. max_hold_hours IS functional (causes oversell warnings for settled positions).
- **NO direction collapses in ALL copy strategies**: structural, not fixable. See `pitfalls/no_hr_collapse_tick.md`
- **Direction-aware consensus matters**: +7pp HR improvement. Strategy must track qualified direction per trader.
- **load_period_trades with NormalizedTrade works** but is slower than lightweight dataclass approach (200K trades OK)
- **ClickHouseBackend (httpx) times out** under load. Use clickhouse_connect for long-running scripts.
- **Absolute HR is misleading without base rate**: culture 70.5% HR is -17pp below NO base (88.6%). See `pitfalls/excess_hr_vs_absolute_hr.md`
- **Vectorized hold times are grossly underestimated**: selection bias (only counts resolved positions). Tick shows 5-10x longer.
- **Positive PnL with negative excess HR is possible**: asymmetric payoffs at low entry prices ($50 risk vs $177+ reward)

## S2 Tag-Aware Hit-Rate Copy -- REJECTED (2026-03-03)
- Tick-by-tick: 46.7% HR, -$6,486 PnL (Jul 25, C>=3) -- WORSE than global pool (50.6%)
- Pool: 3,433 traders (2.1x global), 85% YES-direction, 15 tag+dir combos
- Crypto YES is ONLY viable signal (51.3% HR, +22.4pp excess, 2,364 signals)
- Extreme-NO-bias tags fail for YES: Culture 12.9%, Politics 3.0%, Movies 13.7%
- Vec-to-tick gap SMALLER (11pp vs 34pp global) because vectorized more honest (57.8% vs 84.2%)
- **Key learning**: tag-specific qualification is TOO PERMISSIVE; beating 9-29% YES base is easy
- **Key learning**: aggregate HR != trade-level HR gap persists regardless of base rate granularity
- Script: `research/scripts/s2_tag_aware_tick_validation.py`
- Notebook: `research/notebooks/s2_tag_aware_estimation.py`

## S2 HRC Gap Fixes -- REJECTED (2026-03-03)
- 3 fixes tested: position-level dedup, consensus cap (5), direction-aware filtering
- **Direction-aware filtering is the ONLY fix that helps** (+2-7pp HR)
- **Position-level dedup is COUNTERPRODUCTIVE** (-2.6pp HR avg). Multiple trades = conviction signal, not noise.
- **Consensus cap has ZERO effect** (pool too small to reach 5+ with direction filtering)
- Best config C>=4: 54.7% avg HR, +$2,275 total PnL, but inconsistent (1 of 3 periods negative)
- C>=3: 51.2% avg HR, -$7,185 total PnL -- UNPROFITABLE
- Hold times 13-18 days (consistent with prior vectorized underestimate)
- All rejections are position_limit (no capital/cooldown rejections)
- `on_timer()` fires correctly; causes harmless oversell warnings for settled positions
- StrategyConfig requires `name` field (added since last validation)
- Script: `research/scripts/s2_hitrate_gapfix_validation.py`
- Knowledge: `pitfalls/dedup_counterproductive.md`

## Dead Ends
- **S2 HRC Gap Fixes (dedup+cap+direction)**: 51.2% avg HR at C>=3, -$7,185. C>=4 marginal (+$2,275). Dedup hurts.
- **S2 Tag-Aware Hit-Rate Copy**: 46.7% HR tick (WORSE than global 50.6%). Tag-specific base too permissive.
- **S2 Hit-Rate Copy (BOTH direction)**: 45.9-50.6% HR tick-by-tick (base rate), negative PnL. NO direction kills it.
- **Crypto insider copy**: negative PnL at all consensus/price params despite 79-85% HR (vectorized). Tick: 55.7% HR, -20.5pp NO excess.
- **Esports insider copy**: -$185 to -$1238/pos vectorized; tick: 54.3% HR, $5K total PnL (marginal at best)
- **Culture/weather insider excess HR**: negative despite 70%+ absolute HR. PnL from price asymmetry, not alpha.
- **S2 HRC position dedup**: -2.6pp HR. Ongoing conviction from repeat trades is informative. See `pitfalls/dedup_counterproductive.md`
