# S1: Hit-Rate Copy-Trading Strategy

## Overview

Copy trades from historically high-hit-rate specialist traders when consensus
of qualified traders agree on the same directional position.

**Status**: Paper trading (paper_dev)
**Validated**: 8-month OOS walk-forward (Jul 2025 → Feb 2026), all months profitable

## How It Works — Step by Step

### 1. Qualified Trader Pool (computed in ClickHouse, refreshed every 15 min)

```
trader_positions_resolved (last 9 months)
  → filter: directional only (YES or NO, not HEDGED/CLOSED)
  → filter: exclude gambling markets ("Up or Down" in question)
  → group by trader: count wins, compute hit_rate
  → keep traders with:
      hit_rate >= 0.75
      resolved_positions >= 20
  → result: ~2,000-10,000 qualified traders
```

### 2. Trader Metadata (per qualified trader)

For each qualified trader, compute:
- **Category specialization** — fraction of trades in their dominant category
  (sports, esports, crypto, politics, other). Specialists (>= 70%) outperform generalists.
- **Dominant category** — most-traded category
- **Last-5 streak** — correct count in 5 most recent resolved positions.
  Traders with 0/5 have 33% HR (avoid). Traders with 5/5 have 69% HR.

### 3. Token Outcome Map

Static lookup from `token_market_map`: maps `asset_id` → `"YES"` or `"NO"`.
Needed to determine what side a trade is on.

### 4. On Every Incoming Trade (hot path, < 1ms)

```
trade arrives on trades.raw Kafka topic
  │
  ├─ Is maker in qualified pool? ─── NO → skip
  │
  ├─ Is this a gambling market? ──── YES → skip
  │
  ├─ Resolve outcome (YES/NO) from asset_id
  │
  ├─ Compute TRADER's directional entry price (signal quality):
  │   YES side: trader_dir = trade.price
  │   NO side:  trader_dir = 1.0 - trade.price
  │
  ├─ L2a: Is trader_dir in [0.60, 0.90)? ─── NO → skip
  │   Why: Below 0.60, expected value is negative (Kelly < 0).
  │         Above 0.90, profit/loss ratio is terrible ($1 profit vs $10 risk).
  │
  ├─ L4: Is trader a specialist (>= 70% in one category)? ─── NO → skip
  │   Why: Generalists have ~4pp lower OOS HR and negative PnL.
  │
  ├─ L7: Does trader have >= 1 correct in last 5? ─── NO → skip
  │   Why: Cold-streak traders are noise. Filtering costs nothing (redundant with L4).
  │
  ├─ Update consensus counter for this market+side
  │
  ├─ L3: Are >= 4 qualified traders on the same side? ─── NO → skip
  │   Why: CRITICAL for fixed-size bets. Without consensus, $10 bets have ~$0 EV.
  │         With consensus >= 4, all price bands become strongly positive EV.
  │
  ├─ Already have a position in this market? ─── YES → skip
  │
  ├─ Get OUR entry price from orderbook (best_ask for BUY):
  │   If orderbook available: our_price = best_ask (or 1-best_bid for NO)
  │   If no orderbook: fallback to trader_price + 0.02 buffer
  │   PaperExecutor verifies via CLOB REST API before filling.
  │
  ├─ L2b: Is our_dir_price < 0.90? ─── NO → skip (book moved too far)
  │
  └─ EMIT TradeIntent: BUY same side as trader, $10, max_price from book
```

### 5. Position Lifecycle

```
TradeIntent → ExecutionGateway → PaperExecutor → Fill
  │
  ├─ Position tracked in InMemoryContext
  ├─ Intent logged to strategy_intents (PostgreSQL)
  ├─ Published to strategy.intents (Kafka)
  │
  └─ Resolution: MarketEventsConsumer detects market_resolved
       → position closes automatically at resolution price
       → PnL realized
```

## Performance (8-month OOS backtest)

| Metric | Value |
|--------|-------|
| Positions | 15,013 |
| Hit Rate | 87.9% |
| PnL per $10 bet | $0.94 |
| Total PnL | $14,135 |
| PnL per day | $58 |
| Monthly Sharpe | 1.06 |
| Max drawdown | $69 |
| Profit factor | 1.76 |
| Profitable months | 8/8 |
| Trades per day | ~62 |

### Category Breakdown

| Category | Positions | HR | PnL/Bet | Avg Hold | Capital Efficiency |
|----------|-----------|-----|---------|----------|-------------------|
| **Esports** | 1,468 | 89.4% | $1.20 | **0.3d** | Best rotation |
| **Sports** | 7,317 | 89.0% | $1.24 | **1.4d** | Workhorse |
| Other | 2,665 | 86.5% | $0.57 | 9.3d | Decent |
| Politics | 1,947 | 86.4% | $0.59 | 22.4d | Capital-heavy |
| Crypto | 1,616 | 85.5% | $0.39 | 11.0d | Moderate |

### Capital Requirements

- $1,000 bankroll, 50 max open positions × $10 = $500 max at risk
- Estimated: ~$89/month, ~107% annualized ROI
- Max drawdown ~$69 (7% of bankroll)

## Key Research Findings

### Why Consensus Matters

Without consensus filter, individual high-HR traders copying at $10 fixed bets has near-zero
expected value. The earlier large PnL figures came from variable position sizes (big traders
naturally bet more on high-conviction plays). With consensus >= 4, the signal concentrates
on markets where multiple independent skilled traders agree, filtering out noise.

### Alpha Decay is Slow

Median directional price move after a qualified trader's entry = $0.00 at all horizons
(5 min to 24 hours). Average move only 5-9 cents. No HFT infrastructure needed —
a 5-15 minute polling frequency is sufficient. Copy delay budget: 5-10 cents per position.

### Pool Stability

~79% monthly retention rate for qualified traders. New entrants perform slightly worse
(94.3% HR vs 95.6% for retained traders) but are still profitable. Pool grows over time
as more markets resolve and more traders accumulate history.

### Filters Validated as Destructive (DO NOT ADD)

- **Market age 1-7d**: reduces PnL without improving HR when stacked
- **Position sizing 0.5-2x median**: drops 82% of universe with no HR gain
- **30d time stop**: low impact — most positions resolve in < 7 days

## Configuration

See `configs/s1_hitrate_copy.toml` for all tunable parameters.

Key parameters to adjust:
- `size_usd`: bet size (start at $10, scale after validation)
- `min_consensus`: consensus threshold (4 is validated, try 3 for more volume)
- `min_hr`: hit rate threshold (0.75 validated, 0.80 for higher precision)
- `lookback_months`: training window (9 validated, 12 slightly better but fewer traders)

## Running

```bash
# Paper trading (paper_dev mode)
uv run pm-strategy run --config configs/s1_hitrate_copy.toml

# With verbose logging
uv run pm-strategy run --config configs/s1_hitrate_copy.toml --verbose

# Single strategy (when config has multiple strategies)
uv run pm-strategy run --config configs/s1_hitrate_copy.toml --only s1_hitrate_copy

# Check promotion readiness
uv run pm-strategy promote s1_hitrate_copy --to paper_prod --config configs/s1_hitrate_copy.toml

# Reset paper state
uv run pm-strategy reset --log-dir logs/paper --yes
```

## Architecture

```
ClickHouse (192.168.0.148:18123)
  │
  ├─ trader_positions_resolved (VIEW)
  ├─ markets (PG engine)
  ├─ token_market_map (PG engine)
  │
  └─ S1HitRateProvider.compute() / refresh()
       │  (every 15 min)
       │
       ▼
  InMemoryContext (features)
  ├─ s1_qualified_traders: set[str]
  ├─ s1_trader_meta: dict[str, dict]
  ├─ s1_token_outcomes: dict[str, str]
  ├─ s1_gambling_cids: set[str]
  └─ s1_consensus: dict[str, dict]  ◄── updated per trade (hot path)
       │
       ▼
  S1HitRateCopyStrategy.on_trade()
       │
       ▼
  TradeIntent → ExecutionGateway → PaperExecutor → Fill
       │
       ▼
  strategy_intents (PG) + strategy.intents (Kafka)
```

## Future Opportunities

### High Priority (validated signals, need implementation)

1. **Category-weighted sizing** — Esports has 0.3d hold time (best capital efficiency).
   Weight position sizes inversely to expected hold time for faster compounding.

2. **Dynamic consensus threshold** — Lower to 3 in high-volume periods (many qualified
   traders active), raise to 5 in low-volume periods. Adaptive signal strength.

3. **Multi-timeframe lookback** — Blend 3-month (recent signal) and 12-month (stable
   baseline) hit rates. Weight recent performance more heavily.

4. **Trader cluster analysis** — Some qualified traders are likely following each other
   (correlated signals). Identify clusters and count consensus at the cluster level
   to avoid double-counting.

### Medium Priority (promising but needs more research)

5. **Market volume regime filter** — Markets with volume 1K-100K have best PnL/trade.
   But this filter was destructive when stacked with L4. May work as a sizing signal
   instead (larger bets on mid-volume markets).

6. **Entry price below 0.50** — Massive asymmetric payoff ($49/bet at HR=12.7%).
   Extremely high variance though. Consider as a separate "longshot copy" strategy
   with Kelly-optimal sizing.

7. **Time-of-day patterns** — Friday/Saturday show slightly lower HR (93% vs 96%).
   Consider reducing position limits on weekends.

8. **Contrarian consensus** — When high-HR traders take the minority side (< 30% of
   market volume), the signal may be even stronger. Needs walk-forward validation.

### Low Priority (speculative)

9. **Cross-market correlation** — When the same event has multiple markets (e.g., election
   with 5 candidates), qualified trader activity in one market may predict others.

10. **Trader sentiment shift detection** — Detect when a previously-YES trader flips to NO
    (or vice versa). May signal information arrival.

11. **Resolution timing prediction** — Sports markets resolve same-day, politics can take
    months. Use predicted resolution time to optimize capital allocation.

12. **Network graph features** — Build a trader co-occurrence graph. Traders who frequently
    appear in the same markets may form information networks.

## Research Artifacts

| File | Description |
|------|-------------|
| `research/notebooks/S1_hitrate_copy_exploration.py` | Initial exploration (18 cells) |
| `research/notebooks/S1b_parameter_sweep.py` | Parameter sweep (2,240 combos) |
| `research/notebooks/S1c_strategy_improvements.py` | 6-axis improvement analysis |
| `research/notebooks/S1d_stacked_validation.py` | Stacked filter walk-forward |
| `research/strategies/s1_hitrate_copy.py` | Research prototype |
| `research/scripts/s1_backtest.py` | CH-native walk-forward backtest |
| `research/scripts/s1_parameter_sweep.py` | Sweep engine |
| `research/output/s1_backtest_*.parquet` | Backtest results |
| `research/output/s1_sweep_*.parquet` | Sweep results |
