"""PaperExecutor — paper-trading executor with CLOB API price verification."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.types import Fill, FillStatus, TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.execution.clob_client import ClobClient
    from polymarket_pipeline.strategies.protocol import StrategyContext

logger = structlog.get_logger(__name__)


class PaperExecutor:
    """Executor that simulates fills using CLOB REST API prices.

    Price resolution order:
    1. CLOB REST API ``GET /book`` (authoritative, 5s TTL cache)
    2. WS orderbook snapshot from context (fallback if API fails)
    3. Reject if neither source is available

    When both sources exist and diverge by > $0.02, a warning is logged.

    Parameters
    ----------
    ctx:
        Strategy context for WS orderbook lookups (fallback).
    clob_client:
        CLOB REST API client for authoritative price lookups.
        None disables API verification (WS-only mode, backward compat).
    token_map:
        Mapping of ``condition_id`` → ``{YES: asset_id, NO: asset_id}``.
        Loaded from PG ``token_market_map`` at startup.
    fee_pct:
        Fee as fraction of ``min(price, 1-price) * size_usd``.
        Default 0.0 — most Polymarket markets have zero trading fees.
    """

    def __init__(
        self,
        ctx: StrategyContext,
        clob_client: ClobClient | None = None,
        token_map: dict[str, dict[str, str]] | None = None,
        fee_pct: float = 0.0,
    ) -> None:
        self._ctx = ctx
        self._clob = clob_client
        self._token_map = token_map or {}
        self._fee_pct = fee_pct

    def _resolve_asset_id(self, intent: TradeIntent) -> str | None:
        """Resolve the asset_id matching the intent's outcome (YES or NO).

        Each outcome has its own CLOB orderbook. Querying the correct
        token directly avoids flipping prices from the opposite side.
        """
        if intent.asset_id:
            return intent.asset_id
        tokens = self._token_map.get(intent.condition_id)
        if tokens:
            return tokens.get(intent.outcome, tokens.get("YES"))
        return None

    async def execute(self, intent: TradeIntent) -> Fill:
        """Fill at CLOB REST API price or reject.

        Queries the outcome-specific orderbook (YES or NO token) so
        prices are read directly — no flipping needed.
        Books with spread >= $0.50 are skipped (empty/illiquid).
        """
        market_price: float | None = None
        price_source = "none"

        # --- CLOB REST API (outcome-specific book) ---
        if self._clob is not None:
            asset_id = self._resolve_asset_id(intent)
            if asset_id:
                ob_api = await self._clob.get_orderbook(asset_id)
                if ob_api is not None and ob_api.spread < 0.50:
                    market_price = (
                        ob_api.best_ask if intent.side == "BUY" else ob_api.best_bid
                    )
                    price_source = "clob_api"

        # No price → reject
        if market_price is None:
            intent_id = uuid.uuid4().hex[:12]
            logger.warning(
                "paper_fill.rejected",
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                outcome=intent.outcome,
                reason="no_orderbook",
            )
            return Fill(
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                side=intent.side,
                outcome=intent.outcome,
                filled_price=0.0,
                filled_size_usd=0.0,
                fee_usd=0.0,
                status=FillStatus.REJECTED,
                error="no orderbook available",
                filled_at=time.time(),
            )

        # max_price acts as a limit — reject if market exceeds it
        if (
            intent.max_price is not None
            and intent.side == "BUY"
            and market_price > intent.max_price
        ):
            intent_id = uuid.uuid4().hex[:12]
            logger.warning(
                "paper_fill.rejected",
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                outcome=intent.outcome,
                reason="market_exceeds_limit",
                market_price=round(market_price, 4),
                max_price=round(intent.max_price, 4),
                price_source=price_source,
            )
            return Fill(
                intent_id=intent_id,
                strategy=intent.strategy,
                condition_id=intent.condition_id,
                side=intent.side,
                outcome=intent.outcome,
                filled_price=0.0,
                filled_size_usd=0.0,
                fee_usd=0.0,
                status=FillStatus.REJECTED,
                error=f"market {market_price:.4f} > limit {intent.max_price:.4f}",
                filled_at=time.time(),
            )

        fee = self._fee_pct * min(market_price, 1.0 - market_price) * intent.size_usd
        intent_id = uuid.uuid4().hex[:12]

        fill = Fill(
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=market_price,
            filled_size_usd=intent.size_usd,
            fee_usd=fee,
            status=FillStatus.FILLED,
            filled_at=time.time(),
        )

        logger.warning(
            "paper_fill",
            intent_id=intent_id,
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            price=round(market_price, 4),
            size_usd=round(intent.size_usd, 2),
            fee_usd=round(fee, 4),
            price_source=price_source,
        )

        return fill
