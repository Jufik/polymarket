# Engineer Viability Checklist

## Entry Price Estimation

```
estimated_fill_price = max_price + half_spread + impact
half_spread = calibrated from trade-to-trade price changes (median abs change)
impact = size_usd / estimated_liquidity * impact_scale
```

If `estimated_fill_price > max_price * 1.05`, flag as `> [!WARNING]` — entry prices may be optimistic.

## Slippage Scaling Formula

```
slippage_at_size = half_spread * size_usd + (size_usd^2 / liquidity) * impact_scale

# For typical Polymarket markets:
# half_spread ~ 0.005-0.02 (0.5-2%)
# liquidity ~ $10K-100K daily volume
# impact_scale ~ 0.1
```

## Capital Utilization

```
utilization = sum(open_position_cost) / capital_usd
# Target: 60-80% deployed
# < 40%: strategy too selective (not enough trades)
# > 90%: capital-constrained (rejecting intents due to budget gate)
```

## Promotion Gate Formulas

```python
# From strategies/promotion.py defaults:
min_trades = 1000          # minimum total trades in backtest
min_sharpe = 0.5           # annualized Sharpe ratio
min_fills = 100            # minimum fills in paper period
min_runtime_hours = 168    # 7 days minimum paper runtime
max_drawdown = 500         # max $ drawdown
positive_pnl = True        # net PnL must be positive
```

## Monthly Trade Rate

```
trades_per_month = total_intents / period_months
# If < 50/month: may not meet min_trades in reasonable paper period
# If > 500/month: check if strategy is overtrading
```

## Risk-Adjusted Throughput

```
risk_adj_throughput = trades_per_month * hit_rate * avg_edge_usd
# This is the expected monthly $ generated
# Compare against capital cost: risk_adj_throughput / capital_usd
# Target: > 5% monthly return risk-adjusted
```
