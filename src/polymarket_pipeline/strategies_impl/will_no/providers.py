"""Feature providers for the will-no strategy."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from polymarket_pipeline.strategies.types import MarketInfo

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)


class WillMarketProvider:
    """Pre-filters market metadata to 'Will' binary questions.

    At startup, loads markets from the backend, filters to questions
    matching the "Will" pattern, and builds a ``dict[str, MarketInfo]``
    keyed by condition_id.

    Parameters
    ----------
    question_pattern:
        Regex pattern to match "Will" questions.
    """

    name: str = "will_markets"

    def __init__(self, question_pattern: str = r"^Will\b") -> None:
        self._pattern = re.compile(question_pattern, re.IGNORECASE)
        self._markets: dict[str, MarketInfo] = {}

    async def compute(self, backend: FeatureBackend) -> None:
        markets_df = await backend.query_markets()

        if markets_df.is_empty():
            self._markets = {}
            logger.info("will_markets.compute", count=0)
            return

        result: dict[str, MarketInfo] = {}
        for row in markets_df.iter_rows(named=True):
            question = row.get("question", "")
            condition_id = row.get("condition_id", "")

            if not self._pattern.search(question):
                continue

            result[condition_id] = MarketInfo(
                condition_id=condition_id,
                question=question,
                active=bool(row.get("active", True)),
                yes_price=row.get("yes_price"),
                no_price=row.get("no_price"),
                event_id=row.get("event_id"),
                category=row.get("category"),
            )

        self._markets = result
        logger.info("will_markets.compute", count=len(self._markets))

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — market set is refreshed periodically."""

    async def refresh(self, backend: FeatureBackend) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {"will_markets": self._markets}
