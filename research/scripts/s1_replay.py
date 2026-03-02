"""S1 Tick-by-Tick Walk-Forward Replay.

Replays real historical trades from ClickHouse through the full S1 strategy
stack using the generalist ReplayRunner (asset_id-based resolution, no string
matching).

Monthly walk-forward: compute features from trailing 9-month window, then
replay next month tick-by-tick with mid-replay position settlement.

Usage:
    uv run python research/scripts/s1_replay.py
    uv run python research/scripts/s1_replay.py --months 2
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import structlog

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.calibrate import (
    calibrate_spreads,
    calibrate_volumes,
)
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.realistic import (
    FillModelConfig,
    RealisticFillSimulator,
)
from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
from polymarket_pipeline.strategies.ledger.analytics import compute_summary
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.runners.replay import (
    ReplayRunner,
    load_resolutions_from_rows,
)
from polymarket_pipeline.strategies.types import ExecutionMode
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.provider import S1HitRateProvider
from polymarket_pipeline.strategies_impl.s1_hitrate_copy.strategy import S1HitRateCopyStrategy

log = structlog.get_logger()

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

TEST_MONTHS = [
    "2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01",
    "2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01",
]

CONFIG = StrategyConfig(
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10_000.0,
    max_position_usd=500.0,
    max_open_positions=50,
    cooldown_s=0,
)


# ── Trade loading ──────────────────────────────────────────────────────


def _row_to_trade(row: dict) -> NormalizedTrade | None:
    """Convert a CH trades_raw row to NormalizedTrade."""
    try:
        price = float(row["price"])
        if price <= 0 or price > 1:
            return None
        size = float(row["size"])
        if size <= 0:
            return None

        side = Side.BUY if row["side"] in ("BUY", 1) else Side.SELL

        source_map = {
            "goldsky_sink": Source.GOLDSKY_SINK, "alchemy": Source.ALCHEMY,
            "rtds": Source.RTDS, "websocket": Source.WEBSOCKET,
            "goldsky_subgraph": Source.GOLDSKY_SUBGRAPH,
            "pending_block": Source.PENDING_BLOCK, "mempool": Source.MEMPOOL,
        }
        source = source_map.get(str(row.get("source", "")), Source.GOLDSKY_SINK)

        ts_raw = row["timestamp"]
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw.replace(" ", "T")).replace(tzinfo=UTC)
        elif isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(float(ts_raw), tz=UTC)
        else:
            ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)

        published = float(row.get("published_at", 0))
        if published <= 0:
            published = ts.timestamp()

        return NormalizedTrade(
            trade_id=str(row["trade_id"]),
            condition_id=str(row["condition_id"]),
            asset_id=str(row["asset_id"]),
            side=side,
            price=Decimal(str(round(price, 4))),
            size=Decimal(str(round(size, 2))),
            amount_usd=Decimal(str(round(float(row.get("amount_usd", price * size)), 2))),
            fee_usd=Decimal(str(round(float(row.get("fee_usd", 0)), 4))),
            maker=row.get("maker"),
            taker=row.get("taker"),
            timestamp=ts,
            source=source,
            tx_hash=row.get("tx_hash"),
            order_hash=row.get("order_hash"),
            block_number=int(row["block_number"]) if row.get("block_number") else None,
            is_backfill=bool(row.get("is_backfill", True)),
            version=int(row.get("_version", 2)),
            published_at=published,
        )
    except Exception as e:
        log.debug("row_to_trade_failed", error=str(e))
        return None


async def _ensure_qualified_table(
    backend: ClickHouseBackend, qualified: set[str]
) -> None:
    """Create a CH temp table with qualified trader addresses for JOIN filtering."""
    client = backend._client
    db = backend._database
    await client.post(
        "/", content="DROP TABLE IF EXISTS _tmp_replay_qualified",
        params={"database": db},
    )
    await client.post(
        "/",
        content="CREATE TABLE _tmp_replay_qualified (trader String) ENGINE = Memory",
        params={"database": db},
    )
    # Insert in batches of 1000
    batch: list[str] = []
    for t in qualified:
        batch.append(t)
        if len(batch) >= 1000:
            values = ", ".join(f"('{addr}')" for addr in batch)
            await client.post(
                "/",
                content=f"INSERT INTO _tmp_replay_qualified VALUES {values}",
                params={"database": db},
            )
            batch = []
    if batch:
        values = ", ".join(f"('{addr}')" for addr in batch)
        await client.post(
            "/",
            content=f"INSERT INTO _tmp_replay_qualified VALUES {values}",
            params={"database": db},
        )


async def load_trades(
    backend: ClickHouseBackend,
    month: str,
    qualified_traders: set[str],
) -> list[NormalizedTrade]:
    """Load one month of trades from qualified makers only.

    Uses a CH temp table for the qualified set (avoids giant IN clause).
    Main speed optimization: 6M → ~500K trades.
    """
    t0 = time.monotonic()
    await _ensure_qualified_table(backend, qualified_traders)

    df = await backend.query_custom(f"""
        SELECT t.*
        FROM trades_raw AS t FINAL
        INNER JOIN _tmp_replay_qualified AS q ON lower(t.maker) = q.trader
        WHERE toStartOfMonth(t.timestamp) = '{month}'
        ORDER BY t.timestamp
    """)
    log.info(
        "load_trades", month=month, rows=len(df),
        elapsed=f"{time.monotonic()-t0:.1f}s",
    )
    trades = [
        t for row in df.iter_rows(named=True)
        if (t := _row_to_trade(row)) is not None
    ]
    trades.sort(key=lambda t: t.published_at)
    return trades


# ── Resolution loading (asset_id-based, no strings) ───────────────────


async def load_resolutions(backend: ClickHouseBackend):
    """Load resolution + token_map from CH markets_resolved."""
    t0 = time.monotonic()
    df = await backend.query_custom("""
        SELECT condition_id, asset_id, outcome, token_won,
               toFloat64(resolved_at) AS resolved_epoch
        FROM markets_resolved
        WHERE resolved_at IS NOT NULL AND resolved_at > '1970-01-02'
    """)
    rows = list(df.iter_rows(named=True))
    resolutions, token_map = load_resolutions_from_rows(rows)
    log.info(
        "resolutions_loaded",
        count=len(resolutions),
        token_map=len(token_map),
        elapsed=f"{time.monotonic()-t0:.1f}s",
    )
    return resolutions, token_map


# ── Monthly replay ────────────────────────────────────────────────────


async def replay_month(
    backend: ClickHouseBackend,
    month: str,
    resolutions: dict,
    token_map: dict,
    output_dir: Path,
    *,
    bankroll: float = 500.0,
) -> dict:
    """Replay one month using ReplayRunner."""
    t0 = time.monotonic()

    # 1. Provider: compute features
    provider = S1HitRateProvider(
        lookback_months=9, min_positions=20, min_hr=0.75, initial_bankroll=bankroll,
    )
    await provider.compute(backend)
    n_qualified = len(provider._qualified)
    log.info("provider_computed", month=month, qualified=n_qualified)

    if n_qualified == 0:
        return {"month": month, "skipped": True}

    # 2. Load trades (qualified makers only — 10x fewer)
    trades = await load_trades(backend, month, provider._qualified)
    if not trades:
        return {"month": month, "skipped": True}

    # 3. Calibrate spreads
    cal_df = await backend.query_custom(f"""
        SELECT condition_id, price, amount_usd
        FROM trades_raw FINAL
        WHERE toStartOfMonth(timestamp) = toDate('{month}') - INTERVAL 1 MONTH
        ORDER BY timestamp LIMIT 200000
    """)
    market_spreads = (
        calibrate_spreads(cal_df, method="median_abs_change") if len(cal_df) > 0 else {}
    )
    market_volumes = calibrate_volumes(cal_df) if len(cal_df) > 0 else {}

    # 4. Build components
    strategy = S1HitRateCopyStrategy(
        sizing_mode="fractional", sizing_fraction=0.02,
        max_bet_usd=500.0, min_consensus=4,
    )
    executor = RealisticFillSimulator(
        config=FillModelConfig(fallback_half_spread=0.01),
        market_spreads=market_spreads,
        market_volumes=market_volumes,
        fee_pct=0.0,
    )
    gw = ExecutionGateway(executor, strategy_budgets={"s1_hitrate_copy": 1_000_000.0})
    ctx = InMemoryContext()
    ctx.update_features(provider.get_features())
    ledger = ParquetLedger(output_dir / f"replay_{month[:7]}.parquet")

    # 5. ReplayRunner — asset_id-based resolution, provider in loop
    runner = ReplayRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gw,
        config=CONFIG,
        providers=[provider],
        resolutions=resolutions,
        token_map=token_map,
        ledger=ledger,
    )
    result = await runner.run(trades)

    # 6. Flush + analytics
    await ledger.flush()
    records = await ledger.read_all()
    summary = compute_summary(records) if records else None

    elapsed = time.monotonic() - t0

    # 7. Gap analysis: per-outcome stats + rejection breakdown
    won = [r for r in records if r.resolution == "WON"]
    lost = [r for r in records if r.resolution == "LOST"]
    pending = [r for r in records if r.resolution is None]
    yes_recs = [r for r in records if r.outcome == "YES"]
    no_recs = [r for r in records if r.outcome == "NO"]
    yes_won = sum(1 for r in yes_recs if r.resolution == "WON")
    no_won = sum(1 for r in no_recs if r.resolution == "WON")

    # Rejection reasons
    rej_reasons: dict[str, int] = {}
    for _, reason in result.rejected_intents:
        rej_reasons[reason] = rej_reasons.get(reason, 0) + 1

    log.info(
        "month_done", month=month, trades=len(trades),
        fills=result.total_fills, settled=runner.n_settled,
        won=len(won), lost=len(lost), pending=len(pending),
        yes_n=len(yes_recs), yes_won=yes_won,
        no_n=len(no_recs), no_won=no_won,
        rejections=rej_reasons,
        elapsed=f"{elapsed:.1f}s",
    )

    # Save detailed fill log as parquet
    if records:
        fill_rows = []
        for r in records:
            fill_rows.append({
                "signal_time": r.signal_time,
                "condition_id": r.condition_id,
                "asset_id": r.asset_id,
                "outcome": r.outcome,
                "fill_price": r.fill_price,
                "fill_fee": r.fill_fee_usd,
                "resolution": r.resolution,
                "pnl_gross": r.pnl_gross,
                "pnl_net": r.pnl_net,
                "hold_s": r.hold_duration_s,
                "reason": r.reason,
            })
        fill_df = pl.DataFrame(fill_rows)
        fill_df.write_parquet(output_dir / f"fills_{month[:7]}.parquet")

    return {
        "month": month,
        "trades": len(trades),
        "qualified": n_qualified,
        "intents": result.total_intents,
        "fills": result.total_fills,
        "settled": runner.n_settled,
        "rejected": len(result.rejected_intents),
        "rej_reasons": rej_reasons,
        "pnl": summary.total_pnl_net if summary else 0.0,
        "hr": summary.hit_rate if summary else 0.0,
        "fees": summary.total_fees if summary else 0.0,
        "sharpe": summary.sharpe if summary else 0.0,
        "max_dd": summary.max_drawdown if summary else 0.0,
        "yes_n": len(yes_recs),
        "yes_hr": yes_won / max(len(yes_recs), 1),
        "no_n": len(no_recs),
        "no_hr": no_won / max(len(no_recs), 1),
        "avg_hold_h": (
            sum(r.hold_duration_s or 0 for r in records) / max(len(records), 1) / 3600
        ),
        "elapsed_s": elapsed,
        "skipped": False,
    }


async def main(n_months: int = 8) -> None:
    structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
    output_dir = Path("research/output/replay")
    output_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    backend = ClickHouseBackend(host=CH_HOST, port=CH_PORT, database=CH_DB)
    resolutions, token_map = await load_resolutions(backend)

    months = TEST_MONTHS[:n_months]
    results = []
    bankroll = 500.0

    for month in months:
        r = await replay_month(
            backend, month, resolutions, token_map, output_dir, bankroll=bankroll,
        )
        results.append(r)
        if not r.get("skipped"):
            bankroll = max(bankroll + r.get("pnl", 0.0), 10.0)

    await backend.close()

    # Summary
    w = 120
    print("\n" + "=" * w)
    print("S1 TICK-BY-TICK WALK-FORWARD REPLAY (ReplayRunner)")
    print("=" * w)
    hdr = (
        f"{'Month':<10} {'Trades':>9} {'Fills':>6} {'Sttld':>6} "
        f"{'PnL$':>8} {'HR':>6} "
        f"{'YES':>5}{'yHR':>5} {'NO':>5}{'nHR':>5} "
        f"{'Hold':>6} {'Rej':>5} {'Sharpe':>7}"
    )
    print(hdr)
    print("-" * w)

    cum_pnl = 0.0
    for r in results:
        if r.get("skipped"):
            print(f"{r['month']:<10} SKIPPED")
            continue
        cum_pnl += r["pnl"]
        print(
            f"{r['month']:<10} {r['trades']:>9,} {r['fills']:>6} "
            f"{r['settled']:>6} {r['pnl']:>8,.0f} {r['hr']:>5.1%} "
            f"{r['yes_n']:>5}{r['yes_hr']:>5.0%} "
            f"{r['no_n']:>5}{r['no_hr']:>5.0%} "
            f"{r['avg_hold_h']:>5.0f}h {r['rejected']:>5} "
            f"{r['sharpe']:>7.2f}"
        )

    print("-" * w)
    print(f"Cumulative PnL: ${cum_pnl:,.0f}")

    # Rejection breakdown
    all_rej: dict[str, int] = {}
    for r in results:
        for reason, cnt in r.get("rej_reasons", {}).items():
            all_rej[reason] = all_rej.get(reason, 0) + cnt
    if all_rej:
        print(f"\nRejections: {all_rej}")

    save_results = [
        {k: v for k, v in r.items() if k != "rej_reasons"}
        for r in results if not r.get("skipped")
    ]
    df = pl.DataFrame(save_results)
    df.write_parquet(output_dir / "s1_replay_summary.parquet")
    print(f"\nSaved to {output_dir}/s1_replay_summary.parquet")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=8)
    args = parser.parse_args()
    asyncio.run(main(n_months=args.months))
