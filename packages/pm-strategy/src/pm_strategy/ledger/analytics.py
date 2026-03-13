"""Aggregate analytics from ledger records."""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from pm_strategy.ledger.types import LedgerRecord
from pm_strategy.types import FillStatus


@dataclass(frozen=True)
class LedgerSummary:
    """Aggregate analytics from a set of ledger records."""

    total_fills: int
    win_count: int
    loss_count: int
    pending_count: int
    hit_rate: float
    total_pnl_net: float
    total_fees: float
    avg_edge: float
    sharpe: float
    max_drawdown: float
    profit_factor: float
    avg_hold_duration_s: float


def compute_summary(records: list[LedgerRecord]) -> LedgerSummary:
    """Compute aggregate analytics from a list of LedgerRecords.

    Only resolved records (resolution is not None) contribute to hit_rate,
    edge, Sharpe, drawdown, and profit_factor. Pending records are counted
    but excluded from performance metrics.
    """
    filled = [r for r in records if r.fill_status == FillStatus.FILLED]
    if not filled:
        return _empty_summary()

    resolved = [r for r in filled if r.resolution is not None]
    pending = [r for r in filled if r.resolution is None]

    total_fees = sum(r.fill_fee_usd for r in filled)

    if not resolved:
        return LedgerSummary(
            total_fills=len(filled),
            win_count=0,
            loss_count=0,
            pending_count=len(pending),
            hit_rate=0.0,
            total_pnl_net=0.0,
            total_fees=total_fees,
            avg_edge=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
            profit_factor=0.0,
            avg_hold_duration_s=0.0,
        )

    # Win/loss classification
    pnls = [r.pnl_net for r in resolved if r.pnl_net is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_count = len(wins)
    loss_count = len(losses)
    resolved_count = win_count + loss_count
    hit_rate = win_count / resolved_count if resolved_count > 0 else 0.0

    total_pnl_net = sum(pnls)
    avg_edge = total_pnl_net / resolved_count if resolved_count > 0 else 0.0

    # Sharpe ratio (annualized by trade frequency)
    sharpe = _compute_sharpe(pnls, resolved)

    # Max drawdown on cumulative PnL curve
    max_drawdown = _compute_max_drawdown(pnls)

    # Profit factor
    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    profit_factor = sum_wins / sum_losses if sum_losses > 0 else float("inf")

    # Average hold duration
    durations = [r.hold_duration_s for r in resolved if r.hold_duration_s is not None]
    avg_hold = sum(durations) / len(durations) if durations else 0.0

    return LedgerSummary(
        total_fills=len(filled),
        win_count=win_count,
        loss_count=loss_count,
        pending_count=len(pending),
        hit_rate=hit_rate,
        total_pnl_net=total_pnl_net,
        total_fees=total_fees,
        avg_edge=avg_edge,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        avg_hold_duration_s=avg_hold,
    )


def records_to_dataframe(records: list[LedgerRecord]) -> pl.DataFrame:
    """Convert LedgerRecords to a Polars DataFrame for ad-hoc analysis."""
    from dataclasses import asdict

    if not records:
        return pl.DataFrame()
    return pl.DataFrame([asdict(r) for r in records])


def _compute_sharpe(pnls: list[float], resolved: list[LedgerRecord]) -> float:
    """Annualized Sharpe ratio.

    Annualization factor is based on average trade frequency:
      trades_per_year = 365 * 86400 / avg_hold_duration_s
      sharpe = mean(pnl) / std(pnl) * sqrt(trades_per_year)
    """
    if len(pnls) < 2:
        return 0.0

    mean_pnl = sum(pnls) / len(pnls)
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
    std_pnl = math.sqrt(variance) if variance > 0 else 0.0

    if std_pnl == 0:
        return 0.0

    durations = [r.hold_duration_s for r in resolved if r.hold_duration_s is not None]
    avg_hold = sum(durations) / len(durations) if durations else 86400.0
    trades_per_year = (365.0 * 86400.0) / max(avg_hold, 1.0)

    return (mean_pnl / std_pnl) * math.sqrt(trades_per_year)


def _compute_max_drawdown(pnls: list[float]) -> float:
    """Peak-to-trough decline on the cumulative PnL curve."""
    if not pnls:
        return 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return max_dd


def _empty_summary() -> LedgerSummary:
    return LedgerSummary(
        total_fills=0,
        win_count=0,
        loss_count=0,
        pending_count=0,
        hit_rate=0.0,
        total_pnl_net=0.0,
        total_fees=0.0,
        avg_edge=0.0,
        sharpe=0.0,
        max_drawdown=0.0,
        profit_factor=0.0,
        avg_hold_duration_s=0.0,
    )
