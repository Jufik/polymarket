"""Recover CTF contract events (splits, merges, redemptions, conversions) to Parquet.

Two backends:
  - rpc:      Adaptive eth_getLogs via Infura/Alchemy (fast, ~3-4h for full history)
  - subgraph: Goldsky Activity Subgraph GraphQL pagination (slower, free, no key needed)

Usage:
    pm-ctf-recover                                      # all events, RPC backend
    pm-ctf-recover --entities splits merges             # specific events only
    pm-ctf-recover --backend subgraph                   # use Goldsky subgraph
    pm-ctf-recover --resume                             # resume from last checkpoint
    pm-ctf-recover --from-block 60000000                # start from specific block (RPC)
    pm-ctf-recover --from-timestamp 1700000000          # start from timestamp (subgraph)
    pm-ctf-recover --output-dir data/goldsky/activity   # custom output
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import requests
import structlog

from polymarket_pipeline.constants import (
    CTF_TOKEN_CONTRACT,
    NEGRISK_ADAPTER,
    PAYOUT_REDEMPTION_SIG,
    POSITION_SPLIT_SIG,
    POSITIONS_CONVERTED_SIG,
    POSITIONS_MERGE_SIG,
    USDC_SCALE,
)

log = structlog.get_logger()

DEFAULT_OUTPUT_DIR = Path("data/goldsky/activity")

# ── Schemas ────────────────────────────────────────────────────────────────

SPLIT_SCHEMA = pa.schema([
    ("tx_hash", pa.string()),
    ("block_number", pa.int64()),
    ("log_index", pa.int32()),
    ("timestamp", pa.int64()),
    ("stakeholder", pa.string()),
    ("condition_id", pa.string()),
    ("amount_usdc", pa.float64()),
])

MERGE_SCHEMA = pa.schema([
    ("tx_hash", pa.string()),
    ("block_number", pa.int64()),
    ("log_index", pa.int32()),
    ("timestamp", pa.int64()),
    ("stakeholder", pa.string()),
    ("condition_id", pa.string()),
    ("amount_usdc", pa.float64()),
])

REDEMPTION_SCHEMA = pa.schema([
    ("tx_hash", pa.string()),
    ("block_number", pa.int64()),
    ("log_index", pa.int32()),
    ("timestamp", pa.int64()),
    ("redeemer", pa.string()),
    ("condition_id", pa.string()),
    ("payout_usdc", pa.float64()),
])

CONVERSION_SCHEMA = pa.schema([
    ("tx_hash", pa.string()),
    ("block_number", pa.int64()),
    ("log_index", pa.int32()),
    ("timestamp", pa.int64()),
    ("stakeholder", pa.string()),
    ("neg_risk_market_id", pa.string()),
    ("amount_usdc", pa.float64()),
    ("index_set", pa.int64()),
])

ENTITY_CONFIG: dict[str, dict[str, Any]] = {
    "splits": {
        "sig": POSITION_SPLIT_SIG,
        "contract": CTF_TOKEN_CONTRACT,
        "schema": SPLIT_SCHEMA,
        "subgraph_name": "splits",
        "subgraph_fields": ["id", "timestamp", "stakeholder", "condition", "amount"],
    },
    "merges": {
        "sig": POSITIONS_MERGE_SIG,
        "contract": CTF_TOKEN_CONTRACT,
        "schema": MERGE_SCHEMA,
        "subgraph_name": "merges",
        "subgraph_fields": ["id", "timestamp", "stakeholder", "condition", "amount"],
    },
    "redemptions": {
        "sig": PAYOUT_REDEMPTION_SIG,
        "contract": CTF_TOKEN_CONTRACT,
        "schema": REDEMPTION_SCHEMA,
        "subgraph_name": "redemptions",
        "subgraph_fields": [
            "id", "timestamp", "redeemer", "condition", "indexSets", "payout",
        ],
    },
    "conversions": {
        "sig": POSITIONS_CONVERTED_SIG,
        "contract": NEGRISK_ADAPTER,
        "schema": CONVERSION_SCHEMA,
        "subgraph_name": "negRiskConversions",
        "subgraph_fields": [
            "id", "timestamp", "stakeholder", "negRiskMarketId",
            "amount", "indexSet", "questionCount",
        ],
    },
}

# ── Manifest (resume) ─────────────────────────────────────────────────────

MANIFEST_VERSION = 2


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n")


# ── Parquet writer ─────────────────────────────────────────────────────────


class ParquetBuffer:
    """Buffers rows, flushes to Parquet incrementally."""

    def __init__(self, path: Path, schema: pa.Schema) -> None:
        self._path = path
        self._schema = schema
        self._buffer: list[dict[str, Any]] = []
        self._writer: pq.ParquetWriter | None = None
        self._total = 0

    def add(self, rows: list[dict[str, Any]]) -> None:
        self._buffer.extend(rows)

    def flush(self) -> int:
        if not self._buffer:
            return 0
        cols: dict[str, list[Any]] = {f.name: [] for f in self._schema}
        for row in self._buffer:
            for c in cols:
                cols[c].append(row.get(c))
        table = pa.table(cols, schema=self._schema)
        if self._writer is None:
            self._writer = pq.ParquetWriter(str(self._path), self._schema, compression="zstd")
        self._writer.write_table(table)
        n = len(self._buffer)
        self._total += n
        self._buffer.clear()
        return n

    def close(self) -> int:
        self.flush()
        if self._writer is not None:
            self._writer.close()
        return self._total


# ── RPC Backend ────────────────────────────────────────────────────────────

MAX_RETRIES = 5
RETRY_BASE = 2.0
FLUSH_EVERY = 20  # flush after N batches
RPC_INITIAL_RANGE = 50_000  # starting block range, adapts down


def _decode_split_merge_log(raw: dict[str, Any], entity: str) -> dict[str, Any]:
    """Decode a PositionSplit or PositionsMerge log.

    Topics: [sig, stakeholder, parentCollectionId, conditionId]
    Data:   [collateralToken (address, 32B), partition_offset (32B), amount (32B), ...]
    """
    topics = raw["topics"]
    stakeholder = "0x" + topics[1][-40:]
    condition_id = topics[3]  # conditionId (indexed)
    data_hex = raw["data"][2:]
    # amount is at byte offset 128 (4th 32-byte word: collateral, offset, amount)
    amount_raw = int(data_hex[128:192], 16)
    amount_usdc = float(Decimal(amount_raw) / USDC_SCALE)

    return {
        "tx_hash": raw["transactionHash"],
        "block_number": int(raw["blockNumber"], 16),
        "log_index": int(raw.get("logIndex", "0x0"), 16),
        "timestamp": 0,  # filled later from block
        "stakeholder": stakeholder,
        "condition_id": condition_id,
        "amount_usdc": amount_usdc,
    }


def _decode_redemption_log(raw: dict[str, Any]) -> dict[str, Any]:
    """Decode a PayoutRedemption log.

    Topics: [sig, redeemer, collateralToken(?), conditionId]
    Data:   ABI-encoded (varies)
    """
    topics = raw["topics"]
    redeemer = "0x" + topics[1][-40:]
    condition_id = topics[3]
    data_hex = raw["data"][2:]
    # The payout is the last 32-byte word in data
    # Layout varies but payout is typically the last uint256
    n_words = len(data_hex) // 64
    payout_raw = int(data_hex[(n_words - 1) * 64 : n_words * 64], 16)
    payout_usdc = float(Decimal(payout_raw) / USDC_SCALE)

    return {
        "tx_hash": raw["transactionHash"],
        "block_number": int(raw["blockNumber"], 16),
        "log_index": int(raw.get("logIndex", "0x0"), 16),
        "timestamp": 0,
        "redeemer": redeemer,
        "condition_id": condition_id,
        "payout_usdc": payout_usdc,
    }


def _decode_conversion_log(raw: dict[str, Any]) -> dict[str, Any]:
    """Decode a PositionsConverted log.

    Topics: [sig, stakeholder, ?, negRiskMarketId]
    Data:   [indexSet, amount, ...]
    """
    topics = raw["topics"]
    stakeholder = "0x" + topics[1][-40:]
    neg_risk_market_id = topics[3] if len(topics) > 3 else topics[2]
    data_hex = raw["data"][2:]
    # indexSet and amount in first two 32-byte words
    index_set = int(data_hex[0:64], 16)
    amount_raw = int(data_hex[64:128], 16)
    amount_usdc = float(Decimal(amount_raw) / USDC_SCALE)

    return {
        "tx_hash": raw["transactionHash"],
        "block_number": int(raw["blockNumber"], 16),
        "log_index": int(raw.get("logIndex", "0x0"), 16),
        "timestamp": 0,
        "stakeholder": stakeholder,
        "neg_risk_market_id": neg_risk_market_id,
        "amount_usdc": amount_usdc,
        "index_set": index_set,
    }


def _decode_log(raw: dict[str, Any], entity: str) -> dict[str, Any] | None:
    """Route log decoding to the right handler."""
    try:
        if entity in ("splits", "merges"):
            return _decode_split_merge_log(raw, entity)
        elif entity == "redemptions":
            return _decode_redemption_log(raw)
        elif entity == "conversions":
            return _decode_conversion_log(raw)
    except (IndexError, ValueError, KeyError) as e:
        log.warning("decode_error", entity=entity, error=str(e), tx=raw.get("transactionHash"))
    return None


def _rpc_get_logs(
    session: requests.Session,
    rpc_url: str,
    address: str,
    topic: str,
    from_block: int,
    to_block: int,
) -> dict[str, Any]:
    """Call eth_getLogs with retry."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                "params": [{
                    "address": address,
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    "topics": [[topic]],
                }],
            }, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE * (2 ** attempt)
            log.warning("rpc.retry", attempt=attempt + 1, delay=f"{delay:.0f}s", error=str(exc)[:100])
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _rpc_get_block_timestamp(session: requests.Session, rpc_url: str, block: int) -> int:
    """Get block timestamp. Returns 0 on failure."""
    try:
        resp = session.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
            "params": [hex(block), False],
        }, timeout=10)
        data = resp.json()
        return int(data["result"]["timestamp"], 16)
    except Exception:
        return 0


def _rpc_latest_block(session: requests.Session, rpc_url: str) -> int:
    resp = session.post(rpc_url, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": [],
    }, timeout=10)
    return int(resp.json()["result"], 16)


def recover_rpc(
    entity: str,
    output_dir: Path,
    rpc_url: str,
    *,
    from_block: int = 0,
    resume: bool = False,
) -> int:
    """Recover a single entity via RPC eth_getLogs with adaptive block ranges."""
    cfg = ENTITY_CONFIG[entity]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "_manifest.json"
    output_path = output_dir / f"{entity}.parquet"

    session = requests.Session()

    # Resume
    cursor_block = from_block
    total_rows = 0
    if resume:
        manifest = _load_manifest(manifest_path)
        if entity in manifest:
            cursor_block = manifest[entity].get("cursor_block", from_block)
            total_rows = manifest[entity].get("total_rows", 0)
            log.info("resume", entity=entity, cursor_block=cursor_block, total_rows=total_rows)

    latest_block = _rpc_latest_block(session, rpc_url)
    total_blocks = latest_block - cursor_block
    if total_blocks <= 0:
        log.info("up_to_date", entity=entity)
        return 0

    writer = ParquetBuffer(output_path, cfg["schema"])
    block_range = RPC_INITIAL_RANGE
    batch_num = 0
    batch_stats: deque[tuple[float, int]] = deque(maxlen=100)

    # Cache block timestamps (batch fetch first block of each chunk)
    ts_cache: dict[int, int] = {}

    try:
        while cursor_block < latest_block:
            to_block = min(cursor_block + block_range, latest_block)
            batch_start = time.monotonic()

            data = _rpc_get_logs(
                session, rpc_url, cfg["contract"], cfg["sig"], cursor_block, to_block,
            )

            if "error" in data:
                err = data["error"]
                msg = str(err.get("message", ""))

                if "10000 results" in msg:
                    # Infura tells us the right range
                    suggested = err.get("data", {}).get("to")
                    if suggested:
                        block_range = max(int(suggested, 16) - cursor_block, 1)
                    else:
                        block_range = max(block_range // 4, 1)
                    log.info("range.shrink", entity=entity, new_range=block_range)
                    continue  # retry with smaller range

                if "block range" in msg.lower() or "too large" in msg.lower():
                    block_range = max(block_range // 2, 100)
                    log.info("range.shrink", entity=entity, new_range=block_range)
                    continue

                raise RuntimeError(f"RPC error: {err}")

            logs = data.get("result", [])
            blocks_covered = to_block - cursor_block

            # Decode logs
            rows: list[dict[str, Any]] = []
            for raw_log in logs:
                decoded = _decode_log(raw_log, entity)
                if decoded is not None:
                    # Get block timestamp (cache per block)
                    bn = decoded["block_number"]
                    if bn not in ts_cache:
                        ts_cache[bn] = _rpc_get_block_timestamp(session, rpc_url, bn)
                    decoded["timestamp"] = ts_cache[bn]
                    rows.append(decoded)

            if rows:
                writer.add(rows)
            total_rows += len(rows)

            # Advance cursor
            cursor_block = to_block + 1
            batch_num += 1
            batch_elapsed = time.monotonic() - batch_start
            batch_stats.append((batch_elapsed, blocks_covered))

            # Adapt range up if we're getting few results
            if len(logs) < 2000 and block_range < RPC_INITIAL_RANGE:
                block_range = min(block_range * 2, RPC_INITIAL_RANGE)

            # Trim timestamp cache (keep last 1000 blocks)
            if len(ts_cache) > 2000:
                cutoff = cursor_block - 1000
                ts_cache = {k: v for k, v in ts_cache.items() if k > cutoff}

            # Flush + checkpoint periodically
            if batch_num % FLUSH_EVERY == 0:
                flushed = writer.flush()
                if flushed:
                    log.info("flush", entity=entity, rows=flushed, total=total_rows)

                manifest = _load_manifest(manifest_path)
                manifest[entity] = {
                    "cursor_block": cursor_block,
                    "total_rows": total_rows,
                    "updated_at": time.time(),
                    "backend": "rpc",
                }
                _save_manifest(manifest_path, manifest)

            # Progress
            if batch_num % 5 == 0:
                progress = (cursor_block - from_block) / total_blocks * 100
                eta_str = "..."
                if len(batch_stats) >= 5:
                    total_wall = sum(s[0] for s in batch_stats)
                    total_blk = sum(s[1] for s in batch_stats)
                    if total_blk > 0:
                        remaining = latest_block - cursor_block
                        eta_s = remaining * (total_wall / total_blk)
                        eta_str = f"{eta_s / 3600:.1f}h" if eta_s >= 3600 else f"{eta_s / 60:.0f}m"

                log.info(
                    "progress",
                    entity=entity,
                    block=cursor_block,
                    rows=total_rows,
                    batch_logs=len(logs),
                    range=block_range,
                    progress=f"{progress:.1f}%",
                    eta=eta_str,
                    latency_ms=f"{batch_elapsed * 1000:.0f}",
                )
    finally:
        written = writer.close()
        manifest = _load_manifest(manifest_path)
        manifest[entity] = {
            "cursor_block": cursor_block,
            "total_rows": total_rows,
            "completed": cursor_block >= latest_block,
            "updated_at": time.time(),
            "backend": "rpc",
        }
        _save_manifest(manifest_path, manifest)
        log.info("done", entity=entity, total_rows=total_rows, parquet_rows=written)

    return total_rows


# ── Subgraph Backend ───────────────────────────────────────────────────────

ACTIVITY_SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/activity-subgraph/0.0.4/gn"
)

SUBGRAPH_BATCH_SIZE = 1000
SUBGRAPH_FLUSH_EVERY = 50


def _subgraph_query(
    session: requests.Session, entity_name: str, fields: list[str], *, sticky: bool = False,
) -> str:
    """Build a paginated GraphQL query."""
    fields_str = " ".join(fields)
    if sticky:
        return f"""
        query($timestamp: String!, $id_gt: String!, $first: Int!) {{
            {entity_name}(orderBy: timestamp, orderDirection: asc, first: $first,
                where: {{ timestamp: $timestamp, id_gt: $id_gt }}) {{ {fields_str} }}
        }}"""
    return f"""
    query($timestamp_gt: String!, $first: Int!) {{
        {entity_name}(orderBy: timestamp, orderDirection: asc, first: $first,
            where: {{ timestamp_gt: $timestamp_gt }}) {{ {fields_str} }}
    }}"""


def _subgraph_execute(session: requests.Session, query: str, variables: dict[str, Any]) -> Any:
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
                raise RuntimeError(f"GraphQL: {data['errors']}")
            return data["data"]
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE * (2 ** attempt)
            log.warning("subgraph.retry", attempt=attempt + 1, error=str(exc)[:100])
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _normalize_subgraph_row(entity: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Convert subgraph response to our schema (uses subgraph id as tx_hash proxy)."""
    ts = int(raw["timestamp"])
    amount_raw = int(raw.get("amount", raw.get("payout", "0")))
    amount = float(Decimal(amount_raw) / USDC_SCALE)
    subgraph_id = raw["id"]
    # Subgraph id format: txHash_logIndex
    parts = subgraph_id.rsplit("_", 1)
    tx_hash = parts[0] if parts else subgraph_id
    log_idx = int(parts[1], 16) if len(parts) > 1 else 0

    base: dict[str, Any] = {
        "tx_hash": tx_hash,
        "block_number": 0,  # subgraph doesn't provide this
        "log_index": log_idx,
        "timestamp": ts,
    }

    if entity in ("splits", "merges"):
        base["stakeholder"] = raw["stakeholder"]
        base["condition_id"] = raw["condition"]
        base["amount_usdc"] = amount
    elif entity == "redemptions":
        base["redeemer"] = raw["redeemer"]
        base["condition_id"] = raw["condition"]
        base["payout_usdc"] = amount
    elif entity == "conversions":
        base["stakeholder"] = raw["stakeholder"]
        base["neg_risk_market_id"] = raw["negRiskMarketId"]
        base["amount_usdc"] = amount
        base["index_set"] = int(raw["indexSet"])

    return base


def recover_subgraph(
    entity: str,
    output_dir: Path,
    *,
    from_timestamp: int = 0,
    resume: bool = False,
) -> int:
    """Recover a single entity via Goldsky Activity Subgraph."""
    cfg = ENTITY_CONFIG[entity]
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "_manifest.json"
    output_path = output_dir / f"{entity}.parquet"

    session = requests.Session()

    cursor_ts = str(from_timestamp)
    cursor_id = ""
    total_rows = 0

    if resume:
        manifest = _load_manifest(manifest_path)
        if entity in manifest:
            cursor_ts = str(manifest[entity].get("cursor_ts", from_timestamp))
            cursor_id = manifest[entity].get("cursor_id", "")
            total_rows = manifest[entity].get("total_rows", 0)
            log.info("resume", entity=entity, cursor_ts=cursor_ts, total_rows=total_rows)

    target_ts = int(time.time())
    total_gap = target_ts - int(cursor_ts) if int(cursor_ts) > 0 else target_ts

    query_normal = _subgraph_query(session, cfg["subgraph_name"], cfg["subgraph_fields"])
    query_sticky = _subgraph_query(
        session, cfg["subgraph_name"], cfg["subgraph_fields"], sticky=True,
    )

    writer = ParquetBuffer(output_path, cfg["schema"])
    batch_num = 0
    batch_stats: deque[tuple[float, int]] = deque(maxlen=200)

    try:
        while True:
            batch_start = time.monotonic()
            prev_ts = int(cursor_ts)

            if cursor_id:
                data = _subgraph_execute(session, query_sticky, {
                    "timestamp": cursor_ts, "id_gt": cursor_id, "first": SUBGRAPH_BATCH_SIZE,
                })
            else:
                data = _subgraph_execute(session, query_normal, {
                    "timestamp_gt": cursor_ts, "first": SUBGRAPH_BATCH_SIZE,
                })

            events = data.get(cfg["subgraph_name"], [])
            if not events:
                break

            rows = [_normalize_subgraph_row(entity, e) for e in events]
            writer.add(rows)
            total_rows += len(rows)

            last = events[-1]
            new_ts = last["timestamp"]
            if new_ts == cursor_ts:
                cursor_id = last["id"]
            else:
                cursor_ts = new_ts
                cursor_id = ""

            batch_num += 1
            batch_elapsed = time.monotonic() - batch_start
            data_covered = int(cursor_ts) - prev_ts
            batch_stats.append((batch_elapsed, data_covered))

            if batch_num % SUBGRAPH_FLUSH_EVERY == 0:
                flushed = writer.flush()
                if flushed:
                    log.info("flush", entity=entity, rows=flushed, total=total_rows)
                manifest = _load_manifest(manifest_path)
                manifest[entity] = {
                    "cursor_ts": int(cursor_ts),
                    "cursor_id": cursor_id,
                    "total_rows": total_rows,
                    "updated_at": time.time(),
                    "backend": "subgraph",
                }
                _save_manifest(manifest_path, manifest)

            if batch_num % 10 == 0:
                remaining = target_ts - int(cursor_ts)
                progress = (1 - remaining / total_gap) * 100 if total_gap > 0 else 100
                eta_str = "..."
                if len(batch_stats) >= 10:
                    tw = sum(s[0] for s in batch_stats)
                    td = sum(s[1] for s in batch_stats)
                    if td > 0:
                        eta_s = remaining * (tw / td)
                        eta_str = f"{eta_s / 3600:.1f}h" if eta_s >= 3600 else f"{eta_s / 60:.0f}m"
                log.info(
                    "progress", entity=entity, batch=batch_num, rows=total_rows,
                    progress=f"{progress:.1f}%", eta=eta_str,
                )

            if len(events) < SUBGRAPH_BATCH_SIZE:
                cursor_id = ""
    finally:
        written = writer.close()
        manifest = _load_manifest(manifest_path)
        manifest[entity] = {
            "cursor_ts": int(cursor_ts),
            "cursor_id": cursor_id,
            "total_rows": total_rows,
            "completed": True,
            "updated_at": time.time(),
            "backend": "subgraph",
        }
        _save_manifest(manifest_path, manifest)
        log.info("done", entity=entity, total_rows=total_rows, parquet_rows=written)

    return total_rows


# ── CLI ────────────────────────────────────────────────────────────────────

ALL_ENTITIES = list(ENTITY_CONFIG.keys())

# CTF contract deployment block on Polygon
CTF_DEPLOY_BLOCK = 15_000_000


def _resolve_rpc_url() -> str:
    """Resolve the RPC URL from env or .env file."""
    import os

    # Try Infura first (best getLogs support with adaptive range hints)
    for key in ("PM_INFURA_URL", "PM_RPC_HTTP_URL"):
        url = os.environ.get(key)
        if url:
            return url

    # Fall back to WS URLs, convert to HTTP
    ws_urls = os.environ.get("PM_RPC_WS_URLS", "")
    if not ws_urls:
        # Try loading from .env
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "PM_RPC_WS_URLS":
                    ws_urls = v.strip()
                    break

    if ws_urls:
        # Pick the first one with infura (best getLogs support)
        for url in ws_urls.split(","):
            url = url.strip()
            if "infura" in url:
                return url.replace("wss://", "https://").replace("/ws/", "/")
        # Fall back to first URL
        first = ws_urls.split(",")[0].strip()
        return first.replace("wss://", "https://").replace("ws://", "http://")

    raise RuntimeError(
        "No RPC URL found. Set PM_INFURA_URL, PM_RPC_HTTP_URL, or PM_RPC_WS_URLS"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover CTF contract events (splits, merges, redemptions, conversions)"
    )
    parser.add_argument(
        "--entities",
        nargs="+",
        choices=ALL_ENTITIES,
        default=ALL_ENTITIES,
        help="Entity types to recover (default: all)",
    )
    parser.add_argument(
        "--backend",
        choices=["rpc", "subgraph"],
        default="rpc",
        help="Data source backend (default: rpc)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument(
        "--from-block", type=int, default=CTF_DEPLOY_BLOCK,
        help=f"Start block for RPC backend (default: {CTF_DEPLOY_BLOCK})",
    )
    parser.add_argument(
        "--from-timestamp", type=int, default=0,
        help="Start timestamp for subgraph backend (default: 0)",
    )
    parser.add_argument(
        "--rpc-url", type=str, default=None,
        help="RPC HTTP URL (auto-detected from PM_RPC_WS_URLS if not set)",
    )
    args = parser.parse_args()

    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])

    t0 = time.monotonic()
    grand_total = 0

    for entity in args.entities:
        log.info("recover_start", entity=entity, backend=args.backend)

        if args.backend == "rpc":
            rpc_url = args.rpc_url or _resolve_rpc_url()
            log.info("rpc.url", url=rpc_url.split("/v3/")[0] + "/...")
            rows = recover_rpc(
                entity, args.output_dir, rpc_url,
                from_block=args.from_block, resume=args.resume,
            )
        else:
            rows = recover_subgraph(
                entity, args.output_dir,
                from_timestamp=args.from_timestamp, resume=args.resume,
            )

        grand_total += rows

    elapsed = time.monotonic() - t0
    log.info(
        "recover_complete",
        entities=args.entities,
        backend=args.backend,
        total_rows=grand_total,
        elapsed=f"{elapsed / 60:.1f}m",
    )


if __name__ == "__main__":
    main()
