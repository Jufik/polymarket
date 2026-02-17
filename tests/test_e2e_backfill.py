"""End-to-end backfill test: Parquet -> normalize -> ClickHouse -> query."""

from pathlib import Path

import pytest

from polymarket_pipeline.loaders.parquet import ParquetLoader
from polymarket_pipeline.sinks.clickhouse import ClickHouseSink

PARQUET_DIR = Path("order_filled")
SAMPLE_FILE = PARQUET_DIR / "1769363325-969c3ff6-bad8-4578-95f4-6b371bd68e36-0-0.parquet"


@pytest.fixture
def sink():
    try:
        s = ClickHouseSink(host="192.168.0.148", port=18123, database="polymarket")
        s.execute("DELETE FROM trades_raw WHERE is_backfill = true AND source = 'goldsky_sink'")
        yield s
    except Exception:
        pytest.skip("ClickHouse not available")


@pytest.mark.integration
def test_e2e_backfill_single_file(sink: ClickHouseSink) -> None:
    """Load one Parquet file end-to-end and verify in ClickHouse."""
    if not SAMPLE_FILE.exists():
        pytest.skip("Parquet files not available")

    # Load and normalize
    loader = ParquetLoader(token_market_map={})
    trades, stats = loader.load_file_with_stats(SAMPLE_FILE)

    # Insert into ClickHouse in batches of 10,000
    batch_size = 10_000
    for i in range(0, len(trades), batch_size):
        batch = trades[i : i + batch_size]
        sink.insert_trades(batch)

    # Verify counts
    result = sink.query("SELECT count() as cnt FROM trades_raw WHERE source = 'goldsky_sink'")
    assert result[0]["cnt"] == len(trades)

    # Verify no duplicates made it through
    dup_result = sink.query("""
        SELECT count() as cnt FROM trades_raw
        WHERE source = 'goldsky_sink'
        AND taker IN (
            '0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e',
            '0xc5d563a36ae78145c45a50134d48a1215220f80a'
        )
    """)
    assert dup_result[0]["cnt"] == 0

    # Verify price bounds
    price_result = sink.query("""
        SELECT min(price) as min_p, max(price) as max_p
        FROM trades_raw WHERE source = 'goldsky_sink'
    """)
    assert price_result[0]["min_p"] >= 0
    assert price_result[0]["max_p"] <= 1

    # Verify we can query by condition_id
    market_result = sink.query("""
        SELECT condition_id, count() as cnt, sum(amount_usd) as vol
        FROM trades_raw FINAL
        WHERE source = 'goldsky_sink'
        GROUP BY condition_id
        ORDER BY cnt DESC
        LIMIT 5
    """)
    assert len(market_result) > 0

    # Print summary
    print(f"\n  Rows loaded: {len(trades):,}")
    print(f"  Duplicates dropped: {stats['dropped_duplicates']:,}")
    print(f"  Top market trades: {market_result[0]['cnt']:,}")
