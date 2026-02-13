"""ClickHouse sink for inserting NormalizedTrade batches.

Market metadata is served via the PostgreSQL engine (reads directly from PG).
"""

from typing import Any

import clickhouse_connect

from polymarket_pipeline.models import NormalizedTrade


class ClickHouseSink:
    """Inserts NormalizedTrade batches into ClickHouse trades_raw table."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8123,
        database: str = "polymarket",
    ) -> None:
        self._client = clickhouse_connect.get_client(host=host, port=port, database=database)

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
