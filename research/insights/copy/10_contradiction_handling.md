# Handling Trader Contradictions

**Date**: 2026-02-19
**Method**: Walk-forward monthly holdout, $1,500 equal-weight, May 2025 - Jan 2026
**Pool**: 9m consistent, pure_taker, 20+ markets, entry <= 0.80

---

## Problem

When copying multiple traders, they sometimes take opposite sides of the same market. Trader A buys YES, Trader B buys NO. How should we handle this?

## Frequency

| Metric | Value |
|--------|------:|
| Markets with contradictions | 3-7% of markets per month |
| Volume in contradicted markets | 16-35% of total pool volume |
| Capital at risk in contradictions | ~30% of deployed capital |

Contradictions are rare by market count but disproportionately affect volume because contradicted markets tend to be popular (multiple traders notice them).

## Four Policies Tested (Ex-Ante)

Starting from scratch each month, which markets to enter:

| Policy | 9-Month PnL | Monthly Avg | Description |
|--------|----------:|----------:|-------------|
| Copy all (both sides) | $6,195 | $688 | Enter every signal, contradictions cancel out |
| Majority-wins | $6,540 | $727 | Follow the side with more traders |
| **Skip contradicted** | **$7,529** | **$836** | Don't enter markets with contradictions |
| Vol-weighted net | $6,320 | $702 | Weight by trader volume, take net direction |

**Skip contradicted wins** (+$1,334 over copy-all, +22% improvement). By avoiding markets where the pool disagrees, you filter out genuinely uncertain markets and concentrate capital on consensus plays.

## What About Positions Already Entered?

The harder question: you entered a market copying Trader A (YES), and later Trader B enters the same market on NO. Should you exit?

### Temporal Analysis

| Metric | Value |
|--------|------:|
| Avg time between first and contradicting entry | 17-19 hours |
| First mover wins (correct side) | 49-53% |
| Second mover wins | 47-51% |

Neither the first nor second mover is reliably right — it's a coin flip.

### HOLD Is Optimal

| Action | Reasoning |
|--------|-----------|
| **HOLD (do nothing)** | Best. Exiting costs spread for zero gain. Contradicted markets still have positive aggregate PnL. |
| Exit on contradiction | Bad. You pay the bid-ask spread to exit, and neither side is reliably wrong. |
| Double down on majority | Bad. Increases exposure to a genuinely uncertain market. |

**Evidence**: Contradicted markets contribute $120-352/month in PnL at $1,500 scale, representing ~20-60% of monthly returns. Exiting them would sacrifice this PnL plus pay transaction costs.

## Recommended Policy

1. **Before entry**: Skip markets where the pool is already contradicted. Allocate that capital to non-contradicted markets instead.
2. **After entry**: If a contradiction develops later, HOLD your existing position. Do not exit or reverse.
3. **Rebalancing**: At each monthly rebalance, re-evaluate. If the contradiction persists and the market is still open, skip it for the next period.
