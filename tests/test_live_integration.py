"""Integration smoke test — verifies normalizer → JSON → deserialize round-trip."""

from polymarket_pipeline.models import NormalizedTrade, Source


class TestNormalizedTradeRoundTrip:
    def test_rtds_trade_json_roundtrip(self):
        """RTDS-normalized trade survives JSON serialization for Redpanda."""
        from polymarket_pipeline.normalizers.rtds import RTDSNormalizer

        normalizer = RTDSNormalizer()
        msg = {
            "type": "trades",
            "payload": {
                "asset": "12345",
                "side": "BUY",
                "price": 0.72,
                "size": 100.0,
                "timestamp": 1706800000,
                "conditionId": "cond_abc",
                "proxyWallet": "0xmaker123",
                "transactionHash": "0xtx123",
            },
            "timestamp": 1706800001,
        }
        trade = normalizer.normalize(msg)

        # Serialize to JSON (what gets published to Redpanda)
        trade_json = trade.model_dump_json()

        # Deserialize back (what consumers receive)
        restored = NormalizedTrade.model_validate_json(trade_json)

        assert restored.trade_id == trade.trade_id
        assert restored.condition_id == trade.condition_id
        assert restored.price == trade.price
        assert restored.source == Source.RTDS
        assert restored.version == 1

    def test_polygon_rpc_trade_json_roundtrip(self):
        """Alchemy-normalized trade survives JSON serialization for Redpanda."""
        from eth_abi import encode

        from polymarket_pipeline.live.normalizers.polygon_rpc import PolygonRPCNormalizer

        normalizer = PolygonRPCNormalizer(
            token_market_map={"12345": ("cond_12345", "YES")}
        )
        data = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256"],
            [12345, 0, 1_000_000_000, 500_000_000, 5_000_000],
        )
        log = {
            "address": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
            "topics": [
                "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6",
                "0x" + "cc" * 32,
                "0x" + "00" * 12 + "a1" * 20,
                "0x" + "00" * 12 + "b2" * 20,
            ],
            "data": "0x" + data.hex(),
            "blockNumber": hex(50_000_000),
            "transactionHash": "0x" + "dd" * 32,
            "transactionIndex": "0x1",
            "logIndex": "0x0",
            "_timestamp": 1706800000,
        }
        trade = normalizer.normalize(log)

        trade_json = trade.model_dump_json()
        restored = NormalizedTrade.model_validate_json(trade_json)

        assert restored.trade_id == trade.trade_id
        assert restored.source == Source.ALCHEMY
        assert restored.version == 2
        assert restored.maker is not None

    def test_subgraph_trade_json_roundtrip(self):
        """Subgraph-normalized trade survives JSON serialization for Redpanda."""
        from polymarket_pipeline.live.normalizers.subgraph import SubgraphNormalizer

        token_map = {"12345": ("cond_abc", "YES")}
        normalizer = SubgraphNormalizer(token_market_map=token_map)
        event = {
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
        trade = normalizer.normalize(event)

        trade_json = trade.model_dump_json()
        restored = NormalizedTrade.model_validate_json(trade_json)

        assert restored.trade_id == trade.trade_id
        assert restored.source == Source.GOLDSKY_SUBGRAPH
        assert restored.version == 2
        assert restored.condition_id == "cond_abc"
