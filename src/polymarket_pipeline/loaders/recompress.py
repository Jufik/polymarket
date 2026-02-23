"""Recompress raw Goldsky Sink Parquet files into compact, sorted, ZSTD-compressed files.

Reads raw DECIMAL parquet via fastparquet (the only working reader), normalizes with
load_file_fast(), converts to Arrow IPC for cross-process transport, sorts by
(condition_id, timestamp), and writes optimized parquet readable by pyarrow/Polars/DuckDB.

Output: <output_dir>/compact_NNNN.parquet
Resume: tracks progress in _manifest.json, skips already-processed batches.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Worker process globals (set once per worker via initializer)
# ---------------------------------------------------------------------------

_worker_cond_map: dict[str, str] = {}


def _init_worker(token_map: dict[str, tuple[str, str]]) -> None:
    """Called once per worker process. Pre-computes cond_map from token_map."""
    global _worker_cond_map  # noqa: PLW0603
    _worker_cond_map = {k: v[0] for k, v in token_map.items()}


def _load_one(file_path_str: str) -> tuple[str, bytes | None, int, int]:
    """Load a single raw parquet file, return serialized Arrow IPC bytes.

    Returns (filename, ipc_bytes_or_None, total_rows, dropped).
    Arrow IPC avoids pickle overhead on pandas DataFrames across processes.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    from polymarket_pipeline.loaders.parquet import load_file_fast

    path = Path(file_path_str)
    df, total_rows, dropped = load_file_fast(path, _worker_cond_map)

    if len(df) == 0:
        return path.name, None, total_rows, dropped

    table = pa.Table.from_pandas(df, preserve_index=False)
    sink = pa.BufferOutputStream()
    writer = pa.ipc.new_stream(sink, table.schema)
    writer.write_table(table)
    writer.close()
    return path.name, sink.getvalue().to_pybytes(), total_rows, dropped


def _deserialize_ipc(data: bytes) -> Any:
    """Deserialize Arrow IPC bytes back to a PyArrow Table."""
    import pyarrow as pa

    reader = pa.ipc.open_stream(data)
    return reader.read_all()


# ---------------------------------------------------------------------------
# Compact batch writing
# ---------------------------------------------------------------------------


def _write_compact_batch(
    tables: list[Any],
    batch_idx: int,
    output_dir: Path,
    row_group_size: int,
) -> Path:
    """Concatenate tables, sort by (condition_id, timestamp), and write a compact parquet."""
    import pyarrow as pa
    import pyarrow.compute as pc  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    combined = pa.concat_tables(tables)

    indices = pc.sort_indices(
        combined, sort_keys=[("condition_id", "ascending"), ("timestamp", "ascending")]
    )
    combined = combined.take(indices)

    out_path = output_dir / f"compact_{batch_idx:04d}.parquet"
    pq.write_table(
        combined,
        str(out_path),
        compression="zstd",
        row_group_size=row_group_size,
        use_dictionary=["condition_id", "asset_id", "side", "source", "maker", "taker"],
    )
    return out_path


# ---------------------------------------------------------------------------
# Manifest (resume tracking)
# ---------------------------------------------------------------------------


def _load_manifest(output_dir: Path) -> dict[str, Any]:
    """Load the progress manifest, or return empty state."""
    manifest_path = output_dir / "_manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())  # type: ignore[no-any-return]
    return {"completed_batches": [], "files_processed": []}


def _save_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    """Save the progress manifest atomically."""
    manifest_path = output_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))


# ---------------------------------------------------------------------------
# Main recompression pipeline (pass 1)
# ---------------------------------------------------------------------------


async def run_recompress(
    parquet_dir: Path,
    output_dir: Path,
    pg_dsn: str,
    batch_files: int,
    workers: int,
    row_group_size: int,
    token_map_path: Path | None = None,
) -> None:
    """Recompress raw Goldsky parquet files into compact, sorted batches.

    Args:
        parquet_dir: Directory containing raw ``*.parquet`` files.
        output_dir: Target directory for ``compact_NNNN.parquet`` output.
        pg_dsn: PostgreSQL DSN used to load the token map (ignored when
            *token_map_path* is provided).
        batch_files: Number of raw files to merge into each compact file.
        workers: Number of parallel worker processes for loading.
        row_group_size: Row group size in output parquet files.
        token_map_path: Optional path to a ``token_market_map.parquet`` file.
            When provided the token map is read from disk instead of PostgreSQL.
    """
    from polymarket_pipeline.loaders.parquet import list_parquet_files

    # 1. Load token map -------------------------------------------------
    if token_map_path is not None:
        import polars as pl

        log.info("loading_token_map_from_parquet", path=str(token_map_path))
        tm_df = pl.read_parquet(token_map_path)
        token_map: dict[str, tuple[str, str]] = {
            row["asset_id"]: (row["condition_id"], row["outcome"])
            for row in tm_df.iter_rows(named=True)
        }
    else:
        from polymarket_pipeline.sinks.postgres import PostgresSink

        log.info("loading_token_map", dsn=pg_dsn)
        async with PostgresSink(dsn=pg_dsn) as pg:
            token_map = await pg.fetch_token_market_map()
    log.info("token_map_loaded", tokens=len(token_map))

    # 2. List raw files -------------------------------------------------
    all_files = list_parquet_files(parquet_dir)
    log.info("raw_files_found", count=len(all_files))

    # 3. Manifest for resumability --------------------------------------
    Path.mkdir(output_dir, parents=True, exist_ok=True)  # noqa: ASYNC240
    manifest = _load_manifest(output_dir)
    already_processed: set[str] = set(manifest["files_processed"])

    remaining = [f for f in all_files if f.name not in already_processed]
    log.info(
        "resume_check",
        already_done=len(already_processed),
        remaining=len(remaining),
    )

    if not remaining:
        log.info("nothing_to_do")
        return

    # 4. Process in batches ---------------------------------------------
    mp_context = multiprocessing.get_context("fork" if sys.platform == "linux" else "spawn")
    next_batch_idx = len(manifest["completed_batches"])

    total_rows_all = 0
    total_trades_all = 0
    start = time.monotonic()

    for batch_start in range(0, len(remaining), batch_files):
        batch = remaining[batch_start : batch_start + batch_files]
        batch_idx = next_batch_idx
        next_batch_idx += 1

        log.info(
            "batch_start",
            batch=batch_idx,
            files=len(batch),
            first=batch[0].name,
            last=batch[-1].name,
        )

        # Parallel load raw files
        tables: list[Any] = []
        batch_rows = 0
        batch_dropped = 0

        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp_context,
            initializer=_init_worker,
            initargs=(token_map,),
        ) as executor:
            futures = {executor.submit(_load_one, str(p)): p for p in batch}
            for future in as_completed(futures):
                fname, ipc_bytes, total, dropped = future.result()
                batch_rows += total
                batch_dropped += dropped
                if ipc_bytes is not None:
                    tables.append(_deserialize_ipc(ipc_bytes))
                log.debug("file_loaded", file=fname, rows=total, dropped=dropped)

        if not tables:
            log.warning("batch_empty", batch=batch_idx)
            manifest["files_processed"].extend(f.name for f in batch)
            _save_manifest(output_dir, manifest)
            continue

        # Write compact file
        trades_in_batch = sum(t.num_rows for t in tables)
        out_path = _write_compact_batch(tables, batch_idx, output_dir, row_group_size)
        file_size_mb = out_path.stat().st_size / (1024 * 1024)

        total_rows_all += batch_rows
        total_trades_all += trades_in_batch

        log.info(
            "batch_complete",
            batch=batch_idx,
            trades=trades_in_batch,
            dropped=batch_dropped,
            output=out_path.name,
            size_mb=f"{file_size_mb:.1f}",
        )

        # Update manifest
        manifest["completed_batches"].append(
            {
                "batch_idx": batch_idx,
                "output_file": out_path.name,
                "input_files": [f.name for f in batch],
                "trades": trades_in_batch,
                "size_mb": round(file_size_mb, 1),
            }
        )
        manifest["files_processed"].extend(f.name for f in batch)
        _save_manifest(output_dir, manifest)

    elapsed = time.monotonic() - start
    log.info(
        "recompression_complete",
        total_rows=total_rows_all,
        total_trades=total_trades_all,
        output_files=len(manifest["completed_batches"]),
        elapsed_min=f"{elapsed / 60:.1f}",
    )


# ---------------------------------------------------------------------------
# Global sort (pass 2)
# ---------------------------------------------------------------------------


def run_global_sort(
    output_dir: Path,
    row_group_size: int,
) -> None:
    """Pass 2: globally sort all compact files so each output has disjoint key ranges.

    Reads all ``compact_*.parquet`` via Polars lazy scan, sorts by
    (condition_id, timestamp), and rewrites as ``sorted/sorted_NNNN.parquet``.
    This gives optimal ClickHouse ingestion and enables DuckDB predicate pushdown
    on row group statistics.
    """
    import polars as pl

    from polymarket_pipeline.loaders.parquet import list_compact_files

    files = list_compact_files(output_dir)
    if not files:
        log.error("no_compact_files", dir=str(output_dir))
        sys.exit(1)

    log.info("global_sort_start", files=len(files))
    start = time.monotonic()

    # Lazy scan all compact files
    lf = pl.scan_parquet([str(f) for f in files])

    # Determine partitioning
    total_rows: int = lf.select(pl.len()).collect().item()
    rows_per_file = max(row_group_size * 10, 5_000_000)
    n_output_files = max(1, (total_rows + rows_per_file - 1) // rows_per_file)

    log.info(
        "global_sort_plan",
        total_rows=total_rows,
        rows_per_file=rows_per_file,
        output_files=n_output_files,
    )

    # Sort and collect (streaming engine handles larger-than-memory datasets)
    df_sorted = lf.sort(["condition_id", "timestamp"]).collect(engine="streaming")

    # Write in chunks as sorted_NNNN.parquet
    sorted_dir = output_dir / "sorted"
    sorted_dir.mkdir(parents=True, exist_ok=True)

    total_written = 0
    for i in range(n_output_files):
        chunk_start = i * rows_per_file
        chunk_end = min((i + 1) * rows_per_file, total_rows)
        if chunk_start >= total_rows:
            break

        chunk = df_sorted.slice(chunk_start, chunk_end - chunk_start)
        out_path = sorted_dir / f"sorted_{i:04d}.parquet"
        chunk.write_parquet(
            out_path,
            compression="zstd",
            row_group_size=row_group_size,
            use_pyarrow=True,
            pyarrow_options={
                "use_dictionary": [
                    "condition_id",
                    "asset_id",
                    "side",
                    "source",
                    "maker",
                    "taker",
                ],
            },
        )
        size_mb = out_path.stat().st_size / (1024 * 1024)
        total_written += chunk_end - chunk_start
        log.info(
            "sorted_file_written",
            file=out_path.name,
            rows=chunk_end - chunk_start,
            size_mb=f"{size_mb:.1f}",
            progress=f"{i + 1}/{n_output_files}",
        )

    elapsed = time.monotonic() - start
    log.info(
        "global_sort_complete",
        total_rows=total_written,
        output_files=n_output_files,
        output_dir=str(sorted_dir),
        elapsed_min=f"{elapsed / 60:.1f}",
    )
