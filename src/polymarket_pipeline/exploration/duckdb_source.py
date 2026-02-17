"""DuckDB-backed data source for offline exploration.

Drop-in replacement for ExplorationDataSource that reads compact parquet
files and exported metadata tables via DuckDB. All ClickHouse-specific SQL
is automatically translated to DuckDB dialect.

Usage:
    from polymarket_pipeline.exploration.duckdb_source import DuckDBDataSource

    db = DuckDBDataSource(
        compact_dir=Path("order_filled_compact"),
        metadata_dir=Path("order_filled_compact/metadata"),
    )
    df = db.query_df("SELECT count() FROM polymarket.trades_raw FINAL")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl


def _find_balanced_args(sql: str, start: int) -> tuple[list[str], int]:
    """Parse balanced parenthesized argument list starting at open paren.

    Args:
        sql: Full SQL string.
        start: Index of the opening '('.

    Returns:
        (list_of_arg_strings, index_after_closing_paren)
    """
    depth = 0
    current: list[str] = []
    args: list[str] = []
    i = start
    while i < len(sql):
        c = sql[i]
        if c == "(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif c == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(current).strip())
                return args, i + 1
        elif c == "," and depth == 1:
            args.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    return args, i


def _find_function(sql: str, name: str, start: int = 0) -> int | None:
    """Find next occurrence of function call `name(` in sql, case-insensitive.

    Skips occurrences inside string literals and where the name is part of
    a longer identifier (e.g. avoids matching 'countIf' when looking for 'if').
    """
    pattern = re.compile(
        r"(?<![a-zA-Z_])" + re.escape(name) + r"\s*\(",
        re.IGNORECASE,
    )
    m = pattern.search(sql, start)
    if m is None:
        return None
    return m.start()


def _replace_function(
    sql: str,
    name: str,
    transformer: Any,
) -> str:
    """Replace all occurrences of name(...args...) using transformer(args) -> str."""
    result = []
    pos = 0
    while True:
        idx = _find_function(sql, name, pos)
        if idx is None:
            result.append(sql[pos:])
            break
        result.append(sql[pos:idx])
        # Find the opening paren
        paren_idx = sql.index("(", idx)
        args, end = _find_balanced_args(sql, paren_idx)
        replacement = transformer(args)
        result.append(replacement)
        pos = end
    return "".join(result)


def translate_ch_to_duckdb(sql: str) -> str:
    """Translate ClickHouse SQL dialect to DuckDB.

    Handles all CH-specific functions and syntax used by exploration components.
    """
    # 1. Strip schema prefix
    sql = sql.replace("polymarket.", "")

    # 2. Strip FINAL keyword
    sql = re.sub(r"\bFINAL\b", "", sql)

    # 3. count() -> count(*)
    sql = re.sub(r"\bcount\(\)", "count(*)", sql)

    # 4. quantile(p)(col) -> quantile_cont(col, p) — must come before generic function handling
    # Pattern: quantile(0.25)(pnl)
    quantile_pat = re.compile(r"\bquantile\(\s*([0-9.]+)\s*\)\s*\(", re.IGNORECASE)
    while True:
        m = quantile_pat.search(sql)
        if not m:
            break
        p_val = m.group(1)
        # Find args after the second open paren
        paren_start = m.end() - 1  # position of second '('
        args, end = _find_balanced_args(sql, paren_start)
        replacement = f"quantile_cont({args[0]}, {p_val})"
        sql = sql[: m.start()] + replacement + sql[end:]

    # 5. multiIf(c1, v1, c2, v2, ..., default) -> CASE WHEN ... END
    def _transform_multi_if(args: list[str]) -> str:
        parts = ["CASE"]
        # Every pair (condition, value), last arg is default
        i = 0
        while i < len(args) - 1:
            parts.append(f" WHEN {args[i]} THEN {args[i + 1]}")
            i += 2
        parts.append(f" ELSE {args[-1]} END")
        return "".join(parts)

    sql = _replace_function(sql, "multiIf", _transform_multi_if)

    # 6. sumIf(val, cond) -> SUM(CASE WHEN cond THEN val END)
    def _transform_sum_if(args: list[str]) -> str:
        return f"SUM(CASE WHEN {args[1]} THEN {args[0]} END)"

    sql = _replace_function(sql, "sumIf", _transform_sum_if)

    # 7. countIf(cond) -> COUNT(CASE WHEN cond THEN 1 END)
    def _transform_count_if(args: list[str]) -> str:
        return f"COUNT(CASE WHEN {args[0]} THEN 1 END)"

    sql = _replace_function(sql, "countIf", _transform_count_if)

    # 8. argMax(val, order_col) -> arg_max(val, order_col)
    sql = _replace_function(sql, "argMax", lambda a: f"arg_max({', '.join(a)})")

    # 9. stddevPop(col) -> stddev_pop(col)
    sql = _replace_function(sql, "stddevPop", lambda a: f"stddev_pop({', '.join(a)})")

    # 10. intDiv(a, b) -> CAST(FLOOR((a) / (b)) AS BIGINT)
    def _transform_int_div(args: list[str]) -> str:
        return f"CAST(FLOOR(({args[0]}) / ({args[1]})) AS BIGINT)"

    sql = _replace_function(sql, "intDiv", _transform_int_div)

    # 11. toUnixTimestamp(x) -> epoch(x)::BIGINT
    sql = _replace_function(
        sql, "toUnixTimestamp", lambda a: f"CAST(epoch({a[0]}) AS BIGINT)"
    )

    # 12. toDateTime('str') -> CAST('str' AS TIMESTAMP)
    sql = _replace_function(
        sql, "toDateTime", lambda a: f"CAST({a[0]} AS TIMESTAMP)"
    )

    # 13. toFloat32(x) -> CAST(x AS FLOAT)
    sql = _replace_function(sql, "toFloat32", lambda a: f"CAST({a[0]} AS FLOAT)")

    # 14. toUInt8(x) -> CAST(x AS TINYINT)
    sql = _replace_function(sql, "toUInt8", lambda a: f"CAST({a[0]} AS TINYINT)")

    # 15. if(cond, then, else) -> CASE WHEN cond THEN then ELSE else END
    # Must come AFTER multiIf and sumIf to avoid partial matches
    def _transform_if(args: list[str]) -> str:
        return f"CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END"

    sql = _replace_function(sql, "if", _transform_if)

    # 16. now() works in both, but ensure it's present
    # 17. greatest() and least() work in both

    # Clean up any double spaces from FINAL removal
    sql = re.sub(r"  +", " ", sql)

    return sql


class DuckDBDataSource:
    """DuckDB-backed data source for offline exploration.

    Registers compact parquet as trades_raw and metadata parquet files
    as token_market_map and markets tables.
    """

    def __init__(
        self,
        compact_dir: Path,
        metadata_dir: Path | None = None,
    ) -> None:
        import duckdb

        self._conn = duckdb.connect(":memory:")

        # Register compact parquet as trades_raw
        compact_glob = str(compact_dir / "compact_*.parquet")
        self._conn.execute(
            f"CREATE VIEW trades_raw AS SELECT * FROM read_parquet('{compact_glob}')"
        )

        # Register metadata tables from exported parquet
        meta = metadata_dir or compact_dir / "metadata"
        token_map_path = meta / "token_market_map.parquet"
        markets_path = meta / "markets.parquet"

        if token_map_path.exists():
            self._conn.execute(
                f"CREATE VIEW token_market_map AS "
                f"SELECT * FROM read_parquet('{token_map_path}')"
            )
        else:
            raise FileNotFoundError(
                f"token_market_map.parquet not found at {token_map_path}. "
                "Run: uv run python scripts/recompress_parquet.py --export-metadata"
            )

        if markets_path.exists():
            self._conn.execute(
                f"CREATE VIEW markets AS SELECT * FROM read_parquet('{markets_path}')"
            )
        else:
            raise FileNotFoundError(
                f"markets.parquet not found at {markets_path}. "
                "Run: uv run python scripts/recompress_parquet.py --export-metadata"
            )

        # Also create a 'trades' alias (some validation queries reference it)
        self._conn.execute("CREATE VIEW trades AS SELECT * FROM trades_raw")

    def query_df(self, sql: str, params: dict[str, Any] | None = None) -> pl.DataFrame:
        """Run ClickHouse SQL (auto-translated) and return Polars DataFrame."""
        translated = translate_ch_to_duckdb(sql)
        try:
            result = self._conn.execute(translated)
            arrow_table = result.fetch_arrow_table()
            return pl.from_arrow(arrow_table)
        except Exception:
            # Log the translated SQL for debugging
            import structlog

            structlog.get_logger().error(
                "duckdb_query_failed",
                original_sql=sql[:200],
                translated_sql=translated[:200],
            )
            raise

    def query_raw(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run ClickHouse SQL (auto-translated) and return list of dicts."""
        translated = translate_ch_to_duckdb(sql)
        try:
            result = self._conn.execute(translated)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row, strict=False)) for row in result.fetchall()]
        except Exception:
            import structlog

            structlog.get_logger().error(
                "duckdb_query_failed",
                original_sql=sql[:200],
                translated_sql=translated[:200],
            )
            raise

    def get_schema(self, table: str) -> list[dict[str, str]]:
        """Get column names and types for a registered table."""
        rows = self._conn.execute(
            f"SELECT column_name AS name, data_type AS type "
            f"FROM information_schema.columns WHERE table_name = '{table}'"
        ).fetchall()
        return [{"name": r[0], "type": r[1], "comment": ""} for r in rows]

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._conn.close()
