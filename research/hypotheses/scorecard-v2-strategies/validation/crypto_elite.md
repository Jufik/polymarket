# Crypto Elite Validation — K=50, N=2, HR-Only Ranked, YES-Only

**Strategy**: `crypto_hr_k50_n2`
**Pool**: Top-50 Crypto traders by excess HR (train cutoff 2025-07-01)
**Consensus threshold**: N=2 distinct pool traders buying YES
**Direction filter**: YES-only
**Test period**: 2025-07-01 onward (19,261 Crypto tag markets)
**Capital config**: $10,000 capital, $500/position, max 20 open positions

## Headline Results

| Metric | Value |
|--------|-------|
| Fills | 478 |
| Hit Rate | **82.6%** |
| Net PnL | **$47,358** |
| Sharpe | 0.61 |
| Max Drawdown | $7,587 |
| Profit Factor | 2.14 |
| Avg Hold | 171.0h |
| Median Hold | 3.1h |
| Excess HR (vs base rate 15%) | **+67.5pp** |
| Vectorized excess HR | +72pp (UB) |
| Degradation | -4.5pp |

## Key Finding: Structural Price=0.99 Bias

The headline 82.6% HR is **misleading**. 67.5% of fills (322/477) occurred at fill price=0.99 — these are markets where YES is already near-certain. This is the primary source of high HR and must be treated as an artifact.

### Price-Bucketed Analysis

| Price Range | N | HR | PnL | Med Hold | Interpretation |
|-------------|---|----|----|---------|----------------|
| =0.99 | 322 | 95.0% | -$6,455 | 2.2h | **In-play / near-certain** |
| 0.90-0.99 | 24 | 75.0% | -$2,444 | 18.9h | Mostly resolved |
| 0.70-0.90 | 33 | 75.8% | -$950 | 58.2h | Likely resolved |
| 0.50-0.70 | 28 | 64.3% | +$994 | 69.7h | Genuine uncertainty |
| 0.30-0.50 | 32 | 43.8% | +$1,908 | 87.1h | Genuine uncertainty |
| 0.10-0.30 | 24 | 33.3% | +$8,930 | 123.1h | Genuine uncertainty |
| <0.10 | 14 | 35.7% | +$45,374 | 14.1h | Very uncertain |

**Critical insight**: The PnL at price=0.99 is **-$6,455** despite 95% HR, because:
- Win = $500 × (1/0.99 - 1) = ~$5 per fill
- Loss = -$500 per fill
- Even at 95% HR: 0.95×$5 - 0.05×$500 = -$20.25 expected value per fill

At price=0.99, the pool traders buying YES are not generating alpha — they're trading near-resolved markets. These are not copyable in practice (the market is already known).

### Genuine Uncertainty Signals (price < 0.70)

These 98 fills represent the **true alpha**:

| Metric | Value |
|--------|-------|
| N signals | 98 |
| HR | 45.9% |
| Excess HR | +30.9pp (vs 15% base rate) |
| Total PnL | **$57,206** |
| Avg entry price | 0.361 |
| Med hold | 64.1h |

All net positive PnL in the strategy comes from these 98 genuine signals. The 379 near-certain fills collectively lose $8,898 despite 90%+ HR.

## Hold Time Distribution

```
<1h:   43 fills (9.0%) — HR=97.7%, but avg_entry=0.99 (near-certain)
1-6h: 286 fills (60%)  — HR=94.8%, avg_entry=0.96 (mostly near-certain)
6-24h: 38 fills (8%)   — HR=68.4%, avg_entry=0.64 (genuine)
1-7d:  60 fills (13%)  — HR=70.0%, avg_entry=0.60 (genuine)
>7d:   50 fills (11%)  — HR=26.0%, avg_entry=0.51 (long markets, no edge)
```

The **median hold of 3.1h** is driven entirely by price=0.99 fills (med_hold=2.2h). The genuine uncertainty signals have a median hold of 64h.

The <1h rate (9%) does NOT indicate post-resolution noise — these are in-play markets where the outcome was known to insiders before official settlement.

## Monthly Breakdown (Genuine Signals Only)

| Month | N | HR | PnL |
|-------|---|----|-----|
| 2025-07 | 27 | 44.4% | +$1,515 |
| 2025-08 | 14 | 50.0% | +$13,219 |
| 2025-09 | 5 | 40.0% | +$14,913 |
| 2025-10 | 8 | 62.5% | +$3,819 |
| 2025-11 | 15 | 66.7% | +$27,237 |
| 2025-12 | 5 | 20.0% | -$1,458 |
| 2026-01 | 6 | 83.3% | +$2,152 |
| 2026-02 | 3 | 33.3% | -$694 |

November 2025 is a large outlier month ($27k PnL from 15 fills) — likely one or two large-edge markets. Genuine signal rate is ~8-27 signals/month post July 2025 (when test period is densely covered).

## Degradation Analysis

| Scenario | HR | Excess HR |
|----------|----|-----------|
| Vectorized (UB) | ~98.5% | +72pp |
| Tick (all fills) | 82.6% | +67.5pp |
| Tick (genuine, price<0.70) | 45.9% | +30.9pp |

The -4.5pp tick degradation vs vectorized (headline) is misleadingly small. The real degradation is 72pp → 30.9pp = **-41pp for genuine signals**. This matches the typical 20-40pp consensus gap described in `pitfalls/individual_vs_consensus_signal.md`.

## Root Cause: Price=0.99 Artifact

When pool traders buy YES at $0.99, the consensus threshold (N=2) fires on markets that have already effectively resolved:

1. **Crypto markets resolve quickly** — Bitcoin price questions, crypto election outcomes, etc. often have answers hours before the market formally closes.
2. **Pool traders know early** — They buy YES at $0.99 because they know the outcome.
3. **Strategy fires at $0.99** — The consensus signal fires but the market is already decided.
4. **This is NOT copyable** — By the time a retail trader sees the signal, they'd fill at $0.99 with ~$5 max win and $495 max loss.

## Compounding Score

Using genuine signals only (price < 0.70):
- Excess HR: +30.9pp
- Avg edge per fill: ~$583 (PnL $57,206 / 98 fills)
- Median hold: 64h = 2.67 days
- **Compounding score: (0.309 × 583) / 2.67 = 67.5**

This is a very strong score, but requires a **price ceiling filter at 0.70** to exclude in-play artifacts.

## Recommendation

**DO NOT deploy as-is.** The strategy needs a `max_price` ceiling of 0.65-0.70 to filter out in-play fills.

**With price ceiling added**:
- Expected fills: ~8-27/month (genuine signal rate)
- Expected HR: ~45-66% (across genuine signals)
- Expected excess HR: +30-50pp
- PnL is real: $57,206 from genuine signals alone

### Immediate Next Step

Rerun with `max_price=0.65` in the strategy config to exclude near-certain fills. This will:
1. Reduce fill count from 478 to ~98
2. Reduce headline HR from 82.6% to ~45-50%
3. Maintain all the PnL ($57k is from genuine signals)
4. Make the signal actually copyable

## Data Quality Notes

- Resolution field: `WON`/`LOST` (not `WIN`/`LOSS`)
- 1 fill pending (1/478) — unresolved market in test period
- 1h bucket (43 fills): all at price=0.99, these are NOT post-resolution noise but in-play near-certainty trades
- The vectorized 98.5% HR was dominated by the same price=0.99 effect

## Files

- Script: `research/hypotheses/scorecard-v2-strategies/scripts/validate_crypto.py`
- Ledger: `research/output/ledger_crypto_hr_k50_n2.parquet`
- Analysis scripts: `tmp/crypto_analysis2.py`, `tmp/crypto_analysis3.py`
