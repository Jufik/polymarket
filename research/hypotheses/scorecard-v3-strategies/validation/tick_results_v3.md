# Scorecard V3 — Tick-by-Tick Validation Results

> **TICK-VALIDATED** (SyncReplayRunner, not vectorized upper bounds).
> Per `knowledge/execution/live_infrastructure.md`: no additional latency penalty applied.
> Sub-second WS delivery in production — simulation fill model is the only degradation source.

Generated: 2026-03-09 10:27:04
Test period: 2025-07-01 – 2026-03-01 (8 months)

## Summary Table

| Leg | Config | Fills | Tick HR | Base Rate | **Excess HR** | PnL | Sharpe | Vectorized UB | v2 Baseline |
|-----|--------|-------|---------|-----------|--------------|-----|--------|--------------|-------------|
| Sports YES v3 | K=25 N=2 | 2023 | 63.3% | 33.3% | **+30.0%** | $162,157.42 | 5.23 | +53.4% | +39.8% |
| Politics NO v3 | K=100 N=2 | 347 | 83.0% | 73.6% | **+9.3%** | $33,941.52 | 0.55 | +8.8% | first |
| Sports InPlay v3 | K=25 N=1 | 5936 | 60.2% | 33.3% | **+26.9%** | $9,991.97 | 0.27 | +34.2% | first |

---

## Leg 1: Sports YES K=25 N=2 (v3 BEH-gated)

**Vectorized UB**: 86.7% HR, +53.4pp excess | **v2 baseline**: +39.8pp tick, 612 fills, Sharpe=11.94

| Metric | Value |
|--------|-------|
| Total fills | 2023 |
| Hit rate | 63.3% |
| Sports YES base rate (test) | 33.3% |
| **Excess HR** | **+30.0%** |
| Net PnL | $162,157.42 |
| Sharpe | 5.23 |
| Max drawdown | $4,566.56 |
| Profit factor | 3.18 |
| Avg hold | 6.90h |
| % fills < 1h (in-play proxy) | 0.4% |
| % fills < 4h | 52.2% |
| Vectorized UB excess | +53.4pp |
| v2 tick excess (K=25 N=3) | +39.8pp |
| Degradation from vectorized | -23.4% |

**Key changes vs v2**: BEH gate (bucket_excess_hr >= 0.02) removes near-certainty traders. N=2 vs N=3 lowers consensus bar.

---

## Leg 2: Politics NO K=100 N=2 (first NO-direction tick validation)

**Vectorized UB**: 81.8% HR, +8.8pp excess | **First NO-direction strategy ever tick-validated**

| Metric | Value |
|--------|-------|
| Total fills | 347 |
| Hit rate | 83.0% |
| Politics NO base rate (test) | 73.6% |
| **Excess HR** | **+9.3%** |
| Net PnL | $33,941.52 |
| Sharpe | 0.55 |
| Max drawdown | $1,068.96 |
| Profit factor | 6.75 |
| Avg hold | 1308.33h |
| % fills < 1h | 0.0% |
| % fills < 24h | 19.1% |
| Vectorized UB excess | +8.8pp |
| Degradation from vectorized | +0.6% |

**Note**: Small vectorized excess (+8.8pp) — even a modest degradation may wipe the edge.
Positive tick excess required for this leg to be viable.

---

## Leg 3: Sports In-Play YES K=25 N=1 (RT track)

**Vectorized UB**: 67.5% HR, +34.2pp excess, hold<4h | **RT infra**: sub-second WS, no latency penalty

| Metric | Value |
|--------|-------|
| Total fills | 5936 |
| Hit rate | 60.2% |
| Sports YES base rate (test) | 33.3% |
| **Excess HR** | **+26.9%** |
| Net PnL | $9,991.97 |
| Sharpe | 0.27 |
| Max drawdown | $13,780.02 |
| Avg hold | 13.54h |
| % fills < 1h | 0.9% |
| % fills < 4h | 37.0% |
| Vectorized UB excess | +34.2pp |
| Degradation from vectorized | -7.3% |

**RT infrastructure note**: Degradation comes from fill model only.
Production WS latency (~50ms) << elite trader lead time (58 min median).

---

## Vectorized vs Tick Comparison

| Leg | Vectorized UB | Tick | Degradation | Viable? |
|-----|--------------|------|-------------|---------|
| Sports YES K=25 N=2 | +53.4pp | +30.0% | -23.4% | YES — strong |
| Politics NO K=100 N=2 | +8.8pp | +9.3% | +0.6% | MARGINAL — monitor |
| Sports InPlay K=25 N=1 | +34.2pp | +26.9% | -7.3% | YES — strong |

---

## Artifacts

- Script: `research/hypotheses/scorecard-v3-strategies/scripts/run_tick_v3.py`
- JSON: `research/hypotheses/scorecard-v3-strategies/validation/tick_results_v3.json`
- Ledgers: `research/output/ledger_v3_*.parquet`
- Log: `tmp/tick_v3.log`
