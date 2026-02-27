"""PaperExecutor — paper-trading executor with outcome-specific orderbook pricing."""

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
    """Executor that simulates fills using real orderbook prices.

    Price resolution order (each source is outcome-specific):
    1. WS orderbook snapshot by ``asset_id`` — fastest, already in context
    2. CLOB REST API ``/price?token_id=X&side=Y`` — authoritative fallback
    3. Reject if neither available

    The ``token_map`` maps ``condition_id → {YES: asset_id, NO: asset_id}``
    so we always query the correct outcome token.

    Parameters
    ----------
    ctx:
        Strategy context for WS orderbook lookups.
    clob_client:
        CLOB REST API client for fallback price lookups.
        None disables API fallback (WS-only mode).
    token_map:
        Mapping of ``condition_id`` → ``{YES: asset_id, NO: asset_id}``.
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
        """Resolve the asset_id for the intent's outcome (YES or NO).

        Never fall back to a different outcome — returning the YES asset_id
        for a NO intent causes fills at the wrong price.
        """
        if intent.asset_id:
            return intent.asset_id
        tokens = self._token_map.get(intent.condition_id)
        if tokens:
            return tokens.get(intent.outcome)
        return None

    async def execute(self, intent: TradeIntent) -> Fill:
        """Fill at outcome-specific orderbook price or reject.

        The WS ``price_change`` events already include cross-token matched
        prices per asset_id. We look up the intent's outcome token directly
        — no YES/NO flipping needed.
        """
        market_price: float | None = None
        price_source = "none"
        asset_id = self._resolve_asset_id(intent)

        # --- Source 1: WS orderbook snapshot (by asset_id) ---
        if asset_id is not None and hasattr(self._ctx, "get_orderbook_by_asset"):
            ob = self._ctx.get_orderbook_by_asset(asset_id)
            if ob is not None:
                price_val = ob.best_ask if intent.side == "BUY" else ob.best_bid
                if price_val > 0:
                    market_price = price_val
                    price_source = "ws_orderbook"

        # --- Source 2: CLOB REST API /price (fallback) ---
        if market_price is None and self._clob is not None and asset_id is not None:
            try:
                resp = await self._clob._client.get(
                    "/price",
                    params={"token_id": asset_id, "side": intent.side},
                )
                resp.raise_for_status()
                price_str = resp.json().get("price")
                if price_str:
                    price_val = float(price_str)
                    if price_val > 0:
                        market_price = price_val
                        price_source = "clob_api"
            except Exception:
                logger.debug(
                    "paper_fill.api_price_failed",
                    condition_id=intent.condition_id,
                )

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
