"""Thin read-only ClickHouse wrapper for exploration stages.

All joins and aggregates are pushed to ClickHouse SQL.
Polars is only for post-query prototyping that hasn't been
promoted to materialized views yet.
"""

from __future__ import annotations

from typing import Any

import clickhouse_connect
import polars as pl


class ExplorationDataSource:
    """Read-only ClickHouse client for exploration."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8123,
        database: str = "polymarket",
    ) -> None:
        self._client = clickhouse_connect.get_client(
            host=host, port=port, database=database
        )

    def query_df(self, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
        """Run SQL in ClickHouse, return Polars DataFrame."""
        result = self._client.query(sql, parameters=params or {})
        if not result.result_rows:
            return pl.DataFrame(
                schema={name: pl.Utf8 for name in result.column_names}
            )
        return pl.DataFrame(
            data=result.result_rows,
            schema=result.column_names,
            orient="row",
        )

    def query_raw(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run SQL, return list of dicts (for small results)."""
        result = self._client.query(sql, parameters=params or {})
        return [
            dict(zip(result.column_names, row, strict=False))
            for row in result.result_rows
        ]

    def get_schema(self, table: str) -> list[dict[str, str]]:
        """Get column names and types for a table."""
        rows = self.query_raw(
            "SELECT name, type, comment FROM system.columns "
            "WHERE database = 'polymarket' AND table = {table:String}",
            params={"table": table},
        )
        return rows
