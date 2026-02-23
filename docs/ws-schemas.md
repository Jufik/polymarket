# Polymarket WebSocket Schemas (Empirically Verified Feb 2026)

## Complete Channel Map

| Channel | Endpoint | Auth | Speed | Scope | Identity |
|---------|----------|------|-------|-------|----------|
| CLOB `ws/market` | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | No | Match time (-3.7s vs chain) | All trades (subscribed assets) | **None** |
| CLOB `ws/user` | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | API key | Match time | **Your trades only** | maker_address, maker_orders |
| RTDS `activity/trades` | `wss://ws-live-data.polymarket.com` | No | Post-chain (+0.8s vs Alchemy) | All trades globally | proxyWallet |
| RTDS `activity/orders_matched` | same | No | Post-chain (+0.5s vs Alchemy) | All trades globally | proxyWallet (=taker 100%) |
| RTDS `clob_user` | same | API key | Match time | **Your trades only** | maker_address, maker_orders |
| Data API REST | `https://data-api.polymarket.com/trades` | No | Polling | All trades | proxyWallet |

**Key constraint**: Polymarket intentionally strips identity from fast global channels. Fast+identity only exists for your own trades.

---

## Empirical Timing (measured 2026-02-21, 16s capture, local Mac)

```
T+0.0s   CLOB WS last_trade_price     100% first, always
T+3.7s   Alchemy OrderFilled           on-chain block confirmation
T+4.2s   RTDS orders_matched           +0.5s after Alchemy
T+4.6s   RTDS trades                   +0.8s after Alchemy
```

CLOB WS fires at CLOB match time. RTDS fires AFTER on-chain confirmation (not pre-chain as initially assumed).

## Empirical Identity Attribution (verified via Alchemy cross-match)

| Source | proxyWallet is... | Details |
|--------|-------------------|---------|
| RTDS `trades` | **maker 65%, taker 35%** | Fires 2 msgs per fill (both sides, different wallets) |
| RTDS `orders_matched` | **taker 100%** | Fires 1 msg per fill, always the taker |

Every multi-message tx_hash has **different** proxyWallets (0 cases of same wallet). RTDS gives you both parties per fill across `trades` messages.

`orders_matched` arrives ~0.35s before `trades` (78% of the time), always exposes the taker.

## RPC Provider Latency (measured 2026-02-21)

```
Alchemy      baseline (56 events/s, 100% first vs all others)
dRPC         -337ms median vs Alchemy (free, no key needed)
Tenderly     -7.8s median vs Alchemy (free, ~3 blocks behind)
PublicNode   dead (0 events — does not support eth_subscribe for logs)
```

dRPC (`wss://polygon.drpc.org`) is the only viable free fallback. 337ms behind Alchemy — irrelevant for 60s+ delay strategy.

---

## CLOB WS Market Channel (unauthenticated)

Endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
Subscribe: `{"type": "market", "assets_ids": ["<token_id>"], "auth": {}, "markets": []}`

### Event: `book` (orderbook snapshot, sent on subscribe)
```json
[{
  "market": "0x204d...",
  "asset_id": "46434...",
  "timestamp": "1770537656517",
  "hash": "7d78b6...",
  "last_trade_price": "0.680",
  "event_type": "book",
  "bids": [{"price": "0.01", "size": "1043942.48"}, ...],
  "asks": [...]
}]
```

### Event: `last_trade_price` (TRADE -- fastest source, no identity)
```json
{
  "market": "0xf716...",
  "asset_id": "72678...",
  "price": "0.965",
  "size": "7",
  "fee_rate_bps": "0",
  "side": "BUY",
  "timestamp": "1771675406317",
  "event_type": "last_trade_price",
  "transaction_hash": "0xb021..."
}
```

### Event: `price_change` (orderbook update -- highest volume, ~230/s)
```json
{
  "market": "0x204d...",
  "price_changes": [{
    "asset_id": "46434...",
    "price": "0.69",
    "size": "1407612.8",
    "side": "SELL",
    "hash": "01a180...",
    "best_bid": "0.68",
    "best_ask": "0.69"
  }],
  "timestamp": "1770537660348",
  "event_type": "price_change"
}
```

### Message volume (16s sample): 3915 total
- `price_change`: 3574 (91%)
- `book`: 221 (6%)
- `last_trade_price`: 129 (3%)

---

## CLOB WS User Channel (authenticated -- your trades only)

Endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/user`
Subscribe: `{"type": "user", "markets": ["<condition_id>"], "auth": {"apiKey": "...", "secret": "...", "passphrase": "..."}}`

### Event: `trade` (fires at CLOB match time, before chain)
Fields: `id`, `asset_id`, `market`, `side`, `price`, `size`, `outcome`, `status` (MATCHED->MINED->CONFIRMED), `matchtime`, `timestamp`, `last_update`, `owner` (API key UUID), `taker_order_id`, `maker_address`, `transaction_hash` (populated at MINED), `fee_rate_bps`, `trader_side` ("TAKER"/"MAKER"), `maker_orders[]`

`maker_orders[]` element: `order_id`, `owner`, `maker_address`, `matched_amount`, `price`, `fee_rate_bps`, `asset_id`, `outcome`, `side`

### Event: `order` (PLACEMENT, UPDATE, CANCELLATION)

---

## RTDS (global firehose)

Endpoint: `wss://ws-live-data.polymarket.com`
Subscribe: `{"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}, {"topic": "activity", "type": "orders_matched"}]}`
Heartbeat: Send "PING" every 5s

### `activity/trades` message (fires twice per fill -- both sides)
```json
{
  "connection_id": "ZIXKFes_LPECJAQ=",
  "timestamp": 1771675405025,
  "topic": "activity",
  "type": "trades",
  "payload": {
    "asset": "50305...",
    "conditionId": "0xe1ab...",
    "side": "BUY",
    "price": 0.6,
    "size": 5,
    "timestamp": 1771675404,
    "transactionHash": "0x12d6...",
    "proxyWallet": "0x095F...",
    "outcome": "Up",
    "outcomeIndex": 0,
    "name": "PBot2",
    "pseudonym": "Gregarious-Thirst",
    "bio": "",
    "profileImage": "",
    "icon": "https://...",
    "title": "Bitcoin Up or Down...",
    "eventSlug": "bitcoin-up-or-down-february-21-7am-et",
    "slug": "bitcoin-up-or-down-february-21-7am-et"
  }
}
```

### `activity/orders_matched` message (fires once per fill -- taker side only)
**Identical schema** to `activity/trades`. Same payload keys. `proxyWallet` is always the taker (verified 100%).

### Field notes
- `payload.price`: float, sometimes imprecise (e.g., 0.3996666666666667)
- `payload.timestamp`: Unix seconds (integer) -- trade event time
- `timestamp` (top-level): Unix milliseconds -- RTDS delivery time
- `proxyWallet`: on-chain proxy wallet address
- Both sides of a fill have complementary prices (e.g., 0.035 + 0.965 = 1.00)

### RTDS `clob_user` topic (authenticated -- your trades only)
Subscribe: `{"action": "subscribe", "subscriptions": [{"topic": "clob_user", "type": "*", "clob_auth": {"key": "...", "secret": "...", "passphrase": "..."}}]}`
Same rich data as CLOB `ws/user`: maker_address, maker_orders, match_time, status lifecycle.

### Throughput (16s sample): 1228 msgs total
- `trades`: 912 (74%) -- ~57/s
- `orders_matched`: 315 (26%) -- ~20/s

---

## Optimal Copy Trading Architecture (empirically derived)

```
CLOB WS last_trade_price (T+0.0s)     Alchemy OrderFilled (T+3.7s)
  asset_id, price, size, side            maker, taker (topics[2], topics[3])
  tx_hash, fee_rate_bps                  tx_hash
         |                                      |
         v                                      v
    Buffer by tx_hash -----------------> JOIN on tx_hash
                                               |
                                               v
                                        if maker|taker in watchlist
                                               |
                                               v
                                        POST /order to CLOB API
```

3.7s identity latency is irrelevant -- backtester optimal delay is 60s+.
RTDS useful for enrichment (condition_id, user info) but not on the signal path.
