"""Strategy introspection proxy endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Response

from pm_api.deps import get_generic_client

router = APIRouter(prefix="/api/v1", tags=["introspect"])

# Registry: config stem -> port (matches supervisord.conf assignments).
_INTROSPECT_PORTS: dict[str, int] = {
    "s2_insider_copy": 8010,
    "s3_no_sniper": 8011,
    "crypto_gbm": 8012,
    "portfolio_v3": 8013,
}


@router.get("/introspect")
async def list_introspect() -> dict[str, Any]:
    """List all strategy introspect servers and their status."""
    results: list[dict[str, Any]] = []
    for config_name, port in _INTROSPECT_PORTS.items():
        try:
            resp = await get_generic_client().get(
                f"http://localhost:{port}/status",
                timeout=2.0,
            )
            results.append(
                {
                    "config": config_name,
                    "port": port,
                    "status": "up",
                    "data": resp.json(),
                }
            )
        except Exception:
            results.append(
                {
                    "config": config_name,
                    "port": port,
                    "status": "down",
                }
            )
    return {"servers": results}


@router.get("/introspect/{config_name}/{path:path}")
async def proxy_introspect(config_name: str, path: str) -> Response:
    """Proxy to a strategy introspect server."""
    port = _INTROSPECT_PORTS.get(config_name)
    if port is None:
        return Response(
            content=json.dumps({"error": f"Unknown config: {config_name}"}),
            media_type="application/json",
            status_code=404,
        )
    try:
        resp = await get_generic_client().get(
            f"http://localhost:{port}/{path}",
            timeout=5.0,
        )
        return Response(
            content=resp.content,
            media_type="application/json",
            status_code=resp.status_code,
        )
    except Exception as e:
        return Response(
            content=json.dumps({"error": f"Introspect server down: {e}"}),
            media_type="application/json",
            status_code=502,
        )
