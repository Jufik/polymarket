"""Shared enums for the Polymarket pipeline."""

from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Source(StrEnum):
    GOLDSKY_SINK = "goldsky_sink"
    GOLDSKY_SUBGRAPH = "goldsky_subgraph"
    WEBSOCKET = "websocket"
    RTDS = "rtds"
    ALCHEMY = "alchemy"
    MEMPOOL = "mempool"
    PENDING_BLOCK = "pending_block"


class MarketStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class ExecutionMode(StrEnum):
    """Execution mode determines how intents are routed and filled."""

    VECTORIZED = "vectorized"
    REPLAY = "replay"
    PAPER_DEV = "paper_dev"
    PAPER_PROD = "paper_prod"
    LIVE = "live"


class FillStatus(StrEnum):
    """Outcome status of a fill attempt."""

    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
