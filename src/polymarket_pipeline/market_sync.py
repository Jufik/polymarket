"""Gamma API syncer — fetches events + markets and builds token maps."""

from __future__ import annotations

import httpx
import structlog

from polymarket_pipeline.models import Event, Market, Tag, TokenMarketEntry

log = structlog.get_logger()

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


class SyncResult:
    """Result of a full event + market + tag sync."""

    __slots__ = ("events", "markets", "token_entries", "tags", "event_tag_pairs")

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.markets: list[Market] = []
        self.token_entries: list[TokenMarketEntry] = []
        self.tags: list[Tag] = []
        self.event_tag_pairs: list[tuple[int, int]] = []  # (event_id, tag_id)


async def fetch_events(
    limit: int = 0,
) -> SyncResult:
    """Fetch events from Gamma API and return all related data.

    Uses the /events endpoint which returns embedded markets and tags per event.

    Args:
        limit: Max events to fetch. 0 = fetch all.

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
    return result


async def fetch_token_market_map(
    limit: int = 0,
) -> dict[str, tuple[str, str]]:
    """Backward-compatible wrapper: returns asset_id -> (condition_id, outcome) dict."""
    r = await fetch_events(limit=limit)
    return {e.asset_id: (e.condition_id, e.outcome) for e in r.token_entries}
