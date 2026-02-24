"""Tests for Alchemy eth_subscribe ingestor."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from eth_abi import encode


def _make_subscription_result(
    *,
    maker: str = "0x" + "a1" * 20,
    taker: str = "0x" + "b2" * 20,
    order_hash: str = "0x" + "cc" * 32,
    maker_asset_id: int = 12345,
    taker_asset_id: int = 0,
    maker_amount: int = 1_000_000_000,
    taker_amount: int = 500_000_000,
    fee: int = 5_000_000,
    tx_hash: str = "0x" + "dd" * 32,
    block_number: int = 50_000_000,
) -> dict:
    """Build a mock eth_subscribe log notification.

    Default values produce a valid BUY trade:
      - taker_asset_id=0 means taker pays USDC
      - usdc_raw = taker_amount = 500M  (500 USDC)
      - token_amount = maker_amount = 1000M  (1000 tokens)
      - price = 500 / 1000 = 0.5  (valid: 0 <= price <= 1)
    """
    data = encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256"],
        [maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee],
    )
    return {
        "jsonrpc": "2.0",
        "method": "eth_subscription",
        "params": {
            "subscription": "0xabc123",
            "result": {
                "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
                "topics": [
                    "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6",
                    order_hash,
                    "0x" + "00" * 12 + maker[2:],
                    "0x" + "00" * 12 + taker[2:],
                ],
                "data": "0x" + data.hex(),
                "blockNumber": hex(block_number),
                "transactionHash": tx_hash,
                "transactionIndex": "0x1",
                "logIndex": "0x0",
            },
        },
    }


@pytest.fixture
def mock_broker():
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker


class TestAlchemyIngestor:
    async def test_log_event_published(self, mock_broker):
        """Valid OrderFilled log should be published to trades.raw."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor

        ingestor = AlchemyIngestor(
            broker=mock_broker,
            ws_url="wss://test.example.com",
            topic="trades.raw",
        )
        msg = _make_subscription_result()
        await ingestor._handle_message(json.dumps(msg))

        # Message is now in the backpressure queue — drain it via _publish_loop
        assert ingestor._queue.qsize() == 1
        task = asyncio.create_task(ingestor._publish_loop())
        await asyncio.sleep(0.05)  # let the loop process one item
        task.cancel()

        assert mock_broker.publish.call_count == 1

    async def test_taker_duplicate_not_published(self, mock_broker):
        """Taker-perspective events should be dropped."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor

        ingestor = AlchemyIngestor(
            broker=mock_broker,
            ws_url="wss://test.example.com",
            topic="trades.raw",
        )
        msg = _make_subscription_result(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        await ingestor._handle_message(json.dumps(msg))

        assert ingestor._queue.qsize() == 0
        assert mock_broker.publish.call_count == 0

    async def test_subscription_response_ignored(self, mock_broker):
        """Subscription confirmation (has 'result', no 'method') should be ignored."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor

        ingestor = AlchemyIngestor(
            broker=mock_broker,
            ws_url="wss://test.example.com",
            topic="trades.raw",
        )
        confirm = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0xabc123"})
        await ingestor._handle_message(confirm)

        assert mock_broker.publish.call_count == 0
