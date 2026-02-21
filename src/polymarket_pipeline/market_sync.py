"""Gamma API + CLOB API syncer — fetches events, markets, and resolution data."""

from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass, field

import httpx
import structlog

from polymarket_pipeline.models import Event, Market, Tag, TokenMarketEntry

log = structlog.get_logger()

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_PAGE_SIZE = 500


class SyncResult:
    """Result of a full event + market + tag sync."""

    __slots__ = ("events", "markets", "token_entries", "tags", "event_tag_pairs")

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.markets: list[Market] = []
        self.token_entries: list[TokenMarketEntry] = []
        self.tags: list[Tag] = []
        self.event_tag_pairs: list[tuple[int, int]] = []  # (event_id, tag_id)


@dataclass
class ClobResolution:
    """Resolution data for a single market from CLOB API."""

    resolution_value: int  # 1=resolved, 0=unresolved, -1=voided
    winner_outcome: str  # "Yes" or "No" or ""
    token_winners: dict[str, bool] = field(default_factory=dict)  # asset_id -> winner


async def fetch_clob_resolution() -> dict[str, ClobResolution]:
    """Fetch all markets from CLOB API and extract resolution data.

    Returns:
        Dict mapping condition_id -> ClobResolution.
    """
    resolutions: dict[str, ClobResolution] = {}

    async with httpx.AsyncClient(timeout=60) as client:
        cursor: str | None = None
        page_num = 0

        while True:
            params: dict[str, str | int] = {"limit": CLOB_PAGE_SIZE}
            if cursor is not None:
                params["next_cursor"] = cursor

            resp = await client.get(f"{CLOB_API_BASE}/markets", params=params)
            resp.raise_for_status()
            body = resp.json()

            data = body.get("data", [])
            if not data:
                break

            for m in data:
                condition_id = m.get("condition_id", "")
                if not condition_id:
                    continue

                tokens = m.get("tokens", [])
                if len(tokens) < 2:
                    continue

                closed = bool(m.get("closed", False))
                winners = [t for t in tokens if t.get("winner") is True]

                if closed and len(winners) == 1:
                    resolution_value = 1
                    winner_outcome = winners[0].get("outcome", "")
                elif closed and len(winners) == 0:
                    resolution_value = -1
                    winner_outcome = ""
                else:
                    resolution_value = 0
                    winner_outcome = ""

                token_winners = {}
                for t in tokens:
                    token_id = t.get("token_id", "")
                    if token_id:
                        token_winners[token_id] = bool(t.get("winner", False))

                resolutions[condition_id] = ClobResolution(
                    resolution_value=resolution_value,
                    winner_outcome=winner_outcome,
                    token_winners=token_winners,
                )

            next_cursor = body.get("next_cursor", "")
            if not next_cursor:
                break

            try:
                decoded = b64decode(next_cursor).decode()
                if decoded == "-1":
                    break
            except Exception:
                pass

            cursor = next_cursor
            page_num += 1

            if page_num % 20 == 0:
                log.info(
                    "clob_resolution_progress",
                    pages=page_num,
                    markets=len(resolutions),
                )

    log.info("clob_resolution_complete", markets=len(resolutions))
    return resolutions


async def fetch_events(
    limit: int = 0,
    *,
    fetch_resolution: bool = True,
) -> SyncResult:
    """Fetch events from Gamma API and optionally enrich with CLOB resolution data.

    Uses the /events endpoint which returns embedded markets and tags per event.
    When fetch_resolution=True, also fetches CLOB /markets for tokens[].winner.

    Args:
        limit: Max events to fetch. 0 = fetch all.
        fetch_resolution: If True, also fetch CLOB API for resolution data.

    Returns:
        SyncResult with events, markets, token_entries, tags, and event_tag_pairs.
    """
    result = SyncResult()
    seen_tags: dict[int, Tag] = {}
    offset = 0
    page_size = 500

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{GAMMA_API_BASE}/events",
                params={"limit": page_size, "offset": offset},
            )
            resp.raise_for_status()
            page = resp.json()

            if not page:
                break

            for raw_event in page:
                event = Event.from_gamma(raw_event)
                if event is None:
                    continue
                result.events.append(event)

                # Parse embedded tags
                for raw_tag in raw_event.get("tags", []):
                    tag = Tag.from_gamma(raw_tag)
                    if tag is None:
                        continue
                    if tag.id not in seen_tags:
                        seen_tags[tag.id] = tag
                    result.event_tag_pairs.append((event.id, tag.id))

                # Parse embedded markets
                for raw_market in raw_event.get("markets", []):
                    market = Market.from_gamma(raw_market, event_id=event.id)
                    if market is None:
                        continue
                    result.markets.append(market)
                    result.token_entries.append(
                        TokenMarketEntry(
                            asset_id=market.token_yes,
                            condition_id=market.condition_id,
                            outcome="YES",
                        )
                    )
                    result.token_entries.append(
                        TokenMarketEntry(
                            asset_id=market.token_no,
                            condition_id=market.condition_id,
                            outcome="NO",
                        )
                    )

            offset += page_size

            if 0 < limit <= offset:
                break

            log.debug(
                "gamma_events_fetched",
                offset=offset,
                events=len(result.events),
                markets=len(result.markets),
            )

    result.tags = list(seen_tags.values())
    log.info(
        "events_fetched",
        total_events=len(result.events),
        total_markets=len(result.markets),
        total_tokens=len(result.token_entries),
        total_tags=len(result.tags),
    )

    # Enrich with CLOB resolution data
    if fetch_resolution:
        resolutions = await fetch_clob_resolution()
        enriched = 0

        # Enrich markets
        result.markets = [
            _enrich_market(m, resolutions.get(m.condition_id))
            for m in result.markets
        ]

        # Enrich token entries
        result.token_entries = [
            _enrich_token(t, resolutions.get(t.condition_id))
            for t in result.token_entries
        ]

        enriched = sum(1 for m in result.markets if m.resolution_value != 0)
        log.info(
            "resolution_enrichment_complete",
            total_markets=len(result.markets),
            enriched=enriched,
            clob_markets=len(resolutions),
        )

    return result


def _enrich_market(market: Market, resolution: ClobResolution | None) -> Market:
    """Create a new Market with resolution data if available."""
    if resolution is None:
        return market
    return market.model_copy(update={
        "resolution_value": resolution.resolution_value,
        "winner_outcome": resolution.winner_outcome,
    })


def _enrich_token(token: TokenMarketEntry, resolution: ClobResolution | None) -> TokenMarketEntry:
    """Create a new TokenMarketEntry with winner flag if available."""
    if resolution is None:
        return token
    winner = resolution.token_winners.get(token.asset_id, False)
    if not winner:
        return token
    return token.model_copy(update={"winner": True})


async def fetch_token_market_map(
    limit: int = 0,
) -> dict[str, tuple[str, str]]:
    """Backward-compatible wrapper: returns asset_id -> (condition_id, outcome) dict."""
    r = await fetch_events(limit=limit, fetch_resolution=False)
    return {e.asset_id: (e.condition_id, e.outcome) for e in r.token_entries}
