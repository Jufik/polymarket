---
name: research-engineer
description: "Methodology audit and viability estimation — entry price audit, fill model comparison, promotion gate likelihood. Used by the Engineer agent after validation."
user-invocable: false
---

# Engineer Methodology

Your job is to audit the validation methodology and estimate production viability. You assess whether the research results would translate to real-world paper trading performance.

## Audit Dimensions

### 1. Entry Price Audit

Compare how the strategy computes entry prices vs what would happen live:

| Source | Price | Context |
|--------|-------|---------|
| `max_price` (backtest) | Strategy's intent price | What the strategy asks for |
| wavg entry (positions) | `yes_px_vol / volume` | Historical average from CH |
| Orderbook (live) | Best ask/bid | What PaperExecutor would get |

Questions:
- Is `max_price` realistic? Would the orderbook actually fill at that price?
- How does the wavg entry compare to `max_price`?
- Is there significant spread between intent and likely fill?

### 2. Fill Model Comparison

| Model | Slippage | Impact | Rejection |
|-------|----------|--------|-----------|
| SimulatedExecutor | None | None | Never |
| RealisticFillSimulator | Calibrated half-spread | size/liquidity | Optional |
| PaperExecutor (live) | Real orderbook | Real | If no liquidity |

Check:
- Was `RealisticFillSimulator` used (not `SimulatedExecutor`)?
- Are calibrated spreads reasonable for the markets traded?
- What is the average slippage cost per trade?

### 3. Bootstrap Window Assessment

The strategy needs a warm-up period to build consensus/features:
- Is `bootstrap_hours` sufficient for the strategy's consensus window?
- Are early trades (during bootstrap) included in performance metrics?
- Would the provider have enough data to compute features on day 1 of paper trading?

### 4. Position Sizing Viability

At target capital (from config):
- What is the average position size?
- Is there enough orderbook depth to fill positions at that size?
- What percentage of capital is typically deployed?

### 5. Slippage Estimation at Target Size

Scale slippage to realistic production sizes:
- Research: typically $100/position
- Paper: typically $100-500/position
- Live: typically $500-5000/position
- How does slippage scale? (linear or nonlinear from impact model)

### 6. Promotion Gate Likelihood

Check against promotion thresholds:
- `min_trades`: does the strategy generate enough trades per month?
- `min_sharpe`: is the Sharpe ratio above threshold (typically 0.5)?
- `min_fills`: are most intents getting filled?
- `max_drawdown`: is the maximum drawdown manageable?
- `min_runtime_hours`: can it survive a minimum paper trading period?

## Output Format

Write to the assigned review file. See `checklist.md` for detailed formulas.

```markdown
# Engineer Review: {slug} (Round 2)

## Entry Price Audit
- Strategy max_price: {typical value}
- Estimated live fill price: {estimate}
- Spread impact: {estimate}
- Assessment: {realistic / optimistic / pessimistic}

## Fill Model Assessment
- Executor used: {realistic / simulated}
- Average slippage per trade: ${X}
- Rejection rate: {Y}%
- Assessment: {appropriate / too lenient / too strict}

## Bootstrap Window
- Config: {X} hours
- Strategy needs: {Y} hours (estimated)
- Assessment: {sufficient / insufficient}

## Position Sizing
- Average position: ${X}
- Capital utilization: {Y}%
- Orderbook depth adequate: {yes / no / unknown}

## Slippage at Scale
- At $100/position: {X}% impact
- At $500/position: {Y}% impact
- At $1000/position: {Z}% impact

## Promotion Readiness

| Gate | Threshold | Current | Status |
|------|-----------|---------|--------|
| min_trades | 1000 | {X} | {pass/fail} |
| min_sharpe | 0.5 | {X} | {pass/fail} |
| max_drawdown | $500 | ${X} | {pass/fail} |
| positive PnL | >0 | ${X} | {pass/fail} |

## Summary
{One paragraph: is this strategy viable for paper trading? What are the key risks?}
```
