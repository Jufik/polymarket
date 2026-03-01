"""LedgerBackend protocol and factory function for constructing records."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from polymarket_pipeline.strategies.ledger.types import LedgerRecord
from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent


@runtime_checkable
class LedgerBackend(Protocol):
    """Backend for persisting and reading ledger records."""

    async def append(self, record: LedgerRecord) -> None:
        """Persist a single record."""
        ...

    async def append_batch(self, records: list[LedgerRecord]) -> None:
        """Persist multiple records."""
        ...

    async def read_all(self, strategy: str | None = None) -> list[LedgerRecord]:
        """Read all records, optionally filtered by strategy name."""
        ...

    async def enrich_resolutions(
        self, resolutions: dict[str, tuple[str, float]]
    ) -> int:
        """Update resolution fields for matching condition_ids.

        Parameters
        ----------
        resolutions:
            Mapping of condition_id -> (winner_outcome, resolved_at_epoch).

        Returns
        -------
        Number of records enriched.
        """
        ...


def make_ledger_record(intent: TradeIntent, fill: Fill) -> LedgerRecord:
    """Construct a LedgerRecord from an intent + fill pair.

    This is the single construction point used by both BacktestRunner
    and LiveRunner.
    """
    latency_ms = (fill.filled_at - intent.signal_time) * 1000.0

    return LedgerRecord(
        record_id=fill.intent_id if fill.intent_id else uuid.uuid4().hex[:12],
        signal_time=intent.signal_time,
        strategy=intent.strategy,
        condition_id=intent.condition_id,
        side=intent.side,
        outcome=intent.outcome,
        intent_size_usd=intent.size_usd,
        max_price=intent.max_price,
        reason=intent.reason,
        asset_id=intent.asset_id,
        fill_status=fill.status,
        fill_price=fill.filled_price,
        fill_size_usd=fill.filled_size_usd,
        fill_fee_usd=fill.fee_usd,
        fill_latency_ms=max(latency_ms, 0.0),
    )


def compute_pnl(
    record: LedgerRecord,
    winner_outcome: str,
    resolved_at: float,
) -> LedgerRecord:
    """Return a new LedgerRecord enriched with resolution PnL.

    PnL logic for BUY:
      - If outcome matches winner: payout is 1.0 per token, cost is fill_price per token.
        pnl_gross = (1.0 - fill_price) * qty_tokens
      - If outcome does not match winner: payout is 0.0.
        pnl_gross = -fill_size_usd
      where qty_tokens = fill_size_usd / fill_price (when fill_price > 0).

    PnL logic for SELL:
      - If outcome matches winner: sold at fill_price, payout would have been 1.0.
        pnl_gross = (fill_price - 1.0) * qty_tokens  (negative — sold a winner)
      - If outcome does not match winner: sold at fill_price, payout 0.0.
        pnl_gross = fill_size_usd  (kept the cash from selling a loser)

    pnl_net = pnl_gross - fill_fee_usd
    """
    if record.fill_status != FillStatus.FILLED or record.fill_price <= 0:
        # Can't compute PnL for rejected/zero-price fills
        from dataclasses import replace

        return replace(
            record,
            resolution=winner_outcome,  # type: ignore[arg-type]
            resolved_at=resolved_at,
        )

    qty_tokens = record.fill_size_usd / record.fill_price
    won = record.outcome == winner_outcome

    if record.side == "BUY":
        pnl_gross = (
            (1.0 - record.fill_price) * qty_tokens if won else -record.fill_size_usd
        )
    else:  # SELL
        pnl_gross = (
            (record.fill_price - 1.0) * qty_tokens if won else record.fill_size_usd
        )

    pnl_net = pnl_gross - record.fill_fee_usd
    hold_duration = resolved_at - record.signal_time

    from dataclasses import replace

    return replace(
        record,
        resolution=winner_outcome,  # type: ignore[arg-type]
        pnl_gross=pnl_gross,
        pnl_net=pnl_net,
        hold_duration_s=max(hold_duration, 0.0),
        resolved_at=resolved_at,
    )
