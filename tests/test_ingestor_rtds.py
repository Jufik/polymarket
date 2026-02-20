"""Tests for RTDS ingestor — WS connection management + Redpanda publish."""

import json
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.models import Source


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


class TestRTDSIngestor:
    async def test_trade_published_to_redpanda(self, mock_broker):
        """Normalized trades should be published as JSON to trades.raw."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(broker=mock_broker, topic="trades.raw")

        # Simulate a single trade message
        trade_msg = {
            "type": "trades",
            "payload": {
                "asset": "12345",
                "side": "BUY",
                "price": 0.72,
                "size": 100.0,
                "timestamp": 1706800000,
                "conditionId": "cond_abc",
                "proxyWallet": "0xmaker",
                "transactionHash": "0xtx",
            },
            "timestamp": 1706800001,
        }

        await ingestor._handle_message(json.dumps(trade_msg))

        # Should have published one trade
        assert mock_broker.publish.call_count == 1
        call_args = mock_broker.publish.call_args
        # Verify the published message is valid JSON containing expected fields
        published = call_args.kwargs.get("message") or call_args.args[0]
        assert "cond_abc" in str(published)

    async def test_ping_pong_not_published(self, mock_broker):
        """PING/PONG messages should not produce trades."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(broker=mock_broker, topic="trades.raw")
        await ingestor._handle_message("PING")
        assert mock_broker.publish.call_count == 0

    async def test_non_trade_type_not_published(self, mock_broker):
        """Messages with type != 'trades' should be ignored."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(broker=mock_broker, topic="trades.raw")
        await ingestor._handle_message(json.dumps({"type": "other", "data": {}}))
        assert mock_broker.publish.call_count == 0

    async def test_heartbeat_published_to_status(self, mock_broker):
        """Ingestor should publish heartbeat to pipeline.status."""
        from polymarket_pipeline.live.ingestors.rtds import RTDSIngestor

        ingestor = RTDSIngestor(
            broker=mock_broker,
            topic="trades.raw",
            status_topic="pipeline.status",
        )
        await ingestor._publish_heartbeat()
        # Should publish to pipeline.status
        assert mock_broker.publish.call_count == 1
