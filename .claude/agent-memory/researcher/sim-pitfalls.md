# Simulation Pitfalls — Fill Price Artifacts

## SimulatedExecutor Fill Price Artifact (discovered 2026-03-09)

**Root cause**: `SimulatedExecutor.execute()` fills at `intent.max_price`
(or `default_price=0.50`) regardless of actual market price at signal time:

```python
price = intent.max_price if intent.max_price is not None else self.default_price
```

**Consequence when max_price is used as a filter (e.g., max_price=0.80)**:
- All fills show price=0.80 in ledger
- Break-even HR = 80% (binary market: win +$20, lose -$80)
- Real signal with 62% HR appears as negative PnL
- Sharpe and profit factor are meaningless

**Detection**: `settled['fill_price'].unique()` returns a single value = max_price.

**Correct mental model**:
- max_price=0.80 should mean: "skip this market if YES already costs > 0.80"
- In tick replay, the actual fill should be at triggering_trade_price + slippage
- SimulatedExecutor doesn't look at actual market price at all

**Workaround for analysis**: Run realistic_pnl.py script — uses yes_entry_data to
get Nth pool trader entry price (= consensus trigger price) as fill proxy.

**Workaround for simulation**: Use RealisticFillSimulator with calibrated spreads,
or patch SimulatedExecutor to check actual tick price.

**Key finding from Politics YES v3**:
- max_price=0.80, all 350 fills at 0.80 → PnL = -$7,750 (artifact)
- Actual consensus trigger prices: median 0.54-0.62 (from yes_entry_data)
- Realistic fill at trigger+0.02: avg PnL ≈ +$178/fill (strongly positive)

**Rule**: When max_price is a filter (not a target price), only use HR/excess HR
from tick simulations. PnL, Sharpe, max drawdown are all invalid.

## Fill Price Break-Even Formula

For binary markets (YES/NO tokens):
- Buy at price p, size $S
- Win: receive $1/token, qty = S/p, net = S×(1-p)/p
- Lose: lose $S
- Break-even: S×HR×(1-p)/p = S×(1-HR)
- → HR_breakeven = p (= fill price itself)

Examples:
- Fill at 0.80: need HR > 80% to profit
- Fill at 0.50: need HR > 50% to profit
- Fill at 0.30: need HR > 30% to profit

Always verify avg(fill_price) from ledger and compare to observed HR.
