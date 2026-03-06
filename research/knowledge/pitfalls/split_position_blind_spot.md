# Split Position Blind Spot — Invisible Token Minting

> **TL;DR**: CTF `splitPosition()` mints YES+NO tokens from USDC but doesn't emit `OrderFilled`. These tokens are invisible to `trades_raw`, causing `trader_market_positions` to undercount holdings and `trader_positions_resolved` to miscalculate PnL for ~12% of maker positions (~20% of volume).

> [!WARNING]
> 55.9% of makers have used the split route at least once. 12.15% of maker (trader, asset_id) pairs show negative net_tokens (sold more than ever bought via OrderFilled), proving tokens came from splits. Affects ~23-24% of directional YES/NO positions.

> [!WARNING]
> `realized_pnl = payout + net_usd` in `trader_positions_resolved` is WRONG for split-route traders because `net_usd` misses the split cost ($1 per token pair) and `payout` is based on `net_yes`/`net_no` which misses split-minted tokens.

## Finding (2026-03-05)

The CTF framework has 3 token operations that are NOT `OrderFilled` events:
1. **Split**: `splitPosition()` — $1 USDC → 1 YES + 1 NO (mints tokens, invisible)
2. **Merge**: `mergePositions()` — 1 YES + 1 NO → $1 USDC (burns tokens, invisible)
3. **Redeem**: After resolution, winning tokens → $1 (burns tokens, invisible)

Only the subsequent SELL of split-minted tokens on the orderbook emits `OrderFilled` and appears in `trades_raw`.

### Equivalent Trading Routes

| Route | trades_raw sees | Missing data |
|-------|----------------|--------------|
| BUY YES at 0.60 | BUY, YES, $60 | Nothing |
| Split $100 + SELL NO at 0.40 | SELL, NO, $40 | Split: +100 YES, +100 NO, -$100 |
| Split $100 + SELL YES at 0.60 | SELL, YES, $60 | Split: +100 YES, +100 NO, -$100 |
| Merge 100 YES + 100 NO | Nothing | Merge: -100 YES, -100 NO, +$100 |

### Detection Method

Makers with `net_tokens < -0.01` on a given asset_id have sold more tokens than they ever bought — those tokens must have come from splits (or redemptions, but redemptions only happen after resolution).

## Evidence

```sql
-- 12.15% of maker (trader, asset_id) pairs have negative net_tokens
WITH maker_net AS (
    SELECT maker AS trader, asset_id,
        sum(if(side = 'BUY', toFloat64(size), -toFloat64(size))) AS net_tokens,
        sum(toFloat64(amount_usd)) AS total_vol
    FROM (SELECT * FROM trades_raw FINAL)
    WHERE maker IS NOT NULL AND maker != ''
      AND maker NOT IN ('0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e',
                        '0xc5d563a36ae78145c45a50134d48a1215220f80a')
    GROUP BY maker, asset_id
)
SELECT
    countIf(net_tokens < -0.01) AS split_positions,    -- 5,421,980
    count(*) AS total,                                  -- 44,633,128
    round(countIf(net_tokens < -0.01) * 100.0 / count(*), 2) AS pct  -- 12.15%
FROM maker_net
```

| Metric | Value |
|--------|-------|
| Maker (trader, asset_id) with negative net | 5.4M / 44.6M (12.15%) |
| Volume through split route | $4.2B / $21.3B (19.6%) |
| Unique makers using splits | 670K / 1.2M (55.9%) |
| Directional YES positions with negative side | 23% |
| Directional NO positions with negative side | 24.1% |

## Impact

### On `trader_market_positions`
- `net_yes` / `net_no` are **understated** for split-route traders (missing minted tokens)
- `net_usd` is **understated** (missing the $1/pair split cost)
- `volume` and `trade_count` are correct (they count OrderFilled events, which is what we have)

### On `trader_positions_resolved`
- `realized_pnl = payout + net_usd` is wrong:
  - `payout` uses `net_yes`/`net_no` → misses split-minted tokens
  - `net_usd` → misses split cost
- `correct = realized_pnl > 0` is unreliable for ~12% of positions
- `position` classification (YES/NO/HEDGED/CLOSED) is unreliable when net is negative

### On Copy-Trading Research
- **Trade-level copy (tick-by-tick)**: NOT affected — we see each SELL in real-time and can interpret it directionally
- **Position-level analysis (vectorized)**: AFFECTED — net positions, PnL, and correctness are corrupted
- **Hit rate from `trader_positions_resolved`**: biased for positions involving splits

### Reconstruction (migration 010)

Split corrections are inferred and materialized in CH:
- `maker_positions` — maker-only aggregation (no taker mixing)
- `split_corrections` — inferred min_splits per (trader, condition_id)
- `maker_positions_corrected` — VIEW patching maker_positions with corrections
- `maker_positions_resolved_corrected` — VIEW with PnL + resolution

**Use `maker_positions_resolved_corrected` instead of `trader_positions_resolved` for research.**

The correction: `min_splits = max(0, max(-raw_net_yes, -raw_net_no))`, then:
- `adj_net_yes = raw_net_yes + min_splits`
- `adj_net_no = raw_net_no + min_splits`
- `adj_net_usd = raw_net_usd - min_splits` (split costs $1/pair)

### Validation of Correction

| | Uncorrected | Corrected (all) | Split-only | Non-split |
|--|---|---|---|---|
| YES positions | 14.8M / 40.7% HR | 12.9M / 35.0% HR | 2.0M / 23.0% HR | 10.9M / 37.2% HR |
| NO positions | 13.5M / 55.9% HR | 12.1M / 52.0% HR | 2.2M / 59.1% HR | 9.8M / 50.4% HR |
| HEDGED | 6.5M / 58.0% HR | 4.9M / 63.3% HR | 0 | 4.9M / 63.3% HR |

Split-affected YES positions have 23% HR (below 38% base rate) — likely MMs misclassified as directional.

### Other Mitigations
1. **Capture split events**: add `PositionSplit` / `PositionsMerge` event tracking to RPC ingestor (requires ConditionalTokens contract address + new event signatures)
2. **Use trade-level analysis**: for copy-trading, work with individual BUY/SELL trades rather than net positions — splits don't affect this

## Related

- `data/trade_semantics.md` — full BUY/SELL, maker/taker data model
- `pitfalls/sell_is_exit.md` — SELL is directional (not just exit) because of split mechanic
- `pitfalls/vectorized_counting_unit.md` — another vectorized position-level pitfall

## Tags

`split`, `ctf`, `position-tracking`, `data-gap`, `pnl`, `trader-positions`
