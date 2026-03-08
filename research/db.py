"""DuckDB session manager for research queries.

Loads the Parquet snapshot into DuckDB in-memory at first use.
Positions + metadata loaded in-memory (~4s startup, ~2.5 GB).
trader_trade_agg stays as external Parquet (scanned on demand via DuckDB reader).
Trades stay as external Parquet (scanned via Polars for replay).

Usage:
    from research.db import db

    # SQL query → polars DataFrame
    df = db.query("SELECT count() FROM maker_positions")

    # SQL query → list of dicts
    rows = db.fetchall("SELECT trader, hr FROM ...")

    # Raw DuckDB connection for advanced use
    con = db.con
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

DATA_DIR = Path("data/research")


class ResearchDB:
    """Singleton DuckDB session backed by Parquet snapshot."""

    _instance: ResearchDB | None = None

    def __init__(self) -> None:
        self._con: duckdb.DuckDBPyConnection | None = None
        self._loaded = False

    @classmethod
    def get(cls) -> ResearchDB:
        if cls._instance is None:
            cls._instance = cls()
        if not cls._instance._loaded:
            cls._instance._load()
        return cls._instance

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None or not self._loaded:
            self._load()
        assert self._con is not None
        return self._con

    def query(self, sql: str, params: list[Any] | None = None) -> pl.DataFrame:
        """Execute SQL and return a Polars DataFrame."""
        if params:
            result = self.con.execute(sql, params)
        else:
            result = self.con.execute(sql)
        return result.pl()

    def fetchall(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Execute SQL and return list of dicts."""
        df = self.query(sql, params)
        return df.to_dicts()

    def fetchone(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        """Execute SQL and return first row as dict, or None."""
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        """Execute SQL without returning results (CREATE TABLE, etc.)."""
        if params:
            self.con.execute(sql, params)
        else:
            self.con.execute(sql)

    def _load(self) -> None:
        """Load Parquet snapshot into DuckDB."""
        if not DATA_DIR.exists():
            raise FileNotFoundError(
                f"{DATA_DIR} not found. Run: uv run python research/export_snapshot.py"
            )

        t0 = time.time()
        self._con = duckdb.connect(":memory:")

        # Configure DuckDB for the available resources
        self._con.execute("SET memory_limit = '20GB'")
        self._con.execute("SET threads = 8")

        loaded = []

        # -- In-memory tables (hot path for sweeps) --

        pos_dir = DATA_DIR / "positions"
        meta_dir = DATA_DIR / "metadata"

        # Primary sweep table
        if (pos_dir / "maker_positions_resolved.parquet").exists():
            self._con.execute(
                "CREATE TABLE maker_positions AS "
                f"SELECT * FROM read_parquet('{pos_dir}/maker_positions_resolved.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM maker_positions").fetchone()[0]
            loaded.append(f"maker_positions: {cnt:,}")

        # trader_trade_agg — external Parquet (134M rows, ~12 GB on disk)
        if (pos_dir / "trader_trade_agg.parquet").exists():
            self._con.execute(
                "CREATE VIEW trader_trade_agg AS "
                f"SELECT * FROM read_parquet('{pos_dir}/trader_trade_agg.parquet')"
            )
            loaded.append("trader_trade_agg: external parquet")

        # yes_entry_data — pre-joined YES-side entries sorted by condition_id
        # Pre-filtered: trader_trade_agg FINAL JOIN token_market_map (YES only, vol>0, net_usd>0)
        # Sorted by condition_id for efficient predicate pushdown (1.3 GB vs 12 GB full)
        if (pos_dir / "yes_entry_data.parquet").exists():
            self._con.execute(
                "CREATE VIEW yes_entry_data AS "
                f"SELECT * FROM read_parquet('{pos_dir}/yes_entry_data.parquet')"
            )
            loaded.append("yes_entry_data: external parquet (condition_id sorted)")

        # trader_volumes
        if (pos_dir / "trader_volumes.parquet").exists():
            self._con.execute(
                "CREATE TABLE trader_volumes AS "
                f"SELECT * FROM read_parquet('{pos_dir}/trader_volumes.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM trader_volumes").fetchone()[0]
            loaded.append(f"trader_volumes: {cnt:,}")

        # markets_resolved
        if (meta_dir / "markets_resolved.parquet").exists():
            self._con.execute(
                "CREATE TABLE markets_resolved AS "
                f"SELECT * FROM read_parquet('{meta_dir}/markets_resolved.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM markets_resolved").fetchone()[0]
            loaded.append(f"markets_resolved: {cnt:,}")

        # token_market_map
        if (meta_dir / "token_market_map.parquet").exists():
            self._con.execute(
                "CREATE TABLE token_market_map AS "
                f"SELECT * FROM read_parquet('{meta_dir}/token_market_map.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM token_market_map").fetchone()[0]
            loaded.append(f"token_market_map: {cnt:,}")

        # markets
        if (meta_dir / "markets.parquet").exists():
            self._con.execute(
                "CREATE TABLE markets AS "
                f"SELECT * FROM read_parquet('{meta_dir}/markets.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM markets").fetchone()[0]
            loaded.append(f"markets: {cnt:,}")

        # events
        if (meta_dir / "events.parquet").exists():
            self._con.execute(
                "CREATE TABLE events AS "
                f"SELECT * FROM read_parquet('{meta_dir}/events.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM events").fetchone()[0]
            loaded.append(f"events: {cnt:,}")

        # event_tags (pre-joined with tag labels)
        if (meta_dir / "event_tags.parquet").exists():
            self._con.execute(
                "CREATE TABLE event_tags AS "
                f"SELECT * FROM read_parquet('{meta_dir}/event_tags.parquet')"
            )
            cnt = self._con.execute("SELECT count() FROM event_tags").fetchone()[0]
            loaded.append(f"event_tags: {cnt:,}")

        # -- Trades: external Parquet view for replay --
        trades_dir = DATA_DIR / "trades"
        if trades_dir.exists() and list(trades_dir.glob("trades_*.parquet")):
            self._con.execute(
                "CREATE VIEW trades AS "
                f"SELECT * FROM read_parquet('{trades_dir}/trades_*.parquet', "
                "hive_partitioning=false)"
            )
            loaded.append("trades: external parquet (monthly files)")

        elapsed = time.time() - t0
        self._loaded = True

        print(f"ResearchDB loaded in {elapsed:.1f}s:")
        for item in loaded:
            print(f"  {item}")

    def status(self) -> dict[str, Any]:
        """Return loaded table info."""
        tables = self.con.execute(
            "SELECT table_name, estimated_size FROM duckdb_tables()"
        ).fetchall()
        views = self.con.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal"
        ).fetchall()
        return {
            "tables": {name: size for name, size in tables},
            "views": [name for (name,) in views],
            "data_dir": str(DATA_DIR),
        }


# Module-level singleton accessor
db = ResearchDB.get
