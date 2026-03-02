# Insider Copy Strategy — Design Document

**Date**: 2026-03-02
**Status**: Approved
**Strategy code**: `s2_insider_copy`

## Hypothesis

Some Polymarket traders exhibit "insider knowledge" — they bet infrequently, with high conviction (large sizes), and achieve abnormally high hit rates. By identifying these traders through multi-signal statistical scoring and copying their trades, we can build a low-frequency, high-reward strategy with controlled downside.

## Insider Scoring Model

### Stage 1: Market Classification

Classify resolved markets into susceptibility tiers based on category and question patterns:

| Tier | Categories | Rationale |
|------|-----------|-----------|
| HIGH | Political, regulatory, legal, geopolitical, company/corporate | Outcomes known to insiders before public announcement |
| MEDIUM | Sports, entertainment, awards | Injury/lineup info, production leaks |
| LOW | Crypto price (5/15-min), gambling, "Up or Down", weather | Pure noise or public-info markets |

**Implementation**: Filter via `markets.category` + `question LIKE` patterns. Exclude LOW markets entirely from insider analysis.

### Stage 2: Trader Feature Computation

All features computed **only on HIGH + MEDIUM susceptibility resolved markets**.

#### F1 — Bayesian Hit Rate

Beta-Binomial model with direction-aware priors:

- YES prior: `Beta(alpha=3.81, beta=6.19)` — 38.1% mean (population YES base rate)
- NO prior: `Beta(alpha=6.19, beta=3.81)` — 61.9% mean (population NO base rate)
- Per trader: separate YES/NO posteriors updated from their resolved positions
- `effective_hr = max(posterior_yes_mean, posterior_no_mean)` — best direction
- Prior strength `alpha_0 + beta_0 = 10` means ~10 trades to meaningfully deviate

#### F2 — Bet Conviction

- `avg_position_usd = total_volume / n_markets` on susceptible markets
- Normalize: percentile rank across all traders with 3+ directional positions

#### F3 — Selectivity

- `markets_per_month = n_susceptible_markets / months_active`
- Lower = more selective = more insider-like

#### F4 — Anomaly Score

- Feature vector: `(log(n_markets), log(avg_bet_usd), bayesian_hr, hr_variance)`
- Z-score each dimension across population
- Mahalanobis distance from centroid — outliers in "few markets, big bets, high HR" corner

#### F5 — Timing Edge

- For each resolved position: `price_delta = resolution_price - entry_price`
- `avg_timing_edge = mean(price_delta)` across positions
- Consistently positive = they enter before the price moves toward resolution
- Requires joining `trader_market_positions.first_trade` with `market_prices.parquet`

#### F6 — Susceptibility Concentration

- `high_market_ratio = n_high_susceptibility_positions / n_total_positions`
- Insiders concentrated in HIGH markets score higher

### Composite Insider Score

```
insider_score = w1 * bayesian_hr_excess      # F1: excess over direction base rate
              + w2 * conviction_percentile    # F2: bet size rank
              + w3 * selectivity_rank         # F3: inverse markets/month rank
              + w4 * anomaly_score            # F4: statistical outlier distance
              + w5 * timing_edge_rank         # F5: pre-movement entry percentile
              + w6 * high_market_ratio        # F6: concentration in susceptible markets
```

Starting weights: `w1-w6 = 1/6` each (equal). Calibrate via grid search in vectorized backtest.

### Insider Pool

- **Qualification**: `insider_score > threshold` (sweep: top 0.1%, 0.5%, 1%, 5%)
- **Minimum evidence**: 3+ resolved directional positions on susceptible markets
- **Refresh cadence**: Monthly re-score (or after every 50 new resolutions)
- **Pool size target**: 50-500 traders (too few = no trades; too many = diluted signal)

## Strategy Execution

### Entry Logic

**Mode A — Single Insider Trigger**:
When any Insider Pool member BUYs into a new market:
1. Market is HIGH or MEDIUM susceptibility
2. We don't already hold a position in this market
3. BUY in the same direction (YES or NO) as the insider

**Mode B — Consensus Trigger**:
When N+ distinct insiders enter the same market in the same direction:
1. Same checks as Mode A
2. Enter only after the Nth unique insider converges (sweep N=2,3)

Both modes parameterized for comparative backtesting.

### Position Sizing

- **Flat sizing**: Fixed `size_usd` per position (e.g., $50)
- Future refinement: score-weighted sizing (`size_usd * insider_score_percentile`)

### Exit Logic

- **Primary**: Hold to resolution — collect full $1 payout on correct bets
- **Stop-loss**: If `current_price < entry_price * (1 - stop_loss_pct)`, SELL to exit
  - Default `stop_loss_pct = 0.50` (50% of entry price)
  - Sweep: 0.30, 0.40, 0.50, 0.60 in backtest
  - Freed capital becomes available for new positions

### Capital Management

| Parameter | Default | Sweep |
|-----------|---------|-------|
| `capital_usd` | 1000 | Fixed |
| `max_open_positions` | 20 | 10, 20, 50 |
| `size_usd` | 50 | 25, 50, 100 |
| `stop_loss_pct` | 0.50 | 0.30, 0.40, 0.50, 0.60 |

## Research Plan

### Phase 1: Data Exploration (ClickHouse SQL)

1. Classify all resolved markets by susceptibility tier
2. Profile market distribution: how many HIGH/MEDIUM/LOW?
3. Compute F1-F6 for all traders on susceptible markets
4. Distribution analysis: what does the insider score distribution look like?
5. Sanity check: manually inspect top-50 insiders — do they look real?

### Phase 2: Vectorized Discovery (upper bound)

1. Build vectorized strategy in marimo notebook
2. Sweep: `insider_score_threshold`, `consensus_count` (1 vs 2 vs 3), `stop_loss_pct`
3. Metrics: hit rate, avg edge, trade count/month, Sharpe, max drawdown, profit factor
4. Identify best 3-5 parameter sets for validation

### Phase 3: Manual Gate

- Review vectorized results
- Inspect insider pool composition
- Decide parameter sets to validate in tick-by-tick

### Phase 4: Tick-by-tick Validation (ReplayRunner)

- Run best parameter sets with realistic fills (RealisticFillSimulator)
- Capital constraints, settlement timing, slippage
- Compare with vectorized — expect 20-40pp hit rate degradation (documented pitfall)
- Stop-loss requires price monitoring per tick

### Phase 5: Capture & Score

- Document findings in `research/knowledge/signals/insider_copy.md`
- Update `research/ideas.md` with compounding score
- Decision: promote to paper trading or iterate

## Critical Pitfalls to Address

| Pitfall | Mitigation |
|---------|-----------|
| Consensus dedup | Count DISTINCT traders, not trade events |
| SELL is exit | Only process BUY trades as directional signals |
| Vectorized optimism | Always validate with ReplayRunner before deployment |
| Capital settlement | Use ReplayRunner with tick-by-tick settlement |
| Small sample size | Bayesian shrinkage with direction-aware priors |
| Gambling markets | Two-stage filter: classify markets, then score traders |

## Data Dependencies

| Data | Source | Status |
|------|--------|--------|
| `trader_positions_resolved` | ClickHouse VIEW | Exists |
| `markets` (category, question) | PostgreSQL | Exists |
| `market_prices.parquet` | Offline pipeline | Exists |
| `compact/*.parquet` | Offline pipeline | Exists |
| `qualified_traders.sql` | Research queries | Exists (adapt) |

## File Structure (planned)

```
research/
├── strategies/
│   └── s2_insider_copy.py          # Strategy implementation (Strategy protocol)
├── notebooks/
│   └── S2_insider_exploration.py   # Marimo notebook for Phase 1-2
└── knowledge/
    └── signals/
        └── insider_copy.md         # Findings (Phase 5)

configs/
└── s2_insider_copy.toml            # Strategy config
```
