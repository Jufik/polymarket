"""ReplayRunner — tick-by-tick backtest with mid-replay resolution settlement.

Extends BacktestRunner with:
  - Asset_id-based market resolution (no string matching)
  - Tick-by-tick position settlement (frees capital + open-position slots)
  - Provider hot-path support (on_trade → feature update per tick)
  - Inline ledger enrichment at settlement time

Resolution uses ``MarketResolution.winning_asset_ids`` (set of asset_ids where
``token_won=1``). Position settlement checks ``asset_id in winning_asset_ids``
— never compares outcome strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.runners.backtest import BacktestResult
from polymarket_pipeline.strategies.runners.helpers import (
    apply_fill_to_position,
    check_risk_gate,
)
from polymarket_pipeline.strategies.types import FillStatus

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.ledger.base import LedgerBackend
    from polymarket_pipeline.strategies.ledger.types import LedgerRecord
    from polymarket_pipeline.strategies.protocol import FeatureProvider, Strategy

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MarketResolution:
    """Immutable resolution record for a single market.

    Resolution is asset_id-based: ``winning_asset_ids`` contains the set of
    asset_ids whose tokens pay out $1 at resolution. For binary markets this
    is exactly one asset_id. For multi-outcome markets it may be multiple.
    """

    condition_id: str
    resolved_at: float  # epoch seconds
    winning_asset_ids: frozenset[str] = field(default_factory=frozenset)


class ReplayRunner:
    """Tick-by-tick replay with providers, resolution settlement, and ledger.

    Unlike BacktestRunner (which has no concept of resolution), this runner
    settles positions mid-replay as the simulated clock passes each market's
    ``resolved_at`` timestamp. Settlement zeroes position quantities and
    ``cost_basis``, freeing capital for the risk gate and open-position count.

    Parameters
    ----------
    strategy:
        Event-driven strategy whose ``on_trade`` is called per tick.
    ctx:
        In-memory context (clock, positions, features, orderbooks).
    gateway:
        Execution gateway for intent submission + fill.
    config:
        Strategy config for risk gate. None = no risk gate.
    providers:
        Feature providers — ``on_trade()`` called per tick, features injected.
    resolutions:
        Market resolution data keyed by condition_id.
    token_map:
        ``condition_id → {"YES": asset_id, "NO": asset_id}`` mapping.
    ledger:
        Optional ledger backend for PnL tracking.
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

        # Pre-sort resolution timeline for O(1) per-tick scanning
        self._res_timeline: list[tuple[float, str]] = sorted(
            (r.resolved_at, cid) for cid, r in self._resolutions.items()
        )

    async def run(self, trades: list[NormalizedTrade]) -> BacktestResult:
        """Replay trades in timestamp order with tick-by-tick resolution."""
        result = BacktestResult()
        sorted_trades = sorted(trades, key=lambda t: t.published_at)
        res_idx = 0
        n_res = len(self._res_timeline)

        for trade in sorted_trades:
            now = trade.published_at
            self._ctx.set_time(now)

            # ── 1. Settle markets that resolved before this tick ────────
            while res_idx < n_res:
                res_time, res_cid = self._res_timeline[res_idx]
                if res_time > now:
                    break
                self._settle_market(res_cid)
                res_idx += 1

            # ── 2. Provider hot path ────────────────────────────────────
            for provider in self._providers:
                await provider.on_trade(trade)
            if self._providers:
                for provider in self._providers:
                    self._ctx.update_features(provider.get_features())

            # ── 3. Strategy decision ────────────────────────────────────
            intents = await self._strategy.on_trade(trade, self._ctx)
            result.total_trades += 1

            if intents is None:
                continue

            for intent in intents:
                result.total_intents += 1
                await self._execute_intent(intent, now, result)

        # ── Settle any markets that resolved after last trade ───────────
        while res_idx < n_res:
            _, res_cid = self._res_timeline[res_idx]
            self._settle_market(res_cid)
            res_idx += 1

        logger.info(
            "replay_complete",
            total_trades=result.total_trades,
            total_intents=result.total_intents,
            total_fills=result.total_fills,
            settled=self.n_settled,
            rejected=len(result.rejected_intents),
        )
        return result

    def _settle_market(self, condition_id: str) -> None:
        """Settle a resolved market — asset_id-based, no string matching."""
        positions = self._ctx.get_all_positions()
        pos = positions.get(condition_id)
        if pos is None or (pos.qty_yes == 0 and pos.qty_no == 0):
            return

        resolution = self._resolutions.get(condition_id)
        if resolution is None:
            return

        # Determine which side won via asset_id lookup
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

        # Enrich matching ledger records inline
        if self._ledger is not None:
            self._enrich_ledger(condition_id, resolution)

    def _enrich_ledger(
        self, condition_id: str, resolution: MarketResolution
    ) -> None:
        """Enrich ledger records for a settled market using asset_id matching."""
        # Access buffer directly (ParquetLedger stores records in _buffer)
        buf: list[LedgerRecord] = getattr(self._ledger, "_buffer", [])
        for i, record in enumerate(buf):
            if record.condition_id != condition_id:
                continue
            if record.resolution is not None:
                continue
            if record.fill_status != FillStatus.FILLED or record.fill_price <= 0:
                continue

            # Asset_id-based win check (no string comparison)
            won = record.asset_id in resolution.winning_asset_ids

            qty = record.fill_size_usd / record.fill_price
            if record.side == "BUY":
                pnl_gross = (
                    (1.0 - record.fill_price) * qty if won else -record.fill_size_usd
                )
            else:
                pnl_gross = (
                    (record.fill_price - 1.0) * qty if won else record.fill_size_usd
                )
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

    async def _execute_intent(
        self, intent: Any, current_time: float, result: BacktestResult
    ) -> None:
        """Risk gate → execute → position update → ledger."""
        if self._config is not None:
            positions = self._ctx.get_all_positions()
            allowed, reason = check_risk_gate(
                intent, self._config, positions,
                self._last_trade_times, current_time,
            )
            if not allowed:
                result.rejected_intents.append((intent, reason))
                return

        fill = await self._gateway.submit(intent)
        result.total_fills += 1
        result.fills.append(fill)

        if fill.status == FillStatus.FILLED:
            # Ledger
            if self._ledger is not None:
                from polymarket_pipeline.strategies.ledger.base import make_ledger_record

                record = make_ledger_record(intent, fill)
                await self._ledger.append(record)
                result.ledger_records.append(record)

            # Position update
            old_pos = await self._ctx.get_position(fill.condition_id)
            new_pos = apply_fill_to_position(old_pos, fill)
            self._ctx.set_position(fill.condition_id, new_pos)
            self._last_trade_times[intent.strategy] = fill.filled_at


def load_resolutions_from_rows(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, MarketResolution], dict[str, dict[str, str]]]:
    """Build resolution + token_map dicts from CH ``markets_resolved`` rows.

    Each row must have: condition_id, asset_id, outcome, token_won, resolved_epoch.

    Returns
    -------
    (resolutions, token_map)
        resolutions: cid → MarketResolution
        token_map: cid → {"YES": asset_id, "NO": asset_id}
    """
    from collections import defaultdict

    winning: dict[str, set[str]] = defaultdict(set)
    resolved_at: dict[str, float] = {}
    token_map: dict[str, dict[str, str]] = {}

    for row in rows:
        cid = str(row["condition_id"])
        asset_id = str(row["asset_id"])
        outcome = str(row["outcome"])
        token_won = int(row["token_won"])
        epoch = float(row.get("resolved_epoch", 0))

        if token_won == 1:
            winning[cid].add(asset_id)
        if epoch > 0:
            resolved_at[cid] = epoch
        if cid not in token_map:
            token_map[cid] = {}
        token_map[cid][outcome] = asset_id

    resolutions: dict[str, MarketResolution] = {}
    for cid, winners in winning.items():
        epoch = resolved_at.get(cid, 0.0)
        if epoch > 0:
            resolutions[cid] = MarketResolution(
                condition_id=cid,
                resolved_at=epoch,
                winning_asset_ids=frozenset(winners),
            )

    return resolutions, token_map
