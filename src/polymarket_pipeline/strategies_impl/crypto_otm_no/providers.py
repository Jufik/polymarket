"""Feature providers for the crypto-otm-no strategy."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.types import MarketInfo
from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import _ASSET_MAP

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class CryptoMarketProvider:
    """Pre-filters market metadata to crypto price checkpoint markets.

    At startup, loads markets from the backend, filters to crypto above/below
    markets, and builds a ``dict[str, MarketInfo]`` keyed by condition_id.
    Strategy's ``on_trade`` calls ``ctx.get_features("crypto_markets")`` to
    avoid per-trade regex overhead.

    Parameters
    ----------
    question_pattern:
        Regex pattern to match against market question text.
    assets:
        Set of supported crypto tickers (e.g. ``{"BTC", "ETH"}``).
    """

    name: str = "crypto_markets"

    def __init__(
        self,
        question_pattern: str = r"(above|below)",
        assets: frozenset[str] | None = None,
    ) -> None:
        self._pattern = re.compile(question_pattern, re.IGNORECASE)
        self._assets = assets or frozenset({"BTC", "ETH", "SOL", "XRP"})
        self._markets: dict[str, MarketInfo] = {}

    async def compute(self, backend: FeatureBackend) -> None:
        """Batch compute eligible crypto markets from historical metadata."""
        markets_df = await backend.query_markets()

        if markets_df.is_empty():
            self._markets = {}
            logger.info("crypto_markets.compute", count=0)
            return

        result: dict[str, MarketInfo] = {}
        for row in markets_df.iter_rows(named=True):
            category = row.get("category")
            question = row.get("question", "")
            condition_id = row.get("condition_id", "")

            if not category or "crypto" not in category.lower():
                continue

            if not self._pattern.search(question):
                continue

            # Extract asset from first word
            first_word = question.split()[0].lower() if question else ""
            ticker = _ASSET_MAP.get(first_word)
            if ticker is None or ticker not in self._assets:
                continue

            result[condition_id] = MarketInfo(
                condition_id=condition_id,
                question=question,
                active=bool(row.get("active", True)),
                yes_price=row.get("yes_price"),
                no_price=row.get("no_price"),
                event_id=row.get("event_id"),
                category=category,
            )

        self._markets = result
        logger.info("crypto_markets.compute", count=len(self._markets))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — market set is refreshed periodically, not per-trade."""

    async def refresh(self, backend: FeatureBackend) -> None:
        """Re-query and atomically swap the market set."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        """Return ``{"crypto_markets": dict[str, MarketInfo]}``."""
        return {"crypto_markets": self._markets}
