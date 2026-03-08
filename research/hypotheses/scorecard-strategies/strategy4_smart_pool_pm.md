# Strategy 4: Smart Pool with Position Management

**Hypothesis**: Instead of binary consensus (N traders agree → enter full size), treat the qualified-pool agreement spectrum as a **continuous signal** and size positions proportional to agreement strength.

**Status**: VECTORIZED UPPER BOUNDS — tick-by-tick validation required

**Date**: 2026-03-07

**Script**: `tmp/smart_pool_pm_analysis.py`
**Results JSON**: `tmp/smart_pool_pm_results.json`

---

## Setup

- **Train period**: resolved_at < 2025-12-05
- **Test period**: resolved_at >= 2025-12-05
- **Pool qualification**: excess_hr > 0 (above tag training base rate), avg_conviction >= 0.90 (non-MM), n_markets >= 10, NOT bot (n_positions < 10,000)
- **Gambling exclusion**: slug NOT LIKE '%updown%' OR '%up-or-down%'
- **CRITICAL**: `first_trade >= 2025-12-05` — only copyable test-period entries
- **Counting unit**: DISTINCT traders per market (not trade events)
- **Tool**: DuckDB + `maker_positions` Parquet snapshot

---

## Training Base Rates

| Tag | Base Rate | Train Markets |
|-----|-----------|---------------|
| Sports | 29.0% | 58,471 |
| Crypto | 21.9% | 17,731 |
| Politics | 24.1% | 13,260 |
| Weather | 12.2% | 4,461 |
| Esports | 44.4% | 1,998 |
| Finance | 23.2% | 1,666 |
| Elections | 28.2% | 1,501 |
| Tech | 18.7% | 1,225 |

> NOTE: These are YES win rates (not 50%). Sports YES wins 29% because most Sports markets are NO-biased (the underdog rarely wins). Pool HR is measured against the direction voted by the pool, not raw YES%.

---

## Qualified Pool Sizes

| Tag | Pool Traders | Med Excess HR | Max Excess HR |
|-----|-------------|---------------|---------------|
| Sports | 8,449 | +21.0pp | +71.0pp |
| Politics | 2,302 | +19.0pp | +75.9pp |
| Crypto | 1,096 | +21.0pp | +78.1pp |
| Esports | 315 | +20.6pp | +55.6pp |
| Weather | 243 | +16.7pp | +87.8pp |
| Finance | 164 | +51.8pp | +76.8pp |
| Elections | 108 | +13.3pp | +71.8pp |
| Tech | 105 | +17.7pp | +81.3pp |

Total test markets with ≥2 qualified traders: **69,685**

---

## Phase 1: Pool Agreement vs Resolution Gradient

### Head-Count vs Vol-Weighted HR (All Tags, N>=3)

| Min Traders | N Markets | HR Head-Count | HR Vol-Weighted | Median Vol-Conf | Med Hold |
|-------------|-----------|---------------|-----------------|-----------------|----------|
| N>=2 | 69,685 | 67.2% | 75.5% | 0.609 | 3h |
| N>=3 | 55,098 | 67.9% | 77.1% | 0.558 | 3h |
| N>=5 | 39,242 | 68.6% | 78.6% | 0.520 | 2h |
| N>=10 | 21,669 | 68.1% | 79.7% | 0.508 | 2h |

**Key finding**: Vol-weighted direction beats head-count by 8-12pp across all N thresholds. This confirms and extends the strategy2_smart_pool.md finding.

### Agreement Gradient: Head-Count Confidence → HR (Selected Tags)

#### Crypto (base rate 21.9%, qualified HR ~89%)
| Conf Bucket | N Markets | HR Head | HR Vol |
|-------------|-----------|---------|--------|
| 0.5-0.6 | 2,413 | 73.3% | **86.7%** |
| 0.6-0.7 | 800 | 81.5% | **92.1%** |
| 0.7-0.8 | 253 | 90.5% | **91.7%** |
| 0.8-0.9 | 191 | 92.1% | **95.8%** |
| 0.9-1.0 | 16 | 93.8% | **100%** |
| 1.0 (unani.) | 343 | 97.1% | 97.1% |

#### Politics (base rate 24.1%, qualified HR ~82%)
| Conf Bucket | N Markets | HR Head | HR Vol |
|-------------|-----------|---------|--------|
| 0.5-0.6 | 1,086 | 59.2% | **83.2%** |
| 0.6-0.7 | 1,037 | 61.5% | **83.1%** |
| 0.7-0.8 | 463 | 65.7% | **81.6%** |
| 0.8-0.9 | 254 | 70.9% | **81.9%** |
| 0.9-1.0 | 17 | 82.4% | **88.2%** |
| 1.0 (unani.) | 278 | 74.8% | 74.8% |

#### Sports (base rate 29.0%, qualified HR ~76%)
| Conf Bucket | N Markets | HR Head | HR Vol |
|-------------|-----------|---------|--------|
| 0.5-0.6 | 30,226 | 65.3% | **75.4%** |
| 0.6-0.7 | 9,233 | 70.6% | **75.9%** |
| 0.7-0.8 | 3,201 | 74.3% | **78.6%** |
| 0.8-0.9 | 1,661 | 74.7% | **77.1%** |
| 0.9-1.0 | 176 | 69.9% | 69.9% |
| 1.0 (unani.) | 1,214 | 81.8% | 81.8% |

### Vol-Weighted Confidence Gradient (Key Finding)

For vol-weighted confidence (vol_yes / total_vol), the gradient is **stronger and more monotonic**:

#### Crypto vol-conf → HR
| Vol-Conf Bucket | N | HR Vol |
|-----------------|---|--------|
| 0.5-0.6 | 2,117 | 84.4% |
| 0.6-0.7 | 221 | 90.5% |
| 0.7-0.8 | 225 | 91.6% |
| 0.8-0.9 | 305 | 93.4% |
| 0.9-1.0 | 805 | 97.0% |
| 1.0 (unani.) | 343 | 97.1% |

#### Politics vol-conf → HR (STRONG GRADIENT)
| Vol-Conf Bucket | N | HR Vol |
|-----------------|---|--------|
| 0.5-0.6 | 459 | 62.3% |
| 0.6-0.7 | 262 | 69.8% |
| 0.7-0.8 | 304 | 74.7% |
| 0.8-0.9 | 443 | 83.7% |
| 0.9-1.0 | 1,389 | **93.6%** |
| 1.0 (unani.) | 278 | 74.8% |

Politics vol-conf shows a 31pp gradient from 0.5-0.6 (62%) to 0.9-1.0 (93.6%). This is the **strongest agreement-HR gradient** observed across all tags.

#### Sports vol-conf → HR
| Vol-Conf Bucket | N | HR Vol |
|-----------------|---|--------|
| 0.5-0.6 | 26,593 | 72.2% |
| 0.6-0.7 | 4,488 | 79.5% |
| 0.7-0.8 | 4,312 | 79.8% |
| 0.8-0.9 | 3,893 | 80.8% |
| 0.9-1.0 | 5,211 | **83.5%** |

**Finding**: Higher vol-conf → monotonically higher HR across all major tags. This supports **position sizing proportional to vol-conf**.

### Insight: Unanimous ≠ Best

For Crypto and Politics, 1.0 (unanimous) has **lower** HR than 0.9-1.0 bucket. Unanimous markets are typically small (3 traders → 1 vs 0 splits count as "unanimous"), giving unstable signal. Near-unanimous with N>=5 is the optimal zone.

---

## Phase 2: Position Sizing Simulation

### Binary vs Proportional PnL (per-$1 stake, $1 = resolution value in [0,1])

Each market: simulate a bet of `size × (direction_correct - fill_price)` where:
- `direction_correct` = 1 if pool direction was correct, 0 if wrong
- `fill_price` = vol-weighted avg YES entry price of qualified traders (proxy for market price at entry)

| Tag | Strategy | N Trades | HR | Avg PnL/Bet | Total PnL |
|-----|----------|----------|-----|-------------|-----------|
| Sports | binary_80 (vol_conf≥0.80) | 9,172 | 83.1% | +$0.150 | +$1,379 |
| Sports | binary_70 (vol_conf≥0.70) | 13,165 | 82.3% | +$0.157 | +$2,068 |
| Sports | proportional (all, size=(conf-0.5)×2) | 42,550 | 76.3% | +$0.011 | +$473 |
| Politics | binary_80 | 2,076 | 89.0% | +$0.104 | +$215 |
| Politics | binary_70 | 2,379 | 87.2% | +$0.100 | +$239 |
| Politics | proportional (all) | 3,090 | 82.1% | +$0.068 | +$209 |
| Crypto | binary_80 | 1,280 | 95.9% | +$0.096 | +$123 |
| Crypto | binary_70 | 1,495 | 95.6% | +$0.105 | +$157 |
| Crypto | proportional (all) | 3,725 | 89.3% | +$0.005 | +$20 |

**Critical finding**: **Binary (vol_conf ≥ 0.70-0.80) beats proportional sizing** in both per-bet PnL and total PnL for Sports and Crypto. Proportional sizing across all vol_conf levels (including 0.5-0.6 split markets) dilutes alpha with low-confidence noise.

**Why binary beats proportional here**:
- Low vol_conf markets (0.5-0.6) are only slightly profitable even when vol wins
- Fill price at high-HR markets tends to be near 0.80-0.90 (Crypto), leaving less edge
- Proportional gives largest size when vol_conf is highest, but edge (HR - fill) is NOT monotonic with confidence

### Kelly Calibration by Confidence Bucket

Kelly fraction = HR/fill - 1 (simplified for binary outcomes):

**Sports** — positive Kelly across all confidence levels:
| Vol-Conf | N | HR | Fill | Edge | Kelly Fraction |
|----------|---|----|------|------|----------------|
| 0.5-0.6 | 25,194 | 72.5% | 55.1¢ | +17.4¢ | +0.315 |
| 0.6-0.7 | 4,191 | 79.8% | 60.5¢ | +19.3¢ | +0.319 |
| 0.7-0.8 | 3,993 | 80.5% | 62.9¢ | +17.5¢ | +0.279 |
| 0.8-0.9 | 3,581 | 81.7% | 66.6¢ | +15.0¢ | +0.225 |
| 0.9-1.0 | 4,666 | 84.4% | 73.2¢ | +11.2¢ | +0.153 |

Sports shows a notable pattern: **edge (HR - fill) peaks at 0.6-0.7 confidence, not at 0.9-1.0**. Higher confidence markets price in the signal → edge compresses. The Kelly fraction peaks at 0.5-0.6 vol_conf. This is a key insight for sizing.

**Politics** — strongest gradient, but fills are high:
| Vol-Conf | N | HR | Fill | Edge | Kelly Fraction |
|----------|---|----|------|------|----------------|
| 0.5-0.6 | 455 | 62.0% | 60.6¢ | +1.4¢ | +0.023 |
| 0.8-0.9 | 439 | 83.6% | 78.6¢ | +5.0¢ | +0.063 |
| 0.9-1.0 | 1,371 | 93.7% | 90.6¢ | +3.0¢ | +0.034 |

**Warning**: Politics markets at vol_conf ≥ 0.9 have fills near 0.90 → very little room for error. Real-time fill may be worse than the retroactive vol-weighted avg.

**Crypto** — small Kelly, highly priced:
| Vol-Conf | N | HR | Fill | Edge | Kelly Fraction |
|----------|---|----|------|------|----------------|
| 0.5-0.6 | 2,016 | 84.4% | 83.1¢ | +1.3¢ | +0.016 |
| 0.9-1.0 | 735 | 97.0% | 95.6¢ | +1.5¢ | +0.015 |

**Crypto fills are near the resolution value** — the market prices in the outcome before we can enter. Kelly fractions near zero suggest very limited live edge. This is a **significant warning** for Crypto as a live strategy.

---

## Phase 3: Temporal Dynamics

### Early vs Late Pool Direction

Defined: split entries into first-half and second-half by entry order.

| Tag | N | HR Early | HR Late | Agree (%) | HR When Agree | Flip (%) | HR Late After Flip |
|-----|---|----------|---------|-----------|---------------|----------|--------------------|
| Sports | 38,900 | 50.9% | 74.3% | 52.4% | 72.1% | 47.6% | 76.8% |
| Crypto | 3,113 | 62.6% | 79.2% | 50.0% | 84.4% | 32.6% | 72.3% |
| Politics | 2,576 | 56.8% | 67.6% | 58.7% | 70.7% | 41.1% | 63.3% |
| Elections | 55 | 41.8% | 67.3% | 49.1% | 59.3% | 50.9% | 75.0% |
| Finance | 399 | 47.9% | 63.2% | 45.6% | 59.3% | 47.1% | 66.5% |

**Critical findings**:

1. **Late-majority direction consistently outperforms early-majority** (+13-27pp) across all tags. This means waiting for the Nth qualified trader (consensus) is materially better than entering on the 1st or 2nd trader.

2. **When the pool flips direction, following the late direction is correct** (72-77% HR for Sports, 73% for Crypto) and matches or exceeds the "agree" scenario. The initial signal was noise.

3. **Early-majority direction is near random** (50-63% HR). The first trader's direction has minimal predictive value alone.

**Implication for position management**: Do NOT enter until late-majority confirms direction. Entering early (on 1-2 traders) destroys the edge.

### Phase 3b: Momentum Signal

The `first()` / `last()` cumulative agreement evolution query did not return usable data in this test window due to insufficient markets with ≥4 entries post-cutoff. Inconclusive.

---

## Phase 4: Entry/Exit Rules — Optimal Entry Timing (N-th Trader)

### Signal Quality by Entry Order N (HR at state after N traders)

**Sports** — strikingly flat HR across N:
| N | Markets | HR | Hold | Avg YES Frac |
|---|---------|----|------|--------------|
| 1 | 76,438 | 54.9% | 12h | 27.4% |
| 2 | 55,895 | 54.8% | 9h | 27.6% |
| 3 | 45,719 | 54.0% | 8h | 28.3% |
| 5 | 33,947 | 54.0% | 6h | 30.0% |

**Sports shows NO improvement in HR as N increases**. This is because the vol-weighted signal (not just head-count) is what matters — 27-30% YES fraction at any N means most markets are majority NO, and the pool is nearly always mostly NO. The "direction" signal is embedded in vol_conf, not in N.

**Crypto** — degrading HR as N increases (IMPORTANT):
| N | Markets | HR | Hold | Avg YES Frac |
|---|---------|----|------|--------------|
| 1 | 6,886 | **67.5%** | 103h | 22.9% |
| 2 | 5,202 | **70.8%** | 52h | 21.9% |
| 3 | 4,016 | 69.0% | 28h | 22.5% |
| 5 | 2,411 | 65.7% | 22h | 25.0% |
| 8 | 1,198 | 61.5% | 14h | 27.8% |

**Critical finding**: For Crypto, the 2nd trader's entry is optimal (HR peaks at 70.8%). HR **degrades** as more traders enter and as hold time shrinks. This suggests Crypto markets are fast-moving — by the time the 5th+ qualified trader enters, the market has already priced in the outcome. **Entry timing is critical for Crypto**.

**Esports** — signal improves with N (small sample):
| N | Markets | HR | Hold |
|---|---------|----|------|
| 1 | 18 | 66.7% | 91h |
| 2 | 11 | 72.7% | 90h |
| 3 | 10 | 80.0% | 42h |

**Elections** — flat/noisy:
| N | Markets | HR | Hold |
|---|---------|----|------|
| 1 | 290 | 50.7% | 143h |
| 3 | 79 | 49.4% | 113h |
| 8 | 16 | 62.5% | 222h |

Elections sample too small for reliable N-sweep conclusions.

---

## Phase 5: Binary vs Continuous Comparison (Head-to-Head)

| Tag | Strategy | N Trades | HR | Total PnL | Sharpe Proxy |
|-----|----------|----------|-----|-----------|--------------|
| Sports | binary_80 (vol_conf≥0.80) | 9,172 | 83.1% | +$1,379 | 0.53 |
| Sports | binary_70 (vol_conf≥0.70) | 13,165 | 82.3% | +$2,068 | — |
| Sports | binary_60 (vol_conf≥0.60) | 18,404 | 79.7% | +$2,209 | — |
| Sports | proportional (all) | 42,550 | 76.3% | +$473 | 0.03 |
| Sports | squared (conf²) | 42,550 | — | +$2,030 | — |
| Politics | binary_80 | 2,076 | 89.0% | +$215 | 0.28 |
| Politics | binary_70 | 2,379 | 87.2% | +$239 | — |
| Politics | proportional | 3,090 | 82.1% | +$209 | 0.11 |
| Crypto | binary_80 | 1,280 | 95.9% | +$123 | — |
| Crypto | binary_70 | 1,495 | 95.6% | +$157 | — |
| Crypto | proportional | 3,725 | 89.3% | +$20 | — |

**Verdict**: Binary thresholds **dominate** proportional sizing for total PnL and per-bet PnL. The squared sizing (higher aggression at high confidence) approaches binary_60 performance for Sports.

The proportional strategy trades too many low-confidence markets at low sizes, producing minimal aggregate PnL despite similar direction correctness. The concentration of bets at high-confidence levels (binary_60+) is what drives returns.

**Sharpe proxy** (avg_pnl / std_pnl):
- Sports binary_80: 0.53 (better risk-adjusted)
- Sports proportional: 0.03 (much worse — noise dominates)

---

## Phase 6: Per-Tag Detailed Analysis

| Tag | N Mkts | Med Vol-Conf | HR Vol | HR Head | Pct Disagree | HR Vol Wins Disagreement | Med Hold |
|-----|--------|--------------|--------|---------|-------------|--------------------------|----------|
| Sports | 45,711 | 0.524 | 75.9% | 67.8% | 32% | 67.9% | 3h |
| Crypto | 4,016 | 0.554 | 89.4% | 79.0% | 24% | 83.8% | 4h |
| Politics | 3,135 | 0.917 | 82.1% | 63.4% | 32% | **87.9%** | 4h |
| Weather | 1,137 | 0.890 | 77.6% | 56.5% | 43% | **88.3%** | 8h |
| Finance | 754 | 0.606 | 65.1% | 59.4% | 32% | 59.3% | 15h |
| Tech | 100 | 0.911 | 74.0% | 48.0% | 37% | **83.8%** | 6h |
| Esports | 10 | 0.954 | 90.0% | 80.0% | 10% | 100% | 4h |

**Key observations**:
1. **Politics and Weather have large vol vs head disagreement (32-43%)** — and vol wins heavily (88%). These are the tags where vol-weighted signal provides the largest lift.
2. **Sports has lowest median vol_conf (0.524)** — most markets are very split. The 52% median means there are essentially equal YES and NO voters by count, but vol skews the signal.
3. **Crypto vol_conf is low (0.554) but HR is high** — qualified Crypto traders bet large on the winning side, so vol-weighting recovers a strong signal even from split pools.
4. **Esports has only 10 test markets** (small sample) but 0.954 median vol_conf — very high agreement.

### Vol vs Head Disagreement Deep-Dive

When vol-weighted direction ≠ head-count direction (pool has mixed YES/NO with large-volume minority):
- Politics: vol wins 87.9% (vs head 12.1%) — a 75pp swing
- Weather: vol wins 88.3% (vs head 11.7%) — a 76pp swing
- Tech: vol wins 83.8%

**This is the clearest evidence that vol-weighting is essential**: in split markets, the side with more MONEY is almost always right, even when outvoted by head-count.

---

## Phase 7: The Hard Case — Pool Direction Changes

For markets with ≥3 qualified traders, comparing "early direction" (first 3 traders) vs "final direction" (all traders):

| Tag | Type | N | HR Early Dir | HR Final Dir | Med Hold |
|-----|------|---|--------------|--------------|----------|
| Sports | stable | 31,734 | 60.9% | 60.9% | 3h |
| Sports | flipped | 11,272 | 30.7% | **69.3%** | 2h |
| Crypto | stable | 2,707 | 78.2% | 78.2% | 5h |
| Crypto | flipped | 697 | 32.6% | **67.4%** | 3h |
| Politics | stable | 2,449 | 61.5% | 61.5% | 5h |
| Politics | flipped | 685 | 29.5% | **70.5%** | 4h |
| Elections | stable | 68 | 52.9% | 52.9% | 28h |
| Elections | flipped | 11 | 27.3% | **72.7%** | 18h |

**Critical finding on flipped markets**:
- In ALL tags, when the early direction (first 3 traders) is WRONG (pool later flips), the final direction has **HR ~67-73%** — which is actually SIMILAR to stable markets.
- Following the final direction is correct even after a flip.
- **Early direction in flipped markets is only 27-33% correct** — essentially random-to-anti-signal.

**Implication**: A position management rule of "exit if pool direction flips" and "re-enter in new direction" would preserve signal. The pool flip is informative — it's not noise, it's an update signal.

However, a simpler rule also works: **wait for N qualified traders before entering at all**. If you need N=3, you'll naturally use the updated direction after any early flippers change the pool state.

---

## Phase 8: Compounding Scores (UPPER BOUNDS)

### Top Candidates

| Strategy | Tag | N Signals | HR | Excess HR | Avg Edge | Med Hold | CS (UB) |
|----------|-----|-----------|-----|-----------|----------|----------|---------|
| **Binary_80_N5** | Sports | 5,710 | 85.6% | +56.6pp | $0.175 | 3h | **1,190** |
| **Binary_80_N5** | Crypto | 617 | 98.1% | +76.2pp | $0.135 | 4h | **824** |
| **Binary_80_N5** | Politics | 1,352 | 92.1% | +68.0pp | $0.122 | 4h | **499** |
| Binary_70_N3 | Crypto | 1,495 | 95.6% | +73.7pp | $0.105 | 4h | 465 |
| Prop_N3_all | Crypto | 3,725 | 89.3% | +67.4pp | $0.118 | 4h | 479 |
| Binary_80_N3 | Sports | 9,172 | 83.1% | +54.1pp | $0.150 | 3h | 651 |
| Binary_70_N3 | Sports | 13,165 | 82.3% | +53.3pp | $0.157 | 3h | 670 |
| Binary_80_N3 | Politics | 2,076 | 89.0% | +64.9pp | $0.104 | 4h | 323 |
| Binary_90_N3 | Politics | 1,637 | 90.5% | +66.4pp | $0.101 | 4h | 322 |

> UPPER BOUNDS. Apply 20-40pp HR degradation for tick-by-tick estimate.

**Best vectorized strategy**: `Binary_80_N5/Sports` with CS=1,190 (UB).
After 20-40pp tick degradation: expected real HR = 45-66%, real CS ≈ 120-360.

---

## Key Findings & Conclusions

### Finding 1: Binary Thresholds Beat Proportional Sizing
Proportional sizing (enter all markets with size proportional to confidence) **does not outperform** binary thresholds. The best proportional approach (squared sizing) approximates binary_60. Binary at 70-80% confidence is simpler and better. **Continuous position management adds complexity without proportional reward.**

### Finding 2: Vol-Weighted Direction Dominates
Vol-weighted confidence vs head-count confidence: vol wins by 8-12pp at all N thresholds. When they disagree (24-43% of markets depending on tag), vol wins by 60-76pp. **Always use vol-weighted direction.**

### Finding 3: Late Majority >> Early Majority
Entering after the Nth qualified trader (consensus) is essential. Early (1st-2nd trader) HR is 50-67% — near random for most tags. Late majority HR is 67-79%. **Do not enter on 1-2 traders in split markets.**

### Finding 4: Crypto Entry Timing is Inverted
In Crypto, the 2nd qualified trader entry has **peak HR (70.8%)**. HR degrades as N increases because fast markets price in the signal. For Crypto, enter after N=2 (not N=5). But fills near 90%+ suggest very thin edge in live trading.

### Finding 5: Pool Flip is Informative
When the first 3 traders' direction differs from the final consensus:
- Early direction: ~30% HR (anti-signal)
- Final direction: ~67-73% HR
A "flip" signal can be used to either (a) close early position and re-enter in new direction, or (b) avoid entering until stable consensus forms.

### Finding 6: Sport's Edge is Volume, Not Confidence
Sports shows positive Kelly fractions EVEN at low vol_conf (0.5-0.6), and the edge (HR - fill) actually peaks at 0.6-0.7 confidence. Sports fills are low (55-63¢) because most qualified traders bet on lower-priced YES markets. The edge compresses at high confidence as markets price in the signal.

### Finding 7: Crypto and Politics Fills Are High
Crypto fills avg 83-96¢ (for the direction traded), Politics 60-91¢. This means small absolute edge (1-5¢) even at 85-97% HR. In live trading with real spreads and slippage, these edges may be zero or negative.

---

## Recommended Strategy Parameters

### Primary (Binary, N>=5, vol_conf>=0.80)

```
For each resolved market in test window:
  1. Count DISTINCT qualified traders (excess_hr > 0, non-MM, ≥10 train markets)
  2. Require N >= 5 qualified traders
  3. Require vol_conf >= 0.80 (80% of pool volume on one side)
  4. Direction: vol-weighted (sum net_usd YES vs NO)
  5. Size: FLAT ($100 or configured stake)
  6. Hold to resolution
```

### Per-Tag Parameters

| Tag | Min N | Vol-Conf | Expected HR (UB) | Expected HR (Tick) | N Signals/yr est. |
|-----|-------|----------|------------------|--------------------|-------------------|
| Sports | 5 | ≥0.80 | 85.6% | 45-65% | ~22,000/yr |
| Crypto | 2-3 | ≥0.70 | 95-98% | 55-75% | ~2,500/yr |
| Politics | 5 | ≥0.80 | 92.1% | 52-72% | ~5,000/yr |
| Esports | 2 | ≥0.80 | 90.0% | 50-70% | (too small) |

> **WARNING**: Sports in-play contamination (hold < 4h). Must add hold >= 4h filter for Sports.
> Most Sports signals (80%+) in this dataset are short-hold in-play signals.

### Hold-Time Filter (CRITICAL for Sports)

From prior research (`strategy1_tag_consensus.md`):
- Sports signals with hold < 4h are likely in-play (live score watchers, 99%+ HR but uncopyable)
- Must filter: `hold_hours >= 4` for Sports minimum, `>= 24h` for conservative

After hold filter, Sports signal count drops dramatically (probably 10-20% of raw signals survive).

---

## Comparison with Binary Consensus (Strategy 2)

| Metric | Strategy 2 (Binary) | Strategy 4 (Continuous PM) | Winner |
|--------|---------------------|---------------------------|--------|
| HR at vol_conf≥0.80, N≥3 | ~87% | ~83-90% | Tie |
| PnL per bet (Sports) | Not computed | +$0.15 | — |
| Sizing rule | Binary threshold | Binary threshold | Tie — both use threshold |
| Vol vs head | Vol confirmed better | Vol confirmed better | Tie |
| Direction changes | Not studied | Late > Early (+20pp) | Strategy 4 new finding |
| Kelly calibration | Not done | Edge peaks at 0.6-0.7 conf | Strategy 4 new finding |
| Crypto entry timing | Not optimized | N=2 optimal (not N=5) | Strategy 4 new finding |

**Verdict**: The continuous position management concept (proportional sizing) does NOT outperform binary thresholds in this analysis. The main value-adds from this research are:
1. Entry timing dynamics (late > early)
2. Crypto optimal N = 2 (not 5)
3. Sports edge peaks at moderate confidence (surprising)
4. Pool flip signal (informative for position management)

---

## Critical Risks

1. **Vectorized upper bound**: 20-40pp HR degradation expected. Binary_80_N5/Sports at 85.6% likely produces 45-65% in tick-by-tick.

2. **Sports in-play contamination**: Not filtered in this analysis. Most Sports holds are 2-3h, indicating in-play signals dominate. Real pre-match Sports signals will have far fewer signals and likely similar HR to Politics.

3. **High fills compress edge**: Crypto fills of 83-96¢ leave minimal real edge. Any slippage eliminates it. Politics 90.6¢ fills similarly fragile.

4. **Consensus gap (primary tick gap)**: Vectorized signals fire at `max(first_trade)` — the LAST qualified trader entry. In live trading, we only know Nth trader has entered after observing the trade. The fill will be AFTER the signal event.

5. **Pool overlap**: qualified pool is broad (8K-21K traders). Many may be correlated (following same strategies) — not independent signals. Effective N may be lower than count suggests.

6. **Test window size**: Dec 2025 – Mar 2026 (~3 months). Not enough history to rule out overfitting on base rates and confidence thresholds.

---

## SQL Reference

```sql
-- Smart Pool Market Agreement (DuckDB)
SELECT
    p.condition_id,
    mt.primary_tag AS tag,
    first(CAST(p.yes_won AS DOUBLE)) AS yes_won,
    count(DISTINCT p.trader) AS n_qual_traders,
    count(DISTINCT CASE WHEN p.position = 'YES' THEN p.trader END) AS n_yes_traders,
    count(DISTINCT CASE WHEN p.position = 'NO' THEN p.trader END) AS n_no_traders,
    sum(CASE WHEN p.position = 'YES' THEN abs(p.net_usd) ELSE -abs(p.net_usd) END) AS vol_direction,
    sum(CASE WHEN p.position = 'YES' THEN abs(p.net_usd) ELSE 0 END) AS vol_yes,
    sum(abs(p.net_usd)) AS total_vol_usd,
    greatest(vol_yes, total_vol_usd - vol_yes) / NULLIF(total_vol_usd, 0) AS vol_conf,
    CASE WHEN vol_yes > total_vol_usd - vol_yes THEN 'YES' ELSE 'NO' END AS vol_dir,
    max(p.first_trade) AS last_entry,
    date_diff('hour', max(p.first_trade), first(p.resolved_at)) AS hold_hours
FROM maker_positions p
JOIN _market_tags mt ON p.condition_id = mt.condition_id
JOIN _qualified q ON p.trader = q.trader AND mt.primary_tag = q.tag
WHERE CAST(p.resolved_at AS DATE) >= '2025-12-05'
  AND CAST(p.first_trade AS DATE) >= '2025-12-05'  -- CRITICAL
  AND p.volume > 0
GROUP BY p.condition_id, mt.primary_tag
HAVING count(DISTINCT p.trader) >= 5
   AND greatest(vol_yes, total_vol_usd - vol_yes) / NULLIF(total_vol_usd, 0) >= 0.80
   AND date_diff('hour', max(p.first_trade), first(p.resolved_at)) >= 0;
```

---

## Next Steps

1. **Tick-by-tick validation**: Priority: Sports (N≥5, vol_conf≥0.80, hold≥4h) and Politics (N≥5, vol_conf≥0.80).
2. **Hold filter calibration**: Quantify Sports in-play contamination more precisely (how many signals survive hold≥4h with adequate HR?).
3. **Crypto live fill study**: Check live spreads for Crypto markets with 90%+ fills — is there any real edge?
4. **Pool flip rules**: Build a live signal that fires when N≥3 traders form consensus in one direction after an earlier flip — this may be cleaner than static N≥5.
5. **Compare against Strategy 2 tick results**: Once tick-by-tick validation from Track B comes in, compare directly.

---

## Artifacts

- Analysis script: `/mnt/nvme/git/polymarket/polymarket/tmp/smart_pool_pm_analysis.py`
- Results JSON: `/mnt/nvme/git/polymarket/polymarket/tmp/smart_pool_pm_results.json`
- Full log: `/mnt/nvme/git/polymarket/polymarket/tmp/smart_pool_pm.log`
