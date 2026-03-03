"""Data loading for S3 NO Sniper strategy — tag-filtered, time-bounded trades."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.features.backend_clickhouse import (
        ClickHouseBackend,
    )


def parse_period_range(period: str) -> tuple[str, str]:
    """Convert 'YYYY-MM' to (start_date, end_date) strings."""
    dt = datetime.strptime(period, "%Y-%m")
    year, month = dt.year, dt.month
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    return start, end


def eligible_tag_trades_query(
    eligible_tags: frozenset[str],
    start_date: str,
    end_date: str,
    tag_table: str = "_tmp_tag_markets",
) -> str:
    """Build CH SQL to fetch all trades from eligible-tag markets in a date range.

    Fetches ALL trades (not filtered by maker or age) so the ReplayRunner
    clock advances properly for mid-run settlement.

    Historical (Goldsky) trades have ``published_at=0``. We fill it with
    ``toUnixTimestamp(timestamp)`` so the replay clock advances correctly.
    """
    tag_list = ", ".join(f"'{t}'" for t in eligible_tags)
    return f"""
        SELECT
            tr.trade_id, tr.condition_id, tr.asset_id, tr.side,
            tr.price, tr.size, tr.amount_usd, tr.fee_usd,
            tr.maker, tr.taker, tr.timestamp, tr.source,
            tr.tx_hash, tr.order_hash, tr.block_number, tr.is_backfill,
            tr._version AS version,
            if(tr.published_at > 0, tr.published_at,
               toFloat64(toUnixTimestamp(tr.timestamp))) AS published_at
        FROM trades_raw tr
        JOIN {tag_table} tm ON tr.condition_id = tm.condition_id
        WHERE tm.tag IN ({tag_list})
          AND toDate(tr.timestamp) >= '{start_date}'
          AND toDate(tr.timestamp) < '{end_date}'
        ORDER BY tr.timestamp
    """


def resolutions_query(start_date: str, end_date: str) -> str:
    """Build CH SQL for market resolutions in a date range."""
    return f"""
        SELECT
            condition_id,
            asset_id,
            outcome,
            token_won,
            toUnixTimestamp(resolved_at) AS resolved_epoch
        FROM markets_resolved
        WHERE toDate(resolved_at) >= '{start_date}'
          AND toDate(resolved_at) < '{end_date}'
    """


def market_tags_query(
    eligible_tags: frozenset[str] | None = None,
    tag_table: str = "_tmp_tag_markets",
) -> str:
    """Fetch condition_id -> tag mapping, optionally filtered to eligible tags."""
    base = f"SELECT condition_id, tag FROM {tag_table}"
    if eligible_tags:
        tag_list = ", ".join(f"'{t}'" for t in eligible_tags)
        return f"{base} WHERE tag IN ({tag_list})"
    return base


async def load_market_tags(
    backend: ClickHouseBackend,
    eligible_tags: frozenset[str] | None = None,
    tag_table: str = "_tmp_tag_markets",
) -> dict[str, str]:
    """Load condition_id -> primary_tag mapping from CH."""
    sql = market_tags_query(eligible_tags, tag_table)
    df = await backend._execute(sql)
    if len(df) == 0:
        return {}
    return dict(zip(df["condition_id"].to_list(), df["tag"].to_list()))


async def load_period_trades(
    period: str,
    eligible_tags: frozenset[str],
    backend: ClickHouseBackend,
    *,
    resolution_months_after: int = 3,
    tag_table: str = "_tmp_tag_markets",
) -> tuple[list[Any], dict[str, tuple[str, float]], dict[str, dict[str, str]]]:
    """Load trades, resolutions, and token_map for a single period.

    Returns (trades, resolutions, token_map).
    """
    from polymarket_pipeline.models import NormalizedTrade

    start_date, end_date = parse_period_range(period)

    # Fetch trades from eligible-tag markets
    trades_sql = eligible_tag_trades_query(eligible_tags, start_date, end_date, tag_table)
    trades_df = await backend._execute(trades_sql)

    trades: list[NormalizedTrade] = []
    if len(trades_df) > 0:
        for row in trades_df.iter_rows(named=True):
            try:
                trade = NormalizedTrade(**row)
                trades.append(trade)
            except Exception:
                continue

    # Fetch resolutions with extended window
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    extended_month = dt_end.month + resolution_months_after
    extended_year = dt_end.year + (extended_month - 1) // 12
    extended_month = ((extended_month - 1) % 12) + 1
    extended_end = f"{extended_year}-{extended_month:02d}-01"

    res_sql = resolutions_query(start_date, extended_end)
    res_df = await backend._execute(res_sql)

    resolutions: dict[str, tuple[str, float]] = {}
    token_map: dict[str, dict[str, str]] = {}
    if len(res_df) > 0:
        for row in res_df.iter_rows(named=True):
            cid = str(row["condition_id"])
            asset_id = str(row["asset_id"])
            outcome = str(row["outcome"])
            token_won = int(row["token_won"])
            epoch = float(row.get("resolved_epoch", 0))

            if cid not in token_map:
                token_map[cid] = {}
            token_map[cid][outcome] = asset_id

            if token_won == 1 and epoch > 0:
                resolutions[cid] = (outcome, epoch)

    return trades, resolutions, token_map
