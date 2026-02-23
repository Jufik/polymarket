"""ClickHouse-backed FeatureBackend for live modes.

Runs SQL queries against ClickHouse and returns results as Polars DataFrames.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


class ClickHouseBackend:
    """FeatureBackend backed by ClickHouse for paper-prod and live modes.

    Parameters
    ----------
    host:
        ClickHouse HTTP host.
    port:
        ClickHouse HTTP port.
    database:
        ClickHouse database name.
    """

    def __init__(self, host: str, port: int, database: str) -> None:
        self._host = host
        self._port = port
        self._database = database

    async def _execute(self, query: str) -> pl.DataFrame:
        """Execute a SQL query and return results as a Polars DataFrame."""
        import json

        import httpx

        url = f"http://{self._host}:{self._port}"
        full_query = f"{query} FORMAT JSONEachRow"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=full_query,
                params={"database": self._database},
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()

        text = resp.text.strip()
        if not text:
            return pl.DataFrame()

        rows = [json.loads(line) for line in text.split("\n") if line.strip()]
        if not rows:
            return pl.DataFrame()

        return pl.DataFrame(rows)

    async def query_trades(self, condition_ids: list[str] | None = None) -> pl.DataFrame:
        """Query trades from ClickHouse, optionally filtered."""
        query = "SELECT * FROM trades_raw FINAL"
        if condition_ids:
            ids_str = ", ".join(f"'{cid}'" for cid in condition_ids)
            query += f" WHERE condition_id IN ({ids_str})"
        return await self._execute(query)

    async def query_markets(self) -> pl.DataFrame:
        """Query market metadata from ClickHouse (via PG engine)."""
        return await self._execute("SELECT * FROM markets")

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Run an arbitrary SQL query."""
        return await self._execute(query)
