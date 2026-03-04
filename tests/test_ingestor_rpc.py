"""Tests for RPC eth_subscribe ingestor (formerly Alchemy)."""

import asyncio
import json

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


def _make_resolution_result(
    *,
    condition_id: str = "0x" + "ab" * 32,
    settled_price: int = 10**18,  # YES
    tx_hash: str = "0x" + "ee" * 32,
    block_number: int = 50_000_100,
) -> dict:
    """Build a mock eth_subscribe log for QuestionResolved event."""
    return {
        "jsonrpc": "2.0",
        "method": "eth_subscription",
        "params": {
            "subscription": "0xdef456",
            "result": {
                "address": "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49",
                "topics": [
                    "0x566c3fbd0982e206be981f8d7a42e3e436525258ecc0adc044023b81ab281d0e",
                    condition_id,
                    hex(settled_price),
                ],
                "data": "0x",
                "blockNumber": hex(block_number),
                "transactionHash": tx_hash,
                "transactionIndex": "0x0",
                "logIndex": "0x0",
            },
        },
    }


class TestRPCIngestor:
    async def test_log_event_published(self, mock_broker):
        """Valid OrderFilled log should be published to trades.raw."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            topic="trades.raw",
            token_market_map={"12345": ("cond_12345", "YES")},
        )
        msg = _make_subscription_result()
        await ingestor._handle_message(json.dumps(msg))

        # Message is now in the backpressure queue — drain it via _publish_worker
        assert ingestor._queue.qsize() == 1
        task = asyncio.create_task(ingestor._publish_worker())
        await asyncio.sleep(0.05)  # let the loop process one item
        task.cancel()

        assert mock_broker.publish.call_count == 1

    async def test_cross_endpoint_dedup(self, mock_broker):
        """Same trade from two endpoints should only be queued once."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://a.example.com", "wss://b.example.com"],
            topic="trades.raw",
            token_market_map={"12345": ("cond_12345", "YES")},
        )
        msg = json.dumps(_make_subscription_result())
        # Simulate same event arriving from two endpoints
        await ingestor._handle_message(msg)
        await ingestor._handle_message(msg)

        assert ingestor._queue.qsize() == 1
        assert ingestor._drops_dedup == 1

    async def test_taker_duplicate_not_published(self, mock_broker):
        """Taker-perspective events should be dropped."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
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
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            topic="trades.raw",
        )
        confirm = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0xabc123"})
        await ingestor._handle_message(confirm)

        assert mock_broker.publish.call_count == 0

    async def test_queue_full_increments_drop_counter(self, mock_broker):
        """Queue full should increment _drops_queue_full counter."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor, _QUEUE_MAXSIZE

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            token_market_map={"12345": ("cond_12345", "YES")},
        )
        # Fill the queue to capacity
        for i in range(_QUEUE_MAXSIZE):
            ingestor._queue.put_nowait(("cid", f"trade_{i}"))

        # Now send a valid message — should hit QueueFull
        msg = _make_subscription_result()
        await ingestor._handle_message(json.dumps(msg))

        assert ingestor._drops_queue_full == 1
        assert ingestor._queue.qsize() == _QUEUE_MAXSIZE  # unchanged

    async def test_taker_dedup_increments_drop_counter(self, mock_broker):
        """Taker duplicate should increment _drops_taker_dedup counter."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
        )
        # Taker = exchange address -> normalizer returns None
        msg = _make_subscription_result(
            taker="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
        )
        await ingestor._handle_message(json.dumps(msg))

        assert ingestor._drops_taker_dedup == 1

    async def test_heartbeat_includes_drops(self, mock_broker):
        """Heartbeat should include drop counters."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
        )
        ingestor._drops_queue_full = 5
        ingestor._drops_taker_dedup = 10

        await ingestor._publish_heartbeat()

        call_args = mock_broker.publish.call_args
        published = call_args.kwargs.get("message") or call_args.args[0]
        hb = json.loads(published)
        assert hb["drops_queue_full"] == 5
        assert hb["drops_taker_dedup"] == 10

    async def test_source_name_backward_compat(self, mock_broker):
        """source_name should remain 'alchemy' for backward compat."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
        )
        assert ingestor.source_name == "alchemy"

    async def test_alchemy_shim_imports(self, mock_broker):
        """AlchemyIngestor from alchemy.py should be RPCIngestor."""
        from polymarket_pipeline.live.ingestors.alchemy import AlchemyIngestor
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        assert AlchemyIngestor is RPCIngestor


class TestResolutionLoop:
    async def test_resolution_yes_published(self, mock_broker):
        """QuestionResolved with settledPrice=1e18 should publish YES resolution."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            markets_events_topic="markets.events",
        )
        msg = _make_resolution_result(settled_price=10**18)
        await ingestor._handle_resolution_message(json.dumps(msg))

        assert mock_broker.publish.call_count == 1
        assert ingestor._resolution_count == 1

        call_args = mock_broker.publish.call_args
        published = json.loads(call_args.kwargs.get("message") or call_args.args[0])
        assert published["type"] == "market_resolved"
        assert published["payload"]["winner"] == "YES"
        assert published["payload"]["source"] == "rpc_on_chain"

    async def test_resolution_no_published(self, mock_broker):
        """QuestionResolved with settledPrice=0 should publish NO resolution."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            markets_events_topic="markets.events",
        )
        msg = _make_resolution_result(settled_price=0)
        await ingestor._handle_resolution_message(json.dumps(msg))

        assert mock_broker.publish.call_count == 1
        call_args = mock_broker.publish.call_args
        published = json.loads(call_args.kwargs.get("message") or call_args.args[0])
        assert published["payload"]["winner"] == "NO"

    async def test_resolution_topic_is_markets_events(self, mock_broker):
        """Resolution events should be published to the markets.events topic."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            markets_events_topic="markets.events",
        )
        msg = _make_resolution_result()
        await ingestor._handle_resolution_message(json.dumps(msg))

        call_args = mock_broker.publish.call_args
        assert call_args.kwargs.get("topic") == "markets.events"

    async def test_resolution_ignores_non_subscription(self, mock_broker):
        """Non-subscription messages should be silently ignored."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
        )
        confirm = json.dumps({"jsonrpc": "2.0", "id": 2, "result": "0xdef456"})
        await ingestor._handle_resolution_message(confirm)

        assert mock_broker.publish.call_count == 0
        assert ingestor._resolution_count == 0

    async def test_resolution_ignores_short_topics(self, mock_broker):
        """Messages with fewer than 3 topics should be ignored."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
        )
        msg = _make_resolution_result()
        data = json.loads(json.dumps(msg))
        data["params"]["result"]["topics"] = data["params"]["result"]["topics"][:2]
        await ingestor._handle_resolution_message(json.dumps(data))

        assert mock_broker.publish.call_count == 0

    async def test_heartbeat_includes_resolution_count(self, mock_broker):
        """Heartbeat should include resolution_count when resolution is enabled."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            resolution_enabled=True,
        )
        ingestor._resolution_count = 3

        fields = ingestor._heartbeat_fields()
        assert fields["resolution_count"] == 3

    async def test_heartbeat_omits_resolution_count_when_disabled(self, mock_broker):
        """Heartbeat should NOT include resolution_count when disabled."""
        from polymarket_pipeline.live.ingestors.rpc import RPCIngestor

        ingestor = RPCIngestor(
            broker=mock_broker,
            ws_urls=["wss://test.example.com"],
            resolution_enabled=False,
        )
        fields = ingestor._heartbeat_fields()
        assert "resolution_count" not in fields
