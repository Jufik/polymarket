"""Export crypto Up/Down market data + exchange bars to Parquet for research.

Creates per-asset snapshots (like btc_updown/) plus exchange bars.

Usage:
    PYTHONPATH=. uv run python research/export_crypto_snapshot.py
    PYTHONPATH=. uv run python research/export_crypto_snapshot.py --only bars
    PYTHONPATH=. uv run python research/export_crypto_snapshot.py --only eth
    PYTHONPATH=. uv run python research/export_crypto_snapshot.py --assets ETH SOL
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import clickhouse_connect
import pyarrow.parquet as pq

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
OUTPUT_DIR = Path("data/research")

ASSETS = {
    "ETH": {"pattern": "Ethereum Up or Down", "dir": "eth_updown"},
    "SOL": {"pattern": "Solana Up or Down", "dir": "sol_updown"},
    "XRP": {"pattern": "XRP Up or Down", "dir": "xrp_updown"},
    "BTC": {"pattern": "Bitcoin Up or Down", "dir": "btc_updown"},
}

EXCHANGE_SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT"]


def get_client() -> clickhouse_connect.driver.Client:
    return clickhouse_connect.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)


def export_query(
    client: clickhouse_connect.driver.Client,
    sql: str,
    output_path: Path,
    desc: str = "",
) -> int:
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


def export_exchange_bars(client: clickhouse_connect.driver.Client) -> None:
    """Export exchange bars (1s OHLCV) to Parquet."""
    print("\n=== Exchange Bars ===", flush=True)
    bars_dir = OUTPUT_DIR / "exchange_bars"
    bars_dir.mkdir(parents=True, exist_ok=True)

    for symbol in EXCHANGE_SYMBOLS:
        safe_name = symbol.replace("-", "_").lower()
        export_query(
            client,
            f"""
            SELECT exchange, symbol, ts, open, high, low, close,
                   volume, buy_vol, trades
            FROM exchange_bars
            WHERE symbol = '{symbol}'
            ORDER BY ts
            """,
            bars_dir / f"{safe_name}.parquet",
            f"exchange_bars {symbol}",
        )


def export_asset_snapshot(
    client: clickhouse_connect.driver.Client,
    asset: str,
) -> None:
    """Export markets + trades for a specific crypto Up/Down asset."""
    info = ASSETS[asset]
    pattern = info["pattern"]
    out_dir = OUTPUT_DIR / info["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== {asset} Up/Down Snapshot ===", flush=True)

    # Markets with parsed window metadata
    export_query(
        client,
        f"""
        SELECT m.condition_id, m.question, m.resolution_value,
               mr.winner_outcome, m.closed_at,
               tmm_yes.asset_id AS token_yes,
               tmm_no.asset_id AS token_no
        FROM markets m
        LEFT JOIN markets_resolved mr ON m.condition_id = mr.condition_id
        LEFT JOIN token_market_map tmm_yes
            ON m.condition_id = tmm_yes.condition_id AND tmm_yes.outcome = 'Yes'
        LEFT JOIN token_market_map tmm_no
            ON m.condition_id = tmm_no.condition_id AND tmm_no.outcome = 'No'
        WHERE lower(m.question) LIKE '%{pattern.lower()}%'
        ORDER BY m.closed_at
        """,
        out_dir / "markets.parquet",
        f"{asset} markets",
    )

    # Trades for these markets
    # CH gotcha: FROM table FINAL alias is invalid, use subquery
    export_query(
        client,
        f"""
        SELECT condition_id, asset_id, side, price,
               amount_usd, fee_usd, maker, timestamp
        FROM (SELECT * FROM trades_raw FINAL)
        WHERE condition_id IN (
            SELECT condition_id FROM markets
            WHERE lower(question) LIKE '%{pattern.lower()}%'
        )
        ORDER BY condition_id, timestamp
        """,
        out_dir / "trades.parquet",
        f"{asset} trades",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Export crypto snapshot to Parquet")
    parser.add_argument(
        "--only",
        choices=["bars", "eth", "sol", "xrp", "btc"],
        help="Export only one section",
    )
    parser.add_argument(
        "--assets",
        nargs="+",
        choices=list(ASSETS.keys()),
        help="Export specific assets",
    )
    args = parser.parse_args()

    t0 = time.time()
    client = get_client()
    print(f"Connected to CH at {CH_HOST}:{CH_PORT}/{CH_DB}")

    if args.only == "bars":
        export_exchange_bars(client)
    elif args.only:
        export_asset_snapshot(client, args.only.upper())
    elif args.assets:
        for asset in args.assets:
            export_asset_snapshot(client, asset)
    else:
        # Export everything
        export_exchange_bars(client)
        for asset in ["ETH", "SOL", "XRP"]:
            export_asset_snapshot(client, asset)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s.", flush=True)


if __name__ == "__main__":
    main()
