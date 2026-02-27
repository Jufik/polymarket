"""Event-driven replay of Will-NO strategy with paper trading.

Loads trades from ClickHouse for a specified date range, replays them
through the WillNoStrategy on_trade() path (one at a time, like live),
executes fills via a custom ReplayExecutor, tracks positions, and
computes PnL from actual market resolutions.

This is NOT the vectorized backtest — it exercises the same code path
that would run in production (on_trade → TradeIntent → ExecutionGateway
→ Fill → Position tracking).

Usage:
    uv run python scripts/replay_will_no.py                       # Jan 2026
    uv run python scripts/replay_will_no.py --start 2025-12-01    # Dec 2025
    uv run python scripts/replay_will_no.py --start 2025-09-01 --end 2025-12-01
    uv run python scripts/replay_will_no.py --verbose             # Show every trade processed
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import polars as pl
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.models import NormalizedTrade, Side, Source  # noqa: E402
from polymarket_pipeline.strategies.config import StrategyConfig  # noqa: E402
from polymarket_pipeline.strategies.context.memory import InMemoryContext  # noqa: E402
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway  # noqa: E402
from polymarket_pipeline.strategies.runners.helpers import (  # noqa: E402
    apply_fill_to_position,
    check_risk_gate,
)
from polymarket_pipeline.strategies.types import (  # noqa: E402
    ExecutionMode,
    Fill,
    FillStatus,
    MarketInfo,
    TradeIntent,
)
from polymarket_pipeline.strategies_impl.market_size.classifier import MarketSizeClassifier  # noqa: E402
from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig  # noqa: E402
from polymarket_pipeline.strategies_impl.market_size.features import compute_features_polars  # noqa: E402
from polymarket_pipeline.strategies_impl.will_no.config import WillNoConfig  # noqa: E402
from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy  # noqa: E402

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom executor: fills at actual market price
# ---------------------------------------------------------------------------


class ReplayExecutor:
    """Fills intents at prices derived from the market context.

    For BUY NO:  fills at ``1 - yes_price`` (the NO token cost).
    For SELL YES: fills at ``yes_price``.
    Falls back to 0.50 if the context has no price.
    """

    def __init__(self, ctx: InMemoryContext, fee_pct: float = 0.02) -> None:
        self._ctx = ctx
        self.fee_pct = fee_pct

    async def execute(self, intent: TradeIntent) -> Fill:
        market = await self._ctx.get_market(intent.condition_id)
        yes_price = (market.yes_price if market and market.yes_price else 0.50)

        if intent.outcome == "NO" and intent.side == "BUY":
            price = 1.0 - yes_price
        elif intent.outcome == "YES" and intent.side == "SELL":
            price = yes_price
        else:
            price = intent.max_price or 0.50

        fee = self.fee_pct * min(price, 1 - price) * intent.size_usd

        return Fill(
            intent_id=uuid.uuid4().hex[:12],
            strategy=intent.strategy,
            condition_id=intent.condition_id,
            side=intent.side,
            outcome=intent.outcome,
            filled_price=price,
            filled_size_usd=intent.size_usd,
            fee_usd=fee,
            status=FillStatus.FILLED,
            filled_at=intent.signal_time,
        )


# ---------------------------------------------------------------------------
# ClickHouse helpers
# ---------------------------------------------------------------------------


def _fmt(t0: float) -> str:
    e = time.time() - t0
    return f"{e:.1f}s" if e < 60 else f"{e / 60:.1f}min"


async def query_ch(
    client: httpx.AsyncClient, query: str, *, label: str = "query"
) -> pl.DataFrame:
    t0 = time.time()
    print(f"  [{label}] ...", end="", flush=True)
    async with client.stream(
        "POST",
        "/",
        content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB},
        headers={"Content-Type": "text/plain"},
        timeout=600.0,
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            raise RuntimeError(body)
        chunks: list[bytes] = []
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data.strip():
        print(f" empty ({_fmt(t0)})")
        return pl.DataFrame()
    df = pl.read_csv(
        io.BytesIO(data),
        has_header=True,
        skip_rows_after_header=1,
        null_values=["\\N", ""],
        infer_schema_length=10000,
    )
    print(f" {len(df):,} rows ({_fmt(t0)})")
    return df


# ---------------------------------------------------------------------------
# Trade loading
# ---------------------------------------------------------------------------


def rows_to_trades(df: pl.DataFrame) -> list[NormalizedTrade]:
    """Convert ClickHouse rows to NormalizedTrade objects."""
    trades: list[NormalizedTrade] = []
    for row in df.iter_rows(named=True):
        try:
            ts = row["timestamp"]
            if isinstance(ts, (int, float)):
                dt = datetime.utcfromtimestamp(int(ts))
            else:
                dt = datetime.fromisoformat(str(ts).replace(" ", "T"))

            side_raw = str(row.get("side", "0"))
            # ClickHouse stores side as 0/1 (0=BUY, 1=SELL)
            if side_raw in ("0", "BUY"):
                side = Side.BUY
            elif side_raw in ("1", "SELL"):
                side = Side.SELL
            else:
                side = Side.BUY

            trades.append(
                NormalizedTrade(
                    trade_id=str(row["trade_id"]),
                    condition_id=str(row["condition_id"]),
                    asset_id="",  # not needed for on_trade path
                    side=side,
                    price=Decimal(str(row["price"])),
                    size=Decimal(str(row.get("size", 0))),
                    amount_usd=Decimal(str(row.get("amount_usd", 0))),
                    fee_usd=Decimal("0"),
                    maker=str(row.get("maker") or ""),
                    taker=str(row.get("taker") or ""),
                    timestamp=dt,
                    source=Source.GOLDSKY_SINK,
                    tx_hash=str(row.get("tx_hash") or ""),
                    order_hash=None,
                    block_number=row.get("block_number"),
                    is_backfill=True,
                    version=2,
                    published_at=float(row.get("published_at", ts)),
                )
            )
        except Exception as exc:
            logger.warning("skip_row", error=str(exc)[:100])
    return trades


# ---------------------------------------------------------------------------
# Main replay
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="Will-NO event-driven paper replay")
    parser.add_argument("--start", default="2026-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-02-01", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=5000, help="Capital limit ($)")
    parser.add_argument("--bet-size", type=float, default=50, help="Base bet size ($)")
    parser.add_argument("--max-open", type=int, default=50, help="Max open positions")
    parser.add_argument("--cooldown", type=int, default=0, help="Cooldown between trades (s)")
    parser.add_argument("--fee", type=float, default=0.02, help="Fee fraction (0.02 = 2%%)")
    parser.add_argument("--verbose", action="store_true", help="Show every trade processed")
    parser.add_argument("--no-bucket", action="store_true", help="Disable max_bucket filter")
    args = parser.parse_args()

    t_global = time.time()
    print("=" * 100)
    print("  Will-NO Event-Driven Paper Replay")
    print("=" * 100)
    print(f"  Period:     {args.start} to {args.end}")
    print(f"  Capital:    ${args.capital:,.0f}")
    print(f"  Bet size:   ${args.bet_size:.0f}")
    print(f"  Fee:        {args.fee:.1%}")
    print(f"  Max open:   {args.max_open}")
    print(f"  Cooldown:   {args.cooldown}s")

    # ------------------------------------------------------------------
    # Strategy + context setup
    # ------------------------------------------------------------------
    will_cfg = WillNoConfig(
        base_bet_usd=args.bet_size,
        fee_pct=args.fee,
        max_bucket=None if args.no_bucket else "med",
    )
    strategy = WillNoStrategy(config=will_cfg)

    ctx = InMemoryContext()
    executor = ReplayExecutor(ctx=ctx, fee_pct=args.fee)
    gateway = ExecutionGateway(executor=executor)

    risk_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=args.capital,
        max_position_usd=args.bet_size * 3,  # max 3 bets per market
        max_open_positions=args.max_open,
        cooldown_s=args.cooldown,
    )

    # ------------------------------------------------------------------
    # Phase 1: Load market metadata
    # ------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("  Phase 1: Loading market metadata")
    print(f"{'=' * 100}")

    client = httpx.AsyncClient(
        base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0
    )

    markets_df = await query_ch(
        client,
        """SELECT condition_id, question, category
           FROM markets
           WHERE question LIKE 'Will %%'""",
        label="will_markets",
    )

    # Load resolution data from local derived parquet
    data_dir = Path(__file__).resolve().parent.parent / "data"
    resolved_path = data_dir / "derived" / "markets_resolved.parquet"
    print(f"  [resolutions] loading {resolved_path.name} ...", end="", flush=True)
    t0 = time.time()
    resolution_df = pl.read_parquet(resolved_path)
    print(f" {len(resolution_df):,} rows ({_fmt(t0)})")

    # Build lookup dicts
    market_questions: dict[str, str] = {}
    for row in markets_df.iter_rows(named=True):
        market_questions[row["condition_id"]] = row["question"]

    resolution_map: dict[str, bool] = {}  # cid -> yes_won
    for row in resolution_df.iter_rows(named=True):
        resolution_map[row["condition_id"]] = bool(row.get("yes_won", False))

    # Pre-populate context with MarketInfo (question text, yes_price=None)
    for cid, question in market_questions.items():
        ctx.set_market(
            cid,
            MarketInfo(
                condition_id=cid,
                question=question,
                active=True,
                yes_price=None,  # will be updated per-trade
            ),
        )

    print(f"  Loaded {len(market_questions):,} 'Will' markets, "
          f"{len(resolution_map):,} resolved")

    # Build keyword SQL filter (reused by volume, ML, and trades queries)
    kw_conditions = " OR ".join(
        f"question LIKE '%%{kw}%%'" for kw in will_cfg.prefer_keywords
    )
    avoid_conditions = " AND ".join(
        f"question NOT LIKE '%%{kw}%%'" for kw in will_cfg.avoid_keywords
    )
    target_markets_sql = (
        f"SELECT condition_id FROM markets "
        f"WHERE question LIKE 'Will %%' AND ({kw_conditions}) AND ({avoid_conditions})"
    )

    start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").timestamp())
    end_ts = int(datetime.strptime(args.end, "%Y-%m-%d").timestamp())

    # Pre-compute market volumes for the volume filter
    vol_df = await query_ch(
        client,
        f"""SELECT condition_id, sum(price * size) AS market_volume
            FROM trades_raw
            WHERE condition_id IN ({target_markets_sql})
            AND timestamp >= {start_ts} AND timestamp < {end_ts}
            GROUP BY condition_id""",
        label="market_volumes",
    )
    market_volumes: dict[str, float] = {}
    for row in vol_df.iter_rows(named=True):
        market_volumes[row["condition_id"]] = float(row["market_volume"])
    ctx.update_features({"market_volume": market_volumes})
    print(f"  Injected volume features for {len(market_volumes):,} markets")

    # ------------------------------------------------------------------
    # Phase 1b: Market Size Classification (XGBoost ML model)
    # ------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("  Phase 1b: Market Size Classification (XGBoost)")
    print(f"{'=' * 100}")

    ms_cfg = MarketSizeConfig()
    model_path = str(Path(__file__).resolve().parent.parent / ms_cfg.model_path)

    try:
        classifier = MarketSizeClassifier(ms_cfg)
        classifier.load(model_path)
        print(f"  Model loaded: {model_path}")

        # Markets with event_id + neg_risk (needed by feature pipeline)
        markets_meta_df = await query_ch(
            client,
            f"""SELECT condition_id, toString(event_id) AS event_id,
                       question, neg_risk
                FROM markets
                WHERE condition_id IN ({target_markets_sql})""",
            label="markets_meta",
        )

        # Events metadata
        events_df = await query_ch(
            client,
            """SELECT toString(id) AS id,
                      toFloat64(dateDiff('second', toDateTime64('1970-01-01', 3), assumeNotNull(end_date))) AS end_date,
                      toFloat64(volume) AS volume,
                      toFloat64(liquidity) AS liquidity
               FROM events
               WHERE end_date IS NOT NULL
               AND dateDiff('second', toDateTime64('1970-01-01', 3), assumeNotNull(end_date)) > 86400""",
            label="events",
        )

        # Tags
        tags_df = await query_ch(
            client,
            """SELECT toString(et.event_id) AS event_id, t.label AS tag
               FROM event_tags et
               JOIN tags t ON et.tag_id = t.id""",
            label="tags",
        )

        # Early-life trades: first 6h per market (for feature computation)
        early_trades_df = await query_ch(
            client,
            f"""WITH first_ts AS (
                    SELECT condition_id, min(timestamp) AS ft
                    FROM trades_raw
                    WHERE condition_id IN ({target_markets_sql})
                    GROUP BY condition_id
                )
                SELECT t.condition_id, t.maker,
                       toFloat64(t.price) AS price,
                       toFloat64(t.size) AS size,
                       toUnixTimestamp64Milli(t.timestamp) / 1000.0 AS timestamp
                FROM trades_raw t
                INNER JOIN first_ts f ON t.condition_id = f.condition_id
                WHERE t.timestamp <= addHours(f.ft, {ms_cfg.feature_window_hours})""",
            label="early_trades",
        )

        if not early_trades_df.is_empty():
            # Ensure correct types for feature pipeline
            markets_meta_df = markets_meta_df.with_columns(
                pl.col("neg_risk").cast(pl.Boolean)
            )

            # Compute features
            features_df = compute_features_polars(
                early_trades_df, markets_meta_df, events_df, tags_df, ms_cfg
            )

            if not features_df.is_empty():
                features_df = features_df.drop_nulls(subset=["time_remaining_hours"])

            if not features_df.is_empty():
                # Build question text list (for TF-IDF)
                q_map = dict(zip(
                    markets_meta_df["condition_id"].to_list(),
                    markets_meta_df["question"].to_list(),
                    strict=False,
                ))
                feat_cids = features_df["condition_id"].to_list()
                questions = [q_map.get(c, "") for c in feat_cids]

                # Predict buckets
                feat_for_pred = features_df.drop("total_volume")
                buckets = classifier.predict(feat_for_pred, questions=questions)
                bucket_map = dict(zip(feat_cids, buckets, strict=True))

                ctx.update_features({"market_size_bucket": bucket_map})

                # Distribution stats
                from collections import Counter
                dist = Counter(buckets)
                print(f"  Classified {len(bucket_map):,} markets:")
                for b in ms_cfg.buckets:
                    print(f"    {b:>6}: {dist.get(b, 0):,}")
            else:
                print("  No features computed (empty after null drop)")
        else:
            print("  No early trades found")

    except FileNotFoundError:
        print(f"  WARNING: Model not found at {model_path}, skipping ML classification")
        print(f"  (max_bucket filter will be inactive)")

    # ------------------------------------------------------------------
    # Phase 2: Load trades
    # ------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("  Phase 2: Loading trades")
    print(f"{'=' * 100}")

    # Pre-filter: only load trades for keyword-matching Will markets
    # Skip FINAL — strategy's _signaled set handles dedup naturally
    trades_df = await query_ch(
        client,
        f"""SELECT
              toString(trade_id) AS trade_id,
              condition_id,
              toString(side) AS side,
              price, size,
              price * size AS amount_usd,
              maker, taker,
              toUnixTimestamp64Milli(timestamp) / 1000.0 AS timestamp,
              toUnixTimestamp64Milli(timestamp) / 1000.0 AS published_at,
              block_number
            FROM trades_raw
            WHERE condition_id IN (
                SELECT condition_id FROM markets
                WHERE question LIKE 'Will %%'
                  AND ({kw_conditions})
                  AND ({avoid_conditions})
            )
            AND timestamp >= {start_ts}
            AND timestamp < {end_ts}
            ORDER BY timestamp ASC""",
        label="trades",
    )

    if trades_df.is_empty():
        print("\n  No trades found in the specified range!")
        return

    trades = rows_to_trades(trades_df)
    trades.sort(key=lambda t: t.published_at)
    print(f"  Converted {len(trades):,} NormalizedTrade objects")
    print(f"  Range: {trades[0].timestamp} to {trades[-1].timestamp}")

    unique_markets = {t.condition_id for t in trades}
    print(f"  Unique markets: {len(unique_markets):,}")

    await client.aclose()

    # ------------------------------------------------------------------
    # Phase 3: Event-driven replay
    # ------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("  Phase 3: Event-Driven Replay (trade by trade)")
    print(f"{'=' * 100}")

    fills: list[Fill] = []
    rejected: list[tuple[TradeIntent, str]] = []
    last_trade_times: dict[str, float] = {}
    n_processed = 0

    for trade in trades:
        n_processed += 1
        current_time = trade.published_at
        ctx.set_time(current_time)

        # Update market yes_price with this trade's price
        cid = trade.condition_id
        market = await ctx.get_market(cid)
        if market is not None:
            ctx.set_market(
                cid,
                MarketInfo(
                    condition_id=cid,
                    question=market.question,
                    active=market.active,
                    yes_price=float(trade.price),
                    event_id=market.event_id,
                    category=market.category,
                ),
            )

        if args.verbose and n_processed % 50_000 == 0:
            print(f"    ... processed {n_processed:,}/{len(trades):,} trades, "
                  f"{len(fills)} fills so far")

        # Strategy processes trade
        intents = await strategy.on_trade(trade, ctx)
        if intents is None:
            continue

        for intent in intents:
            # Risk gate
            positions = ctx.get_all_positions()
            allowed, reason = check_risk_gate(
                intent, risk_config, positions, last_trade_times, current_time
            )
            if not allowed:
                rejected.append((intent, reason))
                continue

            # Execute
            fill = await gateway.submit(intent)
            fills.append(fill)

            # Position tracking
            old_pos = await ctx.get_position(fill.condition_id)
            new_pos = apply_fill_to_position(old_pos, fill)
            ctx.set_position(fill.condition_id, new_pos)
            last_trade_times[intent.strategy] = fill.filled_at

            # Print each signal
            yes_p = float(trade.price)
            no_p = 1.0 - yes_p
            question = market_questions.get(cid, "?")[:60]
            ts_str = trade.timestamp.strftime("%Y-%m-%d %H:%M")
            resolved_str = ""
            if cid in resolution_map:
                yes_won = resolution_map[cid]
                won = not yes_won  # We bought NO
                resolved_str = " WON" if won else " LOST"
                pnl = (
                    intent.size_usd * yes_p / no_p - intent.size_usd * args.fee
                    if won
                    else -intent.size_usd
                )
                resolved_str += f" ${pnl:+.1f}"

            print(
                f"  [{ts_str}] {intent.side} {intent.outcome} "
                f"YES={yes_p:.2f} ${intent.size_usd:.0f}"
                f"{resolved_str}  {question}"
            )

    print(f"\n  Replay complete: {n_processed:,} trades processed")
    print(f"  Signals: {len(fills)}")
    print(f"  Rejected: {len(rejected)}")
    if rejected:
        reasons = {}
        for _, r in rejected:
            reasons[r] = reasons.get(r, 0) + 1
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {r}: {c}")

    # ------------------------------------------------------------------
    # Phase 4: Resolution PnL
    # ------------------------------------------------------------------
    print(f"\n{'=' * 100}")
    print("  Phase 4: Resolution PnL")
    print(f"{'=' * 100}")

    total_wagered = 0.0
    total_pnl = 0.0
    wins = 0
    losses = 0
    unresolved = 0
    monthly_pnl: dict[str, float] = {}

    for fill in fills:
        if fill.outcome != "NO" or fill.side != "BUY":
            continue  # only count BUY NO fills

        cid = fill.condition_id
        yes_price = 1.0 - fill.filled_price  # NO price → YES price
        no_price = fill.filled_price

        if cid not in resolution_map:
            unresolved += 1
            continue

        yes_won = resolution_map[cid]
        no_won = not yes_won
        total_wagered += fill.filled_size_usd

        if no_won:
            # NO wins: profit = size * (yes_price / no_price) - fee
            pnl = (fill.filled_size_usd * yes_price / no_price
                    - fill.filled_size_usd * args.fee)
            wins += 1
        else:
            # NO loses: lose the full bet
            pnl = -fill.filled_size_usd
            losses += 1

        total_pnl += pnl

        # Monthly tracking
        month_key = datetime.utcfromtimestamp(fill.filled_at).strftime("%Y-%m")
        monthly_pnl[month_key] = monthly_pnl.get(month_key, 0.0) + pnl

    resolved = wins + losses
    print(f"\n  Resolved signals: {resolved}")
    print(f"  Unresolved:       {unresolved}")
    if resolved > 0:
        hr = wins / resolved
        roi = total_pnl / total_wagered * 100 if total_wagered > 0 else 0
        print(f"  Wins:             {wins} ({hr:.1%})")
        print(f"  Losses:           {losses}")
        print(f"  Total wagered:    ${total_wagered:,.0f}")
        print(f"  Total PnL:        ${total_pnl:+,.0f}")
        print(f"  ROI:              {roi:+.1f}%")
        n_months = len(monthly_pnl) or 1
        print(f"  Monthly PnL avg:  ${total_pnl / n_months:+,.0f}/mo")

    # Monthly breakdown
    if monthly_pnl:
        print(f"\n  Monthly Breakdown:")
        print(f"  {'Month':>10}  {'PnL':>10}  {'Running':>10}")
        running = 0.0
        for month in sorted(monthly_pnl.keys()):
            pnl = monthly_pnl[month]
            running += pnl
            marker = "+" if pnl >= 0 else "-"
            print(f"  {marker} {month}  ${pnl:>+9,.0f}  ${running:>+9,.0f}")

    # ------------------------------------------------------------------
    # Phase 5: Open Positions
    # ------------------------------------------------------------------
    positions = ctx.get_all_positions()
    open_pos = {k: v for k, v in positions.items() if v.qty_no > 0}
    if open_pos:
        print(f"\n{'=' * 100}")
        print(f"  Phase 5: Open Positions ({len(open_pos)})")
        print(f"{'=' * 100}")
        total_exposure = sum(p.cost_basis for p in open_pos.values())
        print(f"  Total exposure: ${total_exposure:,.0f}")
        for cid, pos in sorted(open_pos.items(), key=lambda x: -x[1].cost_basis)[:10]:
            q = market_questions.get(cid, "?")[:50]
            print(f"    ${pos.cost_basis:.0f} / {pos.qty_no:.0f} NO tokens  {q}")
        if len(open_pos) > 10:
            print(f"    ... and {len(open_pos) - 10} more")

    elapsed = time.time() - t_global
    print(f"\n  Done in {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
