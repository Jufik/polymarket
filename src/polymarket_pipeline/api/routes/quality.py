"""Quality state endpoint (reads from pipeline.status topic)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["quality"])

# In-memory cache of last known quality state
_quality_state: dict[str, object] = {"state": "unknown", "failures": [], "ts": 0}


def update_quality_state(state: str, failures: list[str], ts: float) -> None:
    """Called by a background consumer to update quality state."""
    global _quality_state
    _quality_state = {"state": state, "failures": failures, "ts": ts}


@router.get("/quality")
async def get_quality() -> dict[str, object]:
    """Return last known pipeline quality state."""
    return _quality_state
