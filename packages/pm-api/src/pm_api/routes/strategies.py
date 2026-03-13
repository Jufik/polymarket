"""Strategy listing and activity summary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from pm_api.deps import LOG_DIR, read_all_jsonl, strategy_dirs

router = APIRouter(prefix="/api/v1", tags=["strategies"])


@router.get("/strategies")
async def strategies() -> dict[str, Any]:
    return {"strategies": strategy_dirs()}


@router.get("/activity")
async def activity(
    strategy: str | None = Query(None),
    n: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Per-strategy-leg activity summary.

    Groups by the inner ``strategy`` field (e.g. politics_no_v3)
    rather than the log directory (e.g. portfolio_v3).
    """
    dirs = [strategy] if strategy else strategy_dirs()
    summary: dict[str, dict[str, Any]] = {}

    for name in dirs:
        intents_data = read_all_jsonl(LOG_DIR / name / "intents.jsonl")
        fills_data = read_all_jsonl(LOG_DIR / name / "fills.jsonl")

        leg_intents: dict[str, list[dict[str, Any]]] = {}
        for rec in intents_data:
            leg = rec.get("strategy", name)
            leg_intents.setdefault(leg, []).append(rec)

        leg_fills: dict[str, list[dict[str, Any]]] = {}
        for rec in fills_data:
            leg = rec.get("strategy", name)
            leg_fills.setdefault(leg, []).append(rec)

        all_legs = set(leg_intents) | set(leg_fills)
        for leg in all_legs:
            li = leg_intents.get(leg, [])
            lf = leg_fills.get(leg, [])
            filled = [f for f in lf if f.get("status") == "filled"]
            rejected = [f for f in lf if f.get("status") == "rejected"]
            total_usd = sum(f.get("filled_size_usd", 0) for f in filled)
            summary[leg] = {
                "total_intents": len(li),
                "total_fills": len(filled),
                "total_rejected": len(rejected),
                "total_filled_usd": round(total_usd, 2),
                "recent_intents": li[-n:],
                "recent_fills": lf[-n:],
                "config": name,
            }
    return summary
