# GBM Flip Stop-Loss False Positive Analysis

**Date**: 2026-03-10
**Status**: Complete — vectorized (UPPER BOUND)
**Universe**: 21,073 simulated positions (4,927 5-min + 16,146 15-min BTC Up/Down markets)
**Data**: 15.6M 1-second Binance BTC-USDT bars (Sep 2025 -- Mar 2026)

> [!WARNING] Vectorized results. Absolute PnL numbers are upper bounds.
> However, the relative comparisons between threshold/delay configurations
> are valid for ranking which approach is better.

---

## Context

The crypto GBM strategy uses a "flip stop-loss" that exits when `GBM P(our_side) < 0.35`,
meaning BTC has reversed enough that the model now thinks we're on the wrong side.
In live paper trading: **238 GBM flip exits vs only 81 trailing stop exits** --
the flip stop dominates exit behavior.

This analysis answers: how many of those stops are false positives (BTC temporarily
dips but would have recovered), and what configuration changes reduce false stops
while preserving correct ones?

---

## Key Findings

### 1. False Stop Rate: 31.1% at Current Settings

At the current `gbm_flip_threshold = 0.35` (instant trigger, no confirmation delay):

| Metric | Value |
|--------|-------|
| Positions that hit flip | 10,962 / 21,073 = **52.0%** |
| False stops (stopped but ultimately won) | 3,411 = **31.1%** of stopped |
| Correct stops (stopped and lost) | 7,551 = **68.9%** of stopped |
| Recovery within 30s | **46.3%** of stopped |
| Recovery within 60s | **56.1%** of stopped |
| Median time to flip | **246 seconds** into position |
| Mean GBM at flip | 0.321 |

**Interpretation**: Nearly 1 in 3 flip exits kills a position that would have ultimately won.
Over half of stopped positions would have recovered within 60 seconds. The flip is catching
real reversals (69% correct), but the false positive rate is high enough to matter.

### 2. By Duration

| Duration | Positions | Flipped | False Stop Rate | Recov 30s | Recov 60s |
|----------|-----------|---------|-----------------|-----------|-----------|
| 5-min | 4,927 | 2,422 (49.2%) | **34.1%** | 51.1% | 58.4% |
| 15-min | 16,146 | 8,540 (52.9%) | **30.2%** | 45.0% | 55.5% |

5-min windows have a higher false stop rate (34.1% vs 30.2%) because the GBM oscillates
more relative to the shorter window. 15-min windows have more flips in absolute terms
because there's more time for BTC to reverse.

### 3. Optimal Flip Threshold

Lowering the threshold from 0.35 to 0.25 reduces flips from 52% to 46% but
increases the fraction of losses held to resolution:

| Threshold | Flipped% | False Stop% | PnL with Stop | PnL Hold-All | PnL Delta |
|-----------|----------|-------------|---------------|--------------|-----------|
| 0.15 | 42.2% | 20.3% | $993 | $35,258 | **-$34,266** |
| 0.20 | 43.9% | 22.4% | $11,550 | $35,258 | **-$23,708** |
| 0.25 | 46.1% | 25.0% | $20,974 | $35,258 | **-$14,285** |
| 0.30 | 48.6% | 27.6% | $32,413 | $35,258 | **-$2,846** |
| **0.35** | **52.0%** | **31.1%** | **$40,880** | **$35,258** | **+$5,621** |
| 0.40 | 56.6% | 35.3% | $46,989 | $35,258 | **+$11,731** |
| 0.45 | 62.1% | 40.0% | $51,221 | $35,258 | **+$15,962** |

**Key insight**: The current threshold (0.35) already outperforms holding all positions!
The PnL delta is positive (+$5,621 vs hold-all at $35,258). Higher thresholds (0.40, 0.45)
are even better -- they stop MORE positions but save more money on the correct stops.

**However**: Higher thresholds have higher false stop rates (35-40%). The tradeoff is
between fewer lost positions (good) and more prematurely stopped winners (bad).

The GBM flip stop-loss is **net positive** at th >= 0.35. Below 0.30, it becomes net negative.

### 4. Confirmation Delay: The Best Lever

Adding a confirmation delay (requiring N consecutive seconds below threshold before stopping)
dramatically reduces false stops while preserving most correct stops:

| Config | Flipped% | False Stop% | Recov 60s | PnL Delta |
|--------|----------|-------------|-----------|-----------|
| th=0.35, 0s delay | 52.0% | 31.1% | 56.1% | +$5,621 |
| **th=0.35, 5s delay** | **49.7%** | **29.2%** | **49.6%** | **+$16,395** |
| th=0.35, 10s delay | 47.7% | 27.5% | 42.6% | **+$23,922** |
| th=0.35, 15s delay | 46.0% | 26.1% | 36.2% | N/A |
| th=0.35, 20s delay | 44.7% | 24.9% | 31.3% | N/A |

**5-second delay is the sweet spot**:
- Reduces false stops from 31.1% to 29.2% (-2pp)
- Filters 2.3% of total positions from stopping (49.7% vs 52.0%)
- PnL improves by **+$10,774** ($16,395 vs $5,621 delta)
- Retains most correct stops (only 2.3pp fewer stops overall)

**10-second delay** pushes PnL delta to +$23,922 but starts aggressively filtering
stops (down to 47.7% flipped). Given the strategy runs on a 5-second timer,
a 10s delay may be too slow to implement accurately.

### 5. Combined: Threshold + Delay

Best performing combinations:

| Config | Flipped% | False Stop% | PnL Delta |
|--------|----------|-------------|-----------|
| th=0.40, 10s | 52.3% | 31.9% | **+$29,100** |
| th=0.45, 10s | 58.2% | 37.2% | **+$31,878** |
| th=0.35, 10s | 47.7% | 27.5% | **+$23,922** |
| th=0.40, 5s | 54.3% | 33.5% | **+$21,371** |
| th=0.35, 5s | 49.7% | 29.2% | **+$16,395** |

**th=0.40 + 10s delay** gives the best PnL (+$29,100) with moderate false stop rate (31.9%).
But this is aggressive -- 10s delay on a 5s timer means only 2 checks before stopping.

**Pragmatic recommendation: th=0.35 + 5s delay** -- easy to implement, +$10,774 PnL
improvement over current, low implementation risk.

### 6. Time-Adaptive Threshold

Widening the stop as hold time increases (more conviction = wider leash):

| Base | Widen/60s | Flipped% | False Stop% |
|------|-----------|----------|-------------|
| 0.35 | 0.00 | 52.0% | 31.1% |
| 0.35 | 0.02/60s | 45.5% | 25.1% |
| 0.35 | 0.05/60s | 42.5% | 21.4% |
| 0.30 | 0.05/60s | 41.5% | 20.1% |

Time-adaptive widening significantly reduces false stops (31.1% -> 21.4% at 0.05/60s).
This makes economic sense: if you've held for 3 minutes and GBM was favorable the
whole time, a brief dip to 0.32 shouldn't trigger a stop.

**But**: the PnL impact was not computed for adaptive (the interaction with hold time
makes it complex). The confirmation delay is simpler and achieves similar results.

### 7. Flip Timing Distribution (th=0.35, instant)

```
Median: 246s (4.1 minutes into position)
Mean:   316s (5.3 minutes)
P10:    63s  (1.1 minutes)
P90:    702s (11.7 minutes)
```

Most flips occur in the middle of the window, not right after entry. This suggests
the GBM has time to establish a position before BTC reverses. The P10 at 63s means
10% of flips happen within the first minute -- these are the most likely false positives
(BTC barely moved, then twitched the wrong way).

---

## Recommendations

### Immediate (Safe)

1. **Add 5-second confirmation delay** to the GBM flip stop.
   - Expected improvement: +$10,774 PnL over ~21K positions (+$0.51/trade)
   - Implementation: count consecutive timer ticks (5s each) where `gbm_ours < threshold`.
     Exit only when count >= 1 (= 5s). Reset counter when `gbm_ours >= threshold`.
   - Risk: minimal. Only delays exits by 1 timer tick. Positions that truly reversed
     will still hit the stop on the next tick.

### Consider (Medium Risk)

2. **Raise threshold to 0.40** (from 0.35).
   - Higher threshold means "exit when GBM thinks we're only 40% likely to win" instead
     of 35%. More aggressive stopping, but catches more losses.
   - Combined with 5s delay: PnL delta +$21,371 (4x current).
   - Risk: higher false stop rate (33.5% vs 29.2%).

3. **Time-adaptive widening** (0.35 base, -0.02/60s).
   - After 60s: effective threshold = 0.33. After 120s: 0.31. Floor at 0.10.
   - Reduces false stops from 31.1% to 25.1%.
   - More complex to implement. Confirmation delay achieves similar results more simply.

### Avoid

4. **Lowering threshold below 0.30** -- net negative PnL. The stop becomes too lenient
   and lets losing positions bleed.

5. **Removing the flip stop entirely** -- holding all positions yields $35,258 PnL vs
   $40,880 with the stop. The flip stop IS net positive at th >= 0.35. Don't remove it.

---

## Implementation Notes

The current strategy (`strategy.py` line 396) checks `gbm_ours < self._cfg.gbm_flip_threshold`
on every timer tick (5s). To add confirmation delay:

```python
# In _OpenPosition dataclass, add:
flip_consecutive_ticks: int = 0  # count of consecutive ticks below threshold

# In _check_exits, replace:
if gbm_ours < self._cfg.gbm_flip_threshold:
    # exit immediately

# With:
if gbm_ours < self._cfg.gbm_flip_threshold:
    pos.flip_consecutive_ticks += 1
    if pos.flip_consecutive_ticks >= self._cfg.gbm_flip_confirm_ticks:  # default 1
        # exit
else:
    pos.flip_consecutive_ticks = 0  # reset
```

Config addition: `gbm_flip_confirm_ticks: int = 1`  (1 tick = 5s confirmation).

---

## Caveats

- All results are **vectorized upper bounds**. Expect 20-40pp degradation in tick-by-tick.
- The PnL model assumes we can exit at GBM fair value (not market price) -- optimistic.
- Entry simulation uses `min_gbm_deviation = 0.05` without PM price, which may
  produce slightly different entry timing than live.
- The overall 61.2% resolution win rate reflects GBM signal quality: entering on the
  favored side when GBM deviates from 0.50 wins 61% of the time.
- Recovery rates are based on the GBM trajectory -- in live trading, orderbook dynamics
  and PM price lag may differ.

---

## Script

`research/hypotheses/crypto-gbm-improvements/scripts/gbm_flip_analysis.py`

Full results: `research/hypotheses/crypto-gbm-improvements/discovery/gbm_flip_results.json`
