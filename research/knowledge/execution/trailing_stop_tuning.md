# Trailing Stop Loss Tuning on Prediction Markets

> **TL;DR**: Trailing SL is neutral-to-negative in aggregate on prediction markets, but provides +16-37% PnL improvement for positions entered at 0.30-0.70. Harmful outside that range.

> [!CRITICAL]
> Do NOT apply trailing stops to longshot entries (< 0.30) or high-confidence entries (> 0.70). Longshots have 3-10x upside that the SL clips catastrophically; high-entry positions have 80%+ HR so the SL mostly hurts winners.

> [!WARNING]
> Prediction markets have binary resolution ($0 or $1). Winners that dip will recover to $1.00 payout. A trailing stop that triggers on a temporary dip converts a full win into a partial gain. This is fundamentally different from equity markets where there is no guaranteed resolution price.

> [!TIP]
> Gate SL registration on entry price: only register when `0.30 <= fill_price < 0.70`. Use `trail_delta = 0.08-0.10` in this band. This turns aggregate neutral results into +$2K improvement on Sports YES (2023 positions backtest).

## Finding

Swept 9 `trail_delta` values (0.03 to 0.30) across 2,719 historical positions from three consensus strategies. The trailing stop ratchets up with the high watermark and never moves down.

**Aggregate results (all entry prices):**

| Strategy | Positions | Hold PnL | Best SL PnL | Improvement |
|----------|-----------|----------|-------------|-------------|
| Sports YES | 2,023 (63% HR) | $162,157 | $162,194 (δ=0.03) | +$36 (+0.0%) |
| Politics NO | 346 (83% HR) | $33,942 | $31,649 (δ=0.05) | -$2,292 (-6.8%) |
| Politics YES | 350 (62% HR) | -$7,750 | -$8,086 (δ=0.03) | -$336 (-4.3%) |

**Bucketed by entry price — where SL has positive edge:**

| Strategy | Entry Bucket | HR | Best δ | Improvement |
|----------|-------------|-----|--------|-------------|
| Sports YES | [0.30, 0.50) | 43% | 0.10 | **+$631 (+37%)** |
| Sports YES | [0.50, 0.70) | 49% | 0.03 | **+$1,243 (+17%)** |
| Politics NO | [0.50, 0.70) | 50% | 0.05 | **+$97 (+60%)** |

**Where SL hurts:**

| Strategy | Entry Bucket | HR | Why |
|----------|-------------|-----|-----|
| Sports YES | [0.00, 0.30) | 57% | Upside 3-10x, stop clips winners |
| Politics NO | [0.00, 0.30) | 80% | High HR, winners need room |
| Politics YES | [0.70, 1.00) | 62% | Limited upside (15-30c), SL clips more than saves |
| Politics NO | [0.70, 1.00) | 86% | Almost all win, SL only hurts |

## Evidence

Script: `research/scripts/tune_trailing_stop.py`

Method: For each position in the ledger, loaded all trades on the same `asset_id` after entry from the Parquet snapshot (`data/research/trades/`), replayed the trailing stop at each delta value, compared exit PnL vs hold-to-resolution PnL.

Data: ~6.7M post-entry trades across 2,719 positions. Ledgers from tick-by-tick validated backtests (SyncReplayRunner).

## Impact

1. **Configuration**: Only enable `trail_delta` in TOML for strategies that enter in the 0.30-0.70 band. Sports YES qualifies partially; Politics strategies do not.
2. **Implementation**: `PositionMonitor.register()` should gate on `fill_price` — skip registration outside the [0.30, 0.70) band.
3. **Capital recycling**: SL exits free capital days earlier (207 weighted position-days for Sports [0.50, 0.70) at δ=0.10). This benefit is NOT captured in the direct PnL comparison — the freed capital could enter new winning positions.
4. **Drawdown reduction**: Even when aggregate PnL is neutral, the SL smooths the equity curve by capping individual position losses.

## Related

- `execution/hold_time_capital.md` — hold time impact on capital efficiency
- `data/price_level_base_rates.md` — base rates by entry price level
- `pitfalls/vectorized_vs_tick.md` — simulation accuracy context

## Tags

`trailing-stop`, `risk-management`, `entry-price`, `capital-efficiency`, `position-management`
