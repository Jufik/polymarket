---
name: research-challenger
description: "Capital efficiency methodology — compounding score evaluation, hold time analysis, exit criteria optimization. Used by the Challenger agent at review checkpoints."
user-invocable: false
---

# Challenger Methodology

Your job is to push for capital efficiency. Every dollar locked in a position has an opportunity cost.

## Core Metric: Compounding Score

```
compounding_score = excess_hr x avg_edge_usd / median_hold_days
```

Higher = faster capital recycling. This is the single most important metric for strategy comparison.

## Evaluation Framework

### 1. Hold Time Analysis

| Category | Typical Resolution | Capital Lock-up |
|----------|-------------------|-----------------|
| Sports | ~8 days | Low |
| Crypto 5/15-min | <1 hour | Very low |
| Culture/Entertainment | ~14 days | Medium |
| Politics | ~30+ days | High |
| Weather | ~7 days | Low |
| Finance | Variable | Variable |

Questions to ask:
- What is the median hold time for this strategy?
- What is the 90th percentile hold time?
- Are there outlier positions that lock capital for months?
- Could tighter exit criteria reduce hold time without destroying edge?

### 2. Capital Lock-up Cost

For a $1000 research budget:
- 20 max positions x $100 each = $2000 theoretical (but limited to $1000)
- If median hold is 30 days, capital turns over ~1x/month
- If median hold is 8 days, capital turns over ~3.75x/month

**Rule of thumb**: A strategy with 5pp excess HR and 8-day holds beats a strategy with 10pp excess HR and 30-day holds (compounding score: 0.625 vs 0.333).

### 3. Exit Criteria Suggestions

Push for tighter exits:
- Time-based: close after N days if no resolution
- Profit-taking: close if position reaches X% of max edge
- Stop-loss: close if market moves against position by Y%
- Consensus reversal: close if qualified trader sentiment flips

### 4. Throughput Analysis

- Trades per month: how many positions can the strategy generate?
- Hit rate stability: is edge consistent month-over-month?
- Category concentration: is the strategy limited to slow-resolving categories?

## Output Format

Write to the assigned review file:

```markdown
# Challenger Review: {slug} (Round {N})

## Compounding Score Assessment
- Excess HR: {X}pp
- Avg edge: ${Y}/trade
- Median hold: {Z} days
- **Compounding score**: {score}
- Benchmark: {how does this compare to target of 0.5+?}

## Hold Time Analysis
- Median: {X} days
- 90th percentile: {Y} days
- Distribution shape: {concentrated / long-tailed}
- Capital turns per month: {Z}

## Capital Efficiency Suggestions
1. {Specific suggestion to improve compounding score}
2. {Another}

## Category Recommendation
- Current category: {X} (typical resolution: {Y} days)
- Better category fit? {suggestion if applicable}

## Risk Caveat
{Acknowledge what would be lost if suggestions are followed too aggressively}

## Summary
{One paragraph: is the capital efficiency acceptable, or must it improve for viability?}
```
