"""Backward-compatible routes: map old /api/* to new /api/v1/* handlers.

The existing UI and dashboard JS use ``/api/health``, ``/api/intents``, etc.
These routes delegate to the same underlying logic so nothing breaks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response

from pm_api.routes import fills as fills_mod
from pm_api.routes import health as health_mod
from pm_api.routes import ingestion as ingestion_mod
from pm_api.routes import intents as intents_mod
from pm_api.routes import introspect as introspect_mod
from pm_api.routes import metadata as metadata_mod
from pm_api.routes import prices as prices_mod
from pm_api.routes import roundtrips as roundtrips_mod
from pm_api.routes import strategies as strategies_mod

compat = APIRouter(tags=["compat"])


# -- Health & metrics --------------------------------------------------------


@compat.get("/api/health")
async def old_health() -> dict[str, Any]:
    return await health_mod.health()


@compat.get("/api/metrics")
async def old_metrics() -> dict[str, Any]:
    return await health_mod.metrics()


# -- Ingestion ---------------------------------------------------------------


@compat.get("/api/ingestion")
async def old_ingestion(hours: int = Query(1, ge=1, le=24)) -> dict[str, Any]:
    return await ingestion_mod.ingestion(hours=hours)


# -- Metadata ----------------------------------------------------------------


@compat.get("/api/metadata")
async def old_metadata() -> dict[str, Any]:
    return await metadata_mod.pipeline_health()


# -- Strategies & Activity ---------------------------------------------------


@compat.get("/api/strategies")
async def old_strategies() -> dict[str, Any]:
    return await strategies_mod.strategies()


@compat.get("/api/activity")
async def old_activity(
    strategy: str | None = Query(None),
    n: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    return await strategies_mod.activity(strategy=strategy, n=n)


# -- Intents & Fills ---------------------------------------------------------


@compat.get("/api/intents")
async def old_intents(
    strategy: str | None = Query(None),
    n: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    return await intents_mod.list_intents(strategy=strategy, n=n)


@compat.get("/api/intent/{strategy}/{index}")
async def old_intent_detail(strategy: str, index: int) -> dict[str, Any]:
    return await intents_mod.intent_detail(strategy=strategy, index=index)


@compat.get("/api/fills")
async def old_fills(
    strategy: str | None = Query(None),
    n: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    return await fills_mod.list_fills(strategy=strategy, n=n)


# -- Round trips -------------------------------------------------------------


@compat.get("/api/roundtrips")
async def old_roundtrips(
    strategy: str | None = Query(None),
    n: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    return await roundtrips_mod.list_roundtrips(strategy=strategy, n=n)


@compat.get("/api/roundtrip/{config}/{condition_id}")
async def old_roundtrip_detail(config: str, condition_id: str) -> dict[str, Any]:
    return await roundtrips_mod.roundtrip_detail(config=config, condition_id=condition_id)


# -- Prices ------------------------------------------------------------------


@compat.get("/api/prices/{condition_id}")
async def old_prices(
    condition_id: str,
    signal_time: float = Query(...),
    zoom: str = Query("6h"),
) -> dict[str, Any]:
    return await prices_mod.price_history(
        condition_id=condition_id, signal_time=signal_time, zoom=zoom
    )


# -- Introspect --------------------------------------------------------------


@compat.get("/api/introspect")
async def old_introspect() -> dict[str, Any]:
    return await introspect_mod.list_introspect()


@compat.get("/api/introspect/{config_name}/{path:path}")
async def old_proxy_introspect(config_name: str, path: str) -> Response:
    return await introspect_mod.proxy_introspect(config_name=config_name, path=path)
