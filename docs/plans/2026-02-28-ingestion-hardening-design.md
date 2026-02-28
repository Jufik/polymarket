# Ingestion Layer Hardening — Design Document

**Date**: 2026-02-28
**Scope**: Full normalization pipeline redesign + resolution handling overhaul + defensive fixes
**Approach**: C — Full Normalization Redesign (consolidated into `live/normalizers/`)

---

## Motivation

A deep audit of the ingestion/normalization layer uncovered 12 issues across 4 severity tiers. The most critical are data corruption bugs (resolution handling) and silent data loss (inconsistent normalizer behavior). Rather than patching each individually, we redesign the normalization into a composable 3-stage pipeline.

### Issues Addressed

| # | Severity | Issue |
|---|---|---|
| C1 | Critical | **neg_risk resolution detection blind** — RPC only monitors standard UMA adapter (`0x157Ce...`), misses neg_risk UMA adapter (`0x2F5e...`) |
| C2 | Critical | **Voided market resolution corrupts PG** — hardcodes `resolution_value=1` for all resolutions, including voided (50/50) |
| C3 | Critical | **settled_price int256 decoding wrong for voided** — treats 0.5e18 (50/50) as YES |
| H1 | High | **Token map lookup inconsistency** — RPC drops trades, Subgraph uses asset_id as fallback |
| H2 | High | **Token map refresh non-atomic** — `dict.clear()` + `dict.update()` window |
| H3 | High | **Price not validated consistently** — only PendingBlock clamps to [0,1] |
| M1 | Medium | **RTDS dedup TTL too short** — 5min, late arrivals slip through |
| M2 | Medium | **Token ordering not validated** — assumes CLOB API returns YES-first |
| M3 | Medium | **Fill-to-position allows negative quantities** — no oversell guard |
| M4 | Medium | **Market.status derived from broken Gamma `resolved` field** |
| M5 | Medium | **CLOB pagination cursor failures silently swallowed** |

**Not in scope**: Mempool/PendingBlock ingestors (not in production). Strategy framework changes (separate design).

---

## Architecture

### Current State

Each normalizer is a standalone function handling decoding, lookup, filtering, validation, and trade ID generation. Logic is duplicated and inconsistent across sources.

### Target State: 3-Stage Pipeline

```
Raw message
    │
    ▼
┌─────────────────────────────────────┐
│  Stage 1: DECODE (source-specific)  │
│  Pure function, no I/O, no lookups  │
│  Returns: DecodedTrade              │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Stage 2: ENRICH (shared)           │
│  Token map → condition_id           │
│  Taker dedup, side, amounts, ID     │
│  Returns: NormalizedTrade | Reject  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Stage 3: VALIDATE (shared)         │
│  Price ∈ [0,1], size > 0            │
│  Timestamp sanity, format checks    │
│  Returns: NormalizedTrade | Reject  │
└──────────────┬──────────────────────┘
               │
               ▼
           Publish to Kafka
```

Each stage is a pure function. Rejections carry structured reason codes for metrics/debugging.

---

## Types

```python
# live/normalizers/types.py

@dataclass(frozen=True)
class DecodedTrade:
    """Raw decoded fields before enrichment."""
    asset_id: str
    maker_asset_id: str | None       # for side determination (on-chain)
    taker_asset_id: str | None
    price: Decimal | None            # pre-computed (WS sources)
    size: Decimal | None             # pre-computed (WS sources)
    maker_amount: Decimal | None     # raw amounts (on-chain, 1e6 scaled)
    taker_amount: Decimal | None
    fee_raw: Decimal
    maker: str | None
    taker: str | None
    tx_hash: str | None
    order_hash: str | None
    block_number: int | None
    timestamp: datetime              # always UTC
    source: Source
    is_backfill: bool

@dataclass(frozen=True)
class Rejection:
    """Structured rejection for metrics/debugging."""
    reason: str                      # "taker_dedup", "unknown_asset", "price_out_of_range", etc.
    asset_id: str
    source: Source
    timestamp: datetime
    details: str = ""

class ResolutionOutcome(StrEnum):
    YES = "YES"
    NO = "NO"
    VOIDED = "VOIDED"               # 50/50 — each token redeems for $0.50

@dataclass(frozen=True)
class MarketResolution:
    """Canonical resolution event — all paths produce this."""
    condition_id: str
    outcome: ResolutionOutcome
    payout_per_token: float          # 1.0 for winner, 0.0 for loser, 0.5 for voided
    source: str                      # "rpc", "clob_ws", "clob_api"
    timestamp: float
    settled_price_raw: int | None = None
```

---

## Stage 1: Decode (Source-Specific)

One decoder per source. Pure functions, no I/O, no token map lookups.

```python
# live/normalizers/decode/rpc.py
def decode_rpc_log(raw: dict, timestamp: float) -> DecodedTrade | None

# live/normalizers/decode/rtds.py
def decode_rtds_payload(payload: dict) -> DecodedTrade | None

# live/normalizers/decode/subgraph.py
def decode_subgraph_event(event: dict) -> DecodedTrade | None

# live/normalizers/decode/resolution.py
def decode_settled_price(raw_hex: str) -> tuple[ResolutionOutcome, float]
```

Returns `None` only for non-trade messages (e.g., RTDS PING).

### RTDS Decoder Notes

- Rounds price/size to 2 decimals with `ROUND_HALF_UP`
- Uses `payload["timestamp"]` (trade time), NOT top-level `msg["timestamp"]` (delivery time)

### RPC Decoder Notes

- `blockTimestamp` is hex-encoded — `int(..., 16)` conversion
- Topics decoding via `eth_abi`

### Resolution Decoder

```python
def decode_settled_price(raw_hex: str) -> tuple[ResolutionOutcome, float]:
    raw = int(raw_hex, 16)
    if raw == 10**18:
        return (ResolutionOutcome.YES, 1.0)
    elif raw == 0:
        return (ResolutionOutcome.NO, 0.0)
    elif raw == 5 * 10**17:  # 0.5e18 — voided/50-50
        return (ResolutionOutcome.VOIDED, 0.5)
    else:
        log.warning("resolution.unexpected_settled_price", raw=raw)
        return (ResolutionOutcome.VOIDED, raw / 10**18)
```

---

## Stage 2: Enrich (Shared)

```python
# live/normalizers/enrich.py

def enrich(
    decoded: DecodedTrade,
    token_map: TokenMap,
) -> NormalizedTrade | Rejection
```

Handles (in order):
1. **Taker dedup**: `taker.lower() in EXCHANGE_ADDRS` → `Rejection("taker_dedup")`
2. **Token map lookup**: `token_map.lookup(asset_id)` → `Rejection("unknown_asset")` if None
3. **Side determination**: BUY if `maker_asset_id == "0"` (maker provides USDC, receives tokens)
4. **Amount computation**: from raw maker/taker amounts with 1e6 USDC scaling
5. **Trade ID generation**: `make_trade_id_chain()` or `make_trade_id_ws()` based on source

All normalizers get identical behavior for these operations.

---

## Stage 3: Validate (Shared)

```python
# live/normalizers/validate.py

def validate(trade: NormalizedTrade) -> NormalizedTrade | Rejection
```

Checks:
1. **Price range**: if `price < 0 or price > 1`, clamp and log warning
2. **Size positive**: `size <= 0` → `Rejection("zero_size")`
3. **Timestamp sanity**: not in future (5s tolerance), not >24h stale → warning only
4. **Trade ID format**: matches expected prefix (`chain:`, `ws:`, etc.)

---

## Resolution Handling Overhaul

### Fix C1: Add neg_risk UMA Adapter

```python
# constants.py
UMA_CTF_ADAPTER_V3 = "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49"
NEGRISK_UMA_ADAPTER = "0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d"

# rpc.py — resolution subscription
"address": [UMA_CTF_ADAPTER_V3, NEGRISK_UMA_ADAPTER],
```

Both adapters emit identical `QuestionResolved(bytes32, int256)` events (same signature). No decoding changes needed.

### Fix C2 + C3: MarketEventsConsumer

```python
async def _handle_resolution(self, condition_id: str, resolution: MarketResolution) -> None:
    if resolution.outcome == ResolutionOutcome.VOIDED:
        resolution_value = -1
        if self._runner:
            self._runner.settle_voided_market(condition_id, resolution.payout_per_token)
    else:
        resolution_value = 1
        if self._runner:
            self._runner.settle_resolved_market(condition_id, resolution.outcome.value)

    if self._pg_pool:
        await self._upsert_resolution(condition_id, resolution_value, resolution.outcome.value)
```

### LiveRunner: Voided Market Settlement

```python
def settle_voided_market(self, condition_id: str, payout_per_token: float = 0.5) -> None:
    """50/50 resolution: each token redeems for payout_per_token."""
    pos = self._positions.get(condition_id)
    if pos is None:
        return
    pnl_delta = 0.0
    if pos.qty_yes > 0:
        pnl_delta += (payout_per_token - pos.avg_entry_yes) * pos.qty_yes
    if pos.qty_no > 0:
        pnl_delta += (payout_per_token - pos.avg_entry_no) * pos.qty_no
    new_pos = dataclasses.replace(pos,
        qty_yes=0.0, qty_no=0.0,
        cost_basis=0.0,
        realized_pnl=pos.realized_pnl + pnl_delta,
    )
    self._ctx.set_position(condition_id, new_pos)
```

---

## Token Map

### Immutable + Atomic Swap

```python
# live/normalizers/token_map.py

class TokenMap:
    """Immutable token map. Swap entire instance for atomic refresh."""

    def __init__(self, entries: dict[str, tuple[str, str]]):
        self._map: dict[str, tuple[str, str]] = dict(entries)

    def lookup(self, asset_id: str) -> tuple[str, str] | None:
        return self._map.get(asset_id)

    def __len__(self) -> int:
        return len(self._map)
```

Orchestrator swaps reference atomically:
```python
new_map = TokenMap(await pg.fetch_token_market_map())
shared_state.token_map = new_map  # single reference assignment
```

---

## Defensive Fixes

### Token Ordering Validation (M2)

In `market_sync.py`, validate CLOB API token ordering:
```python
for idx, t in enumerate(tokens):
    expected = "Yes" if idx == 0 else "No"
    actual = t.get("outcome", "")
    if actual != expected:
        log.warning("token_ordering.unexpected", condition_id=cid, idx=idx, expected=expected, actual=actual)
```

### Market.status from resolution_value (M4)

```python
if resolution_value == 1:
    status = MarketStatus.RESOLVED
elif resolution_value == -1:
    status = MarketStatus.CLOSED  # voided
elif raw.get("closed"):
    status = MarketStatus.CLOSED
elif raw.get("active"):
    status = MarketStatus.ACTIVE
else:
    status = MarketStatus.UNKNOWN
```

### Fill-to-Position Oversell Guard (M3)

```python
new_qty = max(old_qty - sold_qty, 0.0)
if sold_qty > old_qty:
    log.warning("position.oversell", condition_id=old.condition_id, sold=sold_qty, held=old_qty)
```

### RTDS Dedup TTL (M1)

Increase from 300s to 600s:
```python
_DEDUP_TTL_S = 600.0
```

### CLOB Pagination Safety (M5)

```python
try:
    decoded = b64decode(next_cursor).decode()
    if decoded == "-1":
        break
except Exception as e:
    log.error("clob.cursor_decode_failed", cursor=next_cursor, error=str(e))
    break
```

---

## File Layout

```
src/polymarket_pipeline/live/normalizers/
├── __init__.py
├── types.py                    # DecodedTrade, Rejection, ResolutionOutcome, MarketResolution
├── token_map.py                # TokenMap (immutable, atomic swap)
├── enrich.py                   # Stage 2: shared enrichment
├── validate.py                 # Stage 3: shared validation
├── decode/                     # Stage 1: source-specific decoders
│   ├── __init__.py
│   ├── rpc.py                  # decode_rpc_log()
│   ├── rtds.py                 # decode_rtds_payload()
│   ├── subgraph.py             # decode_subgraph_event()
│   └── resolution.py           # decode_settled_price()
├── polygon_rpc.py              # EXISTING — kept for backward compat during migration
├── pending_block.py            # EXISTING
├── subgraph.py                 # EXISTING
└── mempool.py                  # EXISTING
```

### Migration Strategy

1. New code goes in `types.py`, `token_map.py`, `enrich.py`, `validate.py`, `decode/`
2. Existing normalizers stay as-is during migration
3. Ingestors updated one at a time to use new pipeline
4. Each ingestor migration is independently testable
5. Old normalizers deleted after all ingestors migrated

---

## Testing Strategy

### Unit Tests (per stage)

- `test_decode_rpc.py` — RPC log decoding, hex timestamp, side determination
- `test_decode_rtds.py` — Float rounding, dust filter, timestamp selection
- `test_decode_resolution.py` — settled_price: YES (1e18), NO (0), VOIDED (0.5e18), unexpected values
- `test_enrich.py` — Token map lookup (hit, miss), taker dedup, side, amount scaling, trade ID
- `test_validate.py` — Price clamping, size > 0, timestamp sanity, format checks
- `test_token_map.py` — Immutable, atomic swap, lookup behavior

### Integration Tests

- `test_pipeline_rpc.py` — Full pipeline: raw RPC log → DecodedTrade → NormalizedTrade
- `test_pipeline_rtds.py` — Full pipeline: raw RTDS payload → NormalizedTrade
- `test_resolution_flow.py` — Resolution event → MarketResolution → settlement (YES, NO, VOIDED)

### Regression Tests

- Replay recorded messages through old normalizer AND new pipeline, assert identical output
- Run against production ClickHouse data: compare counts, price distributions

---

## Metrics / Observability

### Rejection Counters (structlog)

```python
log.info("normalizer.rejected", reason=rejection.reason, source=rejection.source, asset_id=rejection.asset_id)
```

Key reason codes to track:
- `taker_dedup` — expected (~40.5% of on-chain)
- `unknown_asset` — should be near-zero after token map refresh
- `price_out_of_range` — should be zero (indicates upstream bug)
- `zero_size` — rare, indicates dust or float precision issue

### Resolution Counters

```python
log.info("resolution.processed", outcome=resolution.outcome, source=resolution.source, condition_id=resolution.condition_id)
```

Track: YES/NO/VOIDED counts by source (rpc, clob_ws, clob_api) to detect discrepancies.

---

## Polymarket Domain Context

### Binary vs Multi-Outcome (neg_risk)

Each outcome in a multi-outcome event is its own binary market with its own `condition_id` and YES/NO token pair. From the ingestion layer's perspective, neg_risk markets are identical to binary markets. The NegRisk Exchange emits the same `OrderFilled` event. What differs:

1. **Resolution cascading**: When one sibling resolves YES, all others resolve NO. Each fires its own `QuestionResolved` event from the NegRisk UMA Adapter.
2. **Contract address**: NegRisk Exchange (`0xC5d5...`) vs standard CTF Exchange (`0x4bfb...`). Both already monitored.
3. **Resolution adapter**: NegRisk UMA Adapter (`0x2F5e...`) — **currently NOT monitored** (fix C1).

### Voided Markets (50/50)

UMA can resolve a market as "Unknown" — payout vector `[1, 1]`, each token worth $0.50. The `settledPrice` in `QuestionResolved` is `0.5e18`. This is the on-chain equivalent of Polymarket's off-chain refund mechanism.

### Fee Structure

Most markets have zero fees. Fees exist on crypto 5/15-min markets (max 1.56%), NCAAB, Serie A. Formula: `fee = C * feeRate * (p * (1-p))^exponent`. The `fee_rate_bps` field from CLOB WS is `"0"` for fee-free markets.
