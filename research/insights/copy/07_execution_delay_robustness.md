# Execution Delay Robustness: Signal Edge Survives (and Improves) with Latency

**Date**: 2026-02-16
**Engine**: `strategies/consistency_copy/backtester/` with forward asof join at delays [0, 30, 60, 300]s
**Data**: 70.9M PnL rows, 340M price records, 390K resolved markets, 22,628 sweep configs

---

## Key Finding

**The consensus copy signal is not only robust to execution delay — it actually performs BETTER with 60-300s of latency.** This is the opposite of what most trading signals exhibit (where speed = edge).

The top 10 configs overall are ALL at delay >= 30s, with #1 and #3 at delay=300s:

| # | Sharpe | HR | PnL | Delay | Dir | MVF | Min T | Agree | Band |
|---|--------|-----|-----|-------|-----|-----|-------|-------|------|
| 1 | 6.04 | 64.5% | $2,901 | 300s | NO | pure | 7 | 70% | wide |
| 2 | 5.93 | 63.4% | $3,550 | 60s | NO | pure | 7 | 70% | wide |
| 3 | 5.91 | 57.0% | $2,309 | 300s | NO | pure | 7 | 80% | wide |
| 4 | 5.54 | 56.1% | $2,506 | 60s | NO | pure | 7 | 80% | wide |
| 5 | 5.50 | 54.6% | $2,204 | 300s | both | pure | 7 | 80% | wide |
| 6 | 5.32 | 62.6% | $1,259 | 300s | NO | informed | 10 | 60% | narrow |
| 7 | 5.25 | 53.8% | $2,399 | 60s | both | pure | 7 | 80% | wide |
| 8 | 5.13 | 62.9% | $2,630 | 30s | NO | pure | 7 | 70% | wide |
| 9 | 5.10 | 60.1% | $2,529 | 300s | both | pure | 7 | 70% | wide |
| 10 | 5.10 | 58.7% | $494 | 30s | NO | informed | 10 | 60% | narrow |

No delay=0s config appears in the top 10.

---

## Forward Price Coverage by Delay

Coverage = % of (trader, market) pairs where a forward price was found within the 1-hour max window.

| Window | delay=0s | delay=30s | delay=60s | delay=300s |
|--------|----------|-----------|-----------|------------|
| 2024 H1 | 72.4% | 56.4% | 56.0% | 54.9% |
| 2024 H2 | 90.8% | 87.9% | 87.8% | 87.3% |
| 2025 H1 | 95.6% | 91.7% | 91.4% | 90.8% |
| Dec 2025 | 99.0% | 92.9% | 91.7% | 80.1% |
| Jan 2026 | 98.7% | 93.2% | 92.5% | 81.7% |

Coverage drops significantly at 300s for recent windows (99% → 80%). This means ~20% of signal entries at 300s delay fall back to trader's own entry price. Despite this, performance improves — the 80% that DO get a delayed price are at better entry points.

---

## Why Delay Helps: The Price Selection Effect

Tracking the best config (NO-only, pure_taker, months=6, mkts=10, min_t=7, agree=70%, wide band) across delays:

### Dec 2025 — Dramatic Improvement
| Delay | Bets | Wins | HR | PnL/Bet | Total PnL |
|-------|------|------|----|---------|-----------|
| 0s | 46 | 21 | 45.7% | $10.61 | $488 |
| 30s | 47 | 22 | 46.8% | $54.30 | $2,552 |
| 60s | 46 | 22 | 47.8% | $98.43 | $4,528 |
| 300s | 46 | 23 | 50.0% | $78.50 | $3,611 |

Same 46 bets, but PnL/bet goes from $10.61 to $98.43 at 60s delay — a **9.3x improvement** with one extra win. The entry price is doing all the work.

### Jan 2026 — Stable
| Delay | Bets | Wins | HR | PnL/Bet | Total PnL |
|-------|------|------|----|---------|-----------|
| 0s | 65 | 51 | 78.5% | $39.15 | $2,545 |
| 30s | 62 | 49 | 79.0% | $43.67 | $2,708 |
| 60s | 62 | 49 | 79.0% | $41.49 | $2,573 |
| 300s | 62 | 49 | 79.0% | $35.35 | $2,192 |

Jan 2026 is already highly liquid (median dt=0s at delay=0). Delay has minimal impact — the signal edge is from directional prediction, not price timing.

### Explanation

The mechanism differs by market liquidity:

1. **In less liquid markets (Dec 2025)**: At delay=0s, the first trade after signal may be at a stale or random price. At 30-60s, the market has had time to process skilled trader flow, and the NO entry price improves because counterparties are crossing at better levels. The delay acts as a **price quality filter**.

2. **In highly liquid markets (Jan 2026)**: Prices are already efficient at delay=0s (median dt=0s). Delay doesn't help because the first available trade is already at a well-informed price.

3. **At 300s**: Improvement plateaus or reverses slightly. The market has fully digested the information, so further delay doesn't improve entry prices. Coverage drops to ~80%, losing some marginal bets.

---

## Aggregate Statistics

### Overall by Delay
| Delay | Configs | Avg HR | Avg Sharpe | Avg PnL | % Positive Sharpe |
|-------|---------|--------|------------|---------|-------------------|
| 0s | 5,683 | 36.2% | -6.58 | -$10,095 | 17.2% |
| 30s | 5,654 | 36.3% | -7.12 | -$9,549 | 16.9% |
| 60s | 5,654 | 36.3% | -7.02 | -$9,310 | 17.0% |
| 300s | 5,637 | 36.7% | -6.57 | -$8,027 | 16.8% |

The averages look flat because most configs are unprofitable (YES-only drags down averages). The action is in the tails.

### Sharpe Distribution Tails
| Delay | Sharpe > 0 | Sharpe > 2 | Sharpe > 4 |
|-------|------------|------------|------------|
| 0s | 17.2% | 8.0% | 2.9% |
| 30s | 16.9% | 7.9% | 2.2% |
| 60s | 17.0% | 7.8% | 2.4% |
| 300s | 16.8% | 8.4% | 3.1% |

The right tail (Sharpe > 4) actually grows at 300s delay (3.1% vs 2.9% at 0s).

### NO-only + Pure Taker (Best Segment)
| Delay | Configs | % Positive Sharpe | Avg Positive Sharpe |
|-------|---------|-------------------|---------------------|
| 0s | 510 | 50.6% | 2.55 |
| 30s | 506 | 50.6% | 2.39 |
| 60s | 505 | 50.9% | 2.28 |
| 300s | 498 | 48.8% | 2.75 |

Half of all NO-only pure_taker configs have positive Sharpe at every delay level. At 300s, the average positive Sharpe is highest (2.75).

---

## Implications for Live Trading

1. **No latency race needed.** Unlike most HFT signals, this consensus copy strategy has a multi-minute window to execute. A 60-300s delay from signal detection to order placement is not just acceptable — it's optimal.

2. **Execution architecture simplifies dramatically.** Instead of sub-second WebSocket monitoring, the live system can:
   - Poll or subscribe to trader activity
   - Detect consensus signals
   - Wait 60s for price stabilization
   - Place order at a BETTER entry price

3. **Slippage concerns are reduced.** With 60-300s to execute, the system can:
   - Use limit orders instead of market orders
   - Wait for favorable orderbook depth
   - Avoid moving the market

4. **The optimal delay is 60s.** It provides the best PnL/bet in the most important window (Dec 2025) while maintaining high coverage (91-92%). 300s delay starts losing coverage in some markets.

---

## Caveats

1. **Only 2 holdout windows** contributed meaningful results (Dec 2025 + Jan 2026)
2. **Dec vs Jan asymmetry**: Dec shows dramatic delay benefit, Jan shows slight degradation. The true out-of-sample behavior may be between these extremes
3. **Coverage at 300s drops to 80%**: 20% of signals fall back to trader entry prices
4. **Small bet counts**: 46-65 bets per window per config
5. **The delay benefit may be an artifact of market microstructure** that changes over time as Polymarket grows
