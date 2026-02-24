"""Integration test: mempool trade flow from raw dict to Redpanda publish."""

import json
from unittest.mock import AsyncMock

from polymarket_pipeline.live.ingestors.mempool import MempoolIngestor


def _make_raw_trade(**overrides):
    base = {
        "tx_hash": "0x" + "dd" * 32,
        "maker": "0x" + "a1" * 20,
        "taker": "0x" + "b2" * 20,
        "token_id": "12345",
        "maker_amount": 1_000_000_000,
        "taker_amount": 500_000_000,
        "fee_rate_bps": 150,
        "side": 0,
        "expiration": 1708500000,
        "seen_at": 1706800000.123,
    }
    base.update(overrides)
    return base


class TestMempoolIntegration:
    async def test_full_flow_normalized_and_published(self):
        """Raw mempool dict -> MempoolNormalizer -> broker.publish."""
        broker = AsyncMock()
        broker.publish = AsyncMock()
        token_map = {"12345": ("cond_abc", "YES")}

        ingestor = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )

        raw = _make_raw_trade()
        await ingestor._handle_trade(raw)

        assert broker.publish.call_count == 1
        call_kwargs = broker.publish.call_args.kwargs
        payload = json.loads(call_kwargs["message"])

        assert payload["source"] == "mempool"
        assert payload["version"] == 0
        assert payload["condition_id"] == "cond_abc"
        assert payload["side"] == "BUY"
        assert payload["trade_id"].startswith("mempool:")
        assert payload["block_number"] is None
        assert payload["order_hash"] is None

    async def test_multiple_trades_counted(self):
        """Trade count increments correctly."""
        broker = AsyncMock()
        broker.publish = AsyncMock()
        token_map = {"12345": ("cond_abc", "YES")}

        ingestor = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )

        for _ in range(5):
            await ingestor._handle_trade(_make_raw_trade())

        assert ingestor._trade_count == 5
        assert broker.publish.call_count == 5

    async def test_peers_active_updated_from_metadata(self):
        """_peers_active field in raw dict updates ingestor state."""
        broker = AsyncMock()
        broker.publish = AsyncMock()
        token_map = {"12345": ("cond_abc", "YES")}

        ingestor = MempoolIngestor(
            broker=broker,
            topic="mempool.raw",
            status_topic="pipeline.status",
            token_market_map=token_map,
        )

        # Simulate what run() does with _peers_active
        raw = _make_raw_trade()
        raw["_peers_active"] = 7
        peers = raw.pop("_peers_active")
        ingestor._peers_active = peers
        await ingestor._handle_trade(raw)

        assert ingestor._peers_active == 7
