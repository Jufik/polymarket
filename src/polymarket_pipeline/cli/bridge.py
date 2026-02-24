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

STRATEGIES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "research" / "strategies"


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


# ---------------------------------------------------------------------------
# Code server bridge helpers
# ---------------------------------------------------------------------------


def run_python_code(code: str) -> str:
    """Execute Python code in a subprocess and capture stdout."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(STRATEGIES_ROOT.parent),
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]: {result.stderr}"
    if result.returncode != 0:
        output += f"\n[exit code: {result.returncode}]"
    return output


def run_registered_tool(handler_path: str, args: dict[str, Any]) -> object:
    """Execute a registered tool's Python handler."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("tool_handler", handler_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load handler from {handler_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "run"):
        raise AttributeError(f"Handler {handler_path} must define a run() function")
    result: object = mod.run(**args)
    return result


# ---------------------------------------------------------------------------
# Orchestrator bridge helpers
# ---------------------------------------------------------------------------


def tree_status(strategy: str, format: str = "text") -> str:
    """Get exploration tree visualization as text or mermaid."""
    from polymarket_pipeline.exploration.lifecycle import load_tree

    tree = load_tree(strategy)

    if format == "mermaid":
        return tree.to_mermaid()

    # Text format: list stages with status and depth
    lines: list[str] = [f"Strategy: {tree.strategy_name}", f"Stages: {len(tree.stages)}", ""]
    for stage in tree.stages.values():
        indent = "  " * stage.depth
        status = stage.status.value
        metrics_info = ""
        if stage.metrics and stage.metrics.unique_traders is not None:
            metrics_info = f" (traders={stage.metrics.unique_traders})"
        rec = ""
        if stage.analysis and stage.analysis.branch_recommendation:
            rec = f" [{stage.analysis.branch_recommendation.value}]"
        lines.append(f"{indent}{stage.id}: {stage.name} ({status}){metrics_info}{rec}")
    return "\n".join(lines)


def suggest_next(strategy: str, n: int = 3) -> list[dict[str, Any]]:
    """Rank and return top-N suggested refinements."""
    from polymarket_pipeline.exploration.agent import suggest_next_stages
    from polymarket_pipeline.exploration.lifecycle import load_tree
    from polymarket_pipeline.exploration.tree import build_exploration_context

    tree = load_tree(strategy)
    context = build_exploration_context(tree)
    suggestions, _filtered = suggest_next_stages(tree, max_suggestions=n, context=context)

    result: list[dict[str, Any]] = []
    for parent_stage, refinement in suggestions:
        result.append(
            {
                "parent_id": parent_stage.id,
                "parent_name": parent_stage.name,
                "refinement_name": refinement.name,
                "refinement_type": refinement.refinement_type.value,
                "description": refinement.description,
                "hypothesis": refinement.hypothesis,
                "priority": refinement.priority,
                "estimated_complexity": refinement.estimated_complexity,
            }
        )
    return result


def compare_stages(strategy: str, stage_ids: list[str]) -> list[dict[str, Any]]:
    """Side-by-side comparison of stages."""
    from polymarket_pipeline.exploration.lifecycle import load_tree

    tree = load_tree(strategy)
    result: list[dict[str, Any]] = []

    for sid in stage_ids:
        stage = tree.get_stage(sid)
        if stage is None:
            result.append({"id": sid, "error": "Stage not found"})
            continue

        entry: dict[str, Any] = {
            "id": stage.id,
            "name": stage.name,
            "status": stage.status.value,
            "depth": stage.depth,
            "parent_id": stage.parent_id,
        }
        if stage.metrics:
            entry["metrics"] = stage.metrics.model_dump(exclude_none=True)
        if stage.analysis:
            entry["analysis"] = {
                "confidence": stage.analysis.confidence,
                "branch_recommendation": stage.analysis.branch_recommendation.value,
                "information_gain": stage.analysis.information_gain,
                "summary": stage.analysis.summary,
                "key_insights": stage.analysis.key_insights,
                "concerns": stage.analysis.concerns,
                "num_refinements": len(stage.analysis.proposed_refinements),
            }
        result.append(entry)

    return result


def run_stage_bridge(strategy: str, stage_id: str) -> dict[str, Any]:
    """Execute a stage script synchronously."""
    from polymarket_pipeline.exploration.lifecycle import (
        SilentCallback,
        load_tree,
        run_stage,
    )

    tree = load_tree(strategy)
    stage = tree.get_stage(stage_id)
    if stage is None:
        return {"success": False, "error": f"Stage '{stage_id}' not found"}

    result = run_stage(strategy, tree, stage, callback=SilentCallback())
    return {
        "success": result.success,
        "stage_id": result.stage.id,
        "status": result.stage.status.value,
        "error": result.error,
    }


async def review_stage_bridge(strategy: str, stage_id: str) -> dict[str, Any]:
    """Claude review of a completed stage."""
    from polymarket_pipeline.exploration.lifecycle import (
        SilentCallback,
        load_tree,
        review_stage_lifecycle,
    )
    from polymarket_pipeline.exploration.tree import build_exploration_context

    tree = load_tree(strategy)
    stage = tree.get_stage(stage_id)
    if stage is None:
        return {"success": False, "error": f"Stage '{stage_id}' not found"}

    context = build_exploration_context(tree)
    result = await review_stage_lifecycle(
        strategy=strategy,
        tree=tree,
        stage=stage,
        callback=SilentCallback(),
        context=context,
    )
    resp: dict[str, Any] = {
        "success": result.success,
        "stage_id": result.stage.id,
        "status": result.stage.status.value,
        "has_critical_dq_issues": result.has_critical_dq_issues,
        "error": result.error,
    }
    if result.analysis:
        resp["analysis"] = {
            "confidence": result.analysis.confidence,
            "branch_recommendation": result.analysis.branch_recommendation.value,
            "summary": result.analysis.summary,
            "num_refinements": len(result.analysis.proposed_refinements),
        }
    return resp


async def generate_stage_bridge(
    strategy: str, parent_id: str, refinement_name: str
) -> dict[str, Any]:
    """Generate a new child stage from a parent's proposed refinement."""
    from polymarket_pipeline.exploration.lifecycle import (
        SilentCallback,
        generate_stage_lifecycle,
        load_tree,
    )

    tree = load_tree(strategy)
    parent = tree.get_stage(parent_id)
    if parent is None:
        return {"success": False, "error": f"Parent stage '{parent_id}' not found"}

    if not parent.analysis:
        return {"success": False, "error": f"Parent stage '{parent_id}' has no analysis"}

    # Find the matching refinement by name
    refinement = None
    for ref in parent.analysis.proposed_refinements:
        if ref.name == refinement_name:
            refinement = ref
            break

    if refinement is None:
        available = [r.name for r in parent.analysis.proposed_refinements]
        return {
            "success": False,
            "error": f"Refinement '{refinement_name}' not found. Available: {available}",
        }

    result = await generate_stage_lifecycle(
        strategy=strategy,
        tree=tree,
        parent=parent,
        refinement=refinement,
        callback=SilentCallback(),
    )
    return {
        "success": result.success,
        "stage_id": result.stage.id,
        "stage_name": result.stage.name,
        "error": result.error,
    }


def set_direction(strategy: str, stage_id: str, direction: str) -> dict[str, Any]:
    """Override branch recommendation for a stage."""
    from polymarket_pipeline.exploration.lifecycle import load_tree, save_tree
    from polymarket_pipeline.exploration.tree import BranchRecommendation

    tree = load_tree(strategy)
    stage = tree.get_stage(stage_id)
    if stage is None:
        return {"success": False, "error": f"Stage '{stage_id}' not found"}

    if not stage.analysis:
        return {"success": False, "error": f"Stage '{stage_id}' has no analysis to update"}

    try:
        rec = BranchRecommendation(direction)
    except ValueError:
        valid = [v.value for v in BranchRecommendation]
        return {"success": False, "error": f"Invalid direction '{direction}'. Valid: {valid}"}

    old_rec = stage.analysis.branch_recommendation.value
    stage.analysis.branch_recommendation = rec
    save_tree(strategy, tree)

    return {
        "success": True,
        "stage_id": stage_id,
        "old_direction": old_rec,
        "new_direction": rec.value,
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
    "run_python_code": run_python_code,
    "run_registered_tool": run_registered_tool,
    "tree_status": tree_status,
    "suggest_next": suggest_next,
    "compare_stages": compare_stages,
    "run_stage_bridge": run_stage_bridge,
    "review_stage_bridge": review_stage_bridge,
    "generate_stage_bridge": generate_stage_bridge,
    "set_direction": set_direction,
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
