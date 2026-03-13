"""Offset-based pagination helpers."""

from __future__ import annotations

from typing import Any


def paginate(
    rows: list[dict[str, Any]],
    total: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Wrap *rows* in a standard paginated envelope."""
    return {
        "data": rows,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }
