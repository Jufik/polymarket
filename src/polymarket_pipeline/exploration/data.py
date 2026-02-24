"""Exploration data source: ClickHouse (online) or DuckDB (offline/travel).

All joins and aggregates are pushed to the SQL engine.
Polars is only for post-query prototyping that hasn't been
promoted to materialized views yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class DataSource(Protocol):
    """Protocol for exploration data sources (ClickHouse or DuckDB)."""

    def query_df(self, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame: ...
    def query_raw(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...
    def get_schema(self, table: str) -> list[dict[str, str]]: ...


class ExplorationDataSource:
    """Read-only ClickHouse client for exploration."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 18123,
        database: str = "polymarket",
    ) -> None:
        import clickhouse_connect

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


def create_data_source(
    backend: str = "clickhouse",
    compact_dir: Path | None = None,
    metadata_dir: Path | None = None,
    ch_host: str = "localhost",
    ch_port: int = 18123,
) -> DataSource:
    """Factory for exploration data sources.

    Args:
        backend: "clickhouse" or "duckdb".
        compact_dir: Path to compact parquet files (required for duckdb).
        metadata_dir: Path to metadata parquet files (default: compact_dir/metadata).
        ch_host: ClickHouse host (for clickhouse backend).
        ch_port: ClickHouse HTTP port (for clickhouse backend).
    """
    if backend == "duckdb":
        from polymarket_pipeline.exploration.duckdb_source import DuckDBDataSource

        if compact_dir is None:
            raise ValueError("compact_dir is required for DuckDB backend")
        return DuckDBDataSource(compact_dir=compact_dir, metadata_dir=metadata_dir)
    elif backend == "clickhouse":
        return ExplorationDataSource(host=ch_host, port=ch_port)
    else:
        raise ValueError(f"Unknown backend: {backend!r}. Use 'clickhouse' or 'duckdb'.")
