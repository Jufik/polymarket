"""FastAPI dependencies: shared clients and configuration."""

from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

LOG_DIR = Path(os.environ.get("PM_LOG_DIR", "logs/paper"))
CH_HOST = os.environ.get("PM_CH_HOST", "localhost")
CH_PORT = os.environ.get("PM_CH_PORT", "18123")
CH_DB = os.environ.get("PM_CH_DATABASE", "polymarket")
CH_URL = f"http://{CH_HOST}:{CH_PORT}/"

# ---------------------------------------------------------------------------
# Request metrics -- sliding window
# ---------------------------------------------------------------------------

request_times: deque[float] = deque()
WINDOW_S = 60.0


def prune_request_times(now: float) -> None:
    cutoff = now - WINDOW_S
    while request_times and request_times[0] < cutoff:
        request_times.popleft()


# ---------------------------------------------------------------------------
# Async HTTP clients (lazy singletons)
# ---------------------------------------------------------------------------

_ch_client: httpx.AsyncClient | None = None
_generic_client: httpx.AsyncClient | None = None


def get_ch() -> httpx.AsyncClient:
    """ClickHouse async client (base_url = CH_URL)."""
    global _ch_client
    if _ch_client is None:
        _ch_client = httpx.AsyncClient(base_url=CH_URL, timeout=30.0)
    return _ch_client


def get_generic_client() -> httpx.AsyncClient:
    """Generic async client for proxying to introspect servers, etc."""
    global _generic_client
    if _generic_client is None:
        _generic_client = httpx.AsyncClient(timeout=5.0)
    return _generic_client


# ---------------------------------------------------------------------------
# ClickHouse parameterized query helper
# ---------------------------------------------------------------------------


async def ch_query(
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run a parameterized ClickHouse query, return rows as dicts.

    Parameters use the ClickHouse HTTP ``param_`` prefix convention:

    .. code-block:: python

        await ch_query(
            "SELECT * FROM t WHERE id = {cid:String}",
            {"cid": "0xabc"},
        )

    Translates to HTTP query-string ``param_cid=0xabc``.
    """
    http_params: dict[str, str] = {"database": CH_DB}
    if params:
        for k, v in params.items():
            http_params[f"param_{k}"] = str(v)
    try:
        resp = await get_ch().post(
            "/",
            content=sql + " FORMAT JSONEachRow",
            params=http_params,
            headers={"Content-Type": "text/plain"},
        )
        resp.raise_for_status()
        rows: list[dict[str, Any]] = []
        for line in resp.text.strip().split("\n"):
            if line.strip():
                rows.append(json.loads(line))
        return rows
    except Exception as e:
        log.warning("ch_query_error", error=str(e))
        return []


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def read_all_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read all lines from a JSONL file."""
    if not path.exists():
        return []
    try:
        records: list[dict[str, Any]] = []
        for line in path.read_text(errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    except Exception:
        return []


def tail_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    """Read the last *n* lines from a JSONL file."""
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
        lines = data.decode(errors="replace").splitlines()
        records: list[dict[str, Any]] = []
        for line in lines[-n:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records
    except Exception:
        return []


def strategy_dirs() -> list[str]:
    """Return sorted list of strategy log directories."""
    if not LOG_DIR.is_dir():
        return []
    return sorted(
        d.name
        for d in LOG_DIR.iterdir()
        if d.is_dir() and ((d / "intents.jsonl").exists() or (d / "fills.jsonl").exists())
    )
