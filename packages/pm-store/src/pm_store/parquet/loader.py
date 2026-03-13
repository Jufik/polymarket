"""Pure I/O functions for parquet files — no pipeline-specific normalizers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any


def list_parquet_files(directory: Path, by_size: bool = True) -> list[Path]:
    """List all Parquet files in directory.

    When *by_size* is True (default), files are sorted smallest-first so that
    large files are spread across the processing timeline rather than all
    landing on workers simultaneously.
    """
    files = list(directory.glob("*.parquet"))
    if by_size:
        return sorted(files, key=lambda p: p.stat().st_size)
    return sorted(files)


def list_compact_files(directory: Path) -> list[Path]:
    """List compact_*.parquet files in directory, sorted by name."""
    return sorted(directory.glob("compact_*.parquet"))


def iter_row_groups_arrow(path: Path, batch_size: int = 500_000) -> Iterator[Any]:
    """Stream row groups from a compact parquet file as PyArrow RecordBatches.

    Yields one RecordBatch per row group for constant-memory insertion.
    Requires pyarrow (only used with pre-processed compact files, not raw DECIMAL).
    """
    import pyarrow.parquet as pq  # lazy import

    pf = pq.ParquetFile(str(path))
    for i in range(pf.metadata.num_row_groups):
        yield pf.read_row_group(i)


def load_file_polars(path: Path) -> Any:
    """Load a compact parquet file as a Polars DataFrame for exploration/ad-hoc use.

    Requires polars. Only works with pre-processed compact files.
    """
    import polars as pl  # lazy import

    return pl.read_parquet(path)
