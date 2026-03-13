"""Layered config resolution: Redis override > ENV > TOML > Schema default."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

log = structlog.get_logger()

# Optional Redis — if not installed, only TOML + ENV layers work.
try:
    from redis import Redis as _Redis

    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    _Redis = None  # type: ignore[assignment,misc]


@dataclass
class LayeredValue:
    """Per-field view of all configuration layers."""

    default: Any = None
    toml: Any = None
    env: Any = None
    override: Any = None
    effective: Any = None


class ConfigStore:
    """Layered configuration store.

    Resolution order: Redis override > ENV var > TOML file > Schema default.
    """

    def __init__(
        self,
        toml_path: Path | None = None,
        redis: Any | None = None,
        prefix: str = "pm:config",
    ) -> None:
        self._prefix = prefix
        self._toml_data: dict[str, dict[str, Any]] = {}
        self._redis = redis
        self._subscribers: dict[str, list[Callable[..., Any]]] = {}

        if toml_path is not None and toml_path.exists():
            with open(toml_path, "rb") as f:
                self._toml_data = tomllib.load(f)

    # ------------------------------------------------------------------
    # Layer readers
    # ------------------------------------------------------------------

    def _toml_section(self, section: str) -> dict[str, Any]:
        """Read a section from parsed TOML data (dot-separated nesting)."""
        parts = section.split(".")
        node: Any = self._toml_data
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part, {})
            else:
                return {}
        return dict(node) if isinstance(node, dict) else {}

    def _env_section(self, section: str) -> dict[str, Any]:
        """Read PM_<SECTION>_<KEY> environment variables."""
        prefix = f"PM_{section.replace('.', '_').upper()}_"
        result: dict[str, Any] = {}
        for key, value in os.environ.items():
            if key.startswith(prefix):
                field_name = key[len(prefix) :].lower()
                # Try to parse as JSON for complex types, fall back to string
                try:
                    result[field_name] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    result[field_name] = value
        return result

    def _redis_section(self, section: str) -> dict[str, Any]:
        """Read overrides from Redis HASH."""
        if self._redis is None:
            return {}
        redis_key = f"{self._prefix}:{section}"
        try:
            raw = self._redis.hgetall(redis_key)
            if not raw:
                return {}
            result: dict[str, Any] = {}
            for k, v in raw.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                try:
                    result[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    result[key] = val
            return result
        except Exception:
            log.warning("redis_section_read_failed", section=section, exc_info=True)
            return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_section(self, section: str) -> dict[str, Any]:
        """Return merged view across all layers (highest priority wins)."""
        merged: dict[str, Any] = {}
        merged.update(self._toml_section(section))
        merged.update(self._env_section(section))
        merged.update(self._redis_section(section))
        return merged

    def set_override(self, section: str, key: str, value: Any) -> None:
        """Write to Redis HASH + PUBLISH change + XADD changelog."""
        if self._redis is None:
            if not _HAS_REDIS:
                raise RuntimeError("redis package not installed — cannot set overrides")
            raise RuntimeError("No Redis connection configured")

        redis_key = f"{self._prefix}:{section}"
        encoded = json.dumps(value)
        self._redis.hset(redis_key, key, encoded)

        # Publish change notification
        changed_channel = f"{self._prefix}:changed"
        change_msg = json.dumps({"section": section, "key": key, "value": value})
        self._redis.publish(changed_channel, change_msg)

        # Append to changelog stream
        changelog_stream = f"{self._prefix}:changelog"
        self._redis.xadd(
            changelog_stream,
            {"section": section, "key": key, "value": encoded, "action": "set"},
        )

    def clear_override(self, section: str, key: str | None = None) -> None:
        """Remove override -> fallback to lower layer."""
        if self._redis is None:
            if not _HAS_REDIS:
                raise RuntimeError("redis package not installed — cannot clear overrides")
            raise RuntimeError("No Redis connection configured")

        redis_key = f"{self._prefix}:{section}"
        if key is None:
            self._redis.delete(redis_key)
        else:
            self._redis.hdel(redis_key, key)

        changed_channel = f"{self._prefix}:changed"
        change_msg = json.dumps({"section": section, "key": key, "action": "clear"})
        self._redis.publish(changed_channel, change_msg)

        changelog_stream = f"{self._prefix}:changelog"
        self._redis.xadd(
            changelog_stream,
            {"section": section, "key": key or "*", "action": "clear"},
        )

    def diff(self, section: str) -> dict[str, LayeredValue]:
        """Per-field: default, toml, env, override, effective."""
        toml_vals = self._toml_section(section)
        env_vals = self._env_section(section)
        redis_vals = self._redis_section(section)

        all_keys = set(toml_vals) | set(env_vals) | set(redis_vals)
        result: dict[str, LayeredValue] = {}

        for key in sorted(all_keys):
            lv = LayeredValue(
                toml=toml_vals.get(key),
                env=env_vals.get(key),
                override=redis_vals.get(key),
            )
            # Effective = highest-priority non-None
            if lv.override is not None:
                lv.effective = lv.override
            elif lv.env is not None:
                lv.effective = lv.env
            elif lv.toml is not None:
                lv.effective = lv.toml
            result[key] = lv

        return result

    def subscribe(self, section: str, callback: Callable[..., Any]) -> None:
        """Register for Pub/Sub change notifications."""
        if section not in self._subscribers:
            self._subscribers[section] = []
        self._subscribers[section].append(callback)
