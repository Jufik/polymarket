"""Promotion gate checker — validates conditions for ExecutionMode transitions.

Allowed transitions:
  vectorized → paper_dev → paper_prod → live

Each transition has gate conditions that must be met (configurable thresholds).
The checker reads evidence from a LedgerBackend and returns a structured report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from polymarket_pipeline.strategies.ledger.analytics import LedgerSummary, compute_summary
from polymarket_pipeline.strategies.ledger.base import LedgerBackend
from polymarket_pipeline.strategies.ledger.types import LedgerRecord
from polymarket_pipeline.strategies.types import ExecutionMode, FillStatus

logger = structlog.get_logger(__name__)

# Allowed transitions (from → to)
_ALLOWED_TRANSITIONS: dict[ExecutionMode, ExecutionMode] = {
    ExecutionMode.VECTORIZED: ExecutionMode.PAPER_DEV,
    ExecutionMode.REPLAY: ExecutionMode.PAPER_DEV,
    ExecutionMode.PAPER_DEV: ExecutionMode.PAPER_PROD,
    ExecutionMode.PAPER_PROD: ExecutionMode.LIVE,
}


@dataclass(frozen=True)
class GateResult:
    """Result of checking a single promotion gate."""

    gate_name: str
    passed: bool
    required: str
    actual: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromotionReport:
    """Full report from checking all gates for a mode transition."""

    strategy: str
    from_mode: ExecutionMode
    to_mode: ExecutionMode
    gates: list[GateResult]
    all_passed: bool
    summary: LedgerSummary | None = None


@dataclass(frozen=True)
class PromotionThresholds:
    """Configurable thresholds for promotion gates."""

    # vectorized/replay → paper_dev
    min_trades: int = 1000
    min_sharpe: float = 0.5

    # paper_dev → paper_prod
    min_paper_fills: int = 50
    min_runtime_hours: float = 24.0

    # paper_prod → live
    min_live_days: int = 7
    max_drawdown: float = 0.20
    manual_signoff: bool = True


class PromotionChecker:
    """Checks gate conditions for ExecutionMode transitions.

    Parameters
    ----------
    ledger:
        Ledger backend to read evidence from.
    thresholds:
        Configurable gate thresholds.
    """

    def __init__(
        self,
        ledger: LedgerBackend,
        thresholds: PromotionThresholds | None = None,
    ) -> None:
        self._ledger = ledger
        self._thresholds = thresholds or PromotionThresholds()

    async def check(
        self,
        strategy: str,
        from_mode: ExecutionMode,
        to_mode: ExecutionMode,
        *,
        force: bool = False,
    ) -> PromotionReport:
        """Check all gates for the requested transition.

        Parameters
        ----------
        strategy:
            Strategy name to check.
        from_mode:
            Current execution mode.
        to_mode:
            Target execution mode.
        force:
            Override manual_signoff gate.

        Raises
        ------
        ValueError:
            If the transition is not allowed.
        """
        expected_to = _ALLOWED_TRANSITIONS.get(from_mode)
        if expected_to != to_mode:
            msg = (
                f"Invalid transition: {from_mode.value} → {to_mode.value}. "
                f"Allowed: {from_mode.value} → {expected_to.value if expected_to else 'none'}"
            )
            raise ValueError(msg)

        records = await self._ledger.read_all(strategy=strategy)
        filled = [r for r in records if r.fill_status == FillStatus.FILLED]
        summary = compute_summary(records)

        if to_mode == ExecutionMode.PAPER_DEV:
            gates = self._check_to_paper_dev(filled, summary)
        elif to_mode == ExecutionMode.PAPER_PROD:
            gates = self._check_to_paper_prod(filled, summary)
        elif to_mode == ExecutionMode.LIVE:
            gates = self._check_to_live(filled, summary, force=force)
        else:
            gates = []

        all_passed = all(g.passed for g in gates)

        logger.info(
            "promotion.check",
            strategy=strategy,
            from_mode=from_mode.value,
            to_mode=to_mode.value,
            all_passed=all_passed,
            gates_passed=sum(1 for g in gates if g.passed),
            gates_total=len(gates),
        )

        return PromotionReport(
            strategy=strategy,
            from_mode=from_mode,
            to_mode=to_mode,
            gates=gates,
            all_passed=all_passed,
            summary=summary,
        )

    def _check_to_paper_dev(
        self,
        filled: list[LedgerRecord],
        summary: LedgerSummary,
    ) -> list[GateResult]:
        """Gates: min fills, positive PnL, Sharpe threshold."""
        t = self._thresholds
        gates: list[GateResult] = []

        # Min trades
        gates.append(
            GateResult(
                gate_name="min_trades",
                passed=len(filled) >= t.min_trades,
                required=f">= {t.min_trades} fills",
                actual=f"{len(filled)} fills",
            )
        )

        # Positive PnL
        gates.append(
            GateResult(
                gate_name="positive_pnl",
                passed=summary.total_pnl_net > 0,
                required="PnL > $0",
                actual=f"${summary.total_pnl_net:,.2f}",
            )
        )

        # Sharpe
        gates.append(
            GateResult(
                gate_name="min_sharpe",
                passed=summary.sharpe >= t.min_sharpe,
                required=f"Sharpe >= {t.min_sharpe:.1f}",
                actual=f"{summary.sharpe:.2f}",
            )
        )

        return gates

    def _check_to_paper_prod(
        self,
        filled: list[LedgerRecord],
        summary: LedgerSummary,
    ) -> list[GateResult]:
        """Gates: min fills, runtime hours."""
        t = self._thresholds
        gates: list[GateResult] = []

        # Min fills
        gates.append(
            GateResult(
                gate_name="min_paper_fills",
                passed=len(filled) >= t.min_paper_fills,
                required=f">= {t.min_paper_fills} fills",
                actual=f"{len(filled)} fills",
            )
        )

        # Runtime hours
        if filled:
            times = [r.signal_time for r in filled]
            runtime_h = (max(times) - min(times)) / 3600.0
        else:
            runtime_h = 0.0

        gates.append(
            GateResult(
                gate_name="min_runtime",
                passed=runtime_h >= t.min_runtime_hours,
                required=f">= {t.min_runtime_hours:.0f}h runtime",
                actual=f"{runtime_h:.1f}h",
            )
        )

        return gates

    def _check_to_live(
        self,
        filled: list[LedgerRecord],
        summary: LedgerSummary,
        *,
        force: bool = False,
    ) -> list[GateResult]:
        """Gates: min days, positive PnL, max drawdown, manual signoff."""
        t = self._thresholds
        gates: list[GateResult] = []

        # Min days
        if filled:
            times = [r.signal_time for r in filled]
            runtime_days = (max(times) - min(times)) / 86400.0
        else:
            runtime_days = 0.0

        gates.append(
            GateResult(
                gate_name="min_live_days",
                passed=runtime_days >= t.min_live_days,
                required=f">= {t.min_live_days}d runtime",
                actual=f"{runtime_days:.1f}d",
            )
        )

        # Positive PnL
        gates.append(
            GateResult(
                gate_name="realized_pnl",
                passed=summary.total_pnl_net > 0,
                required="PnL > $0",
                actual=f"${summary.total_pnl_net:,.2f}",
            )
        )

        # Max drawdown
        gates.append(
            GateResult(
                gate_name="max_drawdown",
                passed=summary.max_drawdown <= t.max_drawdown,
                required=f"drawdown <= ${t.max_drawdown:,.2f}",
                actual=f"${summary.max_drawdown:,.2f}",
            )
        )

        # Manual signoff
        if t.manual_signoff:
            gates.append(
                GateResult(
                    gate_name="manual_signoff",
                    passed=force,
                    required="--force flag required",
                    actual="--force" if force else "not provided",
                )
            )

        return gates
