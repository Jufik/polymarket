# SOL Up/Down GBM Scalp Viability Analysis
**Date**: 2026-03-10
**Status**: Discovery
**Hypothesis folder**: crypto-gbm-improvements

---

## VERDICT: MARGINAL — TICK VALIDATION REQUIRED

### GO Factors
- Rich market universe: 35,649 SOL markets (84% of BTC's 42,470)
- SOL median market volume $4,791 — adequate for $50 fills (vs BTC $46,640 — 10x thinner)
- Mid-window momentum HR 80.7% — SOL price direction is persistent over 15-min window
- Up/Down win rate 50.2% — symmetric, no structural bias

### NO-GO Factors
- SOL vol is 1.64x BTC — threshold must be 0.164 (vs 0.10), signal frequency ~40% lower
- SOL-BTC correlation 0.762 — high concurrent-fire risk, near-zero diversification benefit
- SOL markets 10x thinner than BTC ($4.8k vs $46.6k median volume) — position size cap ~$25
- At $25 notional with 3% fee, break-even EV = $0.75/trade — hard to clear on thinner markets
- Vectorized signal quality not directly measured (no PM tick data in DuckDB snapshot for SOL)

---

## 1. Market Structure

| Metric | Value |
|--------|-------|
| Total SOL Up/Down markets | 35649 |
| With non-empty winner_outcome | ~22,056 (Up: 11,072, Down: 10,982) |
| Up wins (out of resolved Up/Down) | 11,072 / 22,054 = 50.2% |
| Note: 13,593 have empty winner_outcome (unresolved/pending) | — |
| Date range | 2025-03-31 → 2026-03-05 |
| Avg positions/market | 82.2 |
| Median positions/market | 76.0 |

### Window Distribution

| Window | N Markets | N Resolved | Up Win Rate |
|--------|-----------|-----------|-------------|
| 5.0min | 15,907 | 2,992 | 50.5% |
| 15.0min | 12,379 | 11,887 | 49.5% |
| 60.0min | 6,275 | 6,126 | 51.5% |
| 240.0min | 758 | 727 | 50.1% |
| 180.0min | 1 | 1 | 100.0% |
| 1395.0min | 1 | 1 | 0.0% |

**Note**: Window sizes extracted from question text (e.g. "5:00PM-5:15PM ET" = 15-min window).
Up Win Rate ≈ 50% expected for symmetric Up/Down markets.

---

## 2. Volatility Profile: SOL vs BTC

| Metric | SOL | BTC | Ratio |
|--------|-----|-----|-------|
| Overall sigma_1m | 0.001133 | 0.000692 | 1.64x |
| Rolling 24h σ (p25) | 0.000786 | 0.000432 | 1.82x |
| Rolling 24h σ (p50) | 0.000984 | 0.000585 | 1.68x |
| Rolling 24h σ (p75) | 0.001217 | 0.000780 | 1.56x |
| Rolling 24h σ (p95) | 0.001740 | 0.001078 | 1.61x |

### GBM Model Implication: d₂ = ln(S_t/S₀) / (σ√(T-t))

At 10% PM price lag, T=7.5min remaining (midpoint of 15-min window):

| Asset | sigma_1m | d₂ | P(Up) |
|-------|---------|-----|-------|
| SOL | 0.001133 | 32.2 | 1.0000 |
| BTC | 0.000692 | 52.8 | 1.0000 |

**Key insight**: Both d₂ values are very large (>>3), meaning P(Up) ≈ 1.0 for both assets at 10% lag.
This is because 1-minute sigma is tiny compared to a 10% price lag: even a 10% move in 7.5 minutes
is astronomically unlikely under GBM → d₂ → ∞ → P(Up) → 1.

**What this means for the strategy**: The GBM threshold (0.10) is calibrated relative to the PM price
(a probability), NOT the underlying asset price. A 10% PM price lag at price 0.45 means PM says 45%
but GBM says 55% — the signal comes from PM mispricing, not from asset price deviation.
The asset price feeds into GBM only through the log-return component.

For the ACTUAL signal mechanism:
- SOL moves 1.64x more than BTC per minute
- For a given SOL log-return, σ(SOL) is larger → d₂(SOL) is SMALLER → GBM is LESS confident
- This means: at the same asset price movement, GBM fires weaker signals for SOL
- Equivalently: SOL needs a LARGER price move to reach the same GBM confidence as BTC

**Recommended SOL threshold**: `0.164` (scale 0.10 × 1.64 vol ratio)

---

## 3. GBM Signal Quality (Last 90 Days, Resolved Markets)

> **CRITICAL METHODOLOGY NOTE**: This vectorized check measures mid-window asset price momentum,
> NOT the actual BTC GBM strategy signal. The live strategy fires when the **Polymarket price lags
> GBM fair value**. Without PM tick data in this vectorized check, we cannot reconstruct the actual
> signal. What we measure instead: "If SOL is up at T/2, does it end up at T?" — pure price momentum.
>
> The 73-80% hit rates below reflect **SOL 7.5-minute momentum persistence** (strong!), not
> GBM-vs-PM-price divergence calibration. The actual strategy HR will differ based on PM market maker
> responsiveness and the PM lag behavior in SOL markets.

- **Markets analyzed**: 1,000 (last 90 days, resolved)
- **Valid GBM computations**: 1,000
- **Up base rate (actual)**: 51.4% (symmetric — good)
- **Signal frequency** (|lag|>=0.10): 67.4%
  - Note: With d₂>>0 at all nonzero mid-window returns, this measures P(nonzero return at T/2)
- **Signals Up** (p_up>0.60 = SOL up at midpoint): n=362, momentum HR: 73.5%
- **Signals Down** (p_up<0.40 = SOL down at midpoint): n=312, momentum HR: 76.3%

### Mid-Window Momentum Calibration (UPPER BOUND — misses PM lag dynamics)

| Threshold | N Signals | Hit Rate | Excess vs 50% |
|-----------|-----------|----------|---------------|
| 0.05 | 825 | 72.4% | +22.4% |
| 0.08 | 730 | 73.3% | +23.3% |
| 0.10 | 674 | 74.8% | +24.8% |
| 0.12 | 605 | 76.9% | +26.9% |
| 0.15 | 525 | 77.1% | +27.1% |
| 0.20 | 403 | 80.7% | +30.6% |

**Interpretation**: SOL shows strong momentum persistence (73-80%) — once SOL moves in a direction
in the first half of the window, it keeps going. This is a positive signal for the strategy's
underlying mechanics, but the actual strategy fires only when PM has NOT already repriced (lag exists).
The BTC tick results show 96.2% convergence rate — PM consistently lags BTC. Whether SOL PM markets
are equally slow to reprice requires tick validation.

### Lag Distribution

| Percentile | |P(Up)-0.5| |
|------------|-----------|
| p10 | 0.031 |
| p25 | 0.070 |
| p50 | 0.162 |
| p75 | 0.282 |
| p90 | 0.419 |

p50 lag = 0.162 → median market has GBM P(Up) of 0.66 or 0.34 at midpoint.
This reflects the magnitude of SOL's typical mid-window move (high vol → larger displacements).


---

## 4. Liquidity Check

### Market Volume Distribution (USDC per market, sum across all positions)

| Percentile | SOL | BTC |
|------------|-----|-----|
| p10 | $1130.9 | $4589.63 |
| p25 | $2414.6 | $22884.77 |
| p50 | $4790.72 | $46640.32 |
| p75 | $8312.65 | $76265.77 |
| p90 | $13772.07 | $124745.82 |

### Aggregate Participation

| Metric | SOL | BTC |
|--------|-----|-----|
| Markets with positions | 22043.0 | 28861.0 |
| Total positions | 1812086.0 | 9311010.0 |
| Avg positions/market | 82.2 | 322.6 |
| Median position volume | $6.79 | $10.00 |

### Can we get $50 fills?
YES — SOL median market volume $4,791 per market is sufficient for a $50 fill as a fraction of total.
**However**: $50/$4,791 = 1.04% of total market volume. At BTC it's $50/$46,640 = 0.11%.
SOL fills represent a much larger fraction of the book → higher market impact and slippage.
A single $50 order could materially move the SOL market price.
**Recommended max position**: $25 (0.5% of median market, comparable to BTC's footprint).

SOL/BTC volume ratio: 0.10x (SOL markets are 10x thinner than BTC markets)

---

## 5. SOL-Specific Risks

### Volatility and Gap Risk

| Risk Factor | SOL | BTC | Assessment |
|-------------|-----|-----|------------|
| Overall vol (sigma_1m) | 0.001133 | 0.000692 | SOL 1.6x higher |
| P(1m gap > 5%) | <0.001% | <0.001% | LOW under Gaussian (fat tails exist) |
| P(1m gap > 10%) | <0.000001% | <0.000001% | Extremely rare under Gaussian |
| SOL-BTC correlation | 0.762 | 1.000 | HIGH co-fire risk |

### Structural Risk Analysis

1. **Signal attenuation**: SOL's 1.64x higher vol means the GBM model assigns lower P(Up)
   for the same asset price log-return (d₂ is smaller). The threshold (0.10) in the strategy
   refers to the **PM probability mismatch** (e.g. PM says 0.40, GBM says 0.55 → lag=0.15).
   Higher SOL vol means: for a given PM lag, the underlying asset move required to produce that
   GBM probability is larger. This effectively means GBM is less "surprised" by PM mispricing
   in volatile markets, and we need a larger PM lag to get the same confidence level.
   Recommended: threshold = `0.164` vs BTC's `0.100`.
   Note: The BTC strategy uses GBM to interpret PM mispricing. If SOL's PM market makers are
   equally sluggish, the absolute PM lag may be larger (more opportunity), partially compensating.

2. **Gap-through stops**: Trailing stop at 0.05 PM gap. With higher underlying vol,
   the PM price can move through the stop in a single second before execution.
   Recommend widening trailing_stop_gap to 0.08 for SOL.

3. **Market maker coverage**: SOL markets have 82.2 avg positions/market
   vs BTC's 322.6.
   Fewer MMs → wider spreads → higher effective entry cost.

4. **Correlated drawdowns**: SOL-BTC correlation 0.76.
   When BTC experiences a sharp move triggering the GBM strategy, SOL likely moves similarly.
   Both strategies fire simultaneously. Portfolio variance is nearly additive (no diversification benefit).

5. **Market creation pace**: 35,649 SOL markets vs 42,470 BTC markets — SOL has ~84% of BTC's
   market count. The opportunity set is comparable in size.

---

## 6. Parameter Recommendations (if proceeding)

| Parameter | BTC Config | SOL Recommendation | Rationale |
|-----------|-----------|-------------------|-----------|
| `primary_symbol` | BTC-USDT | SOL-USDT | Match to asset |
| `threshold` | 0.100 | `0.164` | Scale by vol ratio (1.64x) |
| `base_bet_usd` | $50.00 | $25.00 | Thinner markets |
| `trailing_stop_gap` | 0.050 | 0.080 | Higher vol → wider gap needed |
| `gbm_flip_threshold` | 0.350 | 0.300 | SOL flips faster |
| `min_time_remaining_min` | 1.5 | 2.0 | Higher vol → late entries riskier |
| `sigma_lookback_min` | 1440 | 1440 | No change |
| `min_gbm_deviation` | 0.050 | 0.050 | No change |

### Expected EV Range (Rough Estimate)

Starting from BTC baseline: +$2.10/trade at $50 = +4.2% per trade.

Adjustments:
- **Half position size** ($25): EV scales to ~$1.05/trade (upper bound)
- **Higher threshold** (0.164 vs 0.10): signal frequency reduced proportionally to vol ratio
- **Signal attenuation**: GBM model weakly calibrated at high vol → expect 20-40% HR reduction
- **Liquidity friction**: Wider spreads in thinner SOL markets → expect 0.5-1.0% additional cost per trade
- **Gap risk**: est 0.01% P(5% gap) → rare but severe adverse fills

**Conservative EV estimate**: $0.30-$0.70/trade at $25 notional (before fees).
At 3% fee on $25 position ≈ $0.75 cost → this strategy may be **below break-even** on SOL.

---

## 7. Recommendation

### MARGINAL — TICK VALIDATION REQUIRED

**Summary**: The GBM model is technically applicable to SOL (same formula, same market structure),
but the operational context is meaningfully worse than BTC:

1. **Higher vol** reduces signal quality and requires threshold adjustment
2. **Thinner markets** limit position size to $25 max
3. **High correlation** with BTC strategy eliminates diversification benefit
4. **Lower EV** per trade may be below break-even after fees at $25 notional

**Key question for GO/NO-GO**: At $25 position and threshold=0.164,
is EV still positive after 3% PM fee (~$0.75)? The BTC strategy at $50 barely clears this bar.
SOL at half the size needs the same absolute EV, which is unlikely.

**Blockers:**
- SOL vol is 1.6x BTC — threshold up 63%, marginal signal quality
- SOL-BTC correlation 0.76 — high concurrent-fire risk with BTC strategy

**Required to flip to GO:**
1. Tick-level validation on 30-day SOL universe with threshold=0.164, base_bet=$25
2. Confirm positive EV after fees (need >$0.75/trade median at $25 notional)
3. Measure actual fill quality vs $50 ideal fill
4. Cap SOL allocation at 25% of crypto GBM capital (correlation constraint)

---

*Results are UPPER BOUNDS based on vectorized/historical analysis. Tick-by-tick validation required.*
