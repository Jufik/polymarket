"""Parquet-backed ledger for backtesting (batch write via Polars)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import polars as pl
import structlog

from polymarket_pipeline.strategies.ledger.base import compute_pnl
from polymarket_pipeline.strategies.ledger.types import LedgerRecord
from polymarket_pipeline.strategies.types import FillStatus

logger = structlog.get_logger(__name__)


class ParquetLedger:
    """Parquet-backed ledger for backtesting.

    Records are buffered in memory and flushed to a Parquet file on demand.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._buffer: list[LedgerRecord] = []

    async def append(self, record: LedgerRecord) -> None:
        """Buffer a single record in memory."""
        self._buffer.append(record)

    async def append_batch(self, records: list[LedgerRecord]) -> None:
        """Buffer multiple records in memory."""
        self._buffer.extend(records)

    async def read_all(self, strategy: str | None = None) -> list[LedgerRecord]:
        """Read all records from buffer (or disk if flushed and buffer is empty).

        If the buffer is non-empty, returns buffered records. Otherwise reads
        from the Parquet file on disk.
        """
        records = self._buffer if self._buffer else self._read_from_disk()
        if strategy is not None:
            records = [r for r in records if r.strategy == strategy]
        return records

    async def enrich_resolutions(
        self, resolutions: dict[str, tuple[str, float]]
    ) -> int:
        """Update resolution fields in the in-memory buffer.

        Parameters
        ----------
        resolutions:
            Mapping of condition_id -> (winner_outcome, resolved_at_epoch).

        Returns
        -------
        Number of records enriched.
        """
        enriched = 0
        updated: list[LedgerRecord] = []
        for record in self._buffer:
            if (
                record.resolution is None
                and record.condition_id in resolutions
            ):
                winner, resolved_at = resolutions[record.condition_id]
                updated.append(compute_pnl(record, winner, resolved_at))
                enriched += 1
            else:
                updated.append(record)
        self._buffer = updated
        return enriched

    async def flush(self) -> Path:
        """Write buffered records to Parquet via Polars. Returns file path."""
        if not self._buffer:
            logger.warning("parquet_ledger.flush_empty")
            return self._path

        rows = [asdict(r) for r in self._buffer]
        df = pl.DataFrame(rows)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(self._path)
        logger.info(
            "parquet_ledger.flushed",
            path=str(self._path),
            records=len(self._buffer),
        )
        return self._path

    @property
    def records(self) -> list[LedgerRecord]:
        """Direct access to the in-memory buffer."""
        return self._buffer

    def _read_from_disk(self) -> list[LedgerRecord]:
        """Read records from the Parquet file on disk."""
        if not self._path.exists():
            return []
        df = pl.read_parquet(self._path)
        return _df_to_records(df)


def _df_to_records(df: pl.DataFrame) -> list[LedgerRecord]:
    """Convert a Polars DataFrame back to LedgerRecord objects."""
    records: list[LedgerRecord] = []
    for row in df.iter_rows(named=True):
        records.append(
            LedgerRecord(
                record_id=row["record_id"],
                signal_time=row["signal_time"],
                strategy=row["strategy"],
                condition_id=row["condition_id"],
                side=row["side"],
                outcome=row["outcome"],
                intent_size_usd=row["intent_size_usd"],
                max_price=row["max_price"],
                reason=row["reason"],
                asset_id=row["asset_id"],
                fill_status=FillStatus(row["fill_status"]),
                fill_price=row["fill_price"],
                fill_size_usd=row["fill_size_usd"],
                fill_fee_usd=row["fill_fee_usd"],
                fill_latency_ms=row["fill_latency_ms"],
                resolution=row.get("resolution"),
                pnl_gross=row.get("pnl_gross"),
                pnl_net=row.get("pnl_net"),
                hold_duration_s=row.get("hold_duration_s"),
                resolved_at=row.get("resolved_at"),
            )
        )
    return records
