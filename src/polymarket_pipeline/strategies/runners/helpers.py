"""Shared runner helpers -- position math and risk gate."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import structlog

from polymarket_pipeline.strategies.types import Fill, Position

_log = structlog.get_logger()

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import TradeIntent


def apply_fill_to_position(old: Position | None, fill: Fill) -> Position:
    """Update a position based on a fill.

    BUY YES/NO: increment qty, update weighted avg_entry_yes/no, add to cost_basis.
    SELL YES/NO: decrement qty, compute realized_pnl delta using per-side avg.

    Since :class:`Position` is frozen, a **new** instance is returned each time.
    """
    if old is None:
        old = Position(condition_id=fill.condition_id, strategy=fill.strategy)

    added_qty = fill.filled_size_usd / fill.filled_price

    if fill.side == "BUY":
        if fill.outcome == "YES":
            old_qty = old.qty_yes
            new_qty = old_qty + added_qty
            new_avg = (
                (old.avg_entry_yes * old_qty + fill.filled_price * added_qty) / new_qty
                if new_qty > 0
                else 0.0
            )
            return replace(
                old,
                qty_yes=new_qty,
                avg_entry_yes=new_avg,
                cost_basis=old.cost_basis + fill.filled_size_usd + fill.fee_usd,
            )
        # BUY NO
        old_qty = old.qty_no
        new_qty = old_qty + added_qty
        new_avg = (
            (old.avg_entry_no * old_qty + fill.filled_price * added_qty) / new_qty
            if new_qty > 0
            else 0.0
        )
        return replace(
            old,
            qty_no=new_qty,
            avg_entry_no=new_avg,
            cost_basis=old.cost_basis + fill.filled_size_usd + fill.fee_usd,
        )

    # SELL
    sold_qty = fill.filled_size_usd / fill.filled_price
    if fill.outcome == "YES":
        old_qty = old.qty_yes
        if sold_qty > old_qty:
            _log.warning("position.oversell", condition_id=old.condition_id,
                         sold=round(sold_qty, 4), held=round(old_qty, 4),
                         outcome=fill.outcome)
        new_qty = max(old_qty - sold_qty, 0.0)
        pnl_delta = (fill.filled_price - old.avg_entry_yes) * sold_qty - fill.fee_usd
        return replace(
            old,
            qty_yes=new_qty,
            realized_pnl=old.realized_pnl + pnl_delta,
        )
    # SELL NO
    old_qty = old.qty_no
    if sold_qty > old_qty:
        _log.warning("position.oversell", condition_id=old.condition_id,
                     sold=round(sold_qty, 4), held=round(old_qty, 4),
                     outcome=fill.outcome)
    new_qty = max(old_qty - sold_qty, 0.0)
    pnl_delta = (fill.filled_price - old.avg_entry_no) * sold_qty - fill.fee_usd
    return replace(
        old,
        qty_no=new_qty,
        realized_pnl=old.realized_pnl + pnl_delta,
    )


def check_risk_gate(
    intent: TradeIntent,
    config: StrategyConfig,
    positions: dict[str, Position],
    last_trade_times: dict[str, float],
    now: float,
) -> tuple[bool, str]:
    """Check if a trade intent passes risk limits.

    Returns ``(allowed, reason)``.  When *allowed* is ``True``, *reason* is
    the empty string.
    """
    # 1. Capital check: total cost_basis + intent size <= capital_usd
    total_cost = sum(p.cost_basis for p in positions.values())
    if total_cost + intent.size_usd > config.capital_usd:
        return (False, "capital_exceeded")

    # 2. Per-position check: this market's cost_basis + intent size <= max_position_usd
    market_cost = (
        positions[intent.condition_id].cost_basis if intent.condition_id in positions else 0.0
    )
    if market_cost + intent.size_usd > config.max_position_usd:
        return (False, "position_limit")

    # 3. Open-positions check
    open_count = sum(1 for p in positions.values() if p.qty_yes > 0 or p.qty_no > 0)
    mkt_pos = positions.get(intent.condition_id)
    already_open = mkt_pos is not None and (mkt_pos.qty_yes > 0 or mkt_pos.qty_no > 0)
    if not already_open and open_count + 1 > config.max_open_positions:
        return (False, "max_open_positions")

    # 4. Cooldown
    if (
        intent.strategy in last_trade_times
        and now - last_trade_times[intent.strategy] < config.cooldown_s
    ):
        return (False, "cooldown")

    return (True, "")
