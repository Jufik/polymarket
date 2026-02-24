"""Unit tests for PendingBlockIngestor, _LRUSet, and _extract_new_txs."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from polymarket_pipeline.constants import FEE_MODULE_ADDRS
from polymarket_pipeline.live.ingestors.pending_block import PendingBlockIngestor, _LRUSet

FEE_ADDR = sorted(FEE_MODULE_ADDRS)[0]  # deterministic pick


# ---------------------------------------------------------------------------
# _LRUSet
# ---------------------------------------------------------------------------
class TestLRUSet:
    def test_add_new_returns_true(self) -> None:
        s = _LRUSet(maxsize=10)
        assert s.add("a") is True

    def test_add_duplicate_returns_false(self) -> None:
        s = _LRUSet(maxsize=10)
        s.add("a")
        assert s.add("a") is False

    def test_eviction_at_maxsize(self) -> None:
        s = _LRUSet(maxsize=3)
        s.add("a")
        s.add("b")
        s.add("c")
        s.add("d")  # evicts "a"
        assert "a" not in s
        assert "d" in s
        assert len(s) == 3

    def test_contains(self) -> None:
        s = _LRUSet(maxsize=5)
        assert "x" not in s
        s.add("x")
        assert "x" in s

    def test_len(self) -> None:
        s = _LRUSet(maxsize=10)
        assert len(s) == 0
        s.add("a")
        s.add("b")
        assert len(s) == 2


# ---------------------------------------------------------------------------
# _extract_new_txs
# ---------------------------------------------------------------------------
class TestExtractNewTxs:
    def _make_ingestor(self) -> PendingBlockIngestor:
        return PendingBlockIngestor(broker=AsyncMock(), rpc_ws_urls=["wss://test"])

    def test_empty_result_returns_empty(self) -> None:
        ing = self._make_ingestor()
        assert ing._extract_new_txs({}, "test") == []

    def test_none_result_returns_empty(self) -> None:
        ing = self._make_ingestor()
        assert ing._extract_new_txs(None, "test") == []  # type: ignore[arg-type]

    def test_no_transactions_key(self) -> None:
        ing = self._make_ingestor()
        assert ing._extract_new_txs({"number": "0x1"}, "test") == []

    def test_non_fee_module_filtered(self) -> None:
        ing = self._make_ingestor()
        result = {
            "number": "0x1",
            "transactions": [{"hash": "0xabc", "to": "0xunrelated"}],
        }
        assert ing._extract_new_txs(result, "test") == []

    def test_fee_module_tx_extracted(self) -> None:
        ing = self._make_ingestor()
        result = {
            "number": "0x10",
            "transactions": [{"hash": "0xabc", "to": FEE_ADDR}],
        }
        txs = ing._extract_new_txs(result, "test")
        assert len(txs) == 1
        assert txs[0]["hash"] == "0xabc"
        assert txs[0]["_pending_block"] == 16  # 0x10 = 16
        assert txs[0]["_source_rpc"] == "test"

    def test_dedup_across_calls(self) -> None:
        ing = self._make_ingestor()
        result = {
            "number": "0x1",
            "transactions": [{"hash": "0xabc", "to": FEE_ADDR}],
        }
        assert len(ing._extract_new_txs(result, "test")) == 1
        assert len(ing._extract_new_txs(result, "test")) == 0  # deduped

    def test_multiple_txs_in_block(self) -> None:
        ing = self._make_ingestor()
        result = {
            "number": "0x1",
            "transactions": [
                {"hash": "0xaaa", "to": FEE_ADDR},
                {"hash": "0xbbb", "to": "0xunrelated"},
                {"hash": "0xccc", "to": FEE_ADDR},
            ],
        }
        txs = ing._extract_new_txs(result, "test")
        assert len(txs) == 2
        assert {tx["hash"] for tx in txs} == {"0xaaa", "0xccc"}


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
async def test_publish_heartbeat() -> None:
    broker = AsyncMock()
    ing = PendingBlockIngestor(broker=broker, rpc_ws_urls=["wss://test"])
    ing._trade_count = 5
    ing._poll_count = 100
    ing._tx_count = 10

    await ing._publish_heartbeat()

    broker.publish.assert_called_once()
    call_kwargs = broker.publish.call_args.kwargs
    payload = json.loads(call_kwargs["message"])
    assert payload["source"] == "pending_block"
    assert payload["event"] == "heartbeat"
    assert payload["trade_count"] == 5
    assert payload["poll_count"] == 100
    assert payload["tx_count"] == 10


# ---------------------------------------------------------------------------
# Drop counters
# ---------------------------------------------------------------------------
async def test_dedup_increments_drop_counter() -> None:
    ing = PendingBlockIngestor(broker=AsyncMock(), rpc_ws_urls=["wss://test"])
    result = {
        "number": "0x1",
        "transactions": [{"hash": "0xabc", "to": FEE_ADDR}],
    }
    ing._extract_new_txs(result, "test")  # first time: new
    ing._extract_new_txs(result, "test")  # second time: dedup
    assert ing._drops_dedup == 1
