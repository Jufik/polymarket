"""ClickHouse sink for inserting NormalizedTrade batches.

Market metadata is served via the PostgreSQL engine (reads directly from PG).
"""

from __future__ import annotations

from typing import Any

import clickhouse_connect

from polymarket_pipeline.models import NormalizedTrade


class ClickHouseSink:
    """Inserts NormalizedTrade batches into ClickHouse trades_raw table."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 18123,
        database: str = "polymarket",
    ) -> None:
        self._client = clickhouse_connect.get_client(host=host, port=port, database=database)

    def close(self) -> None:
        """Close the underlying connection."""
        if self._client:
            self._client.close()

    def ping(self) -> bool:
        """Return True if ClickHouse is reachable."""
        try:
            self._client.command("SELECT 1")
            return True
        except Exception:
            return False

    def __enter__(self) -> ClickHouseSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def insert_trades(self, trades: list[NormalizedTrade]) -> None:
        """Insert a batch of normalized trades."""
        if not trades:
            return

        columns = [
            "trade_id",
            "condition_id",
            "asset_id",
            "side",
            "price",
            "size",
            "amount_usd",
            "fee_usd",
            "maker",
            "taker",
            "timestamp",
            "source",
            "tx_hash",
            "order_hash",
            "block_number",
            "is_backfill",
            "_version",
        ]

        rows = []
        for t in trades:
            rows.append(
                [
                    t.trade_id,
                    t.condition_id,
                    t.asset_id,
                    t.side.value,
                    float(t.price),
                    float(t.size),
                    float(t.amount_usd),
                    float(t.fee_usd),
                    t.maker,
                    t.taker,
                    t.timestamp,
                    t.source.value,
                    t.tx_hash,
                    t.order_hash,
                    t.block_number,
                    t.is_backfill,
                    t.version,
                ]
            )

        self._client.insert("trades_raw", rows, column_names=columns)

    def insert_arrow(self, table: Any) -> None:
        """Insert a PyArrow Table into trades_raw (native Arrow format, no pandas conversion)."""
        self._client.insert_arrow("trades_raw", table)

    def insert_dataframe(self, df: Any, batch_size: int = 100_000) -> None:
        """Insert a pandas DataFrame into trades_raw in batches.

        Column names in the DataFrame must match the table schema.
        Much faster than insert_trades for bulk loading.
        """
        columns = list(df.columns)
        for start in range(0, len(df), batch_size):
            chunk = df.iloc[start : start + batch_size]
            self._client.insert_df("trades_raw", chunk, column_names=columns)

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return results as list of dicts."""
        result = self._client.query(sql, parameters=parameters or {})
        col_names = result.column_names
        return [dict(zip(col_names, row, strict=False)) for row in result.result_rows]

    def execute(self, sql: str) -> None:
        """Execute a statement (no return value)."""
        self._client.command(sql)

    # ------------------------------------------------------------------
    # Staging support for bulk ingest (bypasses projections per-batch)
    # ------------------------------------------------------------------

    _STAGING_TABLE = "_staging_trades"

    _STAGING_DDL = """
    CREATE TABLE IF NOT EXISTS {table} (
        trade_id        String,
        condition_id    String,
        asset_id        String,
        side            String,
        price           Float64,
        size            Float64,
        amount_usd      Float64,
        fee_usd         Float64,
        maker           Nullable(String),
        taker           Nullable(String),
        timestamp       DateTime64(3, 'UTC'),
        source          String,
        tx_hash         Nullable(String),
        order_hash      Nullable(String),
        block_number    Nullable(UInt64),
        is_backfill     UInt8,
        _version        UInt16
    ) ENGINE = MergeTree()
    ORDER BY (condition_id, timestamp, trade_id)
    PARTITION BY toYYYYMM(timestamp)
    """

    _STAGING_COLUMNS = [
        "trade_id", "condition_id", "asset_id", "side", "price", "size",
        "amount_usd", "fee_usd", "maker", "taker", "timestamp", "source",
        "tx_hash", "order_hash", "block_number", "is_backfill", "_version",
    ]

    def create_staging(self) -> None:
        """Create a bare staging table (no projections, no MVs)."""
        self._client.command(self._STAGING_DDL.format(table=self._STAGING_TABLE))

    def insert_staging(self, trades: list[NormalizedTrade]) -> None:
        """Insert trades into the staging table (fast, no projection overhead)."""
        if not trades:
            return
        rows = [
            [
                t.trade_id, t.condition_id, t.asset_id, t.side.value,
                float(t.price), float(t.size), float(t.amount_usd), float(t.fee_usd),
                t.maker, t.taker, t.timestamp, t.source.value,
                t.tx_hash, t.order_hash, t.block_number, t.is_backfill, t.version,
            ]
            for t in trades
        ]
        self._client.insert(self._STAGING_TABLE, rows, column_names=self._STAGING_COLUMNS)

    def flush_staging(self) -> int:
        """Move all staged rows into trades_raw in one bulk INSERT, then truncate.

        Returns the number of rows flushed.
        """
        count_result = self._client.query(
            f"SELECT count() AS n FROM {self._STAGING_TABLE}"
        )
        count = count_result.result_rows[0][0] if count_result.result_rows else 0
        if count == 0:
            return 0
        self._client.command(
            f"INSERT INTO trades_raw"
            f" SELECT *, 0 AS published_at, now64(3, 'UTC') AS ingested_at"
            f" FROM {self._STAGING_TABLE}"
        )
        self._client.command(f"TRUNCATE TABLE {self._STAGING_TABLE}")
        return count

    def drop_staging(self) -> None:
        """Drop the staging table."""
        self._client.command(f"DROP TABLE IF EXISTS {self._STAGING_TABLE}")
