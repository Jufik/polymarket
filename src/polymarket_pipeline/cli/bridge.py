"""JSON-in/JSON-out dispatcher for TypeScript -> Python calls.

Usage:
    python -m polymarket_pipeline.cli.bridge \
        --module polymarket_pipeline.exploration.lifecycle \
        --func run_stage \
        --args '{"strategy": "skilled_traders", "stage_id": "01a_..."}'

Outputs JSON to stdout. Errors go to stderr with non-zero exit code.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

STRATEGIES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "strategies"


def list_strategies() -> list[str]:
    """List all strategy directories."""
    return sorted(
        d.name
        for d in STRATEGIES_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    )


def read_json_file(path: str) -> dict[str, Any] | list[Any] | None:
    """Read and parse a JSON file."""
    p = Path(path)
    if not p.exists():
        return None
    result: dict[str, Any] | list[Any] = json.loads(p.read_text())
    return result


def read_text_file(path: str) -> str | None:
    """Read a text file."""
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text()


def _sleep(seconds: float) -> None:
    """Sleep helper for testing timeout behavior."""
    import time

    time.sleep(seconds)


def query_clickhouse(sql: str) -> list[dict[str, Any]]:
    """Execute read-only SQL against ClickHouse, return list of dicts."""
    from polymarket_pipeline.exploration.data import ExplorationDataSource

    db = ExplorationDataSource()
    return db.query_raw(sql)


def get_schema(table: str) -> list[dict[str, str]]:
    """Get column schema (name, type, comment) for a ClickHouse table."""
    from polymarket_pipeline.exploration.data import ExplorationDataSource

    db = ExplorationDataSource()
    return db.get_schema(table)


def read_parquet(
    path: str,
    columns: list[str] | None = None,
    n_rows: int = 100,
) -> list[dict[str, Any]]:
    """Read rows from a Parquet file, return as list of dicts."""
    import polars as pl

    df = pl.read_parquet(path, columns=columns, n_rows=n_rows)
    return df.to_dicts()


def describe_parquet(path: str) -> dict[str, Any]:
    """Describe a Parquet file: shape, dtypes, null counts, summary stats."""
    import polars as pl

    df = pl.read_parquet(path)
    return {
        "shape": {"rows": df.height, "columns": df.width},
        "dtypes": {col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)},
        "null_counts": {col: df[col].null_count() for col in df.columns},
        "describe": df.describe().to_dicts(),
    }


_BRIDGE_HELPERS: dict[str, Any] = {
    "list_strategies": list_strategies,
    "read_json_file": read_json_file,
    "read_text_file": read_text_file,
    "_sleep": _sleep,
    "query_clickhouse": query_clickhouse,
    "get_schema": get_schema,
    "read_parquet": read_parquet,
    "describe_parquet": describe_parquet,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="TS->Python bridge")
    parser.add_argument("--module", required=True, help="Python module path")
    parser.add_argument("--func", required=True, help="Function name")
    parser.add_argument("--args", default="{}", help="JSON-encoded kwargs")
    args = parser.parse_args()

    try:
        # Allow calling bridge's own helpers (list_strategies, read_json_file, etc.)
        if args.module == "polymarket_pipeline.cli.bridge":
            func = _BRIDGE_HELPERS[args.func]
        else:
            mod = importlib.import_module(args.module)
            func = getattr(mod, args.func)

        kwargs = json.loads(args.args)
        result = func(**kwargs)

        # Handle async functions
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)

        json.dump(result, sys.stdout, default=str)
    except Exception as e:
        # stdout is reserved for JSON output; stderr must be plain text for TS bridge
        print(f"Bridge error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
