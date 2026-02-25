# Completed Plans Archive

All plans below were fully implemented and merged to main. Original files removed to reduce clutter. Each entry preserves the key decisions and what was built.

---

## 2026-02-08: Unified Trade Pipeline

**Built:** Complete ingestion pipeline — Goldsky Parquet backfill, RTDS WS, Alchemy RPC, NormalizedTrade model, ClickHouse ReplacingMergeTree, PostgreSQL metadata sync, deterministic SHA-256 trade IDs with `chain:`/`ws:` prefixes, taker dedup (~40.5% filtered), USDC 1e6 scaling.

**Key files:** `models.py`, `trade_id.py`, `constants.py`, `normalizers/`, `loaders/parquet.py`, `sinks/clickhouse.py`, `sinks/postgres.py`, `market_sync.py`

---

## 2026-02-13: Strategy Explorer

**Built:** `pm-explore` CLI — interactive exploration shell with Claude agent integration, tree-based ML stages, ClickHouse component library (proven SQL), MLflow experiment tracking.

**Key files:** `cli/explore.py`, `exploration/agent.py`, `exploration/components/`

---

## 2026-02-16: Consensus Signal Backtester (design + impl)

**Built:** Backtester for consensus copy trading signal — sweep over (direction, MVF, delay, min_markets) parameters. Found: NO-only dominates, pure_taker MVF best, 60s+ delay optimal.

**Key files:** `research/strategies/consistency_copy/backtester/`

---

## 2026-02-16: pm-chat (design + impl)

**Status: NEVER IMPLEMENTED.** Conversational exploration shell was designed but never built. The `pm-explore` CLI covered the use case instead.

---

## 2026-02-17: Backtester V2 Enhancements (design) + V3 Robust Validation (design + impl)

**Built:** Backtester enhancements — tautology elimination (direction inference from PnL, resolution_value=1 always), YES/NO base rate validation, temporal hold-out splits, proper Kelly sizing.

**Key files:** `research/strategies/consistency_copy/backtester/runner.py`, `signal_table.py`, `sweep.py`

---

## 2026-02-18: Portfolio Replication Backtester (design + impl)

**Built:** Full portfolio copy simulation with position tracking, proportional sizing, market-by-market PnL attribution.

**Key files:** `research/strategies/consistency_copy/backtester/`

---

## 2026-02-19: Informed MM Estimate (design + impl)

**Built:** Scripts estimating market-making edge from skilled trader flow. Spread capture + informed flow analysis.

**Key files:** `research/scripts/informed_mm_estimate.py`, `research/scripts/s2_mm_estimate.py`

---

## 2026-02-19: Kelly Portfolio Sim (design only, 36 lines)

**Built:** Kelly criterion portfolio simulation script.

**Key files:** `research/scripts/kelly_portfolio_sim.py`

---

## 2026-02-20: Live Sync Architecture (design + impl)

**Built:** FastStream + Redpanda live pipeline — 5 ingestors (Alchemy, RTDS, PendingBlock, CLOB WS, Subgraph), Kafka engine tables, circuit breaker, dedup, quality state machine, recovery loop.

**Key files:** `live/app.py`, `live/orchestrator.py`, `live/ingestors/`, `live/settings.py`

---

## 2026-02-20: Monitoring Dashboard (design + impl)

**Built:** Async HTML dashboard with pipeline quality metrics, ingestor health, trade rates.

**Key files:** `live/dashboard.py`, `live/quality/checker.py`

---

## 2026-02-21: Mempool Monitor (design + impl)

**Built:** Rust PyO3 devp2p node for Polygon mempool monitoring. Key finding: operators bypass public mempool entirely (private submission). Mempool approach abandoned in favor of pending block polling.

**Key files:** `crates/polymarket-mempool/` (chain_spec, network/runner, filter, decoder)

**Verdict:** `newPendingTransactions` is dead for Polymarket. `eth_getBlockByNumber("pending")` works instead.

---

## 2026-02-23: Erigon Peer Targeting (design + impl)

**Built:** Auto-detect Erigon peers by client_version, promote to trusted, send GetPooledTransactions requests. Result: 1 Erigon peer found, zero tx messages (operators use private submission).

**Key files:** `crates/polymarket-mempool/src/network/runner.rs`

---

## 2026-02-23: Strategy Framework (design + impl)

**Built:** Protocol-based strategy execution framework — `Strategy`, `VectorizedStrategy`, `FeatureProvider`, `FeatureBackend`, `Executor`, `StrategyContext` protocols. `InMemoryContext`, `LiveRunner`, `BacktestRunner`. TOML config loading.

**Key files:** `strategies/protocol.py`, `strategies/types.py`, `strategies/config.py`, `strategies/runners/`, `strategies/context/`

---

## 2026-02-23: Paper-Dev Mode (design + impl)

**Built:** `PaperExecutor` (orderbook-aware fill simulation), `ExecutionGateway` (quality gate + budget gate + JSONL logging), `PolarsBackend` + `ClickHouseBackend` feature backends, `pm-strategy` CLI.

**Key files:** `strategies/execution/paper.py`, `strategies/execution/gateway.py`, `strategies/features/`, `cli/strategy.py`

---

## 2026-02-24: Industrialization (design + impl)

**Built:** ClickHouse Kafka engine tables, materialized views (trades_kafka_mv, orderbook_kafka_mv), `apply_schema()` function, batch consumer tuning.

**Key files:** `live/schema.py`

---

## 2026-02-24: Three Strategies S1+S2a+S2b (impl)

**Built:** Three concrete strategy implementations — S1 ProportionalCopy (GradedPoolProvider with longshot YES filter), S2a WillNO (binary question NO buyer), S2b CryptoOTMNo (OTM crypto checkpoint NO buyer). All with config, strategy, providers modules. Market size classifier provider shared across S2a and S2b.

**Key files:** `strategies_impl/proportional_copy/`, `strategies_impl/will_no/`, `strategies_impl/crypto_otm_no/`, `strategies_impl/market_size/`

---

## 2026-02-24: Review Fixes + Slice 3 "Self-Protecting" (impl)

**Built:** Auto-protection (close positions on RED state), circuit breaker improvements, risk gate helpers (4 gates: capital, position limit, max open, cooldown).

**Key files:** `live/protection.py`, `strategies/runners/helpers.py`

---

## 2026-02-25: ClickHouse Derived Views (impl)

**Built:** `trader_volumes` (SummingMergeTree), `trader_trade_agg` (SummingMergeTree), `markets_resolved` (VIEW over PG engine), maker/taker materialized views feeding both tables.

**Key files:** `live/schema.py` (TRADER_VOLUMES_TABLE, TRADER_TRADE_AGG_TABLE, MARKETS_RESOLVED_VIEW, 4 MVs)

---

## 2026-02-25: Consistent Traders Provider (impl)

**Built:** `filter_consistent_traders()` pure function (5-filter pipeline), `SkilledTradersProvider` dual-mode (Polars offline + ClickHouse live), `load_skilled_provider()` convenience, wired into CLI + vectorized runner.

**Key files:** `strategies_impl/consensus_copy/consistency.py`, `strategies_impl/consensus_copy/providers.py`

---

## 2026-02-25: Market Size Classifier (impl)

**Built:** XGBRegressor on log1p(volume), 34 numeric + 20 text SVD features, post-hoc bucketing (thin/med/thick/heavy), MarketSizeProvider, training + validation scripts.

**Key files:** `strategies_impl/market_size/classifier.py`, `strategies_impl/market_size/features.py`, `scripts/train_market_size_classifier.py`

---

## 2026-02-25: Pool Refresh Automation (design + impl)

**Built:** CLOB WS `market_resolved` → `markets.events` Kafka topic → `MarketEventsConsumer` (5s debounce) → `LiveRunner.request_refresh()` → providers re-query CH → atomic context swap.

**Key files:** `live/consumers/market_events.py`, `live/ingestors/clob_orderbook.py` (event routing), `strategies/runners/live.py` (asyncio.Event-based refresh)

---

## 2026-02-25: Strategy Next Steps (impl)

**Status: PARTIALLY IMPLEMENTED.** Grading + dual-sided + budgets described. GradedPoolProvider grading and ExecutionGateway budgets already implemented. Superseded by `next-steps-plan.md` for remaining items.
