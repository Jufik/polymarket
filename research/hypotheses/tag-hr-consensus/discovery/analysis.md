# Tag-HR-Consensus: Discovery + Tick Validation Results

> **TICK-VALIDATED** (SyncReplayRunner, not upper bounds).
> Test period: 2025-07-01 to 2026-03-01 (8 months).
> Train cutoff: 2025-07-01.

## Hypothesis

Same qualified pools as tag-hr-copy (Esports, Tennis), but fire entry intent only
when N distinct qualified traders have entered the same market. This replicates what
the vectorized sweep actually measured, fixing the individual-vs-consensus signal mismatch
that caused tag-hr-copy's 21-32pp HR collapse.

## Key Fix from tag-hr-copy

| Aspect | tag-hr-copy (FAILED) | tag-hr-consensus |
|--------|---------------------|------------------|
| Signal unit | Individual trade | N-trader consensus |
| Vectorized counting | Market-level (implicit N) | Market-level (explicit N) |
| Tick trigger | First qualified trade | Nth qualified trade |
| Expected vec-to-tick gap | 20-40pp (structural) | <10pp (aligned) |
| Actual gap | 21-32pp | 3-16pp |

## Vectorized Discovery (UPPER BOUNDS)

Sweep: 2 tags x 3 pool sizes x 5 consensus levels x 3 directions = 90 configs per fold.
5 walk-forward folds.

### Top Configs (by excess HR, >= 50 sigs, >= 3 folds)

| Tag | Config | Folds | Sigs | HR | Excess | med PnL | Hold | CS |
|-----|--------|-------|------|----|--------|---------|------|----|
| Esports | K100_N5_YES | 3 | 149 | 71.8% | +24.1pp | $54.00 | 3.0h | 105.4 |
| Esports | K25_N4_YES | 3 | 64 | 71.2% | +23.5pp | $41.10 | 2.9h | 79.9 |
| Esports | K50_N5_YES | 3 | 58 | 70.0% | +22.3pp | $51.53 | 3.0h | 93.2 |
| Esports | K25_N3_YES | 3 | 142 | 68.4% | +20.7pp | $19.10 | 3.0h | 31.4 |
| Esports | K50_N3_YES | 3 | 283 | 67.3% | +19.6pp | $14.23 | 3.1h | 21.9 |
| Esports | K100_N4_YES | 3 | 288 | 66.9% | +19.2pp | $35.72 | 3.1h | 52.8 |
| Tennis | K50_N2_NO | 3 | 296 | 74.0% | +12.0pp | $12.16 | 3.9h | 9.0 |
| Tennis | K25_N2_NO | 3 | 98 | 73.4% | +11.4pp | $16.52 | 3.9h | 11.7 |

> [!WARNING] Vectorized results are UPPER BOUNDS. Expected 10-20pp degradation (reduced from
> normal 20-40pp because vectorized and tick now measure the same consensus signal).

### Key Observations from Vectorized Sweep

1. **Esports YES dominates**: All top configs are Esports YES with consensus N=3-5
2. **Pool size matters less than consensus threshold**: K=25 vs K=100 gives similar HR
3. **NO direction has long hold times**: 38-44h vs 3h for YES
4. **Tennis YES is weak**: barely above base rate, negative PnL
5. **Only 3 folds have YES-qualified Esports traders** (early folds have zero pool)

## Tick-by-Tick Validation

Three configs validated:

### Results Table

| Config | Vec HR | Tick HR | Gap | Market Base | **Market Excess** | PnL | Sharpe | Fills | Hold |
|--------|--------|---------|-----|-------------|-------------------|-----|--------|-------|------|
| Esports YES K50 N3 | 67.3% | 64.0% | -3.3pp | 46.3% | **+17.7pp** | $14,772 | 9.65 | 297 | 3.8h |
| Esports YES K100 N4 | 66.9% | 64.2% | -2.7pp | 46.3% | **+17.9pp** | $5,968 | 10.98 | 123 | 3.7h |
| Tennis NO K50 N2 | 74.0% | 56.0% | -18.0pp | 56.9% | **-0.9pp** | $29,146 | 2.29 | 323 | 4.2h |

### Compounding Scores (market-level base rate)

| Config | Excess HR | Avg Edge | Hold Days | CS |
|--------|-----------|----------|-----------|-----|
| Esports YES K50 N3 | +0.177 | $49.74 | 0.16 | 55.0 |
| Esports YES K100 N4 | +0.179 | $48.52 | 0.15 | 56.6 |
| Tennis NO K50 N2 | -0.009 | $90.24 | 0.18 | **negative** |

## Critical Base Rate Analysis

> [!CRITICAL]
> **Tennis NO is a FALSE POSITIVE**. The position-level NO base rate (36.5%) makes 56.0% HR
> look like +19.5pp excess. But the market-level NO win rate is 56.9%. Against the correct
> baseline, there is ZERO edge (-0.9pp). The positive PnL ($29K) comes from asymmetric payoffs
> at low entry prices, not prediction quality.

This is the same `excess_hr_vs_absolute_hr` trap documented in
`pitfalls/excess_hr_vs_absolute_hr.md`:

| Base Rate | Level | Tennis NO Excess |
|-----------|-------|-----------------|
| 36.5% | Position-level (avg NO holder) | +19.5pp (MISLEADING) |
| 56.9% | Market-level (random NO bet) | -0.9pp (REAL) |

**Esports YES is GENUINE**: market-level YES base is 46.3%, tick HR is 64.0% = +17.7pp real edge.

## Monthly Breakdown (Esports YES K50 N3)

| Month | Fills | HR | PnL |
|-------|-------|-----|-----|
| 2025-07 | 36 | 66.7% | $2,698 |
| 2025-08 | 89 | 62.9% | $4,444 |
| 2025-09 | 65 | 61.5% | $4,105 |
| 2025-10 | 23 | 65.2% | $1,095 |
| 2025-11 | 10 | 70.0% | -$68 |
| 2025-12 | 24 | 70.8% | $926 |
| 2026-01 | 29 | 51.7% | $241 |
| 2026-02 | 21 | 76.2% | $1,332 |

**7 of 8 months profitable**. Jan 2026 is weakest (51.7% HR) but still positive PnL.
Consistent signal across the full test period.

## Verdicts

| Config | Verdict | Reason |
|--------|---------|--------|
| **Esports YES K50 N3** | **VALIDATED** | +17.7pp market excess, Sharpe 9.65, 297 fills, 3.8h hold, CS=55 |
| **Esports YES K100 N4** | **VALIDATED** | +17.9pp market excess, Sharpe 10.98, 123 fills, CS=57 (but low fill count) |
| Tennis NO K50 N2 | **REJECTED** | Zero market-level excess (-0.9pp). Positive PnL from asymmetric payoffs only. |

## Comparison with Existing Portfolio

| Strategy | Tag | Dir | N | Tick HR | Excess (mkt) | PnL | Sharpe | Fills/8mo |
|----------|-----|-----|---|---------|-------------|-----|--------|-----------|
| Sports YES v3 K25 N2 | Sports | YES | 2 | 70.0% | +30pp | $175K | 7.00 | 612 |
| Politics NO v3 K100 N2 | Politics | NO | 2 | 86.7% | +9.3pp | $18K | 5.23 | 197 |
| **Esports YES K50 N3** | **Esports** | **YES** | **3** | **64.0%** | **+17.7pp** | **$14.8K** | **9.65** | **297** |
| Esports YES K100 N4 | Esports | YES | 4 | 64.2% | +17.9pp | $6.0K | 10.98 | 123 |

**Esports YES K50 N3 is a strong addition**:
- Different tag than existing portfolio (Esports vs Sports/Politics) = genuine diversification
- Sharpe 9.65 is the highest in the portfolio
- Short hold times (3.8h) enable fast capital recycling
- 297 fills/8mo is good throughput

## Recommended Configuration for Production

```toml
# Esports YES consensus strategy
[strategy.esports_yes_consensus]
tag = "Esports"
direction = "YES"
pool_size = 50
consensus_threshold = 3
pool_method = "scorecard_v3"
beh_gate = 0.02
max_position_usd = 100
```

## Artifacts

- Vectorized sweep: `research/hypotheses/tag-hr-consensus/scripts/sweep_consensus.py`
- Tick validation: `research/hypotheses/tag-hr-consensus/scripts/tick_validate.py`
- Vectorized results: `research/hypotheses/tag-hr-consensus/discovery/results.json`
- Tick results: `research/hypotheses/tag-hr-consensus/discovery/tick_results.json`
- Ledgers: `research/output/ledger_thc_*.parquet`

## Spawned Ideas

1. **esports-consensus-robustness** [MEDIUM]: Test K=25 N=2 and K=100 N=3 to map the
   full parameter sensitivity. The validated configs are close together (64.0% vs 64.2%)
   suggesting the edge is robust to parameter choice.

2. **esports-portfolio-integration** [HIGH]: Add Esports YES to the 2-track portfolio
   (Sports YES + Politics NO). Near-zero correlation expected (different tag, different
   market dynamics).

3. **esports-subgame-decomposition** [MEDIUM]: Break Esports into CS2, Dota2, LoL,
   Valorant. Per-game pools may improve signal quality.
