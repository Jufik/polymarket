# Hypothesis: {TITLE}

**Status**: `discovery` | `validation` | `promoted` | `rejected`
**Created**: {DATE}
**Category**: {politics | sports | esports | crypto | culture | finance | weather}

## Statement

{One sentence: what signal are we testing and why it should predict outcomes.}

## Success Criteria

- Excess HR > ___pp above base rate (NO: 62%, YES: 38%)
- Positive PnL after realistic slippage
- Compounding score > ___
- Sample size > 100 trades OOS

## Scores

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | — | — | — |
| Sharpe | — | — | — |
| Avg Edge | — | — | — |
| Compounding | — | — | — |
| Trades/mo | — | — | — |

## Decision

{Why promoted / rejected / parked. Reviewer consensus summary.}

## Anti-Knowledge (if rejected)

What we learned from this failure:

- **Signal tested**: {what didn't work}
- **Why it failed**: {root cause — no signal / survived only in-sample / edge below slippage / etc.}
- **Conditions for revisiting**: {what would need to change for this to become viable}
- **Generalizable lesson**: {what applies beyond this specific hypothesis}

> Captured to: `research/knowledge/{category}/{slug}.md` (if generalizable)
