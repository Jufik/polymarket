"""Zstd-compressed parquet writer utility."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write_parquet_zstd(
    df: Any,
    path: str | Path,
    *,
    compression: str = "zstd",
    compression_level: int = 3,
    row_group_size: int = 500_000,
) -> Path:
    """Write a DataFrame to parquet with zstd compression.

    Supports both Polars and pandas DataFrames (auto-detected).

    Args:
        df: Polars or pandas DataFrame.
        path: Output file path.
        compression: Compression codec (default: zstd).
        compression_level: Compression level (default: 3).
        row_group_size: Rows per row group (default: 500K).

    Returns:
        Path to the written file.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Detect DataFrame type
    type_name = type(df).__module__

    if "polars" in type_name:
        # Polars DataFrame
        df.write_parquet(
            out,
            compression=compression,
            compression_level=compression_level,
            row_group_size=row_group_size,
        )
    else:
        # Assume pandas — use pyarrow engine
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df)
        pq.write_table(
            table,
            out,
            compression=compression,
            compression_level={"zstd": compression_level}.get(compression),
            row_group_size=row_group_size,
        )

    return out
