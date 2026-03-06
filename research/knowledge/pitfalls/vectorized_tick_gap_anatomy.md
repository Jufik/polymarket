# Anatomy of the Vectorized-to-Tick-by-Tick Gap

> [!CRITICAL] The gap is NOT a single issue. It's a cascade of 6 compounding effects,
> each reducing HR by 3-15pp. Understanding each step is required before building
> any signal-following strategy.

## The Pipeline (each step degrades signal)

```
Step 1: Training → OOS          ~3pp drop         (modest, expected)
Step 2: Position → Trade level   ~15-25pp drop     ← BIGGEST GAP
Step 3: Consensus quality        0 to -10pp        (varies by threshold)
Step 4: Entry timing             -2 to -5pp
Step 5: Capital constraints      30-50% fills lost (selection bias)
Step 6: Direction mismatch       -5 to -15pp       (if not filtered)
```

## Step 1: Training Decay (~3pp)

Qualified traders identified in a training window show modest HR decay in OOS. Skill partially persists but some traders drop out entirely (no OOS trades). This is NOT the problem — a few pp of decay is expected.

## Step 2: Position-Level vs Trade-Level Signal (THE CORE GAP)

This is where strategies fundamentally break if not handled correctly.

**Position level** (what vectorized sees):
- Each (trader, condition_id) is ONE observation
- Was the trader's NET position correct? YES/NO

**Trade level** (what tick-by-tick does):
- Each individual BUY trade is a SIGNAL
- Active traders make 10-30+ trades per position on average
- **Signal-to-outcome ratio: 10-30x**

**The dilution effect:**
- 1 correct position (1 outcome) generates N "copy this" trade signals
- 1 incorrect position ALSO generates N "copy this" trade signals
- A tick-by-tick follower enters on EACH of these N trades, paying spread/slippage each time, for the SAME binary outcome. The position-level edge is diluted N-fold.

## Step 3: Consensus Does NOT Improve Prediction (Above a Threshold)

> [!WARNING] More qualified traders in a market may mean WORSE prediction, not better.

High-consensus markets are POPULAR markets (elections, major events). These are efficiently priced with strong NO bias. All "skilled" traders pile in on the same obvious bet, but the price already reflects it.

Typical pattern across market types:
- Consensus 1-4: near random or slightly above base rate
- Consensus 5+: prediction quality degrades
- Consensus 20+: significantly below base rate (anti-predictive)

## Step 4: Entry Timing

| Timing | Pattern |
|--------|---------|
| <1h before resolution | Very high HR but near-zero edge (buying at 0.97+) |
| 1-24h | Moderate HR, low PnL per trade |
| 1-7d | Good HR, best edge per trade |
| 7-30d | Good HR, highest absolute PnL per position |
| 30d+ | HR slightly declines, long capital lock |

Late entries (<1h) have extremely high HR but tiny payoff — they're buying at prices near 1.0. The real edge is at multi-day horizons, but the strategy can't know at entry time how long the hold will be.

## Step 5: Multi-Trade Dilution

Positions with 10+ trades account for the vast majority (~85-90%) of all trade signals. These are heavily-traded markets where the position HR may be good but the follower pays spread 10+ times for one outcome.

A follower seeing the 10th trade in a market where 9 earlier trades already happened is getting ZERO new information. They're just diluting their entry.

## Step 6: Direction Mismatch

Traders qualified as skilled in one direction (e.g., NO) may trade the opposite direction (YES) in OOS. Without direction-aware filtering, the follower treats all trades equally, but cross-direction trades have significantly lower HR.

## Summary: Why Training HR -> Much Lower Tick-by-Tick HR

```
Training HR:           ~75-80%
  - OOS decay:         -3pp    → ~72-77%  (modest, expected)
  - Signal dilution:   -15-25pp → ~50-55%  (N trades per position)
  - Direction mismatch: -5pp   → ~45-50%  (cross-direction noise)
  - Consensus anti-pred: -3pp  → ~42-47%  (high-consensus = traps)
                                           Total gap: ~30-35pp
```

The **signal dilution** (Step 2) is the dominant effect. A follower seeing N trades per position, each paying spread, for one binary outcome, structurally cannot capture the position-level edge.

## Implications for Strategy Design

1. **Enter at the SIGNAL level, not trade level** — enter once per (trader, market), ignore subsequent trades from the same trader in the same market
2. **Limit consensus to small thresholds** — above 5 is often anti-predictive
3. **Direction-aware filtering** — only follow trades matching the trader's qualified direction
4. **De-duplicate signals** — if 3 traders are all trading the same market, that's 1 signal with 3 confirmations, not 3 x N trade-level signals

## Related
- `pitfalls/vectorized_vs_tick.md` — original gap documentation
- `pitfalls/consensus_dedup.md` — consensus must count unique traders
- `pitfalls/sell_is_exit.md` — SELL trades are exits or split-entries

## Tags
`critical`, `gap`, `vectorized`, `tick-by-tick`, `dilution`, `consensus`
