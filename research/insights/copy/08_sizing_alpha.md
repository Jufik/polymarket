# The Edge Is Position Sizing, Not Direction

**Date**: 2026-02-19
**Method**: Walk-forward monthly holdout, May 2025 - Jan 2026
**Pool**: 9m consistent, pure_taker (MVF<0.10), 20+ markets, median entry <= 0.80

---

## Key Finding

Consistent traders' edge is in **position sizing**, not direction prediction. Their directional accuracy is only 37-54% across holdout months — below breakeven for binary bets. Yet they are reliably profitable because they bet large on high-conviction markets and small on speculative ones.

## Evidence: Sizing Asymmetry (Oct 2025 window)

| | Winning Positions | Losing Positions |
|---|--:|--:|
| Count | ~60% | ~40% |
| Avg volume | ~$2,400 | ~$1,000 |
| Median volume | ~$800 | ~$400 |
| Avg PnL | +$680 | -$450 |

**Winners are 2.4x larger than losers by volume.** The average win is bigger than the average loss because more capital was committed to it, not because the directional call was better.

## Why Fixed-Bet Copy Fails

Fixed $100 bets per market destroy the sizing signal:
- A trader's $5,000 high-conviction bet and $200 exploratory bet both become $100
- The exploratories (low hit rate, small original size) now have equal weight
- Net result: **negative PnL** from fixed-bet copy despite copying profitable traders

Walk-forward backtest of $100 fixed bets across the pool: copy win rate drops to 33-40%, below the ~38% YES base rate. The strategy loses money.

## Why Proportional Copy Works

Proportional copy preserves the sizing signal by allocating capital in proportion to the trader's own position sizes:
- `your_pnl = (your_capital / trader_total_volume) * trader_pnl`
- Large bets stay large, small bets stay small
- The sizing alpha is transmitted intact

## Implication for Live Execution

**You must replicate relative sizing, not just direction.** This means:
1. Track each trader's recent average position size as a baseline
2. Scale your copy bet proportionally to the deviation from their baseline
3. A trader betting 5x their average is a much stronger signal than 0.5x
4. Never flatten all copy bets to a uniform size

---

## Three Allocation Approaches Compared ($10K capital, 9 months)

| Approach | Monthly PnL | ROI/mo | Annualized |
|----------|----------:|-------:|-----------:|
| Vol-weighted (all traders) | $1,424 | 14.2% | 171% |
| Vol-weighted (top-5 by volume) | $2,723 | 27.2% | 327% |
| Equal-weight (1/N by ROI) | $2,094 | 20.9% | 251% |

These are upper bounds assuming perfect replication. Apply haircuts:

| Scenario | Vol-weighted | Equal-weight |
|----------|----------:|----------:|
| Optimistic (30% haircut) | $997/mo | $1,466/mo |
| Realistic (50% haircut) | $712/mo | $1,047/mo |
| Conservative (70% haircut) | $427/mo | $628/mo |
