"""Export CH data to Parquet snapshot for DuckDB-based research.

One-time export with FINAL applied. Re-run on demand when fresh data is needed.
The live pipeline continues ingesting into CH independently.

Usage:
    uv run python research/export_snapshot.py                    # all
    uv run python research/export_snapshot.py --only positions   # positions + metadata only
    uv run python research/export_snapshot.py --only trades      # trades only
    uv run python research/export_snapshot.py --only metadata    # metadata only
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import clickhouse_connect
import pyarrow.parquet as pq

CH_HOST = "localhost"
CH_PORT = 18123
CH_DB = "polymarket"

OUTPUT_DIR = Path("data/research")


def get_client() -> clickhouse_connect.driver.Client:
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, database=CH_DB
    )


def export_query(
    client: clickhouse_connect.driver.Client,
    sql: str,
    output_path: Path,
    desc: str = "",
) -> int:
    """Export a CH query result to Parquet via Arrow."""
    print(f"  {desc or output_path.name} ...", end=" ", flush=True)
    t0 = time.time()
    arrow_table = client.query_arrow(sql, settings={"max_block_size": 1_000_000})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        arrow_table,
        output_path,
        compression="zstd",
        compression_level=3,
        use_dictionary=True,
        write_statistics=True,
        row_group_size=500_000,
    )
    elapsed = time.time() - t0
    rows = arrow_table.num_rows
    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"{rows:,} rows, {size_mb:.1f} MB, {elapsed:.1f}s", flush=True)
    return rows


def export_positions(client: clickhouse_connect.driver.Client) -> None:
    """Export position tables for vectorized discovery."""
    print("\n=== Positions ===", flush=True)

    # maker_positions_resolved_corrected — the primary sweep table
    export_query(
        client,
        """
        SELECT trader, condition_id, position, payout, realized_pnl, correct,
               net_yes, net_no, net_usd, inferred_splits, volume, trade_count,
               yes_won, resolved_at, month, first_trade, last_trade
        FROM maker_positions_resolved_corrected
        """,
        OUTPUT_DIR / "positions" / "maker_positions_resolved.parquet",
        "maker_positions_resolved_corrected (~30M)",
    )

    # trader_trade_agg FINAL — needed for entry price computation in sweeps
    export_query(
        client,
        """
        SELECT trader, condition_id, asset_id,
               net_tokens, net_usd, total_fees, volume, trade_count,
               first_trade, last_trade, price_x_vol
        FROM (SELECT * FROM trader_trade_agg FINAL)
        """,
        OUTPUT_DIR / "positions" / "trader_trade_agg.parquet",
        "trader_trade_agg FINAL (~134M)",
    )

    # trader_volumes FINAL
    export_query(
        client,
        "SELECT * FROM (SELECT * FROM trader_volumes FINAL)",
        OUTPUT_DIR / "positions" / "trader_volumes.parquet",
        "trader_volumes FINAL (~1.3M)",
    )


def export_metadata(client: clickhouse_connect.driver.Client) -> None:
    """Export metadata tables."""
    print("\n=== Metadata ===", flush=True)

    export_query(
        client,
        """
        SELECT condition_id, resolution_value, winner_outcome, resolved_at,
               asset_id, outcome, token_won
        FROM markets_resolved
        """,
        OUTPUT_DIR / "metadata" / "markets_resolved.parquet",
        "markets_resolved (~891K)",
    )

    export_query(
        client,
        "SELECT * FROM token_market_map",
        OUTPUT_DIR / "metadata" / "token_market_map.parquet",
        "token_market_map (~1.1M)",
    )

    export_query(
        client,
        "SELECT * FROM markets",
        OUTPUT_DIR / "metadata" / "markets.parquet",
        "markets (~575K)",
    )

    export_query(
        client,
        "SELECT * FROM events",
        OUTPUT_DIR / "metadata" / "events.parquet",
        "events (~237K)",
    )

    # event_tags pre-joined with tag labels
    export_query(
        client,
        """
        SELECT et.event_id, t.id AS tag_id, t.label, t.slug
        FROM event_tags et
        JOIN tags t ON et.tag_id = t.id
        """,
        OUTPUT_DIR / "metadata" / "event_tags.parquet",
        "event_tags + tags (~1.5M)",
    )


def export_trades(client: clickhouse_connect.driver.Client) -> None:
    """Export trades partitioned monthly, sorted by (condition_id, timestamp).

    trades_raw is PARTITION BY toYYYYMM(timestamp), ORDER BY (condition_id, timestamp, trade_id)
    so each monthly FINAL only merges one partition — efficient.
    """
    print("\n=== Trades ===", flush=True)

    months_result = client.query(
        "SELECT DISTINCT toYYYYMM(timestamp) AS m FROM trades_raw ORDER BY m"
    )
    months = [row[0] for row in months_result.result_rows]
    print(f"  {len(months)} monthly partitions to export", flush=True)

    trades_dir = OUTPUT_DIR / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    t0 = time.time()
    for i, month in enumerate(months):
        rows = export_query(
            client,
            f"""
            SELECT condition_id, asset_id, side, price, amount_usd,
                   fee_usd, maker, timestamp
            FROM trades_raw FINAL
            WHERE toYYYYMM(timestamp) = {month}
            ORDER BY condition_id, timestamp
            """,
            trades_dir / f"trades_{month}.parquet",
            f"[{i + 1}/{len(months)}] trades {month}",
        )
        total_rows += rows

    elapsed = time.time() - t0
    total_gb = sum(
        f.stat().st_size for f in trades_dir.glob("trades_*.parquet")
    ) / (1024**3)
    print(
        f"\n  Trades total: {total_rows:,} rows, {total_gb:.2f} GB, {elapsed:.0f}s",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Export CH snapshot to Parquet")
    parser.add_argument(
        "--only",
        choices=["positions", "trades", "metadata"],
        help="Export only one section",
    )
    args = parser.parse_args()

    t0 = time.time()
    client = get_client()
    print(f"Connected to CH at {CH_HOST}:{CH_PORT}/{CH_DB}")

    if args.only is None or args.only == "metadata":
        export_metadata(client)
    if args.only is None or args.only == "positions":
        export_positions(client)
    if args.only is None or args.only == "trades":
        export_trades(client)

    elapsed = time.time() - t0
    total_size = sum(
        f.stat().st_size for f in OUTPUT_DIR.rglob("*.parquet")
    ) / (1024**3)
    print(f"\nDone. {total_size:.2f} GB total, {elapsed:.0f}s elapsed.", flush=True)


if __name__ == "__main__":
    main()
