# Trade Data Semantics — BUY/SELL, Maker/Taker, Deduplication

> **TL;DR**: Each `trades_raw` row represents one side of a fill. `side` is from the filled order's perspective. `maker` placed the resting order (has conviction), `taker` matched it (often an exchange contract). On-chain sources produce two rows per fill (maker + taker perspective) — taker duplicates are filtered by checking against known exchange addresses. Dedup across sources uses deterministic trade IDs with version-based precedence.

> [!CRITICAL]
> Every on-chain fill emits TWO log events (maker-perspective and taker-perspective). If you query `trades_raw` without `FINAL` or without awareness of the taker-dedup filter, you may double-count trades. The normalizers drop taker rows where `taker.lower() IN EXCHANGE_ADDRS`, but raw Parquet files contain both.

> [!WARNING]
> `side` is from the **filled order's** perspective, NOT the maker's directional intent. A BUY means tokens were acquired and USDC was spent. A SELL means tokens were disposed of and USDC was received. However, SELL is NOT always an exit — the CTF split mechanic allows traders to split USDC into YES+NO tokens and sell one side, making a SELL equivalent to entering the opposite position. See `pitfalls/sell_is_exit.md` for the full nuance.

> [!WARNING]
> `maker` is NULL in Market WS trades and `taker` is NULL in RTDS trades. Only on-chain sources (Goldsky Parquet, RPC logs) provide both. Research queries that filter by maker address must account for source coverage.

## The OrderFilled Event

Every Polymarket trade settles on-chain via the CTF Exchange contract's `OrderFilled` event:

```
OrderFilled(
    bytes32 indexed orderHash,   -- unique order identifier
    address indexed maker,       -- resting limit order placer
    address indexed taker,       -- order matcher / aggressor
    uint256 makerAssetId,        -- asset the maker offered
    uint256 takerAssetId,        -- asset the taker offered
    uint256 makerAmountFilled,   -- amount maker gave
    uint256 takerAmountFilled,   -- amount taker gave
    uint256 fee                  -- fee in USDC (usually 0)
)
```

### Side Determination

`side` = BUY when one party pays USDC (asset_id = 0) for outcome tokens. The normalizers check:

```python
# Goldsky Parquet (sink.py)
is_buy = str(raw["maker_asset_id"]) == "0"  # maker offered USDC → BUY

# RPC logs (polygon_rpc.py)
is_buy = taker_asset_id == 0  # taker offered USDC → BUY
```

**Key subtlety**: In Goldsky Parquet, `maker_asset_id == 0` means the maker offered USDC (spent cash to buy tokens). In RPC logs, `taker_asset_id == 0` means the taker offered USDC. Both correctly identify the BUY side but from different row perspectives — this is why taker dedup is essential.

### Maker vs Taker

| Role | Who | Has conviction? | Address known from |
|------|-----|-----------------|-------------------|
| **Maker** | Placed the resting limit order | YES — chose price & direction | On-chain (Goldsky, RPC), RTDS (`proxyWallet`) |
| **Taker** | Matched/aggressed against the order | Often a proxy contract | On-chain (Goldsky, RPC) only |

**For research**: the `maker` field identifies the trader with the position. The `taker` is frequently a Polymarket exchange contract (CTF Exchange or NegRisk CTF Exchange), not a human trader. In `trader_trade_agg`, both perspectives are captured via separate MVs (`_maker_mv` and `_taker_mv`), but for copy-trading research, focus on maker-side entries.

## Taker Duplicate Filtering

Each on-chain fill produces two log entries:
1. **Maker perspective**: maker=human_address, taker=exchange_contract
2. **Taker perspective**: maker=exchange_contract, taker=human_address

The taker-focused rows are duplicates. All normalizers filter them:

```python
# constants.py — known exchange contract addresses
EXCHANGE_ADDRS = frozenset({
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
})

# In every on-chain normalizer:
if taker.lower() in EXCHANGE_ADDRS:
    return None  # Drop taker-focused duplicate
```

This removes ~40.5% of raw Parquet rows. The remaining rows have:
- `maker` = human trader (the one with conviction)
- `taker` = human trader (the counterparty)

**Note**: FeeModule contracts (`constants.py:FEE_MODULE_ADDRS`) are separate — these are operators that route `matchOrders` calls. They appear as transaction senders, not as maker/taker.

## Cross-Source Deduplication

### Trade ID Generation

Each source generates deterministic trade IDs with a source prefix:

| Source | Trade ID Format | Version | Dedup Key |
|--------|----------------|---------|-----------|
| Goldsky Parquet | `chain:sha256(tx_hash:order_hash)[:16]` | 2 | tx_hash + order_hash |
| RPC logs | `chain:sha256(tx_hash:order_hash)[:16]` | 2 | tx_hash + order_hash |
| Subgraph | `chain:sha256(tx_hash:order_hash)[:16]` | 2 | tx_hash + order_hash |
| RTDS WS | `ws:sha256(asset_id:ts_ms:price:size)[:16]` | 1 | composite key |
| Market WS | `ws:sha256(asset_id:ts_ms:price:size)[:16]` | 1 | composite key |
| Pending block | `pending:sha256(tx_hash:index)[:16]` | 0 | tx_hash + fill index |

### Version-Based Precedence

`trades_raw` uses `ReplacingMergeTree(_version)` — for duplicate `(condition_id, timestamp, trade_id)`, ClickHouse keeps the row with the highest `_version`:

- **Version 2** (on-chain): Ground truth. Goldsky, RPC, Subgraph all produce identical `chain:` trade IDs for the same fill, so they naturally dedup.
- **Version 1** (off-chain): RTDS and Market WS. Produce identical `ws:` IDs for the same trade, so they dedup against each other.
- **Version 0** (pre-confirmation): Pending block. Published to `pending.signal` topic only — NOT written to `trades_raw`.

**Important**: `chain:` and `ws:` IDs for the same fill are DIFFERENT strings (different hash inputs). They do NOT dedup against each other. Both versions may exist in `trades_raw`. Use `FINAL` in queries to force dedup within each ID, but understand that the same physical fill may appear as both a `chain:` and a `ws:` row.

## Aggregation Chain (ClickHouse MVs)

```
trades_raw
    |
    ├──> trader_trade_agg_maker_mv  (maker perspective)
    |       maker BUY:  net_tokens = +size, net_usd = -amount_usd  (bought tokens, spent cash)
    |       maker SELL: net_tokens = -size, net_usd = +amount_usd  (sold tokens, received cash)
    |
    ├──> trader_trade_agg_taker_mv  (taker perspective — OPPOSITE signs)
    |       taker BUY:  net_tokens = -size, net_usd = +amount_usd  (counterparty)
    |       taker SELL: net_tokens = +size, net_usd = -amount_usd  (counterparty)
    |
    └──> trader_trade_agg  (SummingMergeTree per trader, condition_id, asset_id)
              |
              └──> trader_market_positions_mv  (maps asset_id → YES/NO via token_market_map)
                        |
                        └──> trader_market_positions  (SummingMergeTree per trader, condition_id)
                                  |                     net_yes, net_no, net_usd, volume
                                  |
                                  └──> trader_positions_resolved  (VIEW)
                                           JOINs with markets_resolved
                                           realized_pnl = payout + net_usd
                                           correct = realized_pnl > 0
```

### Sign Convention in trader_trade_agg

The MVs assign OPPOSITE signs to maker and taker for the same trade:

```sql
-- Maker MV: BUY = acquired tokens, spent USDC
if(side = 'BUY', +size, -size) AS net_tokens    -- positive = holding
if(side = 'BUY', -amount_usd, +amount_usd) AS net_usd  -- negative = cash out

-- Taker MV: BUY = SOLD tokens to the buyer, received USDC
if(side = 'BUY', -size, +size) AS net_tokens    -- negative = gave away
if(side = 'BUY', +amount_usd, -amount_usd) AS net_usd  -- positive = cash in
```

This means `net_usd` is a signed cash flow: negative = money spent (buying), positive = money received (selling). At resolution: `realized_pnl = payout + net_usd` gives the true profit.

## Research Implications

### For Copy-Trading
1. **SELL handling is a research parameter** — SELL YES is bearish, SELL NO is bullish, but signal strength is ambiguous (could be exit or split-entry). Test include vs exclude. See `pitfalls/sell_is_exit.md`.
2. **Use `maker` field** — the trader with the directional conviction
3. **Beware source coverage** — RTDS provides `maker` (proxyWallet) but not `taker`; Market WS provides neither
4. **trader_trade_agg includes both perspectives** — a single fill creates entries for BOTH maker and taker. Don't double-count volume.

### For Vectorized Research (querying trader_positions_resolved)
1. **Positions are net** — SummingMergeTree accumulates across all trades. A trader's `net_yes` is the sum of all their YES token flows, not their latest position.
2. **Use `FINAL`** — without it, intermediate partial sums may appear as separate rows
3. **`position` classification** uses thresholds (>0.01) not zero — accounts for dust
4. **`correct = realized_pnl > 0`** — this is PnL-based, not direction-based. A YES holder who paid 0.99 for a YES-winning market has pnl = 1.00 - 0.99 = 0.01 (correct, but barely).

### For Raw Trade Analysis
1. **Always use `FROM trades_raw FINAL`** to dedup within version families
2. **Both `chain:` and `ws:` rows may exist** for the same fill — they don't dedup against each other
3. **USDC amounts are already scaled** (÷1e6) by normalizers before insertion
4. **`price` is always in [0, 1]** — it's the probability/price of the token, not USDC amount

## Source-Specific Field Availability

| Field | Goldsky Parquet | RPC Logs | RTDS | Market WS | Pending Block |
|-------|:-:|:-:|:-:|:-:|:-:|
| `maker` | yes | yes | yes (proxyWallet) | no | yes |
| `taker` | yes | yes | no | no | yes |
| `tx_hash` | yes | yes | yes | maybe | yes |
| `order_hash` | yes | yes | no | no | no |
| `block_number` | no | yes | no | no | yes |
| `fee_usd` | yes | yes | 0 (not provided) | yes (from bps) | yes |
| `condition_id` | lookup | lookup | direct | lookup | lookup |

## Related

- `pitfalls/sell_is_exit.md` — SELL = exit, not directional signal
- `pitfalls/consensus_dedup.md` — count unique traders, not trade events
- `data/resolution_mechanics.md` — asset_id-based resolution (token_won boolean)
- `pitfalls/vectorized_counting_unit.md` — market-level aggregation for research
- `pitfalls/split_position_blind_spot.md` — CTF splits are invisible, corrupts net positions for 12% of maker entries

## Tags

`trades-raw`, `maker-taker`, `deduplication`, `buy-sell`, `data-model`, `critical`
