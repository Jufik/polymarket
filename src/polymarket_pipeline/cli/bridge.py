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


_BRIDGE_HELPERS: dict[str, Any] = {
    "list_strategies": list_strategies,
    "read_json_file": read_json_file,
    "read_text_file": read_text_file,
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
