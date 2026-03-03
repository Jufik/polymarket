"""S3 NO Sniper — FeatureProvider for tag map, token map, and known markets.

Loads condition_id -> tag mapping and token_market_map from CH (PG-replicated tables)
at startup and on refresh. Also loads known market IDs at startup to prevent false
first-5-minute entries on pre-existing markets.

Required ClickHouse objects (PG-replicated via table engine):
    - markets, events, event_tags, tags
    - token_market_map
    - trades_raw

Features published to context:
    tag_map          dict[condition_id -> tag_label]  (only eligible tags)
    token_map        dict[condition_id -> {YES: asset_id, NO: asset_id}]
    known_market_ids frozenset[condition_id]  (markets with trades before startup)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import FeatureBackend

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# SQL: tag map from PG-replicated tables (same join chain as market_susceptibility)
# ---------------------------------------------------------------------------

_TAG_MAP_SQL = """\
SELECT m.condition_id, t.label AS tag
FROM markets m
JOIN events e ON m.event_id = e.id
JOIN event_tags et ON e.id = et.event_id
JOIN tags t ON et.tag_id = t.id
WHERE t.label IN ({tag_list})
"""

_TOKEN_MAP_SQL = """\
SELECT asset_id, condition_id, outcome
FROM token_market_map
"""

_KNOWN_MARKETS_SQL = """\
SELECT DISTINCT tr.condition_id
FROM trades_raw tr
WHERE tr.condition_id IN (
    SELECT m.condition_id
    FROM markets m
    JOIN events e ON m.event_id = e.id
    JOIN event_tags et ON e.id = et.event_id
    JOIN tags t ON et.tag_id = t.id
    WHERE t.label IN ({tag_list})
)
"""


class S3DataProvider:
    """Tag map + token map + known market IDs for S3 NO Sniper.

    Lifecycle:
        compute()   -> load tag_map + token_map + known_market_ids from CH (startup)
        on_trade()  -> no-op (stateless provider)
        refresh()   -> reload tag_map + token_map (catches new markets)
                       DO NOT reload known_market_ids (only want pre-startup markets)
        get_features() -> return tag_map + token_map + known_market_ids
    """

    name: str = "s3_data_provider"

    def __init__(
        self,
        eligible_tags: list[str] | None = None,
    ) -> None:
        self._eligible_tags = eligible_tags or ["Economy", "Tech"]
        self._tag_map: dict[str, str] = {}
        self._token_map: dict[str, dict[str, str]] = {}
        self._known_market_ids: frozenset[str] = frozenset()
        self._known_markets_loaded: bool = False

    async def compute(self, backend: FeatureBackend) -> None:
        """Initial load from ClickHouse."""
        await self._load_tag_map(backend)
        await self._load_token_map(backend)
        await self._load_known_markets(backend)

    async def refresh(self, backend: FeatureBackend) -> None:
        """Periodic refresh — reload tag_map + token_map (catches new markets).

        DO NOT reload known_market_ids — we only want markets that existed
        before startup. New markets discovered after startup are genuinely new.
        """
        await self._load_tag_map(backend)
        await self._load_token_map(backend)

    async def on_trade(self, trade: NormalizedTrade) -> None:
        """No-op — stateless provider."""

    def get_features(self) -> dict[str, Any]:
        return {
            self.name: {
                "tag_map": self._tag_map,
                "token_map": self._token_map,
                "known_market_ids": self._known_market_ids,
                "known_markets_loaded": self._known_markets_loaded,
            },
        }

    # --- internal loaders ---

    async def _load_tag_map(self, backend: FeatureBackend) -> None:
        tag_list = ", ".join(f"'{t}'" for t in self._eligible_tags)
        sql = _TAG_MAP_SQL.format(tag_list=tag_list)
        try:
            df = await backend.query_custom(sql)
            tag_map: dict[str, str] = {}
            for row in df.iter_rows(named=True):
                tag_map[row["condition_id"]] = row["tag"]
            self._tag_map = tag_map
            logger.info(
                "s3_data_provider.tag_map_loaded",
                count=len(tag_map),
                tags=self._eligible_tags,
            )
        except Exception:
            logger.exception("s3_data_provider.tag_map_load_failed")

    async def _load_token_map(self, backend: FeatureBackend) -> None:
        try:
            df = await backend.query_custom(_TOKEN_MAP_SQL)
            token_map: dict[str, dict[str, str]] = {}
            for row in df.iter_rows(named=True):
                cid = row["condition_id"]
                if cid not in token_map:
                    token_map[cid] = {}
                token_map[cid][row["outcome"]] = row["asset_id"]
            self._token_map = token_map
            logger.info(
                "s3_data_provider.token_map_loaded",
                count=len(token_map),
            )
        except Exception:
            logger.exception("s3_data_provider.token_map_load_failed")

    async def _load_known_markets(self, backend: FeatureBackend) -> None:
        """Load condition_ids that already have trades — these are NOT new markets.

        Uses a subquery JOIN instead of an IN clause to avoid HTTP 400 from
        ClickHouse when the tag_map contains thousands of condition_ids.
        """
        tag_list = ", ".join(f"'{t}'" for t in self._eligible_tags)
        sql = _KNOWN_MARKETS_SQL.format(tag_list=tag_list)
        try:
            df = await backend.query_custom(sql)
            known = frozenset(row["condition_id"] for row in df.iter_rows(named=True))
            self._known_market_ids = known
            self._known_markets_loaded = True
            logger.info(
                "s3_data_provider.known_markets_loaded",
                count=len(known),
            )
        except Exception:
            logger.exception("s3_data_provider.known_markets_load_failed")
            self._known_market_ids = frozenset()
            self._known_markets_loaded = False
