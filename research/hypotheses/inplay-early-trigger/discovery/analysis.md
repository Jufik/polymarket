# InPlay Early Trigger Hypothesis -- REJECTED

> **TL;DR**: The hypothesis "enter when InPlay fires IF Sports consensus >= 2 already exists"
> is structurally invalid. InPlay (N=1) ALWAYS fires BEFORE Sports (N=2) because 1 < 2.
> No hybrid combining these signals can improve on Sports N=2 alone.

> [!CRITICAL]
> This hypothesis contains a fundamental logical error: a lower consensus threshold (N=1)
> will always trigger before a higher one (N=2) on the same pool. Sports consensus cannot
> exist at InPlay's signal time. Using Sports as a retroactive filter for InPlay is look-ahead bias.

## Hypothesis

**Signal**: Enter when InPlay trader fires on a market where Sports consensus >= 2 already exists.
**Thesis**: Combine InPlay's 9h timing advantage with Sports' quality filter. Expected to halve median hold time while maintaining HR.
**Null**: InPlay and Sports are the same pool at different consensus thresholds; no timing advantage is possible.
**Origin**: Portfolio analysis (2026-03-09) identified 99.5% market overlap between tracks.

## Data

Both strategies validated by tick-by-tick replay (SyncReplayRunner, 2025-07-01 to 2026-03-01):

| Metric | Sports YES (K=25, N=2) | InPlay (K=25, N=1) |
|--------|----------------------|---------------------|
| Pool | Same 25 traders | Same 25 traders |
| Fills | 2,023 | 5,930 |
| HR | 63.3% | 60.2% |
| Excess HR | +30.0pp | +26.9pp |
| PnL | $162,157 | $9,992 |
| Sharpe | 5.23 | 0.27 |
| Median hold | 0.16d (3.8h) | 0.20d (4.8h) |
| Compounding score | 148.2 | 2.2 |

Source: `research/output/ledger_sports_yes_v3_k25_n2.parquet`, `research/output/ledger_sports_inplay_v3_k25_n1.parquet`

## Key Findings

### 1. Timing: InPlay ALWAYS fires first

Of 2,013 shared markets (both strategies fired):

| Order | N | Pct | Median gap |
|-------|---|-----|-----------|
| InPlay first (>30 min gap) | 1,238 | 61.5% | 9.2h |
| Simultaneous (<=30 min gap) | 775 | 38.5% | 2.9 min |
| Sports first | 0 | 0% | -- |

The "simultaneous" cases occur when the 2nd pool trader's BUY (reaching N=2 consensus) is itself
the InPlay trigger (same trade triggers both strategies). InPlay never fires AFTER Sports.

### 2. Confirmation rate: only 34% of InPlay signals get confirmed

| Subset | N fills | HR | PnL |
|--------|---------|-----|-----|
| InPlay total | 5,930 | 60.2% | $9,992 |
| Confirmed by Sports N=2 | 2,013 | 63.2% | $10,474 |
| Unconfirmed (InPlay only) | 3,917 | 58.7% | -$482 |

Two-thirds of InPlay signals never reach Sports consensus. The unconfirmed signals have
negative PnL and are degrading (Jan 2026: -$4,180, Feb 2026: -$7,350).

### 3. Price dynamics: Sports gets BETTER prices on eventual winners

On the 1,238 markets where InPlay entered first:

| Metric | Sports | InPlay |
|--------|--------|--------|
| Avg fill price | 0.664 | 0.548 |
| WON avg fill price | 0.702 | 0.587 |
| LOST avg fill price | 0.609 | 0.493 |
| PnL | $88,549 | $14,264 |

**Price movement between InPlay entry and Sports entry:**

| Direction | N | Pct | HR | Sports avg win | InPlay avg win |
|-----------|---|-----|----|---------------|---------------|
| Price moved UP (Sports higher) | 794 | 64.1% | 60.3% | $28.2 | $96.9 |
| Price moved DOWN (Sports lower) | 375 | 30.3% | 55.5% | $594.9 | $79.6 |
| Same | 69 | 5.6% | -- | -- | -- |

When price moves DOWN between signals (30% of cases), Sports enters at dramatically lower
prices (long-shots), yielding enormous per-win payouts ($595 vs $80). This is the primary
source of Sports' PnL advantage: the 2nd trader enters AFTER the market has moved toward
the correct outcome on long-shot markets.

### 4. Hold time INCREASES with earlier entry

| Entry point | Median hold |
|-------------|-------------|
| Sports N=2 | 4.2h |
| InPlay N=1 | 15.1h |
| Additional hold | +10.9h |

Earlier entry does NOT decrease hold time. Resolution timing is fixed; entering earlier
just adds more waiting before the same resolution event.

### 5. No real-time proxy for future confirmation

Feature analysis at InPlay signal time:

| Feature | Confirmed | Unconfirmed |
|---------|-----------|-------------|
| Fill price (mean) | 0.624 | 0.612 |
| Fill price (median) | 0.570 | 0.550 |
| Max price (mean) | 0.624 | 0.612 |
| Hold time (median) | 6.4h | 4.5h |

Differences are marginal and non-actionable. No observable feature at InPlay's signal time
can predict whether Sports N=2 will eventually fire.

Pre-existing pool positions from training period: only 7 markets (Sports events are ephemeral).

### 6. Alternative "staged entry" variant also fails

**Variant**: Place limit order at InPlay's price when N=1 fires; cancel if Sports N=2
doesn't fire within T hours.

Failure modes:
- Only 34% of orders would get confirmed (66% capital waste)
- Capital locked for 9h median before confirmation or cancellation
- On confirmed markets, hold time is +10.9h vs Sports direct entry
- PnL per position-day: $12.37 (hybrid) vs $209.65 (Sports direct)

## Compounding Scores

| Strategy | HR | Excess HR | Avg edge | Median hold | CS |
|----------|----|-----------|----------|-------------|-----|
| Sports N=2 | 63.3% | +30.0pp | $80.2 | 0.16d | **148.2** |
| InPlay N=1 | 60.2% | +26.9pp | $1.7 | 0.20d | 2.2 |
| Hybrid (InPlay-first subset) | 58.7% | +25.4pp | $11.5 | 0.63d | 5.5 |

Sports standalone is **66x** more capital efficient than InPlay.

## Root Cause

The hypothesis rested on a flawed mental model: that InPlay and Sports are independent
signal sources with different timing. In reality:

1. **Same pool**: Both strategies use the identical K=25 BEH-gated Sports YES pool.
2. **Same direction**: Both buy YES on the same condition_ids.
3. **Threshold ordering**: N=1 (InPlay) is mathematically guaranteed to fire before N=2 (Sports).
4. **No information gain**: "InPlay fired first" is tautological when thresholds differ.

The 9h "timing advantage" of InPlay is actually 9h of additional uncertainty -- the market
hasn't yet attracted a 2nd pool trader, meaning confidence is lower. Sports' 2nd trader
entering later is itself the information event.

## Verdict

**REJECTED.** The hypothesis is invalid on three independent grounds:
1. **Structural**: N=1 always fires before N=2 (tautological for same pool)
2. **Look-ahead**: Using future Sports confirmation to filter InPlay entries
3. **No proxy**: No observable feature at InPlay time predicts future confirmation

## Recommendation

Keep Sports YES (K=25, N=2) as a standalone track. InPlay adds negative incremental value.
Capital is better deployed finding new uncorrelated tracks (see portfolio analysis).

## Spawned Ideas

1. **Cross-pool consensus**: Use a DIFFERENT pool for the InPlay signal (e.g., in-play elite
   traders from `knowledge/signals/in_play_elite_traders.md`) and Sports consensus from the
   scorecard pool. Different pools could provide genuinely independent information.

2. **Sports N=3 with higher HR**: Instead of trying to improve timing, raise the consensus
   bar for Sports. N=3 would have fewer fills but potentially higher HR and edge per trade.

3. **Price-movement signal**: The finding that Sports enters at lower prices on eventual
   winners (price moved DOWN in 30% of cases, yielding $595 avg win) suggests that
   price DECLINE after first pool trader entry is itself a bullish signal for long-shots.

## Artifacts

- Analysis: this file
- Sports ledger: `research/output/ledger_sports_yes_v3_k25_n2.parquet`
- InPlay ledger: `research/output/ledger_sports_inplay_v3_k25_n1.parquet`
- Portfolio analysis: `research/hypotheses/portfolio-three-tracks/discovery/portfolio_analysis.md`
- Tick validation: `research/hypotheses/scorecard-v3-strategies/validation/tick_results_v3.md`

## Tags

`inplay`, `sports`, `consensus`, `timing`, `rejected`, `look-ahead-bias`
