"""Build pipeline: Gamma API metadata, compact trades, derived tables, market prices.

Single entry point for all data preparation. Each step is idempotent and skip-if-fresh.

Usage:
    uv run python scripts/build_data.py                        # all steps
    uv run python scripts/build_data.py --step metadata        # Gamma API fetch
    uv run python scripts/build_data.py --step compact         # recompress raw parquet
    uv run python scripts/build_data.py --step derived         # DuckDB PnL + MVF
    uv run python scripts/build_data.py --step prices          # market prices
    uv run python scripts/build_data.py --lookback 2024-01-01  # custom start date
    uv run python scripts/build_data.py --force-metadata       # re-fetch even if fresh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import structlog

log = structlog.get_logger()

DATA_DIR = Path("data")
METADATA_DIR = DATA_DIR / "metadata"
COMPACT_DIR = DATA_DIR / "compact"
DERIVED_DIR = DATA_DIR / "derived"

RAW_PARQUET_DIR = Path("order_filled")
OLD_COMPACT_DIR = Path("order_filled_compact")

FETCH_META_PATH = METADATA_DIR / "_fetch_meta.json"
FRESHNESS_HOURS = 24


# ---------------------------------------------------------------------------
# Step 1: Metadata
# ---------------------------------------------------------------------------
async def step_metadata(force: bool = False) -> None:
    """Fetch events/markets/tokens from Gamma API and write parquet."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force and FETCH_META_PATH.exists():
        meta = json.loads(FETCH_META_PATH.read_text())
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        if age_hours < FRESHNESS_HOURS:
            log.info("metadata_fresh", age_hours=f"{age_hours:.1f}", skip=True)
            return

    from polymarket_pipeline.market_sync import fetch_events

    log.info("fetching_metadata")
    t0 = time.monotonic()
    result = await fetch_events()

    # token_market_map.parquet
    tm_rows = [
        {"asset_id": e.asset_id, "condition_id": e.condition_id, "outcome": e.outcome}
        for e in result.token_entries
    ]
    df_tm = pl.DataFrame(tm_rows)
    df_tm.write_parquet(METADATA_DIR / "token_market_map.parquet", compression="zstd")

    # markets.parquet
    market_rows = []
    for m in result.markets:
        market_rows.append({
            "condition_id": m.condition_id,
            "event_id": m.event_id,
            "question": m.question,
            "slug": m.slug,
            "category": m.category,
            "token_yes": m.token_yes,
            "token_no": m.token_no,
            "neg_risk": m.neg_risk,
            "status": str(m.status),
            "created_at": m.created_at,
            "closed_at": m.closed_at,
            "resolved_at": m.resolved_at,
        })
    df_markets = pl.DataFrame(market_rows)
    df_markets.write_parquet(METADATA_DIR / "markets.parquet", compression="zstd")

    elapsed = time.monotonic() - t0
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "token_entries": len(tm_rows),
        "markets": len(market_rows),
        "events": len(result.events),
        "elapsed_sec": round(elapsed, 1),
    }
    FETCH_META_PATH.write_text(json.dumps(meta, indent=2))

    log.info(
        "metadata_complete",
        tokens=len(tm_rows),
        markets=len(market_rows),
        elapsed=f"{elapsed:.1f}s",
    )


# ---------------------------------------------------------------------------
# Step 2: Compact
# ---------------------------------------------------------------------------
async def step_compact() -> None:
    """Recompress raw parquet into compact files under data/compact/."""
    COMPACT_DIR.mkdir(parents=True, exist_ok=True)

    tm_path = METADATA_DIR / "token_market_map.parquet"
    if not tm_path.exists():
        log.error("token_map_missing", hint="Run --step metadata first")
        raise SystemExit(1)

    if not RAW_PARQUET_DIR.exists():
        log.error("raw_parquet_missing", path=str(RAW_PARQUET_DIR))
        raise SystemExit(1)

    # Migrate from old compact dir if it exists and new is empty
    manifest_path = COMPACT_DIR / "_manifest.json"
    if OLD_COMPACT_DIR.exists() and not manifest_path.exists():
        old_manifest = OLD_COMPACT_DIR / "_manifest.json"
        if old_manifest.exists():
            log.info("migrating_old_compact", src=str(OLD_COMPACT_DIR), dst=str(COMPACT_DIR))
            # Copy manifest and compact files
            shutil.copy2(old_manifest, manifest_path)
            for f in OLD_COMPACT_DIR.glob("compact_*.parquet"):
                dst = COMPACT_DIR / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
            log.info("migration_complete")

    from scripts.recompress_parquet import run_recompress

    await run_recompress(
        parquet_dir=RAW_PARQUET_DIR,
        output_dir=COMPACT_DIR,
        pg_dsn="",  # unused when token_map_path is set
        batch_files=20,
        workers=max(((__import__("os").cpu_count() or 4) - 4), 4),
        row_group_size=500_000,
        token_map_path=tm_path,
    )


# ---------------------------------------------------------------------------
# Step 3: Derived tables
# ---------------------------------------------------------------------------
def step_derived(lookback: str) -> None:
    """Compute PnL, MVF, and resolved markets via DuckDB."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)

    from polymarket_pipeline.exploration.components import (
        MakerFractionConfig,
        PnLConfig,
        ResolvedMarketsConfig,
        compute_market_pnl,
        compute_mvf,
        query_resolved_markets,
    )
    from polymarket_pipeline.exploration.duckdb_source import DuckDBDataSource

    log.info("derived_start", lookback=lookback)
    t0 = time.monotonic()

    db = DuckDBDataSource(compact_dir=COMPACT_DIR, metadata_dir=METADATA_DIR)

    # 1. PnL
    pnl_cfg = PnLConfig(lookback_start=lookback, perspective="both")
    pnl_result = compute_market_pnl(db, pnl_cfg, DERIVED_DIR)
    log.info("pnl_complete", rows=pnl_result.metrics.get("trader_market_pairs", 0))

    # 2. MVF
    mvf_cfg = MakerFractionConfig(lookback_start=lookback)
    mvf_result = compute_mvf(db, mvf_cfg, DERIVED_DIR)
    log.info("mvf_complete", traders=mvf_result.metrics.get("total_traders", 0))

    # 3. Resolved markets
    resolved_cfg = ResolvedMarketsConfig(lookback_start=lookback)
    resolved_result = query_resolved_markets(db, resolved_cfg, DERIVED_DIR)
    log.info(
        "resolved_complete",
        markets=resolved_result.metrics.get("resolved_market_count", 0),
    )

    # 4. markets_resolved — join with metadata resolved_at for backtester
    markets_meta = pl.read_parquet(METADATA_DIR / "markets.parquet")
    resolved_df = resolved_result.dataframes["resolved_markets"]
    markets_resolved = resolved_df.join(
        markets_meta.select(["condition_id", "resolved_at"]),
        on="condition_id",
        how="inner",
    )
    markets_resolved.write_parquet(DERIVED_DIR / "markets_resolved.parquet")
    log.info("markets_resolved_complete", rows=markets_resolved.height)

    db.close()
    elapsed = time.monotonic() - t0
    log.info("derived_complete", elapsed=f"{elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Step 4: Market prices
# ---------------------------------------------------------------------------
def step_prices(lookback: str) -> None:
    """Extract YES price timeseries from compact trades via DuckDB."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)

    import duckdb

    log.info("prices_start", lookback=lookback)
    t0 = time.monotonic()

    compact_glob = str(COMPACT_DIR / "compact_*.parquet")
    tm_path = str(METADATA_DIR / "token_market_map.parquet")

    conn = duckdb.connect(":memory:")
    conn.execute(f"CREATE VIEW trades AS SELECT * FROM read_parquet('{compact_glob}')")
    conn.execute(f"CREATE VIEW tm AS SELECT * FROM read_parquet('{tm_path}')")

    result = conn.execute(f"""
        SELECT
            t.condition_id,
            epoch(t.timestamp) AS timestamp,
            CASE WHEN tm.outcome = 'YES' THEN t.price
                 ELSE 1.0 - t.price END AS yes_price
        FROM trades t
        JOIN tm ON t.asset_id = tm.asset_id
        WHERE t.timestamp >= '{lookback}'
    """).fetch_arrow_table()

    df = pl.from_arrow(result)
    conn.close()

    # Sort for efficient asof joins in backtester
    df = df.sort(["condition_id", "timestamp"])

    out_path = DERIVED_DIR / "market_prices.parquet"
    df.write_parquet(out_path, compression="zstd")

    log.info(
        "prices_complete",
        rows=df.height,
        markets=df["condition_id"].n_unique(),
        elapsed=f"{time.monotonic() - t0:.1f}s",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Build all derived data from raw sources")
    parser.add_argument(
        "--step",
        choices=["metadata", "compact", "derived", "prices"],
        default=None,
        help="Run only this step (default: all steps in order)",
    )
    parser.add_argument(
        "--lookback",
        default="2022-01-01",
        help="Earliest date for derived tables and prices (default: 2022-01-01)",
    )
    parser.add_argument(
        "--force-metadata",
        action="store_true",
        help="Re-fetch metadata even if _fetch_meta.json is fresh",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
    )

    steps = [args.step] if args.step else ["metadata", "compact", "derived", "prices"]

    t0 = time.monotonic()

    for step in steps:
        log.info("step_start", step=step)
        if step == "metadata":
            asyncio.run(step_metadata(force=args.force_metadata))
        elif step == "compact":
            asyncio.run(step_compact())
        elif step == "derived":
            step_derived(args.lookback)
        elif step == "prices":
            step_prices(args.lookback)

    elapsed = time.monotonic() - t0
    log.info("build_complete", steps=steps, elapsed=f"{elapsed:.1f}s")


if __name__ == "__main__":
    main()
