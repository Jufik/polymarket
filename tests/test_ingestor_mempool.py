"""Tests for the mempool ingestor (Python wrapper around Rust sidecar)."""

import json
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor


def _make_raw_trade(
    *,
    tx_hash: str = "0x" + "dd" * 32,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    token_id: str = "12345",
    maker_amount: int = 1_000_000_000,
    taker_amount: int = 500_000_000,
    fee_rate_bps: int = 150,
    side: int = 0,
    expiration: int = 1708500000,
    seen_at: float = 1706800000.123,
) -> dict:
    return {
        "tx_hash": tx_hash,
        "maker": maker,
        "taker": taker,
        "token_id": token_id,
        "maker_amount": maker_amount,
        "taker_amount": taker_amount,
        "fee_rate_bps": fee_rate_bps,
        "side": side,
        "expiration": expiration,
        "seen_at": seen_at,
    }


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


@pytest.fixture
def token_map():
    return {"12345": ("cond_abc", "YES")}


class TestMempoolIngestor:
    async def test_valid_trade_published(self, mock_broker, token_map):
        """Valid mempool trade should be normalized and published."""
        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        raw = _make_raw_trade()
        await ingestor._handle_trade(raw)

        assert mock_broker.publish.call_count == 1
        call_kwargs = mock_broker.publish.call_args.kwargs
        assert call_kwargs["topic"] == "mempool.raw"
        assert call_kwargs["key"] == b"cond_abc"

    async def test_unknown_token_not_published(self, mock_broker, token_map):
        """Trade with unknown token_id should be dropped."""
        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        raw = _make_raw_trade(token_id="999999")
        await ingestor._handle_trade(raw)

        assert mock_broker.publish.call_count == 0

    async def test_taker_duplicate_not_published(self, mock_broker, token_map):
        """Taker == exchange contract should be dropped."""
        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        raw = _make_raw_trade(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        await ingestor._handle_trade(raw)

        assert mock_broker.publish.call_count == 0

    async def test_heartbeat_published(self, mock_broker, token_map):
        """Heartbeat should include peers_active and trade count."""
        ingestor = MempoolIngestor(
            broker=mock_broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )
        ingestor._trade_count = 5
        ingestor._peers_active = 3
        await ingestor._publish_heartbeat()

        assert mock_broker.publish.call_count == 1
        call_kwargs = mock_broker.publish.call_args.kwargs
        payload = json.loads(call_kwargs["message"])
        assert payload["source"] == "mempool"
        assert payload["event"] == "heartbeat"
        assert payload["trade_count"] == 5
        assert payload["peers_active"] == 3
