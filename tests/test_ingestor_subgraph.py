"""Tests for Goldsky Subgraph recovery poller."""

import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


class TestSubgraphPoller:
    async def test_single_batch_published(self, mock_broker):
        """A batch of events should produce published trades."""
        from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

        events = [
            {
                "id": "evt_1",
                "maker": "0x" + "a1" * 20,
                "taker": "0x" + "b2" * 20,
                "makerAssetId": "0",
                "takerAssetId": "12345",
                "makerAmountFilled": "500000000",
                "takerAmountFilled": "1000000000",
                "fee": "5000000",
                "timestamp": "1706800000",
                "transactionHash": "0x" + "dd" * 32,
                "orderHash": "0x" + "cc" * 32,
            }
        ]

        token_map = {"12345": ("cond_abc", "YES")}
        poller = SubgraphPoller(
            broker=mock_broker,
            subgraph_url="http://test.example.com/graphql",
            token_market_map=token_map,
            topic="trades.raw",
        )

        count = await poller._process_batch(events)
        assert count == 1
        assert mock_broker.publish.call_count == 1

    async def test_taker_duplicates_skipped(self, mock_broker):
        """Exchange contract takers should be skipped."""
        from polymarket_pipeline.live.ingestors.subgraph import SubgraphPoller

        events = [
            {
                "id": "evt_1",
                "maker": "0x" + "a1" * 20,
                "taker": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
                "makerAssetId": "0",
                "takerAssetId": "12345",
                "makerAmountFilled": "500000000",
                "takerAmountFilled": "1000000000",
                "fee": "0",
                "timestamp": "1706800000",
                "transactionHash": "0x" + "dd" * 32,
                "orderHash": "0x" + "cc" * 32,
            }
        ]

        poller = SubgraphPoller(
            broker=mock_broker,
            subgraph_url="http://test.example.com/graphql",
            token_market_map={},
            topic="trades.raw",
        )
        count = await poller._process_batch(events)
        assert count == 0
        assert mock_broker.publish.call_count == 0
