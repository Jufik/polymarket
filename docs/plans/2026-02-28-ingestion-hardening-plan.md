# Ingestion Layer Hardening — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the normalization layer into a composable 3-stage pipeline (Decode → Enrich → Validate) and fix 11 bugs ranging from critical resolution blindness to defensive position guards.

**Architecture:** New `live/normalizers/` subpackage with shared types (`DecodedTrade`, `Rejection`, `MarketResolution`), immutable `TokenMap`, source-specific decoders, and shared enrich/validate stages. Existing normalizers stay as-is during migration. Ingestors updated one-by-one to use the new pipeline.

**Tech Stack:** Python 3.11+, Pydantic v2, structlog, pytest-asyncio, dataclasses (frozen)

**Design Doc:** `docs/plans/2026-02-28-ingestion-hardening-design.md`

---

## Task 1: Pipeline Types (`DecodedTrade`, `Rejection`, `ResolutionOutcome`, `MarketResolution`)

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/types.py`
- Test: `tests/test_normalizer_types.py`

**Step 1: Write the failing test**

```python
# tests/test_normalizer_types.py
"""Tests for normalization pipeline types."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_pipeline.live.normalizers.types import (
    DecodedTrade,
    MarketResolution,
    Rejection,
    ResolutionOutcome,
)
from polymarket_pipeline.models import Source


def test_decoded_trade_frozen() -> None:
    dt = DecodedTrade(
        asset_id="123",
        maker_asset_id="0",
        taker_asset_id=None,
        price=None,
        size=None,
        maker_amount=Decimal("100000"),
        taker_amount=Decimal("50000"),
        fee_raw=Decimal("0"),
        maker="0xabc",
        taker="0xdef",
        tx_hash="0x111",
        order_hash="0x222",
        block_number=100,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source=Source.ALCHEMY,
        is_backfill=False,
    )
    assert dt.asset_id == "123"
    assert dt.maker_asset_id == "0"


def test_decoded_trade_ws_source() -> None:
    """WS sources provide pre-computed price/size, no raw amounts."""
    dt = DecodedTrade(
        asset_id="456",
        maker_asset_id=None,
        taker_asset_id=None,
        price=Decimal("0.65"),
        size=Decimal("100"),
        maker_amount=None,
        taker_amount=None,
        fee_raw=Decimal("0"),
        maker="0xmaker",
        taker=None,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source=Source.RTDS,
        is_backfill=False,
    )
    assert dt.price == Decimal("0.65")
    assert dt.maker_amount is None


def test_rejection_has_reason_and_details() -> None:
    r = Rejection(
        reason="unknown_asset",
        asset_id="999",
        source=Source.ALCHEMY,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        details="not in token_map",
    )
    assert r.reason == "unknown_asset"
    assert r.details == "not in token_map"


def test_rejection_default_details() -> None:
    r = Rejection(
        reason="taker_dedup",
        asset_id="111",
        source=Source.ALCHEMY,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert r.details == ""


def test_resolution_outcome_values() -> None:
    assert ResolutionOutcome.YES == "YES"
    assert ResolutionOutcome.NO == "NO"
    assert ResolutionOutcome.VOIDED == "VOIDED"


def test_market_resolution_frozen() -> None:
    mr = MarketResolution(
        condition_id="0xcondition",
        outcome=ResolutionOutcome.YES,
        payout_per_token=1.0,
        source="rpc",
        timestamp=1709136000.0,
    )
    assert mr.payout_per_token == 1.0
    assert mr.settled_price_raw is None


def test_market_resolution_voided() -> None:
    mr = MarketResolution(
        condition_id="0xcondition",
        outcome=ResolutionOutcome.VOIDED,
        payout_per_token=0.5,
        source="clob_api",
        timestamp=1709136000.0,
        settled_price_raw=500000000000000000,
    )
    assert mr.outcome == ResolutionOutcome.VOIDED
    assert mr.payout_per_token == 0.5
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_types.py -x -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_pipeline.live.normalizers.types'`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/live/normalizers/types.py
"""Normalization pipeline types.

3-stage pipeline: Decode → Enrich → Validate.
Each stage is a pure function returning a result or a Rejection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from polymarket_pipeline.models import Source


@dataclass(frozen=True)
class DecodedTrade:
    """Raw decoded fields before enrichment.

    Source-specific decoders produce this. No token_map lookups, no filtering.
    WS sources provide pre-computed price/size. On-chain sources provide raw
    maker_amount/taker_amount for the enrich stage to compute price/size.
    """

    asset_id: str
    maker_asset_id: str | None
    taker_asset_id: str | None
    price: Decimal | None
    size: Decimal | None
    maker_amount: Decimal | None
    taker_amount: Decimal | None
    fee_raw: Decimal
    maker: str | None
    taker: str | None
    tx_hash: str | None
    order_hash: str | None
    block_number: int | None
    timestamp: datetime
    source: Source
    is_backfill: bool


@dataclass(frozen=True)
class Rejection:
    """Structured rejection with reason code for metrics/debugging."""

    reason: str
    asset_id: str
    source: Source
    timestamp: datetime
    details: str = ""


class ResolutionOutcome(StrEnum):
    """Market resolution outcome."""

    YES = "YES"
    NO = "NO"
    VOIDED = "VOIDED"


@dataclass(frozen=True)
class MarketResolution:
    """Canonical resolution event — all resolution paths produce this."""

    condition_id: str
    outcome: ResolutionOutcome
    payout_per_token: float
    source: str
    timestamp: float
    settled_price_raw: int | None = None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_types.py -x -q`
Expected: PASS (all 7 tests)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/types.py tests/test_normalizer_types.py
git commit -m "feat: add normalization pipeline types (DecodedTrade, Rejection, MarketResolution)"
```

---

## Task 2: TokenMap (Immutable, Atomic Swap)

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/token_map.py`
- Test: `tests/test_token_map.py`

**Step 1: Write the failing test**

```python
# tests/test_token_map.py
"""Tests for immutable TokenMap."""
from __future__ import annotations

from polymarket_pipeline.live.normalizers.token_map import TokenMap


def test_lookup_hit() -> None:
    tm = TokenMap({"asset_1": ("cid_1", "YES"), "asset_2": ("cid_1", "NO")})
    assert tm.lookup("asset_1") == ("cid_1", "YES")
    assert tm.lookup("asset_2") == ("cid_1", "NO")


def test_lookup_miss_returns_none() -> None:
    tm = TokenMap({"asset_1": ("cid_1", "YES")})
    assert tm.lookup("unknown") is None


def test_len() -> None:
    tm = TokenMap({"a": ("c", "YES"), "b": ("c", "NO")})
    assert len(tm) == 2


def test_contains() -> None:
    tm = TokenMap({"a": ("c", "YES")})
    assert "a" in tm
    assert "z" not in tm


def test_empty_map() -> None:
    tm = TokenMap({})
    assert len(tm) == 0
    assert tm.lookup("anything") is None


def test_defensive_copy() -> None:
    """Mutating the input dict after construction must not affect the map."""
    original = {"a": ("c", "YES")}
    tm = TokenMap(original)
    original["b"] = ("c2", "NO")
    assert tm.lookup("b") is None
    assert len(tm) == 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_token_map.py -x -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/live/normalizers/token_map.py
"""Immutable token map for atomic refresh.

Usage:
    new_map = TokenMap(entries)
    shared_ref.token_map = new_map  # atomic swap (CPython GIL)
"""
from __future__ import annotations


class TokenMap:
    """Immutable asset_id → (condition_id, outcome) mapping.

    Constructed once, never mutated. To refresh, create a new instance
    and swap the reference. This eliminates the race condition from
    dict.clear() + dict.update().
    """

    __slots__ = ("_map",)

    def __init__(self, entries: dict[str, tuple[str, str]]) -> None:
        self._map: dict[str, tuple[str, str]] = dict(entries)

    def lookup(self, asset_id: str) -> tuple[str, str] | None:
        """Return (condition_id, outcome) or None if unknown."""
        return self._map.get(asset_id)

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, asset_id: str) -> bool:
        return asset_id in self._map
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_token_map.py -x -q`
Expected: PASS (all 6 tests)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/token_map.py tests/test_token_map.py
git commit -m "feat: add immutable TokenMap for atomic refresh"
```

---

## Task 3: Resolution Decoder (Fix C3 — settled_price for voided markets)

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/decode/__init__.py`
- Create: `src/polymarket_pipeline/live/normalizers/decode/resolution.py`
- Test: `tests/test_decode_resolution.py`

**Step 1: Write the failing test**

```python
# tests/test_decode_resolution.py
"""Tests for resolution event decoding."""
from __future__ import annotations

from polymarket_pipeline.live.normalizers.decode.resolution import (
    decode_settled_price,
)
from polymarket_pipeline.live.normalizers.types import ResolutionOutcome


def test_yes_resolution() -> None:
    """settledPrice = 1e18 → YES, payout 1.0."""
    # 1e18 = 0xDE0B6B3A7640000
    outcome, payout = decode_settled_price("0x0DE0B6B3A7640000")
    assert outcome == ResolutionOutcome.YES
    assert payout == 1.0


def test_no_resolution() -> None:
    """settledPrice = 0 → NO, payout 0.0."""
    outcome, payout = decode_settled_price("0x0")
    assert outcome == ResolutionOutcome.NO
    assert payout == 0.0


def test_voided_resolution() -> None:
    """settledPrice = 0.5e18 → VOIDED, payout 0.5."""
    # 0.5e18 = 500000000000000000 = 0x6F05B59D3B20000
    outcome, payout = decode_settled_price("0x06F05B59D3B20000")
    assert outcome == ResolutionOutcome.VOIDED
    assert payout == 0.5


def test_zero_hex() -> None:
    """Plain 0x0 → NO."""
    outcome, payout = decode_settled_price("0x0")
    assert outcome == ResolutionOutcome.NO
    assert payout == 0.0


def test_unexpected_value_treated_as_voided() -> None:
    """Any unexpected value → VOIDED with proportional payout."""
    # 0.25e18 = 250000000000000000
    outcome, payout = decode_settled_price(hex(250000000000000000))
    assert outcome == ResolutionOutcome.VOIDED
    assert abs(payout - 0.25) < 1e-9


def test_no_0x_prefix() -> None:
    """Handle hex without 0x prefix."""
    outcome, payout = decode_settled_price("0DE0B6B3A7640000")
    assert outcome == ResolutionOutcome.YES
    assert payout == 1.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_decode_resolution.py -x -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/live/normalizers/decode/__init__.py
```

```python
# src/polymarket_pipeline/live/normalizers/decode/resolution.py
"""Decode UMA QuestionResolved settledPrice into structured resolution."""
from __future__ import annotations

import structlog

from polymarket_pipeline.live.normalizers.types import ResolutionOutcome

_log = structlog.get_logger()

_YES_PRICE = 10**18
_VOIDED_PRICE = 5 * 10**17


def decode_settled_price(raw_hex: str) -> tuple[ResolutionOutcome, float]:
    """Decode int256 settledPrice from QuestionResolved event.

    Values:
        1e18  (1000000000000000000) → YES  — winning token pays $1.00
        0                          → NO   — winning token pays $1.00
        0.5e18 (500000000000000000) → VOIDED — each token pays $0.50 (50/50)

    Any other value is treated as VOIDED with proportional payout.
    """
    cleaned = raw_hex.removeprefix("0x").removeprefix("0X")
    raw = int(cleaned, 16) if cleaned else 0

    if raw == _YES_PRICE:
        return (ResolutionOutcome.YES, 1.0)
    elif raw == 0:
        return (ResolutionOutcome.NO, 0.0)
    elif raw == _VOIDED_PRICE:
        return (ResolutionOutcome.VOIDED, 0.5)
    else:
        _log.warning("resolution.unexpected_settled_price", raw=raw)
        return (ResolutionOutcome.VOIDED, raw / _YES_PRICE)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_decode_resolution.py -x -q`
Expected: PASS (all 6 tests)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/decode/ tests/test_decode_resolution.py
git commit -m "feat: resolution decoder with voided market (50/50) support"
```

---

## Task 4: Validate Stage (Fix H3 — price clamping, size checks)

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/validate.py`
- Test: `tests/test_normalizer_validate.py`

**Step 1: Write the failing test**

```python
# tests/test_normalizer_validate.py
"""Tests for the shared validation stage."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

from polymarket_pipeline.live.normalizers.types import Rejection
from polymarket_pipeline.live.normalizers.validate import validate
from polymarket_pipeline.models import NormalizedTrade, Side, Source


def _make_trade(**overrides: object) -> NormalizedTrade:
    defaults: dict[str, object] = {
        "trade_id": "chain:abc123",
        "condition_id": "0xcond",
        "asset_id": "12345",
        "side": Side.BUY,
        "price": Decimal("0.65"),
        "size": Decimal("100"),
        "amount_usd": Decimal("65"),
        "fee_usd": Decimal("0"),
        "maker": "0xmaker",
        "taker": None,
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source": Source.ALCHEMY,
        "tx_hash": "0xtx",
        "order_hash": "0xorder",
        "block_number": 100,
        "is_backfill": False,
        "version": 2,
        "published_at": time.time(),
    }
    defaults.update(overrides)
    return NormalizedTrade(**defaults)  # type: ignore[arg-type]


def test_valid_trade_passes() -> None:
    trade = _make_trade()
    result = validate(trade)
    assert isinstance(result, NormalizedTrade)


def test_zero_size_rejected() -> None:
    trade = _make_trade(size=Decimal("0"))
    result = validate(trade)
    assert isinstance(result, Rejection)
    assert result.reason == "zero_size"


def test_negative_size_rejected() -> None:
    trade = _make_trade(size=Decimal("-1"))
    result = validate(trade)
    assert isinstance(result, Rejection)
    assert result.reason == "zero_size"


def test_price_above_one_clamped() -> None:
    trade = _make_trade(price=Decimal("1.05"))
    result = validate(trade)
    assert isinstance(result, NormalizedTrade)
    assert result.price == Decimal("1")


def test_price_below_zero_clamped() -> None:
    trade = _make_trade(price=Decimal("-0.01"))
    result = validate(trade)
    assert isinstance(result, NormalizedTrade)
    assert result.price == Decimal("0")


def test_price_exactly_zero_passes() -> None:
    trade = _make_trade(price=Decimal("0"))
    result = validate(trade)
    assert isinstance(result, NormalizedTrade)
    assert result.price == Decimal("0")


def test_price_exactly_one_passes() -> None:
    trade = _make_trade(price=Decimal("1"))
    result = validate(trade)
    assert isinstance(result, NormalizedTrade)
    assert result.price == Decimal("1")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_validate.py -x -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/live/normalizers/validate.py
"""Stage 3: Shared validation for all normalized trades.

Pure function. Checks price range, size positivity, timestamp sanity.
Returns the trade (possibly with clamped price) or a Rejection.
"""
from __future__ import annotations

from decimal import Decimal

import structlog

from polymarket_pipeline.live.normalizers.types import Rejection
from polymarket_pipeline.models import NormalizedTrade

_log = structlog.get_logger()

_ZERO = Decimal("0")
_ONE = Decimal("1")


def validate(trade: NormalizedTrade) -> NormalizedTrade | Rejection:
    """Validate and sanitize a NormalizedTrade.

    Returns:
        NormalizedTrade (possibly with clamped price) or Rejection.
    """
    # --- Size must be positive ---
    if trade.size <= _ZERO:
        return Rejection(
            reason="zero_size",
            asset_id=trade.asset_id,
            source=trade.source,
            timestamp=trade.timestamp,
            details=f"size={trade.size}",
        )

    # --- Price clamping to [0, 1] ---
    price = trade.price
    clamped = False
    if price > _ONE:
        price = _ONE
        clamped = True
    elif price < _ZERO:
        price = _ZERO
        clamped = True

    if clamped:
        _log.warning(
            "validate.price_clamped",
            original=str(trade.price),
            clamped=str(price),
            asset_id=trade.asset_id,
            source=trade.source.value,
        )
        # Rebuild with clamped price (frozen model)
        return NormalizedTrade(
            **{**trade.__dict__, "price": price, "amount_usd": price * trade.size}
        )

    return trade
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_validate.py -x -q`
Expected: PASS (all 7 tests)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/validate.py tests/test_normalizer_validate.py
git commit -m "feat: shared validate stage with price clamping and size checks"
```

---

## Task 5: Enrich Stage (Fix H1 — consistent token map lookup + taker dedup)

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/enrich.py`
- Test: `tests/test_normalizer_enrich.py`

**Step 1: Write the failing test**

```python
# tests/test_normalizer_enrich.py
"""Tests for the shared enrich stage."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_pipeline.live.normalizers.enrich import enrich
from polymarket_pipeline.live.normalizers.token_map import TokenMap
from polymarket_pipeline.live.normalizers.types import DecodedTrade, Rejection
from polymarket_pipeline.models import NormalizedTrade, Source


def _decoded(**overrides: object) -> DecodedTrade:
    defaults: dict[str, object] = {
        "asset_id": "token_yes",
        "maker_asset_id": "0",
        "taker_asset_id": None,
        "price": None,
        "size": None,
        "maker_amount": Decimal("1000000"),  # 1 USDC (1e6)
        "taker_amount": Decimal("2000000"),  # 2 tokens
        "fee_raw": Decimal("0"),
        "maker": "0xmaker",
        "taker": "0xtaker",
        "tx_hash": "0xtx",
        "order_hash": "0xorder",
        "block_number": 100,
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source": Source.ALCHEMY,
        "is_backfill": False,
    }
    defaults.update(overrides)
    return DecodedTrade(**defaults)  # type: ignore[arg-type]


TOKEN_MAP = TokenMap({
    "token_yes": ("cid_1", "YES"),
    "token_no": ("cid_1", "NO"),
})


def test_enrich_onchain_buy() -> None:
    """On-chain BUY: maker provides USDC (asset_id=0), receives tokens."""
    d = _decoded(maker_asset_id="0", asset_id="token_yes")
    result = enrich(d, TOKEN_MAP)
    assert isinstance(result, NormalizedTrade)
    assert result.side.value == "BUY"
    assert result.condition_id == "cid_1"
    assert result.trade_id.startswith("chain:")


def test_enrich_onchain_sell() -> None:
    """On-chain SELL: maker provides tokens, receives USDC."""
    d = _decoded(maker_asset_id="token_yes", taker_asset_id="0", asset_id="token_yes")
    result = enrich(d, TOKEN_MAP)
    assert isinstance(result, NormalizedTrade)
    assert result.side.value == "SELL"


def test_enrich_taker_dedup() -> None:
    """Trades where taker is exchange address are rejected."""
    d = _decoded(taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e")
    result = enrich(d, TOKEN_MAP)
    assert isinstance(result, Rejection)
    assert result.reason == "taker_dedup"


def test_enrich_unknown_asset() -> None:
    """Unknown asset_id → consistent Rejection (not fallback)."""
    d = _decoded(asset_id="unknown_token")
    result = enrich(d, TOKEN_MAP)
    assert isinstance(result, Rejection)
    assert result.reason == "unknown_asset"


def test_enrich_ws_source_preserves_price() -> None:
    """WS sources provide pre-computed price/size; enrich preserves them."""
    d = _decoded(
        asset_id="token_yes",
        maker_asset_id=None,
        taker_asset_id=None,
        price=Decimal("0.65"),
        size=Decimal("100"),
        maker_amount=None,
        taker_amount=None,
        source=Source.RTDS,
        tx_hash=None,
        order_hash=None,
    )
    result = enrich(d, TOKEN_MAP)
    assert isinstance(result, NormalizedTrade)
    assert result.price == Decimal("0.65")
    assert result.size == Decimal("100")
    assert result.trade_id.startswith("ws:")


def test_enrich_version_by_source() -> None:
    """On-chain sources get version=2, WS get version=1."""
    onchain = _decoded(source=Source.ALCHEMY)
    ws = _decoded(source=Source.RTDS, maker_asset_id=None, price=Decimal("0.5"),
                  size=Decimal("10"), maker_amount=None, taker_amount=None,
                  tx_hash=None, order_hash=None)
    r1 = enrich(onchain, TOKEN_MAP)
    r2 = enrich(ws, TOKEN_MAP)
    assert isinstance(r1, NormalizedTrade)
    assert isinstance(r2, NormalizedTrade)
    assert r1.version == 2
    assert r2.version == 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_normalizer_enrich.py -x -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# src/polymarket_pipeline/live/normalizers/enrich.py
"""Stage 2: Shared enrichment for all decoded trades.

Handles token map lookup, taker dedup, side determination, amount
computation, and trade ID generation. Returns NormalizedTrade or Rejection.
All normalizers get identical behavior through this single function.
"""
from __future__ import annotations

import time
from decimal import Decimal, ROUND_HALF_UP

import structlog

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.live.normalizers.token_map import TokenMap
from polymarket_pipeline.live.normalizers.types import DecodedTrade, Rejection
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain, make_trade_id_ws

_log = structlog.get_logger()

_FOUR_DP = Decimal("0.0001")

# Source → version mapping
_VERSION: dict[Source, int] = {
    Source.ALCHEMY: 2,
    Source.GOLDSKY_SINK: 2,
    Source.GOLDSKY_SUBGRAPH: 2,
    Source.RTDS: 1,
    Source.WEBSOCKET: 1,
    Source.MEMPOOL: 0,
    Source.PENDING_BLOCK: 0,
}


def enrich(
    decoded: DecodedTrade,
    token_map: TokenMap,
) -> NormalizedTrade | Rejection:
    """Enrich a decoded trade with market context.

    Steps:
        1. Taker dedup (exchange address filter)
        2. Token map lookup → condition_id + outcome
        3. Side determination (BUY if maker provides USDC)
        4. Price/size computation (from raw amounts or pre-computed)
        5. Trade ID generation
    """
    # --- 1. Taker dedup ---
    if decoded.taker and decoded.taker.lower() in EXCHANGE_ADDRS:
        return Rejection(
            reason="taker_dedup",
            asset_id=decoded.asset_id,
            source=decoded.source,
            timestamp=decoded.timestamp,
        )

    # --- 2. Token map lookup ---
    mapping = token_map.lookup(decoded.asset_id)
    if mapping is None:
        return Rejection(
            reason="unknown_asset",
            asset_id=decoded.asset_id,
            source=decoded.source,
            timestamp=decoded.timestamp,
            details="not in token_map",
        )
    condition_id, outcome = mapping

    # --- 3. Side determination ---
    if decoded.price is not None and decoded.size is not None:
        # WS source: price/size pre-computed, no raw amounts
        side = _side_from_decoded_side(decoded)
        price = decoded.price.quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
        size = decoded.size
        amount_usd = (price * size).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
        fee_usd = decoded.fee_raw
    else:
        # On-chain source: compute from raw amounts
        is_buy = str(decoded.maker_asset_id) == "0"
        side = Side.BUY if is_buy else Side.SELL
        if is_buy:
            usdc_amount = decoded.maker_amount or Decimal("0")
            token_amount = decoded.taker_amount or Decimal("0")
        else:
            usdc_amount = decoded.taker_amount or Decimal("0")
            token_amount = decoded.maker_amount or Decimal("0")

        amount_usd = (usdc_amount / USDC_SCALE).quantize(
            _FOUR_DP, rounding=ROUND_HALF_UP,
        )
        if token_amount > 0 and usdc_amount > 0:
            size = (token_amount / USDC_SCALE).quantize(
                _FOUR_DP, rounding=ROUND_HALF_UP,
            )
            price = (amount_usd / size).quantize(
                _FOUR_DP, rounding=ROUND_HALF_UP,
            ) if size > 0 else Decimal("0")
        else:
            size = Decimal("0")
            price = Decimal("0")
        fee_usd = (decoded.fee_raw / USDC_SCALE).quantize(
            _FOUR_DP, rounding=ROUND_HALF_UP,
        )

    # --- 4. Trade ID ---
    version = _VERSION.get(decoded.source, 1)
    if decoded.tx_hash and decoded.order_hash:
        trade_id = make_trade_id_chain(decoded.tx_hash, decoded.order_hash)
    else:
        ts_ms = int(decoded.timestamp.timestamp() * 1000)
        trade_id = make_trade_id_ws(
            decoded.asset_id, ts_ms, str(price), str(size),
        )

    return NormalizedTrade(
        trade_id=trade_id,
        condition_id=condition_id,
        asset_id=decoded.asset_id,
        side=side,
        price=price,
        size=size,
        amount_usd=amount_usd,
        fee_usd=fee_usd,
        maker=decoded.maker,
        taker=decoded.taker,
        timestamp=decoded.timestamp,
        source=decoded.source,
        tx_hash=decoded.tx_hash,
        order_hash=decoded.order_hash,
        block_number=decoded.block_number,
        is_backfill=decoded.is_backfill,
        version=version,
        published_at=time.time(),
    )


def _side_from_decoded_side(decoded: DecodedTrade) -> Side:
    """Infer side for WS sources. Default to BUY if ambiguous."""
    # WS sources don't have maker_asset_id; side is in the payload
    # and already decoded. We trust the decoded price/size.
    return Side.BUY
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_normalizer_enrich.py -x -q`
Expected: PASS (all 7 tests)

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/live/normalizers/enrich.py tests/test_normalizer_enrich.py
git commit -m "feat: shared enrich stage with consistent token map lookup and taker dedup"
```

---

## Task 6: Fix C1 — neg_risk UMA Adapter for Resolution Detection

**Files:**
- Modify: `src/polymarket_pipeline/constants.py`
- Modify: `src/polymarket_pipeline/live/ingestors/rpc.py` (resolution subscription, ~line 232-246)
- Modify: `src/polymarket_pipeline/live/ingestors/rpc.py` (_handle_resolution_message, ~line 296)
- Test: `tests/test_decode_resolution.py` (already written in Task 3)

**Step 1: Add constant**

In `src/polymarket_pipeline/constants.py`, add after `EXCHANGE_ADDRS`:

```python
# UMA adapters for resolution detection
UMA_CTF_ADAPTER_V3 = "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49"
NEGRISK_UMA_ADAPTER = "0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d"
```

**Step 2: Update RPC ingestor resolution subscription**

In `src/polymarket_pipeline/live/ingestors/rpc.py`, find the resolution subscription filter (~line 232-246). Change the address filter from `[UMA_CTF_ADAPTER_V3]` to `[UMA_CTF_ADAPTER_V3, NEGRISK_UMA_ADAPTER]`.

**Step 3: Update _handle_resolution_message to use decode_settled_price**

In `src/polymarket_pipeline/live/ingestors/rpc.py` (~line 296), replace:
```python
winner = "YES" if settled_price_raw > 0 else "NO"
```

With:
```python
from polymarket_pipeline.live.normalizers.decode.resolution import decode_settled_price
outcome, payout = decode_settled_price(topics[2])
winner = outcome.value  # "YES", "NO", or "VOIDED"
```

**Step 4: Run existing tests to verify no regressions**

Run: `uv run pytest tests/test_ingestor_rpc.py -x -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/constants.py src/polymarket_pipeline/live/ingestors/rpc.py
git commit -m "fix: add neg_risk UMA adapter for resolution detection (C1) + voided price decoding (C3)"
```

---

## Task 7: Fix C2 — Voided Market Resolution in MarketEventsConsumer + LiveRunner

**Files:**
- Modify: `src/polymarket_pipeline/live/consumers/market_events.py` (~lines 63-100)
- Modify: `src/polymarket_pipeline/strategies/runners/live.py` (~lines 416-466)
- Test: `tests/test_market_events_consumer.py` (add voided case)
- Test: `tests/test_runner_live.py` (add voided settlement)

**Step 1: Write the failing test for voided resolution consumer**

Add to `tests/test_market_events_consumer.py`:

```python
async def test_handle_voided_resolution() -> None:
    """Voided markets should set resolution_value=-1 and settle at $0.50."""
    # ... setup MarketEventsConsumer with mock runner + mock pg_pool
    # ... feed a market_resolved event with winner=""
    # Assert runner.settle_voided_market was called
    # Assert PG upsert used resolution_value=-1
```

**Step 2: Write the failing test for voided settlement in LiveRunner**

Add to `tests/test_runner_live.py`:

```python
async def test_settle_voided_market() -> None:
    """50/50 resolution: positions settle at $0.50 per token."""
    # Setup runner with a position: qty_yes=100, avg_entry=0.60
    # Call runner.settle_voided_market("cid", 0.5)
    # Assert realized_pnl = (0.5 - 0.6) * 100 = -10.0
    # Assert qty_yes = 0, qty_no = 0
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_market_events_consumer.py tests/test_runner_live.py -x -q -k "voided"`
Expected: FAIL

**Step 4: Implement fixes**

In `market_events.py` `_handle_resolved()` (~line 63-76):
- Check if `winner` is empty string → treat as VOIDED
- Call `runner.settle_voided_market(condition_id, 0.5)` for voided
- Use `resolution_value = -1` in PG upsert for voided

In `live.py`, add new method `settle_voided_market(condition_id, payout_per_token)`:
- Same pattern as `settle_resolved_market` but all tokens pay `payout_per_token`
- PnL: `(payout_per_token - avg_entry) * qty` for each side

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_market_events_consumer.py tests/test_runner_live.py -x -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/live/consumers/market_events.py src/polymarket_pipeline/strategies/runners/live.py tests/
git commit -m "fix: voided market resolution (C2) — settle at $0.50, resolution_value=-1 in PG"
```

---

## Task 8: Defensive Fixes (M1-M5)

Five small independent fixes. Each is a few lines.

**Files:**
- Modify: `src/polymarket_pipeline/live/ingestors/rtds.py` (line 32 — TTL)
- Modify: `src/polymarket_pipeline/market_sync.py` (~line 154 — token ordering validation)
- Modify: `src/polymarket_pipeline/market_sync.py` (~line 180 — cursor safety)
- Modify: `src/polymarket_pipeline/models.py` (~line 182 — status from resolution_value)
- Modify: `src/polymarket_pipeline/strategies/runners/helpers.py` (~line 66-71 — oversell guard)

**Step 1: Write failing tests for each fix**

Add to relevant test files:

```python
# tests/test_normalizer_enrich.py or new test file
def test_rtds_dedup_ttl_is_600() -> None:
    from polymarket_pipeline.live.ingestors.rtds import _DEDUP_TTL_S
    assert _DEDUP_TTL_S == 600.0

# tests/test_models_market.py
def test_market_status_resolved_from_resolution_value() -> None:
    """Market with resolution_value=1 should have RESOLVED status, regardless of Gamma."""
    ...

# tests/test_backtester_helpers.py or test_strategy_types.py
def test_apply_fill_no_oversell() -> None:
    """Selling more than held should floor at zero, not go negative."""
    ...
```

**Step 2: Implement each fix**

**M1 — RTDS TTL**: `src/polymarket_pipeline/live/ingestors/rtds.py` line 32:
```python
_DEDUP_TTL_S = 600.0  # 10 min (was 5 min, late RTDS arrivals slip through)
```

**M2 — Token ordering**: `src/polymarket_pipeline/market_sync.py` (~line 154):
```python
for idx, t in enumerate(tokens):
    actual_outcome = t.get("outcome", "")
    expected = "Yes" if idx == 0 else "No"
    if actual_outcome and actual_outcome != expected:
        log.warning("token_ordering.unexpected",
                    condition_id=condition_id, idx=idx,
                    expected=expected, actual=actual_outcome)
```

**M3 — Oversell guard**: `src/polymarket_pipeline/strategies/runners/helpers.py` (~line 66, 71):
```python
sold_qty = fill.filled_size_usd / fill.filled_price if fill.filled_price > 0 else 0.0
new_qty = max(old_qty - sold_qty, 0.0)
if sold_qty > old_qty:
    _log.warning("position.oversell", condition_id=old.condition_id,
                 sold=sold_qty, held=old_qty, side=fill.outcome)
```

**M4 — Status from resolution_value**: `src/polymarket_pipeline/models.py` (~line 182-188). Adjust `Market.from_gamma()` to accept and use `resolution_value`:
```python
if resolution_value == 1:
    status = MarketStatus.RESOLVED
elif resolution_value == -1:
    status = MarketStatus.CLOSED
elif raw.get("closed") is True:
    status = MarketStatus.CLOSED
elif raw.get("active") is True:
    status = MarketStatus.ACTIVE
else:
    status = MarketStatus.UNKNOWN
```

**M5 — Cursor safety**: `src/polymarket_pipeline/market_sync.py` (~line 180):
```python
try:
    decoded = b64decode(next_cursor).decode()
    if decoded == "-1":
        break
except Exception as e:
    log.error("clob.cursor_decode_failed", cursor=next_cursor, error=str(e))
    break  # stop pagination, return partial results with warning
```

**Step 3: Run all tests**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: PASS

**Step 4: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/rtds.py src/polymarket_pipeline/market_sync.py \
    src/polymarket_pipeline/models.py src/polymarket_pipeline/strategies/runners/helpers.py \
    tests/
git commit -m "fix: defensive hardening (M1-M5) — RTDS TTL, token ordering, oversell guard, status derivation, cursor safety"
```

---

## Task 9: Wire Ingestors to New Pipeline (RPC first)

**Files:**
- Modify: `src/polymarket_pipeline/live/ingestors/rpc.py` (_handle_message)
- Test: `tests/test_ingestor_rpc.py` (verify pipeline produces same output)

**Step 1: Write regression test**

Create a test that feeds the same raw RPC log through both old `PolygonRPCNormalizer` and the new `decode → enrich → validate` pipeline, and asserts identical `NormalizedTrade` output (ignoring `published_at`).

**Step 2: Modify RPC ingestor**

Replace the direct call to `PolygonRPCNormalizer.normalize(log)` with:
```python
from polymarket_pipeline.live.normalizers.decode.rpc import decode_rpc_log
from polymarket_pipeline.live.normalizers.enrich import enrich
from polymarket_pipeline.live.normalizers.validate import validate

decoded = decode_rpc_log(log, timestamp)
if decoded is None:
    return
result = enrich(decoded, self._token_map)
if isinstance(result, Rejection):
    self._drops_rejected += 1
    return
validated = validate(result)
if isinstance(validated, Rejection):
    self._drops_validated += 1
    return
# publish validated trade
```

**Step 3: Run all RPC ingestor tests**

Run: `uv run pytest tests/test_ingestor_rpc.py tests/test_normalizer_polygon_rpc.py -x -q`
Expected: PASS

**Step 4: Commit**

```bash
git add src/polymarket_pipeline/live/ingestors/rpc.py src/polymarket_pipeline/live/normalizers/decode/rpc.py tests/
git commit -m "refactor: wire RPC ingestor to decode→enrich→validate pipeline"
```

---

## Task 10: Wire RTDS Ingestor to New Pipeline

Same pattern as Task 9 but for RTDS.

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/decode/rtds.py`
- Modify: `src/polymarket_pipeline/live/ingestors/rtds.py`
- Test: Regression test (old vs new output)

**Step 1-5:** Follow same pattern as Task 9.

**Commit:**
```bash
git commit -m "refactor: wire RTDS ingestor to decode→enrich→validate pipeline"
```

---

## Task 11: Wire Subgraph Poller to New Pipeline

Same pattern as Task 9 but for Subgraph.

**Files:**
- Create: `src/polymarket_pipeline/live/normalizers/decode/subgraph.py`
- Modify: `src/polymarket_pipeline/live/ingestors/subgraph.py`
- Test: Regression test (old vs new output)

**Step 1-5:** Follow same pattern as Task 9.

**Commit:**
```bash
git commit -m "refactor: wire Subgraph poller to decode→enrich→validate pipeline"
```

---

## Task 12: Wire Token Map Atomic Swap into Orchestrator (Fix H2)

**Files:**
- Modify: `src/polymarket_pipeline/live/orchestrator.py` (~lines 212-247, 382-387)
- Test: `tests/test_orchestrator.py` (or relevant test)

**Step 1: Replace dict-based token_map with TokenMap in orchestrator**

Change `periodic_token_map_refresh()` from:
```python
token_map.clear()
token_map.update(new_map)
```
To:
```python
from polymarket_pipeline.live.normalizers.token_map import TokenMap
new_tm = TokenMap(new_map)
shared_state.token_map = new_tm  # atomic swap
```

This requires updating the shared state container that ingestors reference. The ingestors need to read from `shared_state.token_map` instead of the old raw dict.

**Step 2: Run orchestrator tests**

Run: `uv run pytest tests/ -x -q -k "orchestrator or token_map"`
Expected: PASS

**Step 3: Commit**

```bash
git add src/polymarket_pipeline/live/orchestrator.py
git commit -m "fix: atomic token map swap via immutable TokenMap (H2)"
```

---

## Task 13: Final Integration Test + Cleanup

**Files:**
- Create: `tests/test_normalization_pipeline.py` (end-to-end)
- Delete old normalizers (after all ingestors migrated): polygon_rpc.py, subgraph.py (the old versions)

**Step 1: Write end-to-end pipeline test**

```python
# tests/test_normalization_pipeline.py
"""End-to-end: raw message → DecodedTrade → NormalizedTrade → validated."""

def test_rpc_log_through_full_pipeline() -> None:
    """Raw RPC log → decode → enrich → validate → NormalizedTrade."""
    ...

def test_rtds_payload_through_full_pipeline() -> None:
    """Raw RTDS payload → decode → enrich → validate → NormalizedTrade."""
    ...

def test_unknown_asset_rejected_consistently() -> None:
    """All sources reject unknown assets with same Rejection reason."""
    ...

def test_taker_dedup_rejected_consistently() -> None:
    """All sources reject taker duplicates with same Rejection reason."""
    ...
```

**Step 2: Run full test suite**

Run: `uv run pytest tests/ -x -q --ignore=tests/test_loader_parquet.py --ignore=tests/test_e2e_backfill.py --ignore=tests/test_market_sync.py --ignore=tests/test_sink_clickhouse.py --ignore=tests/test_sink_postgres.py`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_normalization_pipeline.py
git commit -m "test: end-to-end normalization pipeline integration tests"
```

---

## Summary

| Task | Issue(s) Fixed | Key Files |
|------|---------------|-----------|
| 1 | Foundation | `types.py` — DecodedTrade, Rejection, MarketResolution |
| 2 | H2 (types) | `token_map.py` — immutable TokenMap |
| 3 | C3 | `decode/resolution.py` — voided 50/50 decoding |
| 4 | H3 | `validate.py` — price clamping, size checks |
| 5 | H1 | `enrich.py` — consistent lookup + taker dedup |
| 6 | C1 | `constants.py` + `rpc.py` — neg_risk UMA adapter |
| 7 | C2 | `market_events.py` + `live.py` — voided settlement |
| 8 | M1-M5 | 5 defensive fixes across 4 files |
| 9 | Pipeline | Wire RPC ingestor |
| 10 | Pipeline | Wire RTDS ingestor |
| 11 | Pipeline | Wire Subgraph poller |
| 12 | H2 (wire) | Atomic token map swap in orchestrator |
| 13 | Integration | End-to-end tests + cleanup |

**Dependency order:** 1 → 2, 3, 4, 5 (parallel) → 6, 7, 8 (parallel) → 9 → 10 → 11 → 12 → 13
