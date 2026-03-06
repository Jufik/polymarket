# Pre-Validation Config Checklist

Before running `pm-harness run`, verify ALL of the following:

## Strategy Config
- [ ] `mode = "replay"` (not "paper_dev" or "live")
- [ ] `capital_usd` matches research budget (typically 1000)
- [ ] `max_position_usd` is reasonable (100 for $1000 capital)
- [ ] `cooldown_s = 0` for replay (no cooldown needed)

## Provider Config
- [ ] Provider `params` match discovery sweep parameters exactly
- [ ] `refresh_interval_s` set (ignored in replay but good practice)

## Harness Config
- [ ] `executor = "realistic"` (NOT "simulated")
- [ ] `settlement_enabled = true`
- [ ] `resolution_source = "asset_id"`
- [ ] `bootstrap_hours` sufficient for strategy's consensus building time

## Strategy Code
- [ ] `on_trade()` has explicit SELL policy (BUY-only, directional mapping, or weighted — see `pitfalls/sell_is_exit.md`)
- [ ] Consensus counts unique traders (set, not counter)
- [ ] No look-ahead: features use only data available at trade time
- [ ] Gambling markets excluded (check susceptibility or question text)

## Data
- [ ] Period has sufficient resolved markets for the category
- [ ] Token map loaded for asset_id resolution
- [ ] Resolution data covers the full period
