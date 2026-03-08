"""Fast replay data loading from Parquet snapshot.

Loads trades via Polars with predicate pushdown on (condition_id, timestamp)
sorted Parquet files. Converts to lightweight ReplayTick objects (~0.5 μs each
vs ~10 μs for NormalizedTrade).

Usage:
    from research.fast_replay import load_replay_trades, load_replay_resolutions

    # Load filtered trades (Polars scan with predicate pushdown)
    ticks = load_replay_trades(universe={"0xabc...", "0xdef..."})

    # Load resolutions from Parquet snapshot
    resolutions, token_map = load_replay_resolutions()

    # Run replay
    runner = ReplayRunner(strategy, ctx, gateway, resolutions=resolutions,
                          token_map=token_map, ledger=ledger)
    result = await runner.run(ticks)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)

DATA_DIR = Path("data/research")
TRADES_DIR = DATA_DIR / "trades"


@dataclass(slots=True)
class ReplayTick:
    """Lightweight trade object for replay — duck-types as NormalizedTrade.

    Construction: ~0.5 μs vs ~10 μs for Pydantic NormalizedTrade.
    Provides all fields accessed by ReplayRunner, strategies, and providers.
    """

    condition_id: str
    asset_id: str
    side: str  # "BUY" or "SELL"
    price: float
    amount_usd: float
    fee_usd: float
    maker: str | None
    published_at: float  # epoch seconds
    # Derived fields accessed by some strategies
    trade_id: str = ""
    size: float = 0.0  # tokens = amount_usd / price


def load_replay_trades(
    *,
    universe: set[str] | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
    trades_dir: Path = TRADES_DIR,
    as_ticks: bool = True,
) -> list[ReplayTick] | pl.DataFrame:
    """Load trades from Parquet snapshot with optional filtering.

    Parameters
    ----------
    universe:
        Set of condition_ids to filter. Uses Parquet predicate pushdown
        on (condition_id, timestamp) sorted files — skips 95%+ of row groups.
    start_month:
        First month to include (YYYYMM format, e.g. 202401).
    end_month:
        Last month to include (YYYYMM format, e.g. 202412).
    trades_dir:
        Directory with trades_YYYYMM.parquet files.
    as_ticks:
        If True, convert to list[ReplayTick]. If False, return DataFrame.

    Returns
    -------
    Sorted by published_at (timestamp).
    """
    t0 = time.time()

    # Select files by month range
    files = sorted(trades_dir.glob("trades_*.parquet"))
    if start_month is not None:
        files = [f for f in files if int(f.stem.split("_")[1]) >= start_month]
    if end_month is not None:
        files = [f for f in files if int(f.stem.split("_")[1]) <= end_month]

    if not files:
        logger.warning("fast_replay.no_files", dir=str(trades_dir))
        return [] if as_ticks else pl.DataFrame()

    # Lazy scan with predicate pushdown
    lf = pl.scan_parquet(files)

    if universe is not None:
        lf = lf.filter(pl.col("condition_id").is_in(list(universe)))

    # Compute published_at from timestamp (datetime[ms, UTC] → epoch float)
    # Map side from CH Enum8 (1=BUY, 2=SELL) to string
    # Compute size (tokens) from amount_usd / price
    lf = lf.with_columns(
        (pl.col("timestamp").dt.epoch("s").cast(pl.Float64)).alias("published_at"),
        pl.when(pl.col("side") == 1).then(pl.lit("BUY")).otherwise(pl.lit("SELL")).alias("side"),
        (pl.col("amount_usd") / pl.col("price").clip(lower_bound=0.0001)).alias("size"),
    ).sort("published_at")

    df = lf.collect()

    load_elapsed = time.time() - t0
    logger.info(
        "fast_replay.loaded",
        rows=len(df),
        files=len(files),
        universe_size=len(universe) if universe else 0,
        elapsed_s=round(load_elapsed, 2),
    )

    if not as_ticks:
        return df

    # Convert to ReplayTick objects (~0.5 μs each)
    t1 = time.time()
    ticks = _df_to_ticks(df)
    convert_elapsed = time.time() - t1
    logger.info(
        "fast_replay.converted",
        ticks=len(ticks),
        convert_s=round(convert_elapsed, 2),
        total_s=round(time.time() - t0, 2),
    )
    return ticks


def _df_to_ticks(df: pl.DataFrame) -> list[ReplayTick]:
    """Convert DataFrame to ReplayTick list. Vectorized column extraction."""
    cids = df["condition_id"].to_list()
    aids = df["asset_id"].to_list()
    sides = df["side"].to_list()
    prices = df["price"].to_list()
    amounts = df["amount_usd"].to_list()
    fees = df["fee_usd"].to_list()
    makers = df["maker"].to_list()
    pubs = df["published_at"].to_list()
    sizes = df["size"].to_list()

    n = len(cids)
    ticks = [None] * n
    for i in range(n):
        ticks[i] = ReplayTick(
            condition_id=cids[i],
            asset_id=aids[i],
            side=sides[i],
            price=prices[i],
            amount_usd=amounts[i],
            fee_usd=fees[i],
            maker=makers[i],
            published_at=pubs[i],
            size=sizes[i],
        )
    return ticks  # type: ignore[return-value]


def load_replay_resolutions(
    meta_dir: Path = DATA_DIR / "metadata",
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Load resolutions and token_map from Parquet snapshot.

    Returns
    -------
    (resolutions, token_map) compatible with ReplayRunner constructor.
    """
    from polymarket_pipeline.strategies.runners.replay import (
        MarketResolution,
    )

    mr_path = meta_dir / "markets_resolved.parquet"
    if not mr_path.exists():
        raise FileNotFoundError(f"{mr_path} not found. Run export_snapshot.py first.")

    df = pl.read_parquet(mr_path)

    # Build token_map: condition_id → {"YES": asset_id, "NO": asset_id}
    token_map: dict[str, dict[str, str]] = {}
    # Build winning asset_ids per condition_id
    winning: dict[str, set[str]] = {}
    resolved_at: dict[str, float] = {}

    for row in df.iter_rows(named=True):
        cid = str(row["condition_id"])
        asset_id = str(row["asset_id"])
        outcome = str(row["outcome"])
        token_won = row["token_won"]
        res_at = row["resolved_at"]

        if cid not in token_map:
            token_map[cid] = {}
        token_map[cid][outcome] = asset_id

        if token_won:
            winning.setdefault(cid, set()).add(asset_id)

        if res_at is not None:
            # Convert datetime to epoch float
            if hasattr(res_at, "timestamp"):
                resolved_at[cid] = res_at.timestamp()
            else:
                resolved_at[cid] = float(res_at)

    resolutions: dict[str, MarketResolution] = {}
    for cid, winners in winning.items():
        epoch = resolved_at.get(cid, 0.0)
        if epoch > 0:
            resolutions[cid] = MarketResolution(
                condition_id=cid,
                resolved_at=epoch,
                winning_asset_ids=frozenset(winners),
            )

    logger.info(
        "fast_replay.resolutions_loaded",
        n_resolutions=len(resolutions),
        n_token_map=len(token_map),
    )
    return resolutions, token_map


def load_calibration_df(
    *,
    universe: set[str] | None = None,
    trades_dir: Path = TRADES_DIR,
) -> pl.DataFrame:
    """Load trades as DataFrame for calibrate_spreads/calibrate_volumes.

    Skips per-object construction entirely — pass directly to calibrate_*().
    """
    lf = pl.scan_parquet(sorted(trades_dir.glob("trades_*.parquet")))
    if universe is not None:
        lf = lf.filter(pl.col("condition_id").is_in(list(universe)))
    return lf.select("condition_id", "price", "amount_usd").collect()
