"""Goldsky Sink Parquet file loader.

CRITICAL: Only fastparquet can read these files. pyarrow fails on DECIMAL(100,18)
precision (max 76). DuckDB casts to lossy DOUBLE. Do NOT use other readers.
"""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from typing import Any

import structlog

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.normalizers.sink import EXCHANGE_ADDRS, GoldskySinkNormalizer

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


# ---------------------------------------------------------------------------
# Vectorized fast path (used by parallel backfill)
# ---------------------------------------------------------------------------


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


_NEEDED_COLS = [
    "taker",
    "maker",
    "maker_asset_id",
    "taker_asset_id",
    "maker_amount_filled",
    "taker_amount_filled",
    "fee",
    "transaction_hash",
    "order_hash",
    "timestamp",
]


def load_file_fast(
    path: Path,
    cond_map: dict[str, str],
) -> tuple[Any, int, int]:
    """Vectorized parquet loading — ~20-50x faster than iterrows.

    Args:
        path: Parquet file to load.
        cond_map: Pre-computed asset_id → condition_id mapping.

    Returns (dataframe, total_rows, dropped_count) where dataframe has
    columns matching the trades_raw ClickHouse schema.
    """
    import fastparquet  # type: ignore[import-untyped]
    import numpy as np
    import pandas as pd

    pf = fastparquet.ParquetFile(str(path))
    df = pf.to_pandas(columns=_NEEDED_COLS)
    total_rows = len(df)

    # 1. Filter taker-focused duplicates (vectorized string match)
    df = df[~df["taker"].str.lower().isin(EXCHANGE_ADDRS)]
    dropped = total_rows - len(df)

    if len(df) == 0:
        empty = pd.DataFrame()
        return empty, total_rows, dropped

    # 2. Side determination (vectorized)
    is_buy = df["maker_asset_id"].astype(str).values == "0"

    # 3. Amount extraction (vectorized conditional)
    maker_amt = df["maker_amount_filled"].values
    taker_amt = df["taker_amount_filled"].values
    usdc_raw = np.where(is_buy, maker_amt, taker_amt)
    token_raw = np.where(is_buy, taker_amt, maker_amt)

    maker_aid = df["maker_asset_id"].astype(str).values
    taker_aid = df["taker_asset_id"].astype(str).values
    token_asset_id = np.where(is_buy, taker_aid, maker_aid)

    # 4. Scale to USDC (float64 precision, inserted as Float32)
    usdc = usdc_raw.astype(np.float64) / 1e6
    tokens = token_raw.astype(np.float64) / 1e6
    price = np.where(tokens > 0, np.round(usdc / tokens, 4), 0.0)
    fee = df["fee"].values.astype(np.float64) / 1e6

    # 5+6. Hex encode + trade IDs in a single pass (avoid 3 separate loops)
    tx_raw = df["transaction_hash"].values
    oh_raw = df["order_hash"].values
    n = len(df)
    tx_hashes = np.empty(n, dtype=object)
    order_hashes = np.empty(n, dtype=object)
    trade_ids = np.empty(n, dtype=object)
    for i in range(n):
        tx = "0x" + tx_raw[i].hex()
        oh = "0x" + oh_raw[i].hex()
        tx_hashes[i] = tx
        order_hashes[i] = oh
        trade_ids[i] = "chain:" + sha256((tx + ":" + oh).encode()).digest()[:8].hex()

    # 7. Map token -> condition_id (pandas C-level hash lookup, cond_map pre-computed)
    condition_ids = pd.Series(token_asset_id).map(cond_map).fillna("unknown").values

    # 8. Timestamps (vectorized)
    timestamps = pd.to_datetime(df["timestamp"].values, unit="s", utc=True)

    # 9. Build output DataFrame matching trades_raw schema
    result = pd.DataFrame({
        "trade_id": trade_ids,
        "condition_id": condition_ids,
        "asset_id": token_asset_id,
        "side": np.where(is_buy, "BUY", "SELL"),
        "price": price.astype(np.float32),
        "size": tokens.astype(np.float32),
        "amount_usd": usdc.astype(np.float32),
        "fee_usd": fee.astype(np.float32),
        "maker": df["maker"].values,
        "taker": df["taker"].values,
        "timestamp": timestamps,
        "source": np.full(n, "goldsky_sink"),
        "tx_hash": tx_hashes,
        "order_hash": order_hashes,
        "block_number": pd.array([None] * n, dtype=pd.Int64Dtype()),
        "is_backfill": np.ones(n, dtype=bool),
        "_version": np.full(n, 2, dtype=np.uint8),
    })

    return result, total_rows, dropped


# ---------------------------------------------------------------------------
# Compact Parquet helpers (Polars / PyArrow)
# ---------------------------------------------------------------------------


def list_compact_files(directory: Path) -> list[Path]:
    """List compact_*.parquet files in directory, sorted by name."""
    return sorted(directory.glob("compact_*.parquet"))


def iter_row_groups_arrow(
    path: Path, batch_size: int = 500_000
) -> Iterator[Any]:
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
