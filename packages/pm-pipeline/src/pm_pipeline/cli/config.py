"""pm config -- read/write dynamic configuration.

Commands:
    get   <section>                   Show effective config for a section
    set   <section> key=value ...     Set runtime overrides (Redis layer)
    diff  <section>                   Show layered view (TOML / ENV / Redis / effective)
    clear <section> [--key KEY]       Clear Redis overrides
    log   [--section SECTION] [-n N]  Show recent changelog entries

Usage:
    uv run pm-config get quality
    uv run pm-config set quality source_liveness_timeout_s=60
    uv run pm-config diff execution
    uv run pm-config clear quality --key source_liveness_timeout_s
    uv run pm-config log --section quality -n 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import structlog
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

log = structlog.get_logger()

# Section name → Pydantic schema for validation
_SECTION_SCHEMAS: dict[str, type[Any]] = {
    "strategy": StrategyConfig,
    "clob_ws": CLOBWSConfig,
    "rtds": RTDSConfig,
    "rpc": RPCConfig,
    "quality": QualityConfig,
    "execution": ExecutionConfig,
    "lifecycle": LifecycleConfig,
}


def _build_store() -> ConfigStore:
    """Create a ConfigStore from env/config conventions."""
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

    return ConfigStore(toml_path=toml_path, redis=redis_client)


def _parse_value(raw: str) -> Any:
    """Parse a CLI value string: try JSON first, fall back to string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def cmd_get(args: argparse.Namespace) -> None:
    """Show effective config for a section."""
    store = _build_store()
    section = args.section
    data = store.get_section(section)

    schema_cls = _SECTION_SCHEMAS.get(section)
    if schema_cls is not None:
        try:
            validated = schema_cls(**data)
            # Show full model including defaults
            data = validated.model_dump()
        except Exception as e:
            print(f"Warning: validation against {schema_cls.__name__} failed: {e}", file=sys.stderr)

    print(json.dumps(data, indent=2, default=str))


def cmd_set(args: argparse.Namespace) -> None:
    """Set runtime config overrides."""
    store = _build_store()
    section = args.section

    if not args.pairs:
        print("Error: at least one key=value pair required", file=sys.stderr)
        sys.exit(1)

    # Parse key=value pairs
    overrides: dict[str, Any] = {}
    for pair in args.pairs:
        if "=" not in pair:
            print(f"Error: invalid format '{pair}', expected key=value", file=sys.stderr)
            sys.exit(1)
        key, raw_value = pair.split("=", 1)
        overrides[key] = _parse_value(raw_value)

    # Validate against schema if available
    schema_cls = _SECTION_SCHEMAS.get(section)
    if schema_cls is not None:
        # Merge with current effective config to validate the full set
        current = store.get_section(section)
        merged = {**current, **overrides}
        try:
            schema_cls(**merged)
        except Exception as e:
            print(f"Error: validation failed against {schema_cls.__name__}: {e}", file=sys.stderr)
            sys.exit(1)

    # Apply each override
    for key, value in overrides.items():
        store.set_override(section, key, value)
        print(f"  {section}.{key} = {json.dumps(value)}")

    print(f"\nSet {len(overrides)} override(s) for [{section}]")


def cmd_diff(args: argparse.Namespace) -> None:
    """Show layered view of config values."""
    store = _build_store()
    section = args.section
    layers = store.diff(section)

    # Also show schema defaults
    schema_cls = _SECTION_SCHEMAS.get(section)
    schema_defaults: dict[str, Any] = {}
    if schema_cls is not None:
        try:
            default_instance = schema_cls()
            schema_defaults = default_instance.model_dump()
        except Exception:
            pass

    # Header
    print(f"\n  [{section}] — layered config view\n")
    cols = ["Field", "Default", "TOML", "ENV", "Redis", "Effective"]
    print(f"  {cols[0]:<30} " + " ".join(f"{c:<15}" for c in cols[1:]))
    print(f"  {'─' * 30} {'─' * 15} {'─' * 15} {'─' * 15} {'─' * 15} {'─' * 15}")

    # Gather all known fields (layers + schema defaults)
    all_fields = set(layers.keys()) | set(schema_defaults.keys())

    for field in sorted(all_fields):
        lv = layers.get(field)
        default = schema_defaults.get(field)
        toml_val = lv.toml if lv else None
        env_val = lv.env if lv else None
        override_val = lv.override if lv else None
        effective = lv.effective if lv else default

        def _fmt(v: Any) -> str:
            if v is None:
                return "—"
            return str(v)

        print(
            f"  {field:<30} {_fmt(default):<15} {_fmt(toml_val):<15} "
            f"{_fmt(env_val):<15} {_fmt(override_val):<15} {_fmt(effective):<15}"
        )

    print()


def cmd_clear(args: argparse.Namespace) -> None:
    """Clear Redis overrides."""
    store = _build_store()
    section = args.section

    if args.key:
        store.clear_override(section, args.key)
        print(f"Cleared override: {section}.{args.key}")
    else:
        store.clear_override(section, None)
        print(f"Cleared all overrides for [{section}]")


def cmd_log(args: argparse.Namespace) -> None:
    """Show recent changelog entries."""
    store = _build_store()
    entries = store.changelog(section=args.section, limit=args.n)

    if not entries:
        print("No changelog entries found.")
        return

    print(f"\n  Recent config changes (latest first, limit={args.n}):\n")
    for entry in entries:
        eid = entry.get("id", "?")
        section = entry.get("section", "?")
        key = entry.get("key", "?")
        action = entry.get("action", "?")
        value = entry.get("value", "")
        print(f"  [{eid}] {action:<6} {section}.{key} = {value}")
    print()


def main() -> None:
    """Entry point for ``pm-config``."""
    parser = argparse.ArgumentParser(
        prog="pm-config",
        description="Read and write dynamic pipeline configuration",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # get
    p_get = subparsers.add_parser("get", help="Show effective config for a section")
    p_get.add_argument("section", help="Config section name (e.g. quality, execution)")

    # set
    p_set = subparsers.add_parser("set", help="Set runtime config overrides")
    p_set.add_argument("section", help="Config section name")
    p_set.add_argument("pairs", nargs="+", help="key=value pairs")

    # diff
    p_diff = subparsers.add_parser("diff", help="Show layered config view")
    p_diff.add_argument("section", help="Config section name")

    # clear
    p_clear = subparsers.add_parser("clear", help="Clear Redis overrides")
    p_clear.add_argument("section", help="Config section name")
    p_clear.add_argument("--key", help="Clear a specific key (default: clear all)")

    # log
    p_log = subparsers.add_parser("log", help="Show recent changelog entries")
    p_log.add_argument("--section", help="Filter by section")
    p_log.add_argument("-n", type=int, default=50, help="Number of entries (default: 50)")

    args = parser.parse_args()

    dispatch = {
        "get": cmd_get,
        "set": cmd_set,
        "diff": cmd_diff,
        "clear": cmd_clear,
        "log": cmd_log,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
