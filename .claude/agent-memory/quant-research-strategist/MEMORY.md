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
See `research/knowledge/` for full details. Top gaps: no orderbook depth, no partial fills, no mark-to-market.

## S2 Insider Copy -- VALIDATED (2026-03-02)
- Best HR: C>=2 66.8% HR, $254K PnL. Best PnL: C>=3 p<0.65 57.3% HR, $784K PnL.
- Vec-to-tick gap: 18-29pp. Entry price filter INVERTS in tick (-7pp HR vs +2.7pp vec).
- Status: READY for production. See `signals/insider_copy.md`.

## ClickHouse SQL Gotchas
- `FROM table FINAL AS alias` INVALID in CH 24.8 -- use subquery
- CTE aliases (`sm.category`) lost when re-joined in outer query -- join raw table again
- Use tag chain for susceptibility, not `market_categories` (see `research/knowledge/queries/tag_susceptibility.sql`)
- CTE column names after CROSS JOIN: prefix `o.column` fails in CH 24.8. Use short aliases in CTE (e.g., `cid`, `dir`, `rpnl`) and reference without prefix in outer query.
- `markets.category` is 99.3% NULL -- never use for filtering; use tag join chain instead
- `markets_resolved` view exposes `token_won` not `yes_won` -- use `mr.token_won` for base rate computation
- CTE with multi-table JOIN: outer SELECT can't resolve unqualified column names from CTE. Fix: alias all columns in the CTE (e.g., `p.trader AS t_trader`) and reference aliases in outer query.

## S2 Hit-Rate Copy -- REJECTED (2026-03-02)
- Tick: 45.9-50.6% HR, negative PnL. NO direction anti-predictive. See `pitfalls/no_hr_collapse_tick.md`.

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

## S2 Rejected Strategies (2026-03-03 to 2026-03-06)
- S2 Tag-Aware HRC: REJECTED. 46.7% tick HR. Tag qualification too permissive.
- S2 HRC Gap Fixes: REJECTED. Dedup counterproductive (-2.6pp). Direction-aware +2-7pp only fix.
- Copy-Trader Contamination: REJECTED. Followers > Leaders. All independence filters harmful.
- See `dead_ends.md` for details.

## Trader Scorecard Framework (2026-03-07)
- Framework doc: `research/hypotheses/trader-scorecard/discovery/scorecard_framework.md`
- **4 scored metrics**: excess_hr_weighted (0.45), consistency_sharpe (0.25), avg_edge_usd (0.20), profit_factor (0.10)
- **Conviction OMITTED**: contaminated by split mechanics (55.9% of makers), subsumed by consensus dedup
- **Striking score REPLACED** with avg_edge_usd: contrarian thesis contradicted by data (high entry price = best signal)
- **Composition**: tiered gate (binary pass/fail) + weighted composite (percentile-ranked)
- **Normalization**: percentile rank within tag cohort (min 20 traders; global fallback below)
- **Tag-specific scorecards MANDATORY**: base rates 9-73%, hold times 0.3-22d, pool dynamics differ
- **Minimum data**: 10 positions/tag, 2 active windows, recency <= 90d, bot guard < 10K
- **Decay**: 90-day half-life (tag-adjustable in v2)
- **Key insight**: entry price floor (>= 0.70) outperforms entry price ceiling -- confirms favorites, not contrarians

## Portfolio Analysis (2026-03-09)
- Sports YES + InPlay share 99.5% markets (same YES direction) — double exposure, not diversification
- InPlay exclusive: -$482 PnL, degrading (Jan -$4K, Feb -$7K). NEGATIVE incremental value.
- InPlay enters 9h earlier but same HR, 12x lower avg PnL than Sports on shared markets
- Recommended: 2-track (Sports YES + Politics NO), disable InPlay
- Combined Sharpe 7.76 (Sports alone 7.00, with InPlay 7.57)
- Sports never capital-constrained at $5K; Politics constrained (197/346 accepted at P=20)
- Bottleneck: signal generation (2,369 fills), not capital. $10K+ provides no benefit.
- Polars Datetime("s") invalid — use "us", "ms", or "ns". Convert epoch: `(col * 1e6).cast(Datetime("us"))`

## DuckDB / Parquet Gotchas
- **markets_resolved has 2 rows per market** (YES + NO tokens). Filter `WHERE outcome = 'YES'` for market-level yes_won.
- Averaging `token_won` without filtering gives exactly 0.5 (artifact, not real base rate).
- DuckDB date literals: use `TIMESTAMP '2025-07-01'` or `DATE '2025-07-01'`, NOT string comparisons.

## Microstructure Calibration (2026-03-09)
- Fill model contributes <1pp HR gap, 1-5% PnL gap (with MAC). NOT the bottleneck.
- MAC half-spread: global 0.01, Sports 0.00, Crypto 0.01, Politics 0.001
- Roll estimator 17x MAC — captures fundamentals, NOT friction. Never use for slippage.
- 44% consecutive trades = zero price change. Min tick = 0.01 (1 cent).
- Larger trades ($1K+) predict MEAN REVERSION (only 14% BUY $1K+ sees continued upward).
- Spread lifecycle: last 10% of market = 53% wider avg spread, 54% of all trades.
- DuckDB OOM on full trades table with window functions. Use SET threads=2; SET memory_limit='16GB'.
- Knowledge: `execution/spread_microstructure.md`

## Tag-HR-Consensus -- TICK-BY-TICK VALIDATED (2026-03-09)
- **Esports YES K50 N3**: 64.0% HR, +17.7pp market excess, $14.8K PnL, Sharpe 9.65, 297 fills, 3.8h hold. CS=55.
- **Esports YES K100 N4**: 64.2% HR, +17.9pp, $6.0K, Sharpe 10.98, 123 fills. CS=57.
- **Tennis NO K50 N2**: REJECTED. 56.0% HR vs 56.9% market-level NO base = -0.9pp. Zero edge.
- **Vec-to-tick gap: 3pp** (vs 21-32pp for tag-hr-copy). Consensus alignment fixes the gap.
- **CRITICAL**: Use MARKET-LEVEL base rate, not position-level. Tennis NO looked +19.5pp (vs pos 36.5%) but actually -0.9pp (vs mkt 56.9%).
- **Positive PnL with zero edge is possible**: Tennis NO PnL from asymmetric payoffs, not prediction.
- Pool: 13 qualified YES traders for Esports (small but effective). Only 3 folds have data (late 2025+).
- Scripts: `research/hypotheses/tag-hr-consensus/scripts/`
- Analysis: `research/hypotheses/tag-hr-consensus/discovery/analysis.md`
- **Status**: Esports YES ready for portfolio integration (3rd track).

## InPlay Early Trigger -- REJECTED (2026-03-09)
- **Structurally invalid**: InPlay (N=1) ALWAYS fires BEFORE Sports (N=2) on same pool -- tautological
- Sports consensus cannot exist at InPlay's signal time (0 cases of Sports first)
- Using Sports N=2 as retroactive filter = look-ahead bias
- Earlier entry INCREASES hold time (+10.9h, not decreases)
- Sports 66x better CS (148 vs 2.2); no hybrid variant improves on Sports alone
- Price moves toward correct outcome between 1st and 2nd trader entry
- On long-shots: Sports enters 9h later at 0.03 vs InPlay at 0.55 → $595 avg win vs $80
- Only 34% of InPlay N=1 signals eventually get Sports N=2 confirmation
- **Key learning**: Same pool at different N thresholds provides no independent timing information
- Analysis: `research/hypotheses/inplay-early-trigger/discovery/analysis.md`

## Esports Sub-Tag Decomposition (2026-03-09)
- **Game-specific pools INFEASIBLE**: Only CS2 has enough game-specific qualified traders (47). All others = 0.
- **Pool traders are cross-game generalists**: 13 BEH-gated traders all bet 2+ games (CS2+LoL primary).
- **Consensus depth**: Only CS2 (271 mkts N>=3) and LoL (194 mkts N>=3) have consensus. Dota2=13, rest=0.
- **LoL shows higher HR** (81.2% at N=3, +36.8pp excess) but only 16 signals -- insufficient for confidence.
- **CS2 is the volume backbone**: 38/54 N=3 signals (70%) are CS2 markets.
- **Parameter robustness CONFIRMED**: All 25 K x N cells show positive excess HR (+9.1 to +38.0pp).
- Gradient is smooth: smaller K and higher N = higher excess HR (expected, not fragile).
- K50 N3 sits at optimal precision/throughput tradeoff. K25 N2 offers 60% more signals at -3pp.
- **Verdict**: No game-specific strategy warranted. Keep unified Esports pool.
- Analysis: `research/hypotheses/esports-sub-tag/discovery/analysis.md`

## Esports Per-Game Base Rates (test period 2025-07 to 2026-03)
| CS2 47.8% | Dota2 45.9% | LoL 44.4% | Valorant 46.3% | HoK 43.8% | SC2 50.9% |

## Politics Active Exit -- VALIDATED (2026-03-09)
- **Exit@50% of max payout**: $31,368 PnL vs $12,646 hold at P=20 (+148%)
- **Mechanism**: Pure capital recycling, NOT per-position improvement (unconstrained: $34,639 vs $33,942)
- **Key stats**: 327 fills (vs 197), median hold 0.9d (vs 4.7d), ROC/day 4.3x better
- **Politics Sharpe**: 4.28 -> 5.66, MaxDD $1,568 -> $900
- **Capital insight**: Exit@50% at P=10 ($1K) > Hold at P=20 ($2K). Frees $1K for Esports.
- **Longshot dominance**: <0.50 fill bucket = 106% of total PnL ($35,854 from 60 positions)
- **0.90+ bucket**: 190 positions, 90% HR, NEGATIVE PnL (-$1,368). Breakeven at 0.93 = 93% HR.
- **Lost positions**: Only 1/19 lost positions at 0.90+ escaped via 50% exit. Losses are inescapable.
- **Hybrid strategies tested**: Uniform Exit@50% beats all price-dependent variants.
- **Slippage**: ~$29 total on 291 exits (MAC 0.001 for politics). Negligible.
- Analysis: `research/hypotheses/politics-active-exit/discovery/analysis.md`

## Dead Ends
See `dead_ends.md` for full list. Key: dual-skill, contamination fixes, HRC gap fixes, tag-aware HRC, NO direction, inplay-early-trigger.
