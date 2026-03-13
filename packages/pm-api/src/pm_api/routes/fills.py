"""Fill listing endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import LOG_DIR, strategy_dirs, tail_jsonl

router = APIRouter(prefix="/api/v1", tags=["fills"])


@router.get("/fills")
async def list_fills(
    strategy: str | None = Query(None),
    n: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    dirs = [strategy] if strategy else strategy_dirs()
    results: list[dict[str, Any]] = []
    for name in dirs:
        for rec in tail_jsonl(LOG_DIR / name / "fills.jsonl", n):
            rec["_source"] = name
            results.append(rec)
    results.sort(key=lambda r: r.get("filled_at", 0), reverse=True)
    return {"count": len(results[:n]), "fills": results[:n]}
