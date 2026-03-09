"""Export Goldsky Activity Subgraph data (splits, merges, redemptions, conversions) to Parquet.

Paginates through the Activity subgraph GraphQL API with cursor-based resume.
Writes incremental Parquet files and a manifest for resume support.

Usage:
    # Export all entity types (full history)
    PYTHONPATH=. uv run python scripts/export_goldsky.py

    # Export only splits and merges
    PYTHONPATH=. uv run python scripts/export_goldsky.py --entities splits merges

    # Resume from where we left off
    PYTHONPATH=. uv run python scripts/export_goldsky.py --resume

    # Custom output directory
    PYTHONPATH=. uv run python scripts/export_goldsky.py --output-dir data/goldsky/
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import requests
import structlog

log = structlog.get_logger()

# ── Goldsky Activity Subgraph ──────────────────────────────────────────────

ACTIVITY_SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/activity-subgraph/0.0.4/gn"
)

USDC_SCALE = Decimal("1000000")  # 6 decimals

BATCH_SIZE = 1000  # Goldsky max per query
FLUSH_EVERY = 50  # Flush to Parquet every N batches
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0

# ── Entity Definitions ─────────────────────────────────────────────────────


@dataclass
class EntityConfig:
    """Configuration for a subgraph entity type."""

    name: str  # GraphQL plural name (e.g., "splits")
    fields: list[str]
    id_field: str = "id"
    timestamp_field: str = "timestamp"
    # Arrow schema for Parquet output
    arrow_schema: pa.Schema = field(default_factory=lambda: pa.schema([]))


ENTITIES: dict[str, EntityConfig] = {
    "splits": EntityConfig(
        name="splits",
        fields=["id", "timestamp", "stakeholder", "condition", "amount"],
        arrow_schema=pa.schema([
            ("id", pa.string()),
            ("timestamp", pa.int64()),
            ("stakeholder", pa.string()),
            ("condition_id", pa.string()),
            ("amount_usdc", pa.float64()),
        ]),
    ),
    "merges": EntityConfig(
        name="merges",
        fields=["id", "timestamp", "stakeholder", "condition", "amount"],
        arrow_schema=pa.schema([
            ("id", pa.string()),
            ("timestamp", pa.int64()),
            ("stakeholder", pa.string()),
            ("condition_id", pa.string()),
            ("amount_usdc", pa.float64()),
        ]),
    ),
    "redemptions": EntityConfig(
        name="redemptions",
        fields=["id", "timestamp", "redeemer", "condition", "indexSets", "payout"],
        arrow_schema=pa.schema([
            ("id", pa.string()),
            ("timestamp", pa.int64()),
            ("redeemer", pa.string()),
            ("condition_id", pa.string()),
            ("index_sets", pa.list_(pa.int32())),
            ("payout_usdc", pa.float64()),
        ]),
    ),
    "neg_risk_conversions": EntityConfig(
        name="negRiskConversions",
        fields=[
            "id", "timestamp", "stakeholder", "negRiskMarketId",
            "amount", "indexSet", "questionCount",
        ],
        arrow_schema=pa.schema([
            ("id", pa.string()),
            ("timestamp", pa.int64()),
            ("stakeholder", pa.string()),
            ("neg_risk_market_id", pa.string()),
            ("amount_usdc", pa.float64()),
            ("index_set", pa.int64()),
            ("question_count", pa.int32()),
        ]),
    ),
}


# ── GraphQL Queries ────────────────────────────────────────────────────────


def _build_query(entity: EntityConfig, *, sticky: bool = False) -> str:
    """Build a paginated GraphQL query for an entity."""
    fields_str = " ".join(entity.fields)

    if sticky:
        # Same timestamp — paginate by id_gt
        return f"""
        query Fetch($timestamp: String!, $id_gt: String!, $first: Int!) {{
            {entity.name}(
                orderBy: {entity.timestamp_field}
                orderDirection: asc
                first: $first
                where: {{ {entity.timestamp_field}: $timestamp, id_gt: $id_gt }}
            ) {{ {fields_str} }}
        }}
        """
    else:
        return f"""
        query Fetch($timestamp_gt: String!, $first: Int!) {{
            {entity.name}(
                orderBy: {entity.timestamp_field}
                orderDirection: asc
                first: $first
                where: {{ {entity.timestamp_field}_gt: $timestamp_gt }}
            ) {{ {fields_str} }}
        }}
        """


# ── Row Conversion ─────────────────────────────────────────────────────────


def _normalize_row(entity_key: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw GraphQL response row into our normalized schema."""
    ts = int(raw["timestamp"])
    amount_raw = int(raw.get("amount", "0"))
    amount_usdc = float(Decimal(amount_raw) / USDC_SCALE)

    if entity_key == "splits":
        return {
            "id": raw["id"],
            "timestamp": ts,
            "stakeholder": raw["stakeholder"],
            "condition_id": raw["condition"],
            "amount_usdc": amount_usdc,
        }
    elif entity_key == "merges":
        return {
            "id": raw["id"],
            "timestamp": ts,
            "stakeholder": raw["stakeholder"],
            "condition_id": raw["condition"],
            "amount_usdc": amount_usdc,
        }
    elif entity_key == "redemptions":
        payout_raw = int(raw.get("payout", "0"))
        payout_usdc = float(Decimal(payout_raw) / USDC_SCALE)
        return {
            "id": raw["id"],
            "timestamp": ts,
            "redeemer": raw["redeemer"],
            "condition_id": raw["condition"],
            "index_sets": [int(x) for x in raw.get("indexSets", [])],
            "payout_usdc": payout_usdc,
        }
    elif entity_key == "neg_risk_conversions":
        return {
            "id": raw["id"],
            "timestamp": ts,
            "stakeholder": raw["stakeholder"],
            "neg_risk_market_id": raw["negRiskMarketId"],
            "amount_usdc": amount_usdc,
            "index_set": int(raw["indexSet"]),
            "question_count": int(raw["questionCount"]),
        }
    else:
        raise ValueError(f"Unknown entity: {entity_key}")


# ── HTTP Client ────────────────────────────────────────────────────────────


def _execute_query(
    session: requests.Session,
    query: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    """Execute a GraphQL query with retry."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(
                ACTIVITY_SUBGRAPH_URL,
                json={"query": query, "variables": variables},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]
        except Exception as exc:
            err_str = str(exc)
            is_transient = any(
                s in err_str
                for s in ("502", "503", "504", "timeout", "Timeout", "ConnectionError")
            )
            if not is_transient or attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            log.warning("retry", attempt=attempt + 1, delay_s=f"{delay:.1f}", error=err_str[:120])
            time.sleep(delay)
    raise RuntimeError("unreachable")


# ── Manifest (Resume Support) ──────────────────────────────────────────────


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n")


# ── Parquet Writer ─────────────────────────────────────────────────────────


class IncrementalParquetWriter:
    """Buffers rows in memory, flushes to Parquet file periodically."""

    def __init__(self, output_path: Path, schema: pa.Schema):
        self._path = output_path
        self._schema = schema
        self._buffer: list[dict[str, Any]] = []
        self._writer: pq.ParquetWriter | None = None
        self._total_rows = 0

    def add_rows(self, rows: list[dict[str, Any]]) -> None:
        self._buffer.extend(rows)

    def flush(self) -> int:
        """Write buffered rows to Parquet. Returns number of rows flushed."""
        if not self._buffer:
            return 0

        # Build columnar dict from row dicts
        columns: dict[str, list[Any]] = {f.name: [] for f in self._schema}
        for row in self._buffer:
            for col_name in columns:
                columns[col_name].append(row.get(col_name))

        table = pa.table(columns, schema=self._schema)

        if self._writer is None:
            self._writer = pq.ParquetWriter(
                str(self._path),
                self._schema,
                compression="zstd",
            )

        self._writer.write_table(table)
        n = len(self._buffer)
        self._total_rows += n
        self._buffer.clear()
        return n

    def close(self) -> int:
        """Flush remaining and close. Returns total rows written."""
        self.flush()
        if self._writer is not None:
            self._writer.close()
        return self._total_rows


# ── Main Export Loop ───────────────────────────────────────────────────────


def export_entity(
    entity_key: str,
    output_dir: Path,
    *,
    resume: bool = False,
    from_timestamp: int = 0,
) -> int:
    """Export a single entity type from the Activity subgraph to Parquet.

    Returns total rows exported.
    """
    cfg = ENTITIES[entity_key]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"_manifest_{entity_key}.json"
    output_path = output_dir / f"{entity_key}.parquet"

    # Resume support
    cursor_ts = str(from_timestamp)
    cursor_id = ""
    rows_so_far = 0

    if resume:
        manifest = _load_manifest(manifest_path)
        if entity_key in manifest:
            cursor_ts = str(manifest[entity_key].get("cursor_ts", from_timestamp))
            cursor_id = manifest[entity_key].get("cursor_id", "")
            rows_so_far = manifest[entity_key].get("total_rows", 0)
            log.info(
                "resume",
                entity=entity_key,
                cursor_ts=cursor_ts,
                cursor_id=cursor_id[:30],
                rows_so_far=rows_so_far,
            )

    query_normal = _build_query(cfg, sticky=False)
    query_sticky = _build_query(cfg, sticky=True)

    target_ts = int(time.time())
    total_gap = target_ts - int(cursor_ts) if int(cursor_ts) > 0 else target_ts

    writer = IncrementalParquetWriter(output_path, cfg.arrow_schema)
    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    batch_stats: deque[tuple[float, int]] = deque(maxlen=200)
    batch_num = 0
    total_rows = rows_so_far

    try:
        while True:
            batch_start = time.monotonic()
            prev_ts = int(cursor_ts)

            if cursor_id:
                data = _execute_query(session, query_sticky, {
                    "timestamp": cursor_ts,
                    "id_gt": cursor_id,
                    "first": BATCH_SIZE,
                })
            else:
                data = _execute_query(session, query_normal, {
                    "timestamp_gt": cursor_ts,
                    "first": BATCH_SIZE,
                })

            events = data.get(cfg.name, [])
            if not events:
                break

            # Normalize and buffer
            rows = [_normalize_row(entity_key, e) for e in events]
            writer.add_rows(rows)
            total_rows += len(rows)

            # Advance cursor
            last = events[-1]
            new_ts = last[cfg.timestamp_field]
            if new_ts == cursor_ts:
                cursor_id = last[cfg.id_field]
            else:
                cursor_ts = new_ts
                cursor_id = ""

            batch_num += 1
            batch_elapsed = time.monotonic() - batch_start
            data_covered = int(cursor_ts) - prev_ts
            batch_stats.append((batch_elapsed, data_covered))

            # Flush to Parquet periodically
            if batch_num % FLUSH_EVERY == 0:
                flushed = writer.flush()
                log.info("flush", entity=entity_key, rows_flushed=flushed, total=total_rows)

                # Save cursor for resume
                manifest = _load_manifest(manifest_path)
                manifest[entity_key] = {
                    "cursor_ts": int(cursor_ts),
                    "cursor_id": cursor_id,
                    "total_rows": total_rows,
                    "updated_at": time.time(),
                }
                _save_manifest(manifest_path, manifest)

            # Progress + ETA
            remaining_s = target_ts - int(cursor_ts)
            progress_pct = (1 - remaining_s / total_gap) * 100 if total_gap > 0 else 100

            eta_str = "..."
            if len(batch_stats) >= 10:
                total_wall = sum(s[0] for s in batch_stats)
                total_data = sum(s[1] for s in batch_stats)
                if total_data > 0:
                    eta_s = remaining_s * (total_wall / total_data)
                    eta_str = f"{eta_s / 3600:.1f}h" if eta_s >= 3600 else f"{eta_s / 60:.0f}m"

            if batch_num % 10 == 0:
                log.info(
                    "progress",
                    entity=entity_key,
                    batch=batch_num,
                    rows=total_rows,
                    progress=f"{progress_pct:.1f}%",
                    eta=eta_str,
                    latency_ms=f"{batch_elapsed * 1000:.0f}",
                )

            # Partial batch from sticky → exhausted this ts, advance
            if len(events) < BATCH_SIZE:
                cursor_id = ""

    finally:
        written = writer.close()
        log.info("done", entity=entity_key, total_rows=total_rows, parquet_rows=written)

        # Final manifest save
        manifest = _load_manifest(manifest_path)
        manifest[entity_key] = {
            "cursor_ts": int(cursor_ts),
            "cursor_id": cursor_id,
            "total_rows": total_rows,
            "completed": True,
            "updated_at": time.time(),
        }
        _save_manifest(manifest_path, manifest)

    return total_rows


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Goldsky Activity Subgraph to Parquet"
    )
    parser.add_argument(
        "--entities",
        nargs="+",
        choices=list(ENTITIES.keys()),
        default=list(ENTITIES.keys()),
        help="Entity types to export (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/goldsky/activity"),
        help="Output directory (default: data/goldsky/activity)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint",
    )
    parser.add_argument(
        "--from-timestamp",
        type=int,
        default=0,
        help="Start from this Unix timestamp (default: 0 = beginning)",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    t0 = time.monotonic()
    grand_total = 0

    for entity_key in args.entities:
        log.info("export_start", entity=entity_key)
        rows = export_entity(
            entity_key,
            args.output_dir,
            resume=args.resume,
            from_timestamp=args.from_timestamp,
        )
        grand_total += rows

    elapsed = time.monotonic() - t0
    log.info(
        "export_complete",
        entities=args.entities,
        total_rows=grand_total,
        elapsed=f"{elapsed / 60:.1f}m",
    )


if __name__ == "__main__":
    main()
