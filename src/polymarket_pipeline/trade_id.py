"""Deterministic trade_id generation for cross-source deduplication."""

from hashlib import sha256


def make_trade_id_chain(*, tx_hash: str, order_hash: str) -> str:
    """Generate trade_id for on-chain sources (Sink/Subgraph).

    Same tx_hash + order_hash from Sink and Subgraph produce identical IDs,
    enabling automatic deduplication via ClickHouse ReplacingMergeTree.
    """
    raw = f"{tx_hash}:{order_hash}"
    digest = sha256(raw.encode()).hexdigest()[:16]
    return f"chain:{digest}"


def make_trade_id_ws(
    *,
    asset_id: str,
    timestamp_ms: int,
    price: str,
    size: str,
) -> str:
    """Generate trade_id for off-chain sources (Market WS / RTDS).

    Uses composite key since WS sources don't have order_hash.
    RTDS and Market WS for the same trade produce identical IDs.
    """
    raw = f"{asset_id}:{timestamp_ms}:{price}:{size}"
    digest = sha256(raw.encode()).hexdigest()[:16]
    return f"ws:{digest}"


def make_trade_id_pending(*, tx_hash: str, index: int) -> str:
    """Generate trade_id for pending block trades.

    Uses tx_hash + fill index. These are published to the ``pending.signal``
    topic (NOT ``trades.raw``) and are consumed directly by strategies as
    early signals. They are NOT written to ClickHouse trades_raw and do NOT
    participate in version-based deduplication.
    """
    raw = f"{tx_hash}:{index}"
    digest = sha256(raw.encode()).hexdigest()[:16]
    return f"pending:{digest}"
