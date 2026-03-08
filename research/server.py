"""Research server — persistent DuckDB + replay engine over HTTP.

Provides /query, /sweep, /replay, /status, /refresh endpoints.
Useful for notebooks and persistent state across Claude Code sessions.

Usage:
    PYTHONPATH=. uv run python research/server.py           # port 9999
    PYTHONPATH=. uv run python research/server.py --port 8888
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from research.db import ResearchDB

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_db()
    yield


app = FastAPI(title="Polymarket Research Server", version="0.1.0", lifespan=lifespan)

# Singleton — loaded at startup
_db: ResearchDB | None = None


def get_db() -> ResearchDB:
    global _db
    if _db is None:
        _db = ResearchDB.get()
    return _db


# ── Request / Response models ──────────────────────────────────────────


class QueryRequest(BaseModel):
    sql: str
    params: list[Any] | None = None


class SweepRequest(BaseModel):
    tags: list[str] | None = None
    min_trades: list[int] | None = None
    excess_hr_pp: list[int] | None = None
    price_ceil: list[float] | None = None
    max_hold_hours: int = 48


class ReplayRequest(BaseModel):
    universe: list[str]  # condition_ids
    start_month: int | None = None
    end_month: int | None = None
    threshold: float = 0.30
    capital_usd: float = 1000.0
    max_position_usd: float = 50.0


# ── Endpoints ──────────────────────────────────────────────────────────


@app.get("/status")
async def status() -> dict[str, Any]:
    """Return loaded table info and data directory."""
    d = get_db()
    info = d.status()
    info["uptime_s"] = time.time() - _startup_time
    return info


@app.post("/query")
async def query(req: QueryRequest) -> dict[str, Any]:
    """Execute ad-hoc DuckDB SQL and return results as JSON."""
    d = get_db()
    t0 = time.time()
    try:
        df = d.query(req.sql, req.params)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    elapsed = time.time() - t0
    return {
        "rows": len(df),
        "elapsed_s": round(elapsed, 3),
        "columns": df.columns,
        "data": df.to_dicts(),
    }


@app.post("/sweep")
async def sweep(req: SweepRequest) -> dict[str, Any]:
    """Run tag-HR-copy sweep with DuckDB backend."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sweep_duckdb",
        Path("research/hypotheses/tag-hr-copy/scripts/sweep_duckdb.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    t0 = time.time()
    if req.min_trades:
        mod.MT = req.min_trades
    if req.excess_hr_pp:
        mod.EP = req.excess_hr_pp
    if req.price_ceil:
        mod.PRICE_CEIL = req.price_ceil
    mod.MAX_H = req.max_hold_hours

    results = await asyncio.to_thread(mod.run_sweep, req.tags)
    elapsed = time.time() - t0
    results["elapsed_s"] = round(elapsed, 1)
    return results


@app.post("/replay")
async def replay(req: ReplayRequest) -> dict[str, Any]:
    """Run tick-by-tick replay for a strategy universe."""
    from research.fast_replay import load_replay_resolutions, load_replay_trades
    from research.sync_replay import SyncReplayRunner
    from research.strategies.example import ExampleStrategy

    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
    from polymarket_pipeline.strategies.ledger.analytics import compute_summary
    from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
    from polymarket_pipeline.strategies.types import ExecutionMode

    universe = set(req.universe)
    t0 = time.time()

    # Load trades
    ticks = load_replay_trades(
        universe=universe,
        start_month=req.start_month,
        end_month=req.end_month,
    )

    # Load resolutions
    resolutions, token_map = load_replay_resolutions()
    resolutions = {k: v for k, v in resolutions.items() if k in universe}

    # Setup
    strategy = ExampleStrategy(threshold=req.threshold)
    config = StrategyConfig(
        name="replay_api", enabled=True, mode=ExecutionMode.REPLAY,
        capital_usd=req.capital_usd,
        max_position_usd=req.max_position_usd,
        max_open_positions=500, cooldown_s=0,
    )
    executor = SimulatedExecutor(fee_pct=0.0)
    gateway = ExecutionGateway(executor, strategy_budgets={strategy.name: req.capital_usd})
    ctx = InMemoryContext()
    ledger_path = Path("research/output/api_replay.parquet")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = ParquetLedger(ledger_path)

    runner = SyncReplayRunner(
        strategy=strategy, ctx=ctx, gateway=gateway, config=config,
        resolutions=resolutions, token_map=token_map, ledger=ledger,
    )

    result = runner.run(ticks)
    await ledger.flush()
    records = await ledger.read_all()

    elapsed = time.time() - t0

    response: dict[str, Any] = {
        "total_trades": result.total_trades,
        "total_fills": result.total_fills,
        "settled": runner.n_settled,
        "rejected": len(result.rejected_intents),
        "elapsed_s": round(elapsed, 2),
        "ticks_loaded": len(ticks),
    }

    if records:
        summary = compute_summary(records)
        response["summary"] = {
            "hit_rate": round(summary.hit_rate, 4),
            "total_pnl_net": round(summary.total_pnl_net, 2),
            "sharpe": round(summary.sharpe, 2),
            "max_drawdown": round(summary.max_drawdown, 2),
            "profit_factor": round(summary.profit_factor, 2),
            "total_fills": summary.total_fills,
        }

    return response


@app.post("/refresh")
async def refresh() -> dict[str, Any]:
    """Re-export from CH and reload DuckDB. Runs export_snapshot.py."""
    import subprocess

    t0 = time.time()
    proc = subprocess.run(
        ["uv", "run", "python", "research/export_snapshot.py"],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=proc.stderr[-500:])

    # Reload DuckDB
    global _db
    ResearchDB._instance = None
    _db = ResearchDB.get()

    elapsed = time.time() - t0
    return {"status": "refreshed", "elapsed_s": round(elapsed, 1)}


_startup_time = time.time()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
