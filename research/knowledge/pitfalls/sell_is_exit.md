# SELL Trade Semantics — Not Simply "Exit"

> **TL;DR**: SELL trades are directional (SELL YES = bearish, SELL NO = bullish), but their signal strength is ambiguous because they could be a position exit OR a new entry via the split mechanic. Whether to include or exclude SELLs from copy signals is a research parameter, not a hard rule.

> [!WARNING]
> Blindly interpreting SELL YES as "go NO" (or vice versa) caused a 33pp HR collapse in early tick-by-tick testing. However, blanket exclusion of all SELLs also discards legitimate directional signal from split-and-sell entries. Test both approaches in vectorized sweeps.

## The Split Mechanic (CTF)

Polymarket's Conditional Token Framework allows:
- **Split**: $100 USDC → 100 YES + 100 NO tokens (atomic, on-chain, NOT an OrderFilled event)
- **Merge**: 100 YES + 100 NO → $100 USDC (reverse)
- **Redeem**: After resolution, winning tokens → $1 each

A trader wanting to go long YES at 0.60 has two equivalent routes:
1. **Direct BUY YES** at 0.60 → appears as `side=BUY, asset_id=YES_token`
2. **Split $1 → 1 YES + 1 NO**, then **SELL NO** at 0.40 → appears as `side=SELL, asset_id=NO_token`

Both routes produce the same economic exposure (long YES at effective price 0.60), but route 2 appears as a SELL in `trades_raw`. The split itself is invisible — only the subsequent SELL shows up as an OrderFilled event.

## Directional Interpretation

| Trade | Direction | Route A (Position Change) | Route B (Split Entry) |
|-------|-----------|--------------------------|----------------------|
| BUY YES | Bullish | New YES entry | — (no split needed) |
| BUY NO | Bearish | New NO entry | — (no split needed) |
| SELL YES | Bearish | Exiting/reducing YES position | Split + sell YES → long NO |
| SELL NO | Bullish | Exiting/reducing NO position | Split + sell NO → long YES |

**Key insight**: The directional interpretation is the same regardless of route. A maker SELL NO is always bullish (reducing NO exposure). The difference is in conviction strength — a BUY is always a fresh entry, while a SELL might be an exit (lower conviction) or a new split-entry (high conviction, but indistinguishable from exit in `trades_raw`).

## Why the Old "Ignore All SELLs" Rule Was Wrong

The original finding (22.8% of qualified trades are SELLs) led to a blanket filter. But this discards:
1. **Split-and-sell entries** — traders entering positions via the split route
2. **Large exits by informed traders** — a whale exiting YES early IS a bearish signal
3. **Market maker rebalancing** — pattern differs from directional trader SELLs

## Why Naive SELL Copying Also Failed

The original tick-by-tick test showed 33pp NO HR collapse when copying SELLs. Likely causes:
1. **Mixing exit noise with signal** — most SELLs are profit-taking, not new conviction
2. **Timing mismatch** — a SELL at profit-take time is late; the move already happened
3. **Asymmetric information** — exits carry less information than entries

## Research Approach

SELL handling should be treated as a research parameter, not a hard rule:

### Option 1: Exclude SELLs (conservative baseline)
```sql
WHERE side = 'BUY'  -- only BUY trades build consensus
```
Pro: Clean signal, no exit noise. Con: Misses split-entry signals.

### Option 2: Include SELLs with directional mapping
```sql
-- SELL YES → bearish signal (same as BUY NO)
-- SELL NO → bullish signal (same as BUY YES)
CASE
    WHEN side = 'BUY' AND outcome = 'YES' THEN 'YES'
    WHEN side = 'BUY' AND outcome = 'NO' THEN 'NO'
    WHEN side = 'SELL' AND outcome = 'YES' THEN 'NO'   -- bearish
    WHEN side = 'SELL' AND outcome = 'NO' THEN 'YES'   -- bullish
END AS signal_direction
```
Pro: Captures all signals. Con: Noisy from exits.

### Option 3: Use net position changes (recommended for validation)
Instead of interpreting individual trades, look at `trader_market_positions` net changes:
```sql
-- Net position reveals true directional exposure regardless of route
SELECT trader, condition_id,
    net_yes - lag(net_yes) AS delta_yes,  -- positive = going more long YES
    net_no - lag(net_no) AS delta_no      -- positive = going more long NO
FROM trader_market_positions FINAL
```
Pro: Route-agnostic, captures true intent. Con: Requires position tracking, not trade-level.

### Option 4: Weight SELLs differently
```sql
-- Give SELLs lower weight in consensus building
CASE WHEN side = 'BUY' THEN 1.0 ELSE 0.5 END AS signal_weight
```

## Distinguishing Exit from Split-Entry

While `trades_raw` alone can't tell the difference, context clues help:
- **Trader had no prior position in this market** + SELL → likely split-entry
- **Trader's position decreased toward zero** + SELL → likely exit
- **SELL size ≈ existing position size** → likely exit
- **SELL in a market the trader never BUY'd** → definitely split-entry

This requires querying `trader_market_positions` for context.

## Evidence

```sql
-- How many SELL trades exist per side?
SELECT side, count(*) AS n,
    round(count(*) * 100.0 / sum(count(*)) OVER (), 1) AS pct
FROM trades_raw FINAL
WHERE maker != '' AND maker NOT IN (
    '0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e',
    '0xc5d563a36ae78145c45a50134d48a1215220f80a'
)
GROUP BY side
-- Typical: BUY ~77%, SELL ~23%
```

## Related

- `data/trade_semantics.md` — Full BUY/SELL, maker/taker data model
- `pitfalls/consensus_dedup.md` — SELL trades can inflate consensus if not handled correctly
- `pitfalls/vectorized_vs_tick.md` — SELL handling is one of the 9 identified vectorized→tick gaps

## Tags

`sell-signal`, `split-mechanic`, `direction`, `copy-trading`, `research-parameter`
