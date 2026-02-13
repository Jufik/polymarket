"""Tests for ClickHouse sink.

These are integration tests that require a running ClickHouse instance.
Mark with pytest.mark.integration and skip if not available.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink


def _make_trade(trade_id: str = "chain:test123", **overrides) -> NormalizedTrade:
    defaults = dict(
        trade_id=trade_id,
        condition_id="0xtest",
        asset_id="12345",
        side=Side.BUY,
        price=Decimal("0.55"),
        size=Decimal("100"),
        amount_usd=Decimal("55"),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
        source=Source.GOLDSKY_SINK,
        tx_hash="0xtxhash",
        order_hash="0xorderhash",
        block_number=None,
        is_backfill=True,
        version=2,
    )
    defaults.update(overrides)
    return NormalizedTrade(**defaults)


@pytest.fixture
def sink():
    """Create a ClickHouse sink connected to local Docker instance."""
    try:
        s = ClickHouseSink(host="localhost", port=8123, database="polymarket")
        # Clean up test data before each test
        s.execute("DELETE FROM trades_raw WHERE condition_id = '0xtest'")
        yield s
    except Exception:
        pytest.skip("ClickHouse not available")


class TestClickHouseSink:
    def test_insert_single_trade(self, sink: ClickHouseSink) -> None:
        trade = _make_trade()
        sink.insert_trades([trade])
        result = sink.query(
            "SELECT trade_id, price, size FROM trades_raw FINAL WHERE trade_id = {id:String}",
            parameters={"id": "chain:test123"},
        )
        assert len(result) == 1
        assert result[0]["trade_id"] == "chain:test123"

    def test_insert_batch(self, sink: ClickHouseSink) -> None:
        trades = [_make_trade(trade_id=f"chain:batch{i}") for i in range(100)]
        sink.insert_trades(trades)
        result = sink.query("SELECT count() as cnt FROM trades_raw WHERE condition_id = '0xtest'")
        assert result[0]["cnt"] == 100

    def test_replacing_merge_tree_dedup(self, sink: ClickHouseSink) -> None:
        """Version 2 (on-chain) should overwrite version 1 (off-chain)."""
        # Insert WS version first (version=1, no maker)
        ws_trade = _make_trade(version=1, maker=None, taker=None, source=Source.WEBSOCKET)
        sink.insert_trades([ws_trade])

        # Insert on-chain version (version=2, with maker)
        chain_trade = _make_trade(version=2, maker="0xmaker", source=Source.GOLDSKY_SINK)
        sink.insert_trades([chain_trade])

        # Query with FINAL — should get version 2
        result = sink.query(
            "SELECT maker, _version FROM trades_raw FINAL WHERE trade_id = {id:String}",
            parameters={"id": "chain:test123"},
        )
        assert len(result) == 1
        assert result[0]["maker"] == "0xmaker"
        assert result[0]["_version"] == 2
