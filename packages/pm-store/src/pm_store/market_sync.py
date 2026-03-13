"""Gamma API + CLOB API syncer — fetches events, markets, and resolution data."""

from __future__ import annotations

import asyncio
from base64 import b64decode
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from pm_core.models import Event, Market, Tag, TokenMarketEntry
from pm_core.types import MarketStatus

log = structlog.get_logger()

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_PAGE_SIZE = 500


class SyncResult:
    """Result of a full event + market + tag sync."""

    __slots__ = (
        "events",
        "markets",
        "token_entries",
        "tags",
        "event_tag_pairs",
        "raw_market_dicts",
    )

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.markets: list[Market] = []
        self.token_entries: list[TokenMarketEntry] = []
        self.tags: list[Tag] = []
        self.event_tag_pairs: list[tuple[int, int]] = []  # (event_id, tag_id)
        self.raw_market_dicts: dict[str, dict[str, Any]] = {}  # condition_id -> raw API dict


@dataclass
class ClobResolution:
    """Resolution data for a single market from CLOB API."""

    resolution_value: int  # 1=resolved, 0=unresolved, -1=voided
    winner_outcome: str  # "Yes" or "No" or ""
    token_winners: dict[str, bool] = field(default_factory=dict)  # asset_id -> winner


@dataclass
class ClobMarketRow:
    """Full market data from CLOB API (superset of ClobResolution)."""

    condition_id: str
    question: str
    market_slug: str
    closed: bool
    active: bool
    end_date_iso: str
    neg_risk: bool
    resolution_value: int
    winner_outcome: str
    tags: str  # comma-separated
    maker_base_fee: float = 0.0
    taker_base_fee: float = 0.0
    minimum_order_size: float = 0.0
    minimum_tick_size: float = 0.0


@dataclass
class ClobTokenRow:
    """Token entry from CLOB API with token_index."""

    asset_id: str
    condition_id: str
    outcome: str
    winner: bool
    token_index: int  # 0 = affirmative/"YES" side


@dataclass
class ClobMarketsResult:
    """Result of fetch_clob_markets() — full CLOB data + resolution."""

    markets: list[ClobMarketRow]
    tokens: list[ClobTokenRow]
    resolutions: dict[str, ClobResolution]


@dataclass
class MetadataSyncResult:
    """Unified result of fetch_all_metadata() — CLOB + Gamma merged."""

    clob: ClobMarketsResult
    gamma: SyncResult
    market_rows: list[dict[str, Any]]  # merged rows for parquet
    token_rows: list[dict[str, Any]]  # token_map rows for parquet


async def fetch_clob_markets() -> ClobMarketsResult:
    """Fetch all markets from CLOB API — full market data, token map, and resolution.

    Returns ClobMarketsResult with market rows, token rows (with token_index),
    and a resolutions dict for backward-compatible enrichment of Gamma models.
    """
    market_rows: list[ClobMarketRow] = []
    token_rows: list[ClobTokenRow] = []
    resolutions: dict[str, ClobResolution] = {}

    async with httpx.AsyncClient(timeout=60) as client:
        cursor: str | None = None
        page_num = 0

        while True:
            params: dict[str, str | int] = {"limit": CLOB_PAGE_SIZE}
            if cursor is not None:
                params["next_cursor"] = cursor

            # Retry transient errors (502/503/504) up to 3 times
            for attempt in range(4):
                resp = await client.get(f"{CLOB_API_BASE}/markets", params=params)
                if resp.status_code in (502, 503, 504) and attempt < 3:
                    wait = 2**attempt
                    log.warning(
                        "clob.transient_error",
                        status=resp.status_code,
                        page=page_num,
                        attempt=attempt + 1,
                        retry_in=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
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

                market_rows.append(
                    ClobMarketRow(
                        condition_id=condition_id,
                        question=m.get("question", ""),
                        market_slug=m.get("market_slug", ""),
                        closed=closed,
                        active=bool(m.get("active", False)),
                        end_date_iso=m.get("end_date_iso", ""),
                        neg_risk=bool(m.get("neg_risk", False)),
                        resolution_value=resolution_value,
                        winner_outcome=winner_outcome,
                        tags=",".join(m.get("tags") or []),
                        maker_base_fee=float(m.get("maker_base_fee", 0) or 0),
                        taker_base_fee=float(m.get("taker_base_fee", 0) or 0),
                        minimum_order_size=float(m.get("minimum_order_size", 0) or 0),
                        minimum_tick_size=float(m.get("minimum_tick_size", 0) or 0),
                    )
                )

                token_winners: dict[str, bool] = {}
                for idx, t in enumerate(tokens):
                    actual_outcome = t.get("outcome", "")
                    # Only validate ordering for Yes/No markets (not matchup markets
                    # where outcomes are team names like "Rockies" / "Brewers")
                    if idx == 0 and actual_outcome == "No" or idx == 1 and actual_outcome == "Yes":
                        log.warning("token_ordering.yes_no_swapped", condition_id=condition_id)
                    token_id = t.get("token_id", "")
                    if not token_id:
                        continue
                    is_winner = bool(t.get("winner", False))
                    token_winners[token_id] = is_winner
                    token_rows.append(
                        ClobTokenRow(
                            asset_id=token_id,
                            condition_id=condition_id,
                            outcome=t.get("outcome", ""),
                            winner=is_winner,
                            token_index=idx,
                        )
                    )

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
            except Exception as e:
                log.error("clob.cursor_decode_failed", cursor=next_cursor, error=str(e))
                break  # stop pagination, return partial results

            cursor = next_cursor
            page_num += 1

            if page_num % 20 == 0:
                log.info(
                    "clob_fetch_progress",
                    pages=page_num,
                    markets=len(market_rows),
                    tokens=len(token_rows),
                )

    log.info(
        "clob_fetch_complete",
        markets=len(market_rows),
        tokens=len(token_rows),
    )
    return ClobMarketsResult(
        markets=market_rows,
        tokens=token_rows,
        resolutions=resolutions,
    )


async def fetch_all_metadata() -> MetadataSyncResult:
    """Fetch CLOB ground truth + Gamma supplementary metadata, merge into unified result.

    Returns MetadataSyncResult with:
    - clob: raw CLOB data (for parquet export)
    - gamma: SyncResult with Pydantic models (for PG upsert), enriched with CLOB resolution
    - market_rows: merged dicts for markets.parquet
    - token_rows: dicts for token_map.parquet
    """
    # 1. Fetch CLOB (ground truth for resolution + token_index)
    log.info("fetching_clob_markets")
    clob = await fetch_clob_markets()

    # 2. Fetch Gamma (supplementary: event_id, category, closed_at, tags)
    log.info("fetching_gamma_events")
    gamma = await fetch_events(fetch_resolution=False)

    # 3. Enrich Gamma models with CLOB resolution
    gamma.markets = [_enrich_market(m, clob.resolutions.get(m.condition_id)) for m in gamma.markets]
    gamma.token_entries = [
        _enrich_token(t, clob.resolutions.get(t.condition_id)) for t in gamma.token_entries
    ]
    enriched = sum(1 for m in gamma.markets if m.resolution_value != 0)
    log.info(
        "resolution_enrichment_complete",
        total_markets=len(gamma.markets),
        enriched=enriched,
        clob_markets=len(clob.resolutions),
    )

    # 4. Build token_map rows for parquet (from CLOB — has token_index)
    token_rows = [
        {
            "asset_id": t.asset_id,
            "condition_id": t.condition_id,
            "outcome": t.outcome,
            "winner": t.winner,
            "token_index": t.token_index,
        }
        for t in clob.tokens
    ]

    # 5. Build merged market rows for parquet (CLOB left-join Gamma)
    gamma_map: dict[str, Market] = {m.condition_id: m for m in gamma.markets}
    clob_ids: set[str] = set()
    market_rows: list[dict[str, Any]] = []
    for cm in clob.markets:
        clob_ids.add(cm.condition_id)
        gm = gamma_map.get(cm.condition_id)
        row: dict[str, Any] = {
            "condition_id": cm.condition_id,
            "question": cm.question,
            "market_slug": cm.market_slug,
            "closed": cm.closed,
            "active": cm.active,
            "end_date_iso": cm.end_date_iso,
            "neg_risk": cm.neg_risk,
            "resolution_value": cm.resolution_value,
            "winner_outcome": cm.winner_outcome,
            "tags": cm.tags,
            # Gamma supplementary fields
            "event_id": gm.event_id if gm else None,
            "slug": gm.slug if gm else None,
            "category": gm.category if gm else None,
            "closed_at": gm.closed_at if gm else None,
            "end_date": gm.end_date if gm else None,
            "description": gm.description if gm else "",
            "resolution_source": gm.resolution_source if gm else "",
            "outcomes": gm.outcomes if gm else "",
            "market_volume": gm.market_volume if gm else 0.0,
            # CLOB operational params (parquet-only)
            "maker_base_fee": cm.maker_base_fee,
            "taker_base_fee": cm.taker_base_fee,
            "minimum_order_size": cm.minimum_order_size,
            "minimum_tick_size": cm.minimum_tick_size,
        }
        # resolved_at = closed_at when CLOB says resolved
        row["resolved_at"] = row["closed_at"] if cm.resolution_value == 1 else None
        market_rows.append(row)

    # 6. Gamma-only markets (not in CLOB — purged old markets)
    gamma_only_count = 0
    for gm in gamma.markets:
        if gm.condition_id in clob_ids:
            continue
        gamma_only_count += 1
        row = {
            "condition_id": gm.condition_id,
            "question": gm.question,
            "market_slug": gm.slug,
            "closed": gm.status in (MarketStatus.CLOSED, MarketStatus.RESOLVED),
            "active": gm.status == MarketStatus.ACTIVE,
            "end_date_iso": gm.end_date.isoformat() if gm.end_date else "",
            "neg_risk": gm.neg_risk,
            "resolution_value": gm.resolution_value,
            "winner_outcome": gm.winner_outcome,
            "tags": "",
            "event_id": gm.event_id,
            "slug": gm.slug,
            "category": gm.category,
            "closed_at": gm.closed_at,
            "end_date": gm.end_date,
            "description": gm.description,
            "resolution_source": gm.resolution_source,
            "outcomes": gm.outcomes,
            "market_volume": gm.market_volume,
            "maker_base_fee": 0.0,
            "taker_base_fee": 0.0,
            "minimum_order_size": 0.0,
            "minimum_tick_size": 0.0,
            "resolved_at": gm.resolved_at,
        }
        market_rows.append(row)
        # Add token entries for Gamma-only markets
        token_rows.append(
            {
                "asset_id": gm.token_yes,
                "condition_id": gm.condition_id,
                "outcome": "YES",
                "winner": gm.winner_outcome == "Yes" if gm.resolution_value == 1 else False,
                "token_index": 0,
            }
        )
        token_rows.append(
            {
                "asset_id": gm.token_no,
                "condition_id": gm.condition_id,
                "outcome": "NO",
                "winner": gm.winner_outcome == "No" if gm.resolution_value == 1 else False,
                "token_index": 1,
            }
        )

    log.info(
        "metadata_merge_complete",
        clob_markets=len(clob.markets),
        gamma_markets=len(gamma.markets),
        gamma_only=gamma_only_count,
        merged=len(market_rows),
        tokens=len(token_rows),
    )

    return MetadataSyncResult(
        clob=clob,
        gamma=gamma,
        market_rows=market_rows,
        token_rows=token_rows,
    )


async def fetch_clob_resolution() -> dict[str, ClobResolution]:
    """Fetch resolution data from CLOB API. Delegates to fetch_clob_markets()."""
    result = await fetch_clob_markets()
    return result.resolutions


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
            # Retry transient errors (502/503/504) up to 3 times
            for attempt in range(4):
                resp = await client.get(
                    f"{GAMMA_API_BASE}/events",
                    params={"limit": page_size, "offset": offset},
                )
                if resp.status_code in (502, 503, 504) and attempt < 3:
                    wait = 2**attempt
                    log.warning(
                        "gamma.transient_error",
                        status=resp.status_code,
                        offset=offset,
                        attempt=attempt + 1,
                        retry_in=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
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
                    result.raw_market_dicts[market.condition_id] = raw_market
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

        # Enrich markets
        result.markets = [
            _enrich_market(m, resolutions.get(m.condition_id)) for m in result.markets
        ]

        # Enrich token entries
        result.token_entries = [
            _enrich_token(t, resolutions.get(t.condition_id)) for t in result.token_entries
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
    return market.model_copy(
        update={
            "resolution_value": resolution.resolution_value,
            "winner_outcome": resolution.winner_outcome,
        }
    )


def _enrich_token(token: TokenMarketEntry, resolution: ClobResolution | None) -> TokenMarketEntry:
    """Create a new TokenMarketEntry with winner flag if available."""
    if resolution is None:
        return token
    winner = resolution.token_winners.get(token.asset_id, False)
    if not winner:
        return token
    return token.model_copy(update={"winner": True})


@dataclass
class OpenMarketsSyncResult:
    """Result of fetch_open_markets() — events, markets, and tokens for open markets."""

    events: list[Event]
    markets: list[Market]
    token_entries: list[TokenMarketEntry]
    tags: list[Tag]
    event_tag_pairs: list[tuple[int, int]]


async def fetch_open_markets() -> OpenMarketsSyncResult:
    """Fetch events/markets/tokens for open (non-closed) markets only via Gamma API.

    Much faster than fetch_all_metadata() — ~8K open events vs ~450K+ total.
    Returns full metadata needed for PG upsert (events, markets, tokens in FK order).
    """
    result = OpenMarketsSyncResult(
        events=[], markets=[], token_entries=[], tags=[], event_tag_pairs=[]
    )
    seen_tags: dict[int, Tag] = {}
    offset = 0
    page_size = 500

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            for attempt in range(4):
                resp = await client.get(
                    f"{GAMMA_API_BASE}/events",
                    params={"limit": page_size, "offset": offset, "closed": "false"},
                )
                if resp.status_code in (502, 503, 504) and attempt < 3:
                    wait = 2**attempt
                    log.warning(
                        "gamma.transient_error",
                        status=resp.status_code,
                        offset=offset,
                        attempt=attempt + 1,
                        retry_in=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            page = resp.json()
            if not page:
                break

            for raw_event in page:
                event = Event.from_gamma(raw_event)
                if event is None:
                    continue
                result.events.append(event)

                for raw_tag in raw_event.get("tags", []):
                    tag = Tag.from_gamma(raw_tag)
                    if tag is None:
                        continue
                    if tag.id not in seen_tags:
                        seen_tags[tag.id] = tag
                    result.event_tag_pairs.append((event.id, tag.id))

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
            if len(page) < page_size:
                break

    result.tags = list(seen_tags.values())
    log.info(
        "open_markets_fetched",
        events=len(result.events),
        markets=len(result.markets),
        tokens=len(result.token_entries),
    )
    return result


async def fetch_token_market_map(
    limit: int = 0,
) -> dict[str, tuple[str, str]]:
    """Backward-compatible wrapper: returns asset_id -> (condition_id, outcome) dict."""
    r = await fetch_events(limit=limit, fetch_resolution=False)
    return {e.asset_id: (e.condition_id, e.outcome) for e in r.token_entries}
