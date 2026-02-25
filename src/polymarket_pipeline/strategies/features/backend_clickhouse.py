"""ClickHouse-backed FeatureBackend for live modes.

Runs SQL queries against ClickHouse and returns results as Polars DataFrames.
"""

from __future__ import annotations

import re
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

        import httpx

        self._client = httpx.AsyncClient(
            base_url=f"http://{host}:{port}",
            timeout=30.0,
        )

    async def _execute(self, query: str) -> pl.DataFrame:
        """Execute a SQL query and return results as a Polars DataFrame."""
        import json

        full_query = f"{query} FORMAT JSONEachRow"

        resp = await self._client.post(
            "/",
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

    _VALID_CID = re.compile(r"^0x[0-9a-fA-F]+$")

    async def query_trades(self, condition_ids: list[str] | None = None) -> pl.DataFrame:
        """Query trades from ClickHouse, optionally filtered."""
        query = "SELECT * FROM trades_raw FINAL"
        if condition_ids:
            for cid in condition_ids:
                if not self._VALID_CID.match(cid):
                    raise ValueError(f"Invalid condition_id format: {cid!r}")
            ids_str = ", ".join(f"'{cid}'" for cid in condition_ids)
            query += f" WHERE condition_id IN ({ids_str})"
        return await self._execute(query)

    async def query_markets(self) -> pl.DataFrame:
        """Query market metadata from ClickHouse (via PG engine)."""
        return await self._execute("SELECT * FROM markets")

    async def query_custom(self, query: str, **params: Any) -> pl.DataFrame:
        """Run an arbitrary SQL query."""
        return await self._execute(query)

    # ------------------------------------------------------------------
    # Derived-view query builders (static for testability without httpx)
    # ------------------------------------------------------------------

    @staticmethod
    def mvf_query(traders: list[str] | None = None) -> str:
        """Build SQL for maker volume fractions from trader_volumes."""
        where = ""
        if traders:
            ids = ", ".join(f"'{t}'" for t in traders)
            where = f"WHERE trader IN ({ids})"
        return f"""
            SELECT
                trader,
                maker_vol,
                taker_vol,
                maker_vol + taker_vol AS total_vol,
                maker_vol / (maker_vol + taker_vol) AS mvf
            FROM trader_volumes FINAL
            {where}
        """

    @staticmethod
    def trader_pnl_query(
        traders: list[str] | None = None,
        condition_ids: list[str] | None = None,
    ) -> str:
        """Build SQL for trader-market PnL from derived views."""
        filters = []
        if traders:
            ids = ", ".join(f"'{t}'" for t in traders)
            filters.append(f"a.trader IN ({ids})")
        if condition_ids:
            cids = ", ".join(f"'{c}'" for c in condition_ids)
            filters.append(f"a.condition_id IN ({cids})")
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        # NOTE: ClickHouse 24.8 does not support `FROM table FINAL AS alias`.
        # Use subquery: `FROM (SELECT * FROM table FINAL) AS alias`.
        return f"""
            SELECT
                a.trader,
                a.condition_id,
                sum(a.net_tokens * if(mr.token_won, 1.0, 0.0)
                    + a.net_usd - a.total_fees) AS market_pnl,
                sum(a.volume) AS market_volume,
                sum(a.trade_count) AS trade_count,
                min(a.first_trade) AS first_trade,
                max(a.last_trade) AS last_trade,
                sum(a.price_x_vol) / nullIf(sum(a.volume), 0) AS wavg_entry_price
            FROM (SELECT * FROM trader_trade_agg FINAL) AS a
            INNER JOIN markets_resolved AS mr
                ON a.condition_id = mr.condition_id AND a.asset_id = mr.asset_id
            {where}
            GROUP BY a.trader, a.condition_id
        """

    async def query_mvf(self, traders: list[str] | None = None) -> pl.DataFrame:
        """Query maker volume fractions from derived views."""
        return await self._execute(self.mvf_query(traders=traders))

    async def query_trader_pnl(
        self,
        traders: list[str] | None = None,
        condition_ids: list[str] | None = None,
    ) -> pl.DataFrame:
        """Query trader-market PnL from derived views."""
        return await self._execute(
            self.trader_pnl_query(traders=traders, condition_ids=condition_ids)
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
