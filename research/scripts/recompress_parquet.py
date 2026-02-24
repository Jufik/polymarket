"""Recompress raw Goldsky Sink Parquet files into compact, sorted, ZSTD-compressed files.

Reads raw DECIMAL parquet via fastparquet (the only working reader), normalizes with
load_file_fast(), converts to Polars, sorts by (condition_id, timestamp), and writes
optimized parquet readable by pyarrow/Polars/DuckDB.

Output: order_filled_compact/compact_NNNN.parquet
Resume: tracks progress in _manifest.json, skips already-processed batches.

Usage:
    # Pass 1: Recompress raw files into compact batches
    uv run python scripts/recompress_parquet.py --parquet-dir order_filled/
    uv run python scripts/recompress_parquet.py --parquet-dir order_filled/ --batch-files 5

    # Pass 1 with file-based token map (no PostgreSQL needed):
    uv run python scripts/recompress_parquet.py --parquet-dir order_filled/ \
        --token-map data/metadata/token_market_map.parquet --output-dir data/compact/

    # Pass 2: Global sort across all compact files (disjoint key ranges per file)
    uv run python scripts/recompress_parquet.py --global-sort --output-dir order_filled_compact/

    # Export metadata for offline DuckDB use
    uv run python scripts/recompress_parquet.py --export-metadata --output-dir order_filled_compact/
"""

import argparse
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

PG_DSN_DEFAULT = "postgresql://polymarket:polymarket@localhost:15432/polymarket"

# Worker process globals
_worker_cond_map: dict[str, str] = {}


def _init_worker(token_map: dict[str, tuple[str, str]]) -> None:
    """Called once per worker process. Pre-computes cond_map."""
    global _worker_cond_map
    _worker_cond_map = {k: v[0] for k, v in token_map.items()}


def _load_one(file_path_str: str) -> tuple[str, bytes | None, int, int]:
    """Load a single raw parquet file, return serialized Arrow IPC bytes.

    Returns (filename, ipc_bytes_or_None, total_rows, dropped).
    We serialize to Arrow IPC to avoid pickle overhead on pandas DataFrames.
    """
    import pyarrow as pa

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


def _write_compact_batch(
    tables: list,
    batch_idx: int,
    output_dir: Path,
    row_group_size: int,
) -> Path:
    """Concatenate tables, sort, and write as a single compact parquet file."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    combined = pa.concat_tables(tables)

    # Sort by (condition_id, timestamp) to match ClickHouse ORDER BY
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


def _load_manifest(output_dir: Path) -> dict:
    """Load the progress manifest, or return empty state."""
    manifest_path = output_dir / "_manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return {"completed_batches": [], "files_processed": []}


def _save_manifest(output_dir: Path, manifest: dict) -> None:
    """Save the progress manifest."""
    manifest_path = output_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))


async def run_recompress(
    parquet_dir: Path,
    output_dir: Path,
    pg_dsn: str,
    batch_files: int,
    workers: int,
    row_group_size: int,
    token_map_path: Path | None = None,
) -> None:
    """Main recompression pipeline."""
    from polymarket_pipeline.loaders.parquet import list_parquet_files

    # 1. Load token map
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

    # 2. List raw files
    all_files = list_parquet_files(parquet_dir)
    log.info("raw_files_found", count=len(all_files))

    # 3. Load manifest for resumability (mkdir is sync but one-time, trivial)
    Path.mkdir(output_dir, parents=True, exist_ok=True)  # noqa: ASYNC240
    manifest = _load_manifest(output_dir)
    already_processed = set(manifest["files_processed"])

    remaining = [f for f in all_files if f.name not in already_processed]
    log.info(
        "resume_check",
        already_done=len(already_processed),
        remaining=len(remaining),
    )

    if not remaining:
        log.info("nothing_to_do")
        return

    # 4. Process in batches
    import multiprocessing

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
        tables = []
        batch_rows = 0
        batch_dropped = 0

        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp_context,
            initializer=_init_worker,
            initargs=(token_map,),
        ) as executor:
            futures = {
                executor.submit(_load_one, str(p)): p for p in batch
            }
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
        manifest["completed_batches"].append({
            "batch_idx": batch_idx,
            "output_file": out_path.name,
            "input_files": [f.name for f in batch],
            "trades": trades_in_batch,
            "size_mb": round(file_size_mb, 1),
        })
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


def run_global_sort(
    output_dir: Path,
    row_group_size: int,
) -> None:
    """Pass 2: Globally sort all compact files so each output has disjoint key ranges.

    Reads all compact_*.parquet via Polars lazy scan, sorts by (condition_id, timestamp),
    and rewrites as sorted_NNNN.parquet. This gives optimal ClickHouse ingestion
    and enables DuckDB predicate pushdown on row group statistics.
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

    # Get total row count for partitioning
    total_rows = lf.select(pl.len()).collect().item()
    rows_per_file = max(row_group_size * 10, 5_000_000)  # ~5M rows per output file
    n_output_files = max(1, (total_rows + rows_per_file - 1) // rows_per_file)

    log.info(
        "global_sort_plan",
        total_rows=total_rows,
        rows_per_file=rows_per_file,
        output_files=n_output_files,
    )

    # Sort and collect in one pass (streaming handles larger-than-memory on Polars 1.x)
    df_sorted = (
        lf.sort(["condition_id", "timestamp"])
        .collect(streaming=True)
    )

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
                    "condition_id", "asset_id", "side", "source", "maker", "taker",
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


async def run_export_metadata(
    output_dir: Path,
    pg_dsn: str,
) -> None:
    """Export PostgreSQL metadata tables to parquet for offline DuckDB use.

    Exports token_market_map and markets tables needed by exploration components.
    """
    import polars as pl

    from polymarket_pipeline.sinks.postgres import PostgresSink

    meta_dir = output_dir / "metadata"
    Path.mkdir(meta_dir, parents=True, exist_ok=True)  # noqa: ASYNC240

    log.info("exporting_metadata", dsn=pg_dsn, output=str(meta_dir))

    async with PostgresSink(dsn=pg_dsn) as pg:
        # Export token_market_map
        rows = await pg.query("SELECT asset_id, condition_id, outcome FROM token_market_map")
        df_tm = pl.DataFrame(rows)
        tm_path = meta_dir / "token_market_map.parquet"
        df_tm.write_parquet(tm_path, compression="zstd")
        log.info("exported_token_market_map", rows=len(df_tm), path=str(tm_path))

        # Export markets (columns used by exploration components + useful extras)
        rows = await pg.query(
            "SELECT condition_id, event_id, question, slug, category, "
            "token_yes, token_no, neg_risk, status, "
            "created_at, closed_at, resolved_at FROM markets"
        )
        df_m = pl.DataFrame(rows)
        markets_path = meta_dir / "markets.parquet"
        df_m.write_parquet(markets_path, compression="zstd")
        log.info("exported_markets", rows=len(df_m), path=str(markets_path))

    log.info("metadata_export_complete", output=str(meta_dir))


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompress raw Goldsky Parquet to compact format")
    parser.add_argument(
        "--parquet-dir",
        type=Path,
        default=Path("order_filled"),
        help="Directory containing raw Parquet files (default: order_filled/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("order_filled_compact"),
        help="Output directory for compact files (default: order_filled_compact/)",
    )
    parser.add_argument(
        "--pg-dsn",
        default=PG_DSN_DEFAULT,
        help="PostgreSQL DSN for token map",
    )
    parser.add_argument(
        "--token-map",
        type=Path,
        default=None,
        help="Path to token_market_map.parquet (bypasses PostgreSQL)",
    )
    parser.add_argument(
        "--batch-files",
        type=int,
        default=20,
        help="Number of raw files per output compact file (default: 20)",
    )
    cpu = os.cpu_count() or 4
    parser.add_argument(
        "--workers",
        type=int,
        default=max(cpu - 4, 4),
        help=f"Number of parallel worker processes (default: {max(cpu - 4, 4)})",
    )
    parser.add_argument(
        "--row-group-size",
        type=int,
        default=500_000,
        help="Row group size in output files (default: 500,000)",
    )
    parser.add_argument(
        "--global-sort",
        action="store_true",
        help="Pass 2: globally sort compact files so each output has disjoint key ranges",
    )
    parser.add_argument(
        "--export-metadata",
        action="store_true",
        help="Export PG metadata tables (token_market_map, markets) to parquet for offline use",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
    )

    if args.global_sort:
        if not args.output_dir.exists():
            log.error("output_dir_not_found", path=str(args.output_dir))
            sys.exit(1)
        run_global_sort(args.output_dir, args.row_group_size)
    elif args.export_metadata:
        asyncio.run(run_export_metadata(args.output_dir, args.pg_dsn))
    else:
        if not args.parquet_dir.exists():
            log.error("parquet_dir_not_found", path=str(args.parquet_dir))
            sys.exit(1)
        asyncio.run(
            run_recompress(
                args.parquet_dir,
                args.output_dir,
                args.pg_dsn,
                args.batch_files,
                args.workers,
                args.row_group_size,
                token_map_path=args.token_map,
            )
        )


if __name__ == "__main__":
    main()
