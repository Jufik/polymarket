"""Config management API endpoints.

Endpoints:
    GET    /api/v1/config              List all known config sections
    GET    /api/v1/config/:section     Get effective config with layer breakdown
    PUT    /api/v1/config/:section     Set overrides (body: {"key": "value", ...})
    DELETE /api/v1/config/:section     Clear overrides (?key=... for specific key)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pm_core.config import (
    CLOBWSConfig,
    ConfigStore,
    ExecutionConfig,
    LifecycleConfig,
    QualityConfig,
    RPCConfig,
    RTDSConfig,
    StrategyConfig,
)

router = APIRouter(prefix="/api/v1", tags=["config"])

# Section name → Pydantic schema
_SECTION_SCHEMAS: dict[str, type[Any]] = {
    "strategy": StrategyConfig,
    "clob_ws": CLOBWSConfig,
    "rtds": RTDSConfig,
    "rpc": RPCConfig,
    "quality": QualityConfig,
    "execution": ExecutionConfig,
    "lifecycle": LifecycleConfig,
}

# Lazy singleton — created on first request
_config_store: ConfigStore | None = None


def _get_store() -> ConfigStore:
    """Get or create the ConfigStore singleton."""
    global _config_store
    if _config_store is not None:
        return _config_store

    redis_client = None
    try:
        from redis import Redis

        redis_url = os.environ.get("PM_REDIS_URL", "redis://localhost:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=False)
        redis_client.ping()
    except Exception:
        redis_client = None

    toml_path_str = os.environ.get("PM_CONFIG_PATH", "")
    toml_path = Path(toml_path_str) if toml_path_str else None

    _config_store = ConfigStore(toml_path=toml_path, redis=redis_client)
    return _config_store


@router.get("/config")
async def list_sections() -> dict[str, Any]:
    """List all known config sections and their schemas."""
    sections = []
    for name, schema_cls in _SECTION_SCHEMAS.items():
        fields = []
        for field_name, field_info in schema_cls.model_fields.items():
            fields.append({
                "name": field_name,
                "type": str(field_info.annotation),
                "default": field_info.default if field_info.default is not None else None,
            })
        sections.append({"name": name, "schema": schema_cls.__name__, "fields": fields})
    return {"sections": sections}


@router.get("/config/{section}")
async def get_section(section: str) -> dict[str, Any]:
    """Get effective config for a section with layer breakdown."""
    store = _get_store()

    # Effective merged values
    effective = store.get_section(section)

    # Fill in schema defaults
    schema_cls = _SECTION_SCHEMAS.get(section)
    defaults: dict[str, Any] = {}
    if schema_cls is not None:
        try:
            default_instance = schema_cls()
            defaults = default_instance.model_dump()
        except Exception:
            pass
        try:
            validated = schema_cls(**effective)
            effective = validated.model_dump()
        except Exception:
            pass

    # Layer breakdown
    layers = store.diff(section)
    breakdown: dict[str, dict[str, Any]] = {}
    for field in sorted(set(list(layers.keys()) + list(defaults.keys()))):
        lv = layers.get(field)
        breakdown[field] = {
            "default": defaults.get(field),
            "toml": lv.toml if lv else None,
            "env": lv.env if lv else None,
            "override": lv.override if lv else None,
            "effective": effective.get(field, defaults.get(field)),
        }

    return {
        "section": section,
        "effective": effective,
        "layers": breakdown,
    }


@router.put("/config/{section}")
async def set_overrides(section: str, body: dict[str, Any]) -> dict[str, Any]:
    """Set config overrides for a section."""
    store = _get_store()

    if not body:
        raise HTTPException(status_code=400, detail="Request body must contain key-value pairs")

    # Validate against schema if available
    schema_cls = _SECTION_SCHEMAS.get(section)
    if schema_cls is not None:
        current = store.get_section(section)
        merged = {**current, **body}
        try:
            schema_cls(**merged)
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Validation failed against {schema_cls.__name__}: {e}",
            ) from None

    applied: dict[str, Any] = {}
    for key, value in body.items():
        store.set_override(section, key, value)
        applied[key] = value

    return {"section": section, "applied": applied, "count": len(applied)}


@router.delete("/config/{section}")
async def clear_overrides(
    section: str,
    key: str | None = Query(default=None, description="Clear a specific key (default: all)"),
) -> dict[str, Any]:
    """Clear Redis overrides for a section."""
    store = _get_store()

    try:
        store.clear_override(section, key)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None

    if key:
        return {"section": section, "cleared": key}
    return {"section": section, "cleared": "*"}
