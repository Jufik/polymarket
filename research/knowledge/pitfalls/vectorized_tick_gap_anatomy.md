# Anatomy of the Vectorized-to-Tick-by-Tick Gap

> [!CRITICAL] The gap is NOT a single issue. It's a cascade of 6 compounding effects,
> each reducing HR by 3-15pp. Understanding each step is required before building
> any copy-trading strategy.

## The Pipeline (each step degrades signal)

```
Step 1: Training → OOS          76.5% → 73.5%    (-3.0pp)
Step 2: Position → Trade level   73.5% → ~50%     (-23pp) ← BIGGEST GAP
Step 3: Consensus quality        varies            (0 to -10pp)
Step 4: Entry timing             varies            (-2 to -5pp)
Step 5: Capital constraints      50% fills lost    (selection bias)
Step 6: Direction mismatch       varies            (-5 to -15pp)
```

## Step 1: Training Decay (-3pp)

| Phase | Traders | Positions | HR | Avg PnL |
|-------|---------|-----------|-----|---------|
| Training (Jan-Jun '25) | 284 | 94,215 | 76.5% | $86.01 |
| OOS (Jul '25) | 272 | 15,812 | **73.5%** | $26.01 |

**-3pp decay.** Modest and expected — skill partially persists. This is NOT the problem.
12 traders (4%) dropped out entirely (no OOS trades).

## Step 2: Position-Level vs Trade-Level Signal (THE CORE GAP)

This is where the strategy fundamentally breaks.

**Position level** (what vectorized sees):
- Each (trader, condition_id) is ONE observation
- Was the trader's NET position correct? YES/NO
- YES positions: 9,236 at 66.5% HR
- NO positions: 21,895 at 81.8% HR

**Trade level** (what tick-by-tick does):
- Each individual BUY trade is a SIGNAL
- YES positions generate 16.6 trades each on average
- NO positions generate 26.2 trades each on average
- **Signal-to-outcome ratio: 16-26x**

**The dilution effect:**
- 1 correct YES position (1 outcome) generates 16.6 "copy this" signals
- 1 incorrect YES position ALSO generates 16.6 "copy this" signals
- The copier sees 152,897 YES trade signals for 9,236 unique outcomes
- **PnL per trade signal: $1.75 (YES) and $3.83 (NO)** vs per position: $29/$100

A tick-by-tick copier enters on EACH of these 16-26 trades, paying spread/slippage
each time, for the SAME binary outcome. The position-level edge is diluted 16-26x.

## Step 3: Consensus Does NOT Improve Prediction

> [!WARNING] More qualified traders in a market = WORSE prediction, not better.

| Consensus | Markets | YES Rate | Avg Entry |
|-----------|---------|----------|-----------|
| 1 | 1,057 | 50.0% | 0.493 |
| 2 | 901 | 49.3% | 0.490 |
| 3 | 489 | 51.7% | 0.503 |
| 4 | 291 | 54.0% | 0.519 |
| 5 | 167 | 47.3% | 0.470 |
| 6-10 | 543 | 37.0% | 0.375 |
| 11-20 | 504 | 25.6% | 0.260 |
| 20+ | 186 | 19.0% | 0.200 |

**Consensus is ANTI-PREDICTIVE above 5 traders.** Markets with 20+ qualified
traders have only 19% YES rate (vs 50% at consensus 1-3).

**Why**: High-consensus markets are POPULAR markets (elections, major events).
These are efficiently priced with strong NO bias. All "skilled" traders pile in
on the same obvious bet, but the price already reflects it.

## Step 4: Entry Timing

| Timing | Positions | HR | Avg PnL | Entry Price |
|--------|-----------|-----|---------|-------------|
| <1h before resolution | 1,168 | **97.4%** | $25.54 | 0.966 |
| 1-24h | 11,204 | 74.5% | $2.23 | 0.740 |
| 1-7d | 7,724 | 76.4% | $71.48 | 0.739 |
| 7-30d | 5,186 | 79.2% | **$307.17** | 0.753 |
| 30d+ | 5,849 | 77.9% | $45.41 | 0.749 |

**Late entries (<1h) have 97% HR but only $25 PnL** — they're buying at 0.97,
so even if correct, the payout is tiny. **The real edge is at 7-30d** ($307/pos)
but the copier can't know at entry time how long the hold will be.

## Step 5: Multi-Trade Dilution

| Trades per Position | Positions | Total Trades | Position HR | Avg PnL |
|---------------------|-----------|-------------|-------------|---------|
| 1 trade | 5,460 | 5,460 | 77.7% | $11.47 |
| 2-3 trades | 5,867 | 14,190 | 77.6% | $1.35 |
| 4-10 trades | 8,757 | 55,732 | 78.7% | $19.32 |
| **10+ trades** | **11,047** | **650,969** | 75.7% | $201.48 |

**10+ trade positions account for 89% of all trade signals** (650K of 726K).
These are heavily-traded markets where the position HR is good (75.7%) and
PnL is high ($201), but the copier pays spread 10+ times for one outcome.

A copier seeing the 10th trade in a market where 9 earlier trades already
happened is getting ZERO new information. They're just diluting their entry.

## Step 6: Direction Mismatch

| Qualified As | OOS Direction | Trades | HR | Avg PnL |
|-------------|---------------|--------|-----|---------|
| NO → NO | Same | 6,030 | **82.5%** | $82.86 |
| YES → YES | Same | 3,230 | 64.1% | $55.79 |
| NO → YES | Cross | 1,706 | 61.9% | $13.60 |
| YES → NO | Cross | 3,750 | **76.9%** | $179.76 |

**Cross-direction trades are problematic:**
- NO-qualified traders buying YES: 61.9% HR (vs 82.5% when staying NO)
- YES-qualified traders buying NO: 76.9% HR (decent, but they're not qualified for NO)

Without direction-aware filtering, the copier treats all trades equally.
22% of OOS positions (5,456 of 14,716) are in the WRONG direction.

## Summary: Why 76.5% Training → 42% Tick-by-Tick

```
Training HR:           76.5%
  - OOS decay:         -3.0pp  → 73.5%  (modest, expected)
  - Signal dilution:   -23pp   → ~50%   (16-26 trades per position)
  - Direction mismatch: -5pp   → ~45%   (22% of trades are cross-direction)
  - Consensus anti-pred: -3pp  → ~42%   (high-consensus markets are traps)
                                         Total gap: ~34pp
```

The **signal dilution** (Step 2) is the dominant effect. A copier seeing 16-26
trades per position, each paying spread, for one binary outcome, structurally
cannot capture the position-level edge.

## Implications for Strategy Design

1. **Copy at the POSITION level, not trade level** — enter once per (trader, market),
   ignore subsequent trades from the same trader in the same market
2. **Limit consensus to 3-4** — above 5 is anti-predictive
3. **Direction-aware filtering** — only copy trades matching the trader's qualified direction
4. **De-duplicate signals** — if 3 traders are all trading the same market,
   that's 1 signal, not 3 × N signals (where N is trades per trader)
5. **The insider copy strategy works because** it uses position-level signals
   (infrequent, high-conviction entries) rather than copying every trade

## Related
- `pitfalls/vectorized_vs_tick.md` — original gap documentation
- `pitfalls/consensus_dedup.md` — consensus must count unique traders
- `pitfalls/sell_is_exit.md` — SELL trades are exits
- `signals/tag_edge_analysis.md` — tag-specific analysis

## Tags
`critical`, `gap`, `vectorized`, `tick-by-tick`, `dilution`, `consensus`
