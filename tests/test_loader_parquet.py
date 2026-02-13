"""Tests for Parquet file loader.

Integration test — requires actual Parquet files in order_filled/.
"""

from pathlib import Path

import pytest

from polymarket_pipeline.loaders.parquet import ParquetLoader

PARQUET_DIR = Path("order_filled")
SAMPLE_FILE = PARQUET_DIR / "1769363325-969c3ff6-bad8-4578-95f4-6b371bd68e36-0-0.parquet"


@pytest.fixture
def loader():
    if not SAMPLE_FILE.exists():
        pytest.skip("Parquet files not available")
    return ParquetLoader(token_market_map={})


class TestParquetLoader:
    def test_load_single_file(self, loader: ParquetLoader) -> None:
        trades = loader.load_file(SAMPLE_FILE)
        # File has ~362K rows, ~40.5% duplicates -> ~215K real trades
        assert len(trades) > 200_000
        assert len(trades) < 250_000

    def test_duplicates_filtered(self, loader: ParquetLoader) -> None:
        trades = loader.load_file(SAMPLE_FILE)
        exchange_addrs = {
            "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
            "0xc5d563a36ae78145c45a50134d48a1215220f80a",
        }
        for t in trades[:1000]:
            assert t.taker.lower() not in exchange_addrs if t.taker else True

    def test_all_trades_are_valid(self, loader: ParquetLoader) -> None:
        trades = loader.load_file(SAMPLE_FILE)
        for t in trades[:1000]:
            assert 0 <= float(t.price) <= 1
            assert float(t.size) > 0
            assert t.source.value == "goldsky_sink"
            assert t.version == 2

    def test_list_files(self, loader: ParquetLoader) -> None:
        files = loader.list_files(PARQUET_DIR)
        assert len(files) > 2000

    def test_stats_returned(self, loader: ParquetLoader) -> None:
        trades, stats = loader.load_file_with_stats(SAMPLE_FILE)
        assert stats["total_rows"] > 300_000
        assert stats["dropped_duplicates"] > 100_000
        assert stats["normalized"] == len(trades)
