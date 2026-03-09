# Microstructure Calibration Upgrade — Discovery Analysis

> **TL;DR**: The fill model is NOT the bottleneck. It contributes ~0pp of the 20-40pp vectorized-to-tick HR gap and at most 1-13% of PnL degradation (depending on calibration). The upgrade is a nice-to-have for PnL accuracy but will NOT close the simulation fidelity gap.

> [!WARNING]
> Do NOT pursue this as a priority. The vectorized-to-tick gap is driven by consensus dedup, SELL filtering, capital constraint, and look-through bias — not fill model inadequacy.

## 1. Current Fill Model Audit

### Architecture

Three executors exist, all implementing the `Executor` protocol:

| Executor | Fill Price | Slippage | Used By |
|----------|-----------|----------|---------|
| `SimulatedExecutor` | `max_price` or 0.50 | Zero | `run_fast_backtest()`, SyncReplayRunner |
| `RealisticFillSimulator` | `max_price` (same!) | `(half_spread + impact) * size_usd` added to `fee_usd` | `run_backtest()` with `fill_config` |
| `PaperExecutor` | WS orderbook / CLOB REST | None | Paper trading |

**Critical observation**: All three executors fill at the SAME price. The RealisticFillSimulator does NOT adjust the fill price — it adds slippage cost to `fee_usd`. This means the fill model has **zero effect on hit rate** and only affects net PnL through the fee channel.

### Current Calibration (`calibrate.py`)

Two methods for spread estimation:
1. **Median Absolute Change (MAC)**: `half_spread = median(|p_t - p_{t-1}|)` — simple, robust
2. **Roll (1984)**: `half_spread = sqrt(-cov(dp_t, dp_{t-1}))` — theoretically grounded

Impact model: `impact = size_usd / max(default_liquidity, vol * 0.01) * impact_scale`

**Defaults**: `fallback_half_spread=0.005`, `default_liquidity=5000`, `impact_scale=1.0`

### What `run_fast_backtest()` Actually Uses

The fast path (our validated pipeline) uses `SimulatedExecutor(fee_pct=0.0)` — **zero friction whatsoever**. The RealisticFillSimulator is only used in the legacy `run_backtest()` async path when `fill_config` is explicitly provided.

## 2. Trade Tape Measurements

### 2.1 Spread Estimates (Oct-Dec 2025, 101K markets with 20+ trades)

| Metric | Value |
|--------|-------|
| Markets analyzed | 101,659 |
| Median MAC half-spread | **0.01** (1 cent) |
| Average MAC half-spread | 0.011 |
| P90 MAC half-spread | 0.02 |
| P99 MAC half-spread | 0.08 |
| Avg trades/market | 894 |
| Median volume/market | $6,616 |

### 2.2 MAC vs Roll Estimator Comparison (Nov 2025, 36K markets)

| Estimator | Median | Average | Coverage |
|-----------|--------|---------|----------|
| MAC | **0.01** | 0.011 | 100% of markets |
| Roll | **0.191** | 0.188 | 98.1% of markets |
| Roll/MAC ratio | **17.4x** (median) | 34.7x (average) | — |

**Interpretation**: The Roll estimator is ~17x larger than MAC. This is because Roll captures ALL serial price variation (fundamentals + microstructure + noise), while MAC median is dominated by the 44% of trade pairs with zero price change. The true bid-ask spread lies between these bounds, closer to MAC for liquid markets.

### 2.3 Spread by Volume Tier

| Volume Tier | Markets | Median Spread | Average Spread |
|-------------|---------|--------------|----------------|
| <$1K | 20,460 | 0.000 | 0.011 |
| $1K-10K | 38,688 | 0.010 | 0.013 |
| $10K-100K | 35,681 | 0.010 | 0.010 |
| $100K-1M | 6,082 | 0.001 | 0.006 |
| $1M+ | 748 | 0.000 | 0.003 |

**Pattern**: Higher volume = tighter spreads. The relationship is monotonic. Liquid markets (>$100K) have half the spread of illiquid ones.

### 2.4 Spread by Tag Category (Nov 2025)

| Tag | Trades | Med Spread | Avg Spread | P75 | P90 |
|-----|--------|-----------|------------|-----|-----|
| Crypto | 15.5M | **0.010** | 0.150 | 0.15 | 0.63 |
| Weather | 173K | 0.002 | 0.189 | 0.12 | 0.94 |
| Politics | 3.1M | 0.001 | 0.173 | 0.06 | 0.87 |
| Sports | 5.6M | **0.000** | 0.113 | 0.04 | 0.51 |

**Sports has the tightest spreads** (median = 0), which is consistent with its high liquidity and rapid resolution. Crypto has the widest median spread (0.01), likely due to the Up/Down gambling markets with 1-cent tick structures.

### 2.5 Roll Estimator by Tag

| Tag | Markets | MAC Spread | Roll Spread | Roll Valid % |
|-----|---------|-----------|-------------|-------------|
| Weather | 519 | 0.008 | **0.252** | 99.8% |
| Politics | 3,081 | 0.014 | **0.219** | 97.2% |
| Crypto | 18,344 | 0.011 | **0.201** | 99.4% |
| Sports | 10,269 | 0.008 | **0.148** | 95.9% |

### 2.6 Price Change Frequency

| Price Change | % of Trade Pairs |
|-------------|-----------------|
| Zero (exact same price) | **43.8%** |
| 1 cent (0.01) | 14.7% |
| 1-5 cents | 13.1% |
| >5 cents | 28.4% |

The minimum tick size is 0.01 (1 cent), which is also the most common non-zero price change (44% of non-zero changes). The 44% zero-change rate explains why MAC median is so low — nearly half of consecutive trades execute at the same price.

### 2.7 Microstructure vs Fundamental Decomposition

| Gap Between Trades | Pairs | Med |dp|| Avg |dp|| P75 | P90 |
|--------------------|-------|-----------|-----------|-----|-----|
| <5 seconds | 3.8M | **0.010** | 0.153 | 0.18 | 0.62 |
| 1-10 minutes | 2.6M | **0.001** | 0.131 | 0.03 | 0.67 |

**Surprising**: Near-simultaneous trades (<5s) show LARGER spreads than spaced-out trades (1-10 min). This suggests that rapid-fire trading clusters (batch fills, taker sweeps) involve larger price jumps. The microstructure spread is at least 0.01 based on rapid trades.

## 3. Market Impact Analysis

### 3.1 Impact by Trade Size (Nov 2025)

| Size Bucket | Trades | Med |dp_next|| Avg |dp_next|| P75 |
|-------------|--------|----------------|----------------|-----|
| <$5 | 14.6M | 0.002 | 0.134 | 0.06 |
| $5-10 | 4.1M | 0.010 | 0.150 | 0.18 |
| $10-50 | 4.9M | 0.010 | 0.163 | 0.18 |
| $50-100 | 1.2M | 0.010 | 0.178 | 0.21 |
| $100-500 | 1.4M | 0.001 | 0.169 | 0.15 |
| $500-1K | 259K | 0.000 | 0.170 | 0.10 |
| $1K-5K | 218K | 0.000 | 0.164 | 0.06 |
| $5K+ | 35K | 0.000 | 0.167 | 0.06 |

**Non-monotonic**: Larger trades ($500+) show LOWER median impact than mid-sized trades ($50-100). This is because large trades cluster in highly liquid markets where impact is absorbed. The average is flat across sizes (~0.16), dominated by the fat tail.

### 3.2 Directional Impact by Side

| Side | Size | Trades | Avg Signed dp | % Same-Dir |
|------|------|--------|---------------|-----------|
| BUY (1) | <$10 | 14.9M | +0.040 | 30.8% |
| BUY (1) | $10-100 | 4.5M | -0.070 | 25.6% |
| BUY (1) | $100-1K | 1.2M | -0.101 | 19.1% |
| BUY (1) | $1K+ | 198K | -0.105 | 14.4% |
| SELL (2) | <$10 | 3.8M | +0.037 | 38.0% |
| SELL (2) | $10-100 | 1.6M | -0.113 | 43.7% |
| SELL (2) | $100-1K | 388K | -0.180 | 46.1% |
| SELL (2) | $1K+ | 55K | -0.253 | 48.6% |

**Counter-intuitive**: Larger BUY trades predict NEGATIVE subsequent price changes (mean reversion). Only 14.4% of $1K+ BUY trades see the price continue upward. This is classic informed-trading signature: large buys are followed by mean reversion as the market maker adjusts.

## 4. Spread Over Market Lifecycle

### 4.1 Sports (Oct-Nov 2025, resolved markets)

| Lifecycle % | Trades | Med Spread | Avg Spread | P75 |
|-------------|--------|-----------|------------|-----|
| 0-10% | 106K | 0.000 | 0.075 | 0.02 |
| 10-20% | 102K | 0.000 | 0.103 | 0.04 |
| 30-50% | 299K | 0.000 | 0.086 | 0.04 |
| 50-70% | 734K | 0.000 | 0.085 | 0.02 |
| 70-90% | 2.1M | 0.000 | 0.090 | 0.04 |
| **90-100%** | **3.9M** | **0.000** | **0.130** | **0.10** |

**Last-10% surge**: 54% of all trades occur in the final 10% of market life. Spread widens at the end (avg 0.130 vs 0.085 mid-life, p75 doubles). This is the "race to resolution" phase.

### 4.2 Politics (Oct-Nov 2025, resolved markets)

| Lifecycle % | Trades | Med Spread | Avg Spread | P75 |
|-------------|--------|-----------|------------|-----|
| 0-10% | 117K | 0.000 | 0.139 | 0.06 |
| 10-30% | 201K | 0.001 | 0.160 | 0.12 |
| 40-60% | 360K | 0.002 | 0.189 | 0.24 |
| 60-80% | 833K | 0.001 | 0.180 | 0.14 |
| **90-100%** | **1.5M** | **0.001** | **0.199** | **0.12** |

Politics spreads are wider throughout (avg 0.14-0.20 vs sports 0.075-0.130) and show less lifecycle variation. The mid-life hump (40-60% at avg 0.189) suggests information arrival drives spreads more than resolution proximity.

## 5. Inter-Arrival Time Analysis

| Activity Tier | Markets | Median IAT | Average IAT |
|---------------|---------|-----------|-------------|
| <100 trades/month | 15,189 | **234s** (3.9 min) | 10,663s |
| 100-500 | 16,583 | **6s** | 2,039s |
| 500-2K | 3,795 | **26s** | 871s |
| 2K-10K | 852 | **45s** | 444s |
| 10K+ | 30 | **26s** | 120s |

Most markets trade infrequently (median IAT 4+ minutes for the majority). Only 30 markets trade often enough (10K+/month) to have meaningful intra-minute dynamics.

## 6. PnL Impact Quantification

For our validated Sports YES v3 strategy: ~130 fills/month, $10/trade, entry price ~0.35, HR = 74.3%

| Fill Model | Slippage/Trade | EV/Trade | Monthly PnL | PnL Impact |
|-----------|---------------|---------|-------------|------------|
| SimulatedExecutor (current) | $0.00 | $11.23 | $1,460 | baseline |
| RealisticFill (defaults) | $0.07 | $11.16 | $1,451 | **-0.6%** |
| RealisticFill (MAC global) | $0.12 | $11.11 | $1,444 | **-1.1%** |
| RealisticFill (MAC sports) | $0.10 | $11.13 | $1,447 | **-0.9%** |
| Time-varying (lifecycle) | $0.52 | $10.71 | $1,392 | **-4.7%** |
| RealisticFill (Roll sports) | $1.50 | $9.73 | $1,265 | **-13.4%** |

**The fill model contributes 1-13% PnL degradation** depending on calibration. Even the most aggressive (Roll) reduces PnL by only 13%, which is small compared to the 70-80% PnL degradation from the vectorized-to-tick gap.

## 7. Implementation Sketch (if pursued)

### MarketMicrostructure Dataclass

```python
@dataclass(frozen=True)
class MarketMicrostructure:
    """Pre-computed microstructure features for a single market."""
    condition_id: str
    tag: str

    # Spread curve: half_spread at different lifecycle percentiles
    spread_curve: dict[float, float]  # {0.0: 0.075, 0.5: 0.085, 0.9: 0.130}

    # Liquidity proxy
    median_iat_s: float  # inter-arrival time
    daily_volume_usd: float

    # Impact model
    impact_coefficient: float  # from regression: dp_next = coeff * sqrt(size)

    def half_spread_at(self, lifecycle_frac: float) -> float:
        """Interpolate spread from lifecycle curve."""
        ...

    def market_impact(self, size_usd: float) -> float:
        """Concave impact: coeff * sqrt(size_usd / daily_volume)."""
        return self.impact_coefficient * (size_usd / max(self.daily_volume_usd, 100)) ** 0.5
```

### Integration with RealisticFillSimulator

```python
class ImprovedFillSimulator(RealisticFillSimulator):
    def __init__(self, micro: dict[str, MarketMicrostructure], ...):
        self._micro = micro

    def _get_spread(self, condition_id: str, lifecycle_frac: float) -> float:
        ms = self._micro.get(condition_id)
        if ms is None:
            return self._config.fallback_half_spread
        return ms.half_spread_at(lifecycle_frac)

    def _compute_impact(self, size_usd: float, condition_id: str) -> float:
        ms = self._micro.get(condition_id)
        if ms is None:
            return super()._compute_impact(size_usd, condition_id)
        return ms.market_impact(size_usd)
```

### Runtime Data Requirements

- **Pre-computed**: tag-specific spread curves (5-10 lifecycle buckets per tag)
- **Per-market**: daily volume (from trade tape or feature provider)
- **Per-trade**: lifecycle fraction (requires market creation time + expected resolution)

The main challenge is estimating lifecycle fraction at entry time. For sports, this is tractable (games have known start times). For politics, it is harder.

## 8. Verdict

### Is the upgrade worth it?

**No, not as a priority.**

| Factor | Assessment |
|--------|-----------|
| HR improvement | **0pp** — fill model does not affect hit rate |
| PnL improvement | **1-5%** with MAC calibration, **4-13%** with time-varying |
| Gap closure | **<1pp** of the 20-40pp vectorized-to-tick gap |
| Implementation effort | Medium (2-3 days for data pipeline + simulator upgrade) |
| Runtime complexity | Low (pre-computed curves, one dict lookup per fill) |
| Risk | Low (strictly additive, backward compatible) |

### What IS responsible for the vectorized-to-tick gap?

Based on validated findings:

| Gap Source | Estimated Contribution | Status |
|-----------|----------------------|--------|
| Consensus dedup (trades vs unique traders) | **8-15pp** | FIXED in tick validation |
| SELL filtering (exits as signals) | **5-10pp** | FIXED in tick validation |
| Capital constraint (unlimited vs N positions) | **3-8pp** | FIXED in tick validation |
| Entry price divergence | **2-5pp** | Partially addressed |
| Look-through bias | **2-4pp** | FIXED in tick validation |
| **Fill model** | **<1pp on HR, 1-5% on PnL** | Current topic |

### Recommendation

1. **SHORT TERM**: Keep `SimulatedExecutor` for tick-by-tick validation. The zero-friction model is fine because our strategies use small ($10) trades in liquid (Sports) markets where true slippage is <$0.10/trade.

2. **MEDIUM TERM (if pursuing PnL accuracy)**: Switch tick validation to `RealisticFillSimulator` with MAC calibration (`calibrate_spreads(method="median_abs_change")`). Expected PnL impact: -1% to -3%. Implementation: 2 lines of config change.

3. **LONG TERM (if building multiple strategies)**: Implement tag-specific spread curves as a FeatureProvider. Worth it only when running strategies across Sports + Politics + Crypto simultaneously, where spread differences are material (Sports 0.008 vs Politics 0.014 vs Weather 0.008 MAC).

4. **DO NOT**: Use the Roll estimator for calibration — it overestimates spreads by 17x due to capturing fundamental price moves. The MAC estimator is the correct choice for slippage modeling.

### Novel Findings Worth Capturing

1. **44% of consecutive trade pairs have zero price change** — dominates the MAC estimator
2. **Larger trades predict mean reversion** — only 14% of $1K+ BUY trades see continued upward movement
3. **Near-simultaneous trades (<5s) have WIDER spreads than spaced trades** — batch fills / sweep dynamics
4. **Sports has effectively zero spread at the median** — extremely tight microstructure
5. **Last-10% lifecycle surge**: 54% of sports trades occur in final 10% of market life, with 53% wider average spreads
