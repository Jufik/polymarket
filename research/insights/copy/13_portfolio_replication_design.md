# Portfolio Replication vs Consensus Signal Copy

**Date**: 2026-02-19
**Method**: Comparison of two copy trading approaches using the same trader pool

---

## Two Approaches

### Consensus Signal Copy (existing backtester)
- Count how many pool traders are on each side of a market
- If >= `min_traders` agree on a direction, enter a fixed $100 bet
- Edge comes from crowd wisdom: when 3+ skilled traders agree, the signal is strong
- **Preserves direction signal, destroys sizing signal**

### Portfolio Replication (new)
- Allocate capital proportionally across all pool traders
- For each trader, replicate their actual position sizing (volume-weighted)
- Your PnL per trader = `(your_allocation / trader_volume) * trader_pnl`
- **Preserves both direction and sizing signals**

## Head-to-Head Comparison

| Metric | Consensus Copy | Portfolio Replication |
|--------|----------:|----------:|
| Monthly Sharpe | 3.5 - 5.0 (best configs) | ~1.0 |
| Bets/month | 20-65 | 200-800 (full portfolio) |
| Total PnL (9 months) | +$2,901 (best config) | +$6,195 ($1,500 capital) |
| Capital efficiency | Very high per bet | Moderate (full deployment) |
| Execution complexity | Moderate (detect consensus) | High (track all positions) |
| Sizing requirement | Fixed $100/bet | Proportional (varies) |

## Why Both Exist

**Consensus copy** is better for:
- Small capital (<$500): fixed $100 bets are practical
- Low maintenance: fewer positions to manage
- Higher per-bet conviction: only acts on strong agreement

**Portfolio replication** is better for:
- Larger capital ($1,500+): can allocate across 30-40 traders
- Capturing sizing alpha: the 2.4x winner/loser volume asymmetry
- Compounding: reinvesting into a growing pool
- Robustness: diversification across many traders reduces variance

## Optimal Configuration (Portfolio Replication)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Allocation | Equal-weight (1/N) | Beats Kelly (see insight #09) |
| Pool filter | 9m consistent, pure_taker, entry<=0.80 | Removes near-certainty traders |
| Contradiction handling | Skip contradicted markets | +22% over copy-all (see insight #10) |
| Rebalance frequency | Monthly | Matches training window cadence |
| Compounding | Yes | +413% vs +188% flat over 9 months |

## The Strategies Are Combinable

Consensus copy and portfolio replication operate on different dimensions:
- **Consensus copy** is a signal filter (which markets to enter)
- **Portfolio replication** is a capital allocation method (how much to deploy per trader)

A hybrid approach: use consensus agreement as an additional filter within portfolio replication. When >= 3 traders agree AND your allocation formula says to enter, deploy with higher conviction. When only 1 trader is in a market, deploy with baseline allocation.
