# SELL Trades Are Exits, Not Directional Signals

> **TL;DR**: On Polymarket, SELL always means closing/reducing a position. Never interpret SELL as a new directional bet.

> [!CRITICAL]
> Filter `side != "BUY"` at the top of `on_trade()` in EVERY copy-trading strategy and provider. SELL YES is NOT a NO signal.

## Finding

Polymarket has no short selling. Traders can only sell tokens they own. Therefore:
- SELL YES = reducing/closing a YES position (bearish EXIT, not a new NO bet)
- SELL NO = reducing/closing a NO position (bullish EXIT, not a new YES bet)

Interpreting SELL YES as "go NO" caused a 33pp NO HR collapse in tick-by-tick simulation (82% vectorized → 49% tick). The vectorized backtest was immune because it uses NET position (which correctly captures the final directional stance).

22.8% of qualified-trader trades are SELLs. Of these, 12.3% are SELL YES (falsely copied as NO direction).

## Evidence

```sql
SELECT
    side,
    count(*) AS n,
    round(count(*) * 100.0 / sum(count(*)) OVER (), 1) AS pct
FROM trades_raw FINAL
WHERE lower(maker) IN (SELECT trader FROM _tmp_replay_qualified)
  AND toStartOfMonth(timestamp) = '2025-07-01'
GROUP BY side
-- BUY: 77.2%, SELL: 22.8%
```

## Impact

- **Strategy `on_trade()`**: Filter `if str(trade.side) != "BUY": return None` early
- **Provider `on_trade()`**: Same — only BUY trades should build consensus
- **Vectorized**: Not affected (uses net position, which is correct)
- This applies to ANY copy-trading or trade-following strategy, not just S1

## Related

- `pitfalls/vectorized_vs_tick.md` — SELL misinterpretation is Gap #2 (33pp NO HR collapse)
- `pitfalls/consensus_dedup.md` — SELL trades can also inflate consensus if not filtered first

## Tags

`sell-signal`, `direction`, `copy-trading`, `critical-bug`, `execution`
