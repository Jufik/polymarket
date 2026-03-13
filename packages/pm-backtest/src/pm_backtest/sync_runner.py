"""SyncReplayRunner -- zero-async replay for maximum throughput.

Drops all coroutine overhead from the replay hot path. Every await in the
original ReplayRunner calls a sync operation (dict lookup, list append) --
this version calls them directly.

Strategies can implement an optional ``on_trade_sync()`` method. If absent,
the runner falls back to running the async ``on_trade()`` via a direct
coroutine send (no event loop scheduling overhead).

Usage:
    from pm_backtest.sync_runner import SyncReplayRunner

    runner = SyncReplayRunner(strategy, ctx, gateway, config,
                              resolutions=resolutions, token_map=token_map)
    result = runner.run(ticks)  # sync, no asyncio.run() needed
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import structlog
from pm_strategy.helpers import (
    apply_fill_to_position,
    check_risk_gate,
)
from pm_strategy.types import FillStatus

from pm_backtest.runners.backtest import BacktestResult

if TYPE_CHECKING:
    from pm_strategy.config import StrategyConfig
    from pm_strategy.context.memory import InMemoryContext
    from pm_strategy.execution.gateway import ExecutionGateway
    from pm_strategy.ledger.base import LedgerBackend
    from pm_strategy.protocols import FeatureProvider, Strategy

    from pm_backtest.runners.replay import MarketResolution

logger = structlog.get_logger(__name__)


def _run_coro(coro: Any) -> Any:
    """Run a coroutine synchronously without an event loop.

    For simple coroutines (single await on a dict lookup / list append),
    this avoids all asyncio scheduling overhead.
    """
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    # If the coroutine actually suspends, we can't handle it sync
    raise RuntimeError("Coroutine suspended -- cannot run sync. Use async ReplayRunner instead.")


class SyncReplayRunner:
    """Tick-by-tick replay with zero async overhead.

    Same semantics as ReplayRunner but all calls are synchronous.
    ~2-3x faster per tick due to eliminated coroutine creation/scheduling.
    """

    __slots__ = (
        "_config",
        "_ctx",
        "_gateway",
        "_last_trade_times",
        "_ledger",
        "_providers",
        "_res_timeline",
        "_resolutions",
        "_strategy",
        "_token_map",
        "n_settled",
    )

    def __init__(
        self,
        strategy: Strategy,
        ctx: InMemoryContext,
        gateway: ExecutionGateway,
        config: StrategyConfig | None = None,
        *,
        providers: list[FeatureProvider] | None = None,
        resolutions: dict[str, MarketResolution] | None = None,
        token_map: dict[str, dict[str, str]] | None = None,
        ledger: LedgerBackend | None = None,
    ) -> None:
        self._strategy = strategy
        self._ctx = ctx
        self._gateway = gateway
        self._config = config
        self._providers = providers or []
        self._resolutions = resolutions or {}
        self._token_map = token_map or {}
        self._ledger = ledger
        self._last_trade_times: dict[str, float] = {}
        self.n_settled = 0

        self._res_timeline: list[tuple[float, str]] = sorted(
            (r.resolved_at, cid) for cid, r in self._resolutions.items()
        )

    def run(
        self,
        trades: list[Any],
        *,
        timer_interval_s: float = 3600.0,
    ) -> BacktestResult:
        """Replay trades synchronously."""
        result = BacktestResult()
        sorted_trades = sorted(trades, key=lambda t: t.published_at)
        res_idx = 0
        n_res = len(self._res_timeline)
        next_timer = (
            (sorted_trades[0].published_at + timer_interval_s)
            if sorted_trades and timer_interval_s > 0
            else float("inf")
        )

        # Check if strategy has sync on_trade
        has_sync = hasattr(self._strategy, "on_trade_sync")

        for trade in sorted_trades:
            now = trade.published_at
            self._ctx.set_time(now)

            # 1. Settle resolved markets
            while res_idx < n_res:
                res_time, res_cid = self._res_timeline[res_idx]
                if res_time > now:
                    break
                self._settle_market(res_cid)
                res_idx += 1

            # 1b. Timer callbacks
            while now >= next_timer:
                timer_intents = _run_coro(self._strategy.on_timer(next_timer, self._ctx))
                if timer_intents:
                    for intent in timer_intents:
                        result.total_intents += 1
                        self._execute_intent(intent, next_timer, result)
                next_timer += timer_interval_s

            # 2. Provider hot path
            for provider in self._providers:
                _run_coro(provider.on_trade(trade))
            if self._providers:
                for provider in self._providers:
                    self._ctx.update_features(provider.get_features())

            # 3. Strategy decision
            if has_sync:
                intents = self._strategy.on_trade_sync(trade, self._ctx)
            else:
                intents = _run_coro(self._strategy.on_trade(trade, self._ctx))
            result.total_trades += 1

            if intents is None:
                continue

            for intent in intents:
                result.total_intents += 1
                self._execute_intent(intent, now, result)

        # Settle remaining
        while res_idx < n_res:
            _, res_cid = self._res_timeline[res_idx]
            self._settle_market(res_cid)
            res_idx += 1

        # Warn when settlement rate is low -- likely a universe/resolution coverage gap
        # or a harness bug causing capital to be permanently locked.
        if result.total_fills > 0 and self.n_settled < result.total_fills * 0.5:
            logger.warning(
                "sync_replay.low_settlement_rate",
                n_settled=self.n_settled,
                n_fills=result.total_fills,
                rate=round(self.n_settled / result.total_fills, 3),
                hint="Check universe/resolution coverage -- capital may be permanently locked",
            )

        logger.info(
            "sync_replay_complete",
            total_trades=result.total_trades,
            total_intents=result.total_intents,
            total_fills=result.total_fills,
            settled=self.n_settled,
            rejected=len(result.rejected_intents),
        )
        return result

    def _settle_market(self, condition_id: str) -> None:
        """Settle a resolved market -- identical to ReplayRunner."""
        positions = self._ctx.get_all_positions()
        pos = positions.get(condition_id)
        if pos is None or (pos.qty_yes == 0 and pos.qty_no == 0):
            return

        resolution = self._resolutions.get(condition_id)
        if resolution is None:
            return

        cid_tokens = self._token_map.get(condition_id, {})
        yes_asset = cid_tokens.get("YES", "")
        yes_won = yes_asset in resolution.winning_asset_ids

        pnl_delta = 0.0
        if pos.qty_yes > 0:
            if yes_won:
                pnl_delta += (1.0 - pos.avg_entry_yes) * pos.qty_yes
            else:
                pnl_delta -= pos.avg_entry_yes * pos.qty_yes
        if pos.qty_no > 0:
            if not yes_won:
                pnl_delta += (1.0 - pos.avg_entry_no) * pos.qty_no
            else:
                pnl_delta -= pos.avg_entry_no * pos.qty_no

        new_pos = replace(
            pos,
            qty_yes=0.0,
            qty_no=0.0,
            cost_basis=0.0,
            realized_pnl=pos.realized_pnl + pnl_delta,
        )
        self._ctx.set_position(condition_id, new_pos)
        self.n_settled += 1

        if self._ledger is not None:
            self._enrich_ledger(condition_id, resolution)

    def _enrich_ledger(self, condition_id: str, resolution: MarketResolution) -> None:
        """Enrich ledger records for a settled market."""
        from pm_strategy.ledger.types import LedgerRecord

        buf: list[LedgerRecord] = getattr(self._ledger, "_buffer", [])
        for i, record in enumerate(buf):
            if record.condition_id != condition_id:
                continue
            if record.resolution is not None:
                continue
            if record.fill_status != FillStatus.FILLED or record.fill_price <= 0:
                continue

            won = record.asset_id in resolution.winning_asset_ids
            qty = record.fill_size_usd / record.fill_price
            if record.side == "BUY":
                pnl_gross = (1.0 - record.fill_price) * qty if won else -record.fill_size_usd
            else:
                pnl_gross = (record.fill_price - 1.0) * qty if won else record.fill_size_usd
            pnl_net = pnl_gross - record.fill_fee_usd
            hold_s = max(resolution.resolved_at - record.signal_time, 0.0)

            buf[i] = replace(
                record,
                resolution="WON" if won else "LOST",
                pnl_gross=pnl_gross,
                pnl_net=pnl_net,
                hold_duration_s=hold_s,
                resolved_at=resolution.resolved_at,
            )

    def _execute_intent(self, intent: Any, current_time: float, result: BacktestResult) -> None:
        """Risk gate -> execute -> position update -> ledger (all sync)."""
        if self._config is not None:
            positions = self._ctx.get_all_positions()
            allowed, reason = check_risk_gate(
                intent,
                self._config,
                positions,
                self._last_trade_times,
                current_time,
            )
            if not allowed:
                result.rejected_intents.append((intent, reason))
                return

        fill = _run_coro(self._gateway.submit(intent))
        result.total_fills += 1
        result.fills.append(fill)

        if fill.status == FillStatus.FILLED:
            if self._ledger is not None:
                from pm_strategy.ledger.base import make_ledger_record

                record = make_ledger_record(intent, fill)
                _run_coro(self._ledger.append(record))
                result.ledger_records.append(record)

            old_pos = _run_coro(self._ctx.get_position(fill.condition_id))
            new_pos = apply_fill_to_position(old_pos, fill)
            self._ctx.set_position(fill.condition_id, new_pos)
            self._last_trade_times[intent.strategy] = fill.filled_at
