"""Goldsky Sink Parquet file loader.

CRITICAL: Only fastparquet can read these files. pyarrow fails on DECIMAL(100,18)
precision (max 76). DuckDB casts to lossy DOUBLE. Do NOT use other readers.
"""

from pathlib import Path
from typing import Any

import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.normalizers.sink import GoldskySinkNormalizer

log = structlog.get_logger()


class ParquetLoader:
    """Loads and normalizes Goldsky Sink Parquet files."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        self._normalizer = GoldskySinkNormalizer(token_market_map=token_market_map)

    def list_files(self, directory: Path) -> list[Path]:
        """List all Parquet files in directory, sorted by name."""
        return sorted(directory.glob("*.parquet"))

    def load_file(self, path: Path) -> list[NormalizedTrade]:
        """Load and normalize a single Parquet file. Filters duplicates."""
        trades, _ = self.load_file_with_stats(path)
        return trades

    def load_file_with_stats(self, path: Path) -> tuple[list[NormalizedTrade], dict[str, Any]]:
        """Load a Parquet file, returning trades and processing stats."""
        import fastparquet  # type: ignore[import-untyped]

        pf = fastparquet.ParquetFile(str(path))
        df = pf.to_pandas()

        total_rows = len(df)
        trades: list[NormalizedTrade] = []
        dropped = 0

        for _, row in df.iterrows():
            raw = row.to_dict()
            trade = self._normalizer.normalize(raw)
            if trade is None:
                dropped += 1
            else:
                trades.append(trade)

        stats = {
            "file": path.name,
            "total_rows": total_rows,
            "dropped_duplicates": dropped,
            "normalized": len(trades),
        }

        log.info(
            "parquet_file_loaded",
            file=path.name,
            total=total_rows,
            dropped=dropped,
            normalized=len(trades),
        )

        return trades, stats
