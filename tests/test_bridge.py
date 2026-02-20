"""Tests for the TypeScript -> Python bridge dispatcher."""
import json
import subprocess
import sys
from typing import Any


def _run_bridge(module: str, func: str, args: dict[str, Any]) -> Any:
    """Call bridge.py as subprocess, return parsed JSON output."""
    result = subprocess.run(
        [
            sys.executable, "-m", "polymarket_pipeline.cli.bridge",
            "--module", module,
            "--func", func,
            "--args", json.dumps(args),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Bridge failed: {result.stderr}"
    return json.loads(result.stdout)


def test_bridge_dispatches_sync_function():
    """Bridge can call a sync function and return JSON."""
    # json.loads is a sync function that returns a dict — perfect for round-trip test.
    # json.dumps would double-encode (returns str, then json.dump wraps it again).
    result = _run_bridge("json", "loads", {"s": '{"hello": "world"}'})
    assert result == {"hello": "world"}


def test_bridge_returns_error_on_missing_module():
    """Bridge returns non-zero exit code for missing module."""
    result = subprocess.run(
        [
            sys.executable, "-m", "polymarket_pipeline.cli.bridge",
            "--module", "nonexistent_module_xyz",
            "--func", "foo",
            "--args", "{}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0


def test_bridge_returns_error_on_missing_function():
    """Bridge returns non-zero exit code for missing function."""
    result = subprocess.run(
        [
            sys.executable, "-m", "polymarket_pipeline.cli.bridge",
            "--module", "json",
            "--func", "nonexistent_func_xyz",
            "--args", "{}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0


def test_bridge_handles_list_strategies():
    """Bridge can list strategy directories."""
    result = _run_bridge(
        "polymarket_pipeline.cli.bridge",
        "list_strategies",
        {},
    )
    assert isinstance(result, list)
    assert "consistency_copy" in result
