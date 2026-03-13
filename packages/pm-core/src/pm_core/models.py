"""Canonical trade model — all sources normalize into this shape."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

from pm_core.types import MarketStatus, Side, Source


class NormalizedTrade(BaseModel):
    """Single canonical trade shape for all sources."""

    model_config = {"frozen": True}

    # Identity (dedup key for ClickHouse)
    trade_id: str

    # Market context
    condition_id: str
    asset_id: str

    # Trade data
    side: Side
    price: Decimal = Field(ge=0, le=1)
    size: Decimal = Field(gt=0)
    amount_usd: Decimal
    fee_usd: Decimal

    # Participants (nullable for WS sources)
    maker: str | None
    taker: str | None

    # Timing
    timestamp: datetime

    # Provenance
    source: Source
    tx_hash: str | None
    order_hash: str | None
    block_number: int | None
    is_backfill: bool

    # ReplacingMergeTree version: on-chain (2) > off-chain (1) > mempool (0)
    version: int = Field(ge=0, le=2)

    # Pipeline metadata: when the ingestor published to Redpanda (time.time())
    published_at: float = 0.0

    @field_serializer("timestamp")
    @classmethod
    def serialize_timestamp(cls, v: datetime) -> str:
        """Format as 'YYYY-MM-DD HH:MM:SS.fff' for ClickHouse JSONEachRow."""
        return v.strftime("%Y-%m-%d %H:%M:%S.") + f"{v.microsecond // 1000:03d}"

    @field_validator("price")
    @classmethod
    def round_price(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Market metadata models
# ---------------------------------------------------------------------------


def _parse_dt(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


def _resolve_from_outcome_prices(raw: dict[str, Any]) -> tuple[int, str]:
    """Derive resolution from Gamma outcomePrices when CLOB data is unavailable.

    Returns (resolution_value, winner_outcome).  Only resolves when one price
    is exactly 1 and the other exactly 0 — anything else is ambiguous.
    """
    op = raw.get("outcomePrices")
    if not op:
        return 0, ""
    try:
        prices: list[str] | Any = json.loads(op) if isinstance(op, str) else op
        if not isinstance(prices, list) or len(prices) != 2:
            return 0, ""
        p0, p1 = float(prices[0]), float(prices[1])
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0, ""

    # Parse outcome labels for non-Yes/No markets (e.g. team names)
    outcomes_raw = raw.get("outcomes")
    try:
        outcomes: list[str] = (
            json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw or []
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        outcomes = []
    label_0 = outcomes[0] if len(outcomes) > 0 else "Yes"
    label_1 = outcomes[1] if len(outcomes) > 1 else "No"

    if p0 == 1.0 and p1 == 0.0:
        return 1, label_0
    if p1 == 1.0 and p0 == 0.0:
        return 1, label_1
    return 0, ""


class Event(BaseModel):
    """Polymarket event metadata from Gamma API /events endpoint."""

    model_config = {"frozen": True}

    id: int
    slug: str
    title: str
    category: str
    neg_risk: bool
    active: bool
    closed: bool
    archived: bool
    liquidity: float
    volume: float
    start_date: datetime | None
    end_date: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_gamma(cls, raw: dict[str, Any]) -> Event | None:
        """Parse a Gamma API event response. Returns None if id is missing."""
        event_id = raw.get("id")
        if not event_id:
            return None
        return cls(
            id=int(event_id),
            slug=raw.get("slug", ""),
            title=raw.get("title", ""),
            category=raw.get("category", ""),
            neg_risk=bool(raw.get("negRisk", False)),
            active=bool(raw.get("active", True)),
            closed=bool(raw.get("closed", False)),
            archived=bool(raw.get("archived", False)),
            liquidity=float(raw.get("liquidity", 0)),
            volume=float(raw.get("volume", 0)),
            start_date=_parse_dt(raw.get("startDate")),
            end_date=_parse_dt(raw.get("endDate")),
            created_at=_parse_dt(raw.get("createdAt")),
            updated_at=_parse_dt(raw.get("updatedAt")),
        )


class Market(BaseModel):
    """Polymarket market metadata from Gamma API + CLOB resolution."""

    model_config = {"frozen": True}

    condition_id: str
    event_id: int | None
    question: str
    slug: str
    category: str
    token_yes: str
    token_no: str
    neg_risk: bool
    status: MarketStatus
    resolution_value: int = 0  # 1=resolved, 0=unresolved, -1=voided
    winner_outcome: str = ""  # "Yes" or "No" (from CLOB tokens[].winner)
    created_at: datetime | None
    closed_at: datetime | None
    end_date: datetime | None = None
    resolved_at: datetime | None
    updated_at: datetime | None
    description: str = ""
    resolution_source: str = ""  # URL for resolution verification
    outcomes: str = ""  # JSON-encoded outcome labels, e.g. '["Yes","No"]'
    market_volume: float = 0.0

    @classmethod
    def from_gamma(
        cls, raw: dict[str, Any], event_id: int | None = None, resolution_value: int = 0
    ) -> Market | None:
        """Parse a Gamma API market response. Returns None if essential fields missing."""
        condition_id = raw.get("conditionId", "")
        if not condition_id:
            return None

        tokens = raw.get("clobTokenIds")
        if not tokens:
            return None

        if isinstance(tokens, str):
            tokens = json.loads(tokens)

        if len(tokens) < 2:
            return None

        winner_outcome = ""

        # Resolution fallback: when CLOB data is unavailable, derive from outcomePrices
        if resolution_value == 0 and raw.get("closed") is True:
            fb_rv, fb_wo = _resolve_from_outcome_prices(raw)
            if fb_rv != 0:
                resolution_value = fb_rv
                winner_outcome = fb_wo

        # Status: prefer resolution_value (from CLOB) over Gamma's broken resolved field
        if resolution_value == 1:
            status = MarketStatus.RESOLVED
        elif resolution_value == -1:
            status = MarketStatus.CLOSED
        elif raw.get("closed") is True:
            status = MarketStatus.RESOLVED if raw.get("resolved") else MarketStatus.CLOSED
        elif raw.get("active") is True:
            status = MarketStatus.ACTIVE
        else:
            status = MarketStatus.UNKNOWN

        # Gamma API uses "closedTime" (not "closedAt") for the close timestamp.
        # There is no separate resolution timestamp; closedTime serves both.
        closed_time = _parse_dt(raw.get("closedTime"))
        is_resolved = bool(raw.get("resolvedBy")) or resolution_value == 1

        return cls(
            condition_id=condition_id,
            event_id=event_id,
            question=raw.get("question", ""),
            slug=raw.get("slug", ""),
            category=raw.get("category", ""),
            token_yes=tokens[0],
            token_no=tokens[1],
            neg_risk=raw.get("marketType", "normal") != "normal",
            status=status,
            resolution_value=resolution_value,
            winner_outcome=winner_outcome,
            created_at=_parse_dt(raw.get("createdAt")),
            closed_at=closed_time,
            end_date=_parse_dt(raw.get("endDate")),
            resolved_at=closed_time if is_resolved else None,
            updated_at=_parse_dt(raw.get("updatedAt")),
            description=raw.get("description", "") or "",
            resolution_source=raw.get("resolutionSource", "") or "",
            outcomes=raw.get("outcomes", "") or "",
            market_volume=float(raw.get("volumeNum", 0) or 0),
        )


class Tag(BaseModel):
    """Polymarket tag from Gamma API /tags endpoint."""

    model_config = {"frozen": True}

    id: int
    label: str
    slug: str

    @classmethod
    def from_gamma(cls, raw: dict[str, Any]) -> Tag | None:
        """Parse a Gamma API tag response. Returns None if id is missing."""
        tag_id = raw.get("id")
        if not tag_id:
            return None
        return cls(
            id=int(tag_id),
            label=raw.get("label", ""),
            slug=raw.get("slug", ""),
        )


class TokenMarketEntry(BaseModel):
    """Maps a token asset_id to its market condition_id and outcome."""

    model_config = {"frozen": True}

    asset_id: str
    condition_id: str
    outcome: str
    winner: bool = False
