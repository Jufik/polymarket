# Hypothesis: Tag-Isolated Hit-Rate Copy

**Status**: `rejected`
**Created**: 2026-03-05
**Category**: esports, 1h (crypto), tennis (sports)

## Statement

Traders who consistently outperform their tag-specific YES base rate (by >15pp excess HR over
>=50 qualifying markets) produce a copyable BUY signal when they enter YES positions in their
qualified tag, with entries filtered to max avg entry price <= 0.75.

## Success Criteria

- Excess HR > 15pp above tag base rate (Esports: 34.3%, 1H: 50.7%, Tennis: 30.1%)
- Positive PnL after realistic slippage
- Compounding score > 5
- Sample size > 100 trades OOS

## Scores

| Metric | Vectorized (UB) | Tick-by-Tick | Degradation |
|--------|----------------|-------------|-------------|
| **Esports HR** | 67.2% (+35.7pp excess) | 45.8% (+10.9pp excess) | -21.4pp |
| **1H HR** | 78.0% (+27.3pp excess) | 49.8% (+2.5pp excess) | -28.2pp |
| **Tennis HR** | 72.4% (+33.6pp excess) | 40.6% (+10.5pp excess) | -31.8pp |
| **Esports Median PnL** | $8.13 | -$102.50 | collapsed |
| **1H Median PnL** | $4.01 | -$102.50 | collapsed |
| **Tennis Median PnL** | $2.40 | -$102.50 | collapsed |
| **Esports CS** | 34.87 | N/A | rejected |
| **1H CS** | 19.71 | N/A | dead |
| **Tennis CS** | 9.67 | N/A | marginal |
| **Esports Signals** | ~4,769/fold | ~452/year | much lower |
| **1H Signals** | ~5,009/fold | ~2,534/year | higher! |
| **Tennis Signals** | ~5,725/fold | ~271/year | much lower |

Vectorized params (optimal): Esports mt=50/ep=15/pc=0.75; 1H mt=50/ep=15/pc=0.75; Tennis mt=20/ep=15/pc=0.80

Tick-by-tick config: RealisticFillSimulator, $1,000 capital, $100/position, 2025-01 to 2026-01.

## Decision

Rejected as individual-trade copy strategy. All three tags show 20-32pp HR degradation and
deeply negative PnL (-$102.50 median on $100 positions). The structural cause is a
consensus gap: vectorized measured N-trader convergence; tick-by-tick copied individual trades.

1H is confirmed gambling (HR=49.8% vs base 47.3%). Esports and Tennis show marginal positive
excess but not sufficient for positive PnL after slippage.

Spawned: `tag-hr-consensus` [HIGH] — same pools, N-trader convergence trigger.

## Anti-Knowledge

What we learned from this failure:

- **Signal tested**: Individual qualified trader BUY YES entry in their qualified tag
- **Why it failed**: Vectorized discovery measured consensus signal (N traders in market), but
  execution copied individual trades. The vectorized HR was the HR of consensus-confirmed markets,
  not individual trader trade quality. Single-trader entry is near-random noise.
- **Conditions for revisiting**: Only viable with N-trader consensus filter (n >= 3) implemented
  in tick-by-tick strategy. Price floor (>= 0.55) also required to remove harmful low-price entries.
- **Generalizable lesson**: Any vectorized sweep with a `HAVING n_qualified >= N` filter measures
  consensus signal. Executing this as individual-trade copy is a structural mismatch. The execution
  must replicate the counting unit of the signal.

> Captured to:
> - `research/knowledge/pitfalls/individual_vs_consensus_signal.md` [CRITICAL]
> - `research/knowledge/pitfalls/1h_crypto_gambling.md` [WARNING]
> - `research/knowledge/pitfalls/training_window_lookahead.md` [CRITICAL]
> - `research/knowledge/pitfalls/phantom_test_signals.md` [CRITICAL]
> - `research/knowledge/pitfalls/entry_price_ceiling_tradeoff.md` [WARNING]
> - `research/knowledge/signals/price_regime_hr_correlation.md` [TIP]
> - `research/knowledge/data/1h_market_characteristics.md` [TIP]
