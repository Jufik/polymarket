"""Data loading for S2 strategy — period-based, pre-filtered by qualified makers."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polymarket_pipeline.strategies.features.backend_clickhouse import (
        ClickHouseBackend,
    )


def invert_token_map(
    token_map: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Invert cid -> {outcome: asset_id} to asset_id -> {condition_id, outcome}.

    Input:  {"cid_A": {"YES": "asset_1", "NO": "asset_2"}}
    Output: {"asset_1": {"condition_id": "cid_A", "outcome": "YES"},
             "asset_2": {"condition_id": "cid_A", "outcome": "NO"}}
    """
    inverted: dict[str, dict[str, str]] = {}
    for cid, outcomes in token_map.items():
        for outcome, asset_id in outcomes.items():
            inverted[asset_id] = {"condition_id": cid, "outcome": outcome}
    return inverted


def parse_period_range(period: str) -> tuple[str, str]:
    """Convert 'YYYY-MM' to (start_date, end_date) strings.

    Returns first day of month and first day of next month.
    """
    dt = datetime.strptime(period, "%Y-%m")
    year, month = dt.year, dt.month
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    return start, end


def qualified_trades_query(
    traders: set[str],
    start_date: str,
    end_date: str,
) -> str:
    """Build CH SQL to fetch BUY trades from qualified makers in a date range.

    Selects only columns matching NormalizedTrade (CH uses ``_version`` not
    ``version``; ``ingested_at`` has no model counterpart).

    Historical (Goldsky) trades have ``published_at=0``. We fill it with
    ``toUnixTimestamp(timestamp)`` so the replay clock advances correctly
    and tick-by-tick settlement works.
    """
    trader_list = ", ".join(f"'{t.lower()}'" for t in traders)
    return f"""
        SELECT
            trade_id, condition_id, asset_id, side,
            price, size, amount_usd, fee_usd,
            maker, taker, timestamp, source,
            tx_hash, order_hash, block_number, is_backfill,
            _version AS version,
            if(published_at > 0, published_at,
               toFloat64(toUnixTimestamp(timestamp))) AS published_at
        FROM trades_raw FINAL
        WHERE lower(maker) IN ({trader_list})
          AND side = 'BUY'
          AND toDate(timestamp) >= '{start_date}'
          AND toDate(timestamp) < '{end_date}'
        ORDER BY timestamp
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


async def load_period_trades(
    period: str,
    qualified_traders: set[str],
    backend: ClickHouseBackend,
    *,
    resolution_months_after: int = 3,
) -> tuple[list[Any], dict[str, tuple[str, float]], dict[str, dict[str, str]]]:
    """Load trades, resolutions, and token_map for a single period.

    Returns (trades, resolutions, token_map).
    trades are NormalizedTrade objects.
    resolutions are cid -> (winner_outcome, resolved_at_epoch).
    token_map is cid -> {"YES": asset_id, "NO": asset_id}.

    Parameters
    ----------
    resolution_months_after:
        How many months after the test period to load resolutions.
        Positions entered in the test month may not resolve until later.
        Default 3 months ensures most positions settle during the sim.
    """
    from polymarket_pipeline.models import NormalizedTrade

    start_date, end_date = parse_period_range(period)

    if not qualified_traders:
        return [], {}, {}

    # Fetch trades
    trades_sql = qualified_trades_query(qualified_traders, start_date, end_date)
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
    # Positions entered in the test month may resolve months later.
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
