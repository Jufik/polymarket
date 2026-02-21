"""Build pipeline: CLOB + Gamma metadata, compact trades, Polars-native derived tables.

Single entry point for all data preparation. Each step is idempotent and skip-if-fresh.

Resolution source: CLOB API tokens[].winner (ground truth, ~100% coverage post-2023).
Gamma API provides supplementary metadata (event_id, category, closed_at).
Derived tables use pure Polars (no DuckDB).

Usage:
    uv run python scripts/build_data.py                        # all steps
    uv run python scripts/build_data.py --step metadata        # CLOB + Gamma fetch
    uv run python scripts/build_data.py --step compact         # recompress raw parquet
    uv run python scripts/build_data.py --step derived         # Polars PnL + MVF
    uv run python scripts/build_data.py --step prices          # market prices
    uv run python scripts/build_data.py --force-metadata       # re-fetch even if fresh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from base64 import b64decode
from datetime import UTC, datetime
from pathlib import Path

import httpx
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

CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_PAGE_SIZE = 500


# ---------------------------------------------------------------------------
# Step 1: Metadata (CLOB + Gamma)
# ---------------------------------------------------------------------------


async def _fetch_clob_markets() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Fetch all markets from CLOB API. Returns (markets_df, token_map_df)."""
    market_rows: list[dict] = []
    token_rows: list[dict] = []

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

                # Derive resolution from tokens[].winner
                closed = bool(m.get("closed", False))
                winners = [t for t in tokens if t.get("winner") is True]

                if closed and len(winners) == 1:
                    resolution_value = 1  # resolved: one winner
                    winner_outcome = winners[0].get("outcome", "")
                elif closed and len(winners) == 0:
                    resolution_value = -1  # voided: closed but no winner
                    winner_outcome = ""
                else:
                    resolution_value = 0  # unresolved (or still open)
                    winner_outcome = ""

                market_rows.append(
                    {
                        "condition_id": condition_id,
                        "question": m.get("question", ""),
                        "market_slug": m.get("market_slug", ""),
                        "closed": closed,
                        "active": bool(m.get("active", False)),
                        "end_date_iso": m.get("end_date_iso", ""),
                        "neg_risk": bool(m.get("neg_risk", False)),
                        "resolution_value": resolution_value,
                        "winner_outcome": winner_outcome,
                        "tags": ",".join(m.get("tags") or []),
                    }
                )

                for idx, t in enumerate(tokens):
                    token_id = t.get("token_id", "")
                    if not token_id:
                        continue
                    token_rows.append(
                        {
                            "asset_id": token_id,
                            "condition_id": condition_id,
                            "outcome": t.get("outcome", ""),
                            "winner": bool(t.get("winner", False)),
                            "token_index": idx,  # 0 = affirmative/"YES" side
                        }
                    )

            # Check pagination
            next_cursor = body.get("next_cursor", "")
            if not next_cursor:
                break

            # Decode cursor to check for end sentinel (-1)
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

    df_markets = pl.DataFrame(market_rows)
    df_tokens = pl.DataFrame(token_rows)
    return df_markets, df_tokens


async def _fetch_gamma_markets() -> pl.DataFrame:
    """Fetch market metadata from Gamma API for supplementary fields."""
    from polymarket_pipeline.market_sync import fetch_events

    result = await fetch_events()

    rows = []
    for m in result.markets:
        rows.append(
            {
                "condition_id": m.condition_id,
                "event_id": m.event_id,
                "slug": m.slug,
                "category": m.category,
                "status": str(m.status),
                "created_at": m.created_at,
                "closed_at": m.closed_at,
            }
        )

    log.info("gamma_fetch_complete", markets=len(rows))
    return pl.DataFrame(rows)


async def step_metadata(force: bool = False) -> None:
    """Fetch from CLOB (resolution truth) + Gamma (supplementary), merge, write parquet."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force and FETCH_META_PATH.exists():
        meta = json.loads(FETCH_META_PATH.read_text())
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        age_hours = (datetime.now(UTC) - fetched_at).total_seconds() / 3600
        if age_hours < FRESHNESS_HOURS:
            log.info("metadata_fresh", age_hours=f"{age_hours:.1f}", skip=True)
            return

    t0 = time.monotonic()

    # 1a. CLOB API — ground truth for resolution
    log.info("fetching_clob_markets")
    clob_markets, token_map = await _fetch_clob_markets()

    # Write token_map immediately (needed by compact step)
    token_map.write_parquet(METADATA_DIR / "token_map.parquet", compression="zstd")
    log.info("token_map_written", rows=token_map.height)

    # 1b. Gamma API — supplementary metadata
    log.info("fetching_gamma_markets")
    gamma_markets = await _fetch_gamma_markets()

    # 1c. Merge: CLOB left-join Gamma on condition_id
    # Resolution comes from CLOB winner (ground truth)
    # closed_at comes from Gamma closedTime (= resolution timestamp for resolved markets)
    merged = clob_markets.join(
        gamma_markets.select(
            [
                "condition_id",
                "event_id",
                "slug",
                "category",
                "closed_at",
            ]
        ),
        on="condition_id",
        how="left",
    )

    # Derive resolved_at: use Gamma's closed_at when CLOB says resolved (resolution_value=1)
    merged = merged.with_columns(
        pl.when(pl.col("resolution_value") == 1)
        .then(pl.col("closed_at"))
        .otherwise(pl.lit(None))
        .alias("resolved_at")
    )

    merged.write_parquet(METADATA_DIR / "markets.parquet", compression="zstd")

    elapsed = time.monotonic() - t0
    meta_info = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "clob_markets": clob_markets.height,
        "gamma_markets": gamma_markets.height,
        "merged_markets": merged.height,
        "token_entries": token_map.height,
        "resolved": int(merged.filter(pl.col("resolution_value") == 1).height),
        "voided": int(merged.filter(pl.col("resolution_value") == -1).height),
        "elapsed_sec": round(elapsed, 1),
    }
    FETCH_META_PATH.write_text(json.dumps(meta_info, indent=2))

    log.info(
        "metadata_complete",
        clob=clob_markets.height,
        gamma=gamma_markets.height,
        merged=merged.height,
        tokens=token_map.height,
        resolved=meta_info["resolved"],
        voided=meta_info["voided"],
        elapsed=f"{elapsed:.1f}s",
    )


# ---------------------------------------------------------------------------
# Step 2: Compact
# ---------------------------------------------------------------------------


async def step_compact() -> None:
    """Recompress raw parquet into compact files under data/compact/."""
    COMPACT_DIR.mkdir(parents=True, exist_ok=True)

    tm_path = METADATA_DIR / "token_map.parquet"
    if not tm_path.exists():
        log.error("token_map_missing", hint="Run --step metadata first")
        raise SystemExit(1)

    if not RAW_PARQUET_DIR.exists():  # noqa: ASYNC240
        log.error("raw_parquet_missing", path=str(RAW_PARQUET_DIR))
        raise SystemExit(1)

    # Migrate from old compact dir if it exists and new is empty
    manifest_path = COMPACT_DIR / "_manifest.json"
    if OLD_COMPACT_DIR.exists() and not manifest_path.exists():  # noqa: ASYNC240
        old_manifest = OLD_COMPACT_DIR / "_manifest.json"
        if old_manifest.exists():
            log.info("migrating_old_compact", src=str(OLD_COMPACT_DIR), dst=str(COMPACT_DIR))
            shutil.copy2(old_manifest, manifest_path)
            for f in OLD_COMPACT_DIR.glob("compact_*.parquet"):  # noqa: ASYNC240
                dst = COMPACT_DIR / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
            log.info("migration_complete")

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "recompress_parquet", Path(__file__).parent / "recompress_parquet.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recompress_parquet"] = mod
    spec.loader.exec_module(mod)

    await mod.run_recompress(
        parquet_dir=RAW_PARQUET_DIR,
        output_dir=COMPACT_DIR,
        pg_dsn="",  # unused when token_map_path is set
        batch_files=20,
        workers=max(((__import__("os").cpu_count() or 4) - 4), 4),
        row_group_size=500_000,
        token_map_path=tm_path,
    )


# ---------------------------------------------------------------------------
# Step 3: Derived tables (pure Polars)
# ---------------------------------------------------------------------------


def step_derived() -> None:
    """Compute PnL, MVF, and resolved markets. Uses DuckDB for out-of-core aggregation."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)

    log.info("derived_start")
    t0 = time.monotonic()

    # Load metadata
    markets = pl.read_parquet(METADATA_DIR / "markets.parquet")
    token_map = pl.read_parquet(METADATA_DIR / "token_map.parquet")

    # Compact trade files
    compact_files = sorted(COMPACT_DIR.glob("compact_*.parquet"))
    if not compact_files:
        log.error("no_compact_files", hint="Run --step compact first")
        raise SystemExit(1)

    log.info("loading_compact", files=len(compact_files))

    # --- 3a. markets_resolved.parquet ---
    # resolution_value: 1=resolved (any winner), 0=unresolved, -1=voided
    # Only include actually resolved markets (resolution_value == 1)
    resolved = markets.filter(pl.col("resolution_value") == 1).select(
        [
            "condition_id",
            "resolution_value",
            "resolved_at",
            "winner_outcome",
        ]
    )
    # Compute yes_won: True if the winning token was token_index=0 (affirmative/YES side)
    # This gives us directional resolution without relying on outcome string matching
    yes_tokens = token_map.filter(
        (pl.col("token_index") == 0) & (pl.col("winner") == True)  # noqa: E712
    ).select("condition_id").with_columns(pl.lit(True).alias("yes_won"))
    resolved = resolved.join(yes_tokens, on="condition_id", how="left").with_columns(
        pl.col("yes_won").fill_null(False)
    )
    resolved.write_parquet(DERIVED_DIR / "markets_resolved.parquet", compression="zstd")
    log.info(
        "markets_resolved_complete",
        rows=resolved.height,
        yes_won=int(resolved["yes_won"].sum()),
        no_won=int((~resolved["yes_won"]).sum()),
    )

    # --- 3b. trader_market_pnl.parquet ---
    # Process in chunks to stay within memory. Each compact file is ~200MB.
    # We aggregate per-file then combine, which is exact for sum/count/min/max.
    log.info("computing_pnl")
    t_pnl = time.monotonic()

    resolved_set = set(resolved["condition_id"].to_list())
    winner_map: dict[str, str] = dict(
        zip(
            resolved["condition_id"].to_list(),
            resolved["winner_outcome"].to_list(),
            strict=True,
        )
    )
    outcome_map: dict[str, str] = dict(
        zip(
            token_map["asset_id"].to_list(),
            token_map["outcome"].to_list(),
            strict=True,
        )
    )
    # token_index: 0 = affirmative/YES side, 1 = negative/NO side
    token_index_map: dict[str, int] = dict(
        zip(
            token_map["asset_id"].to_list(),
            token_map["token_index"].to_list(),
            strict=True,
        )
    )

    # Write per-file aggregates to temp parquet, then DuckDB to final aggregate.
    # This keeps memory bounded: only 1 compact file in memory at a time.
    tmp_pnl_dir = DERIVED_DIR / "_tmp_pnl"
    tmp_pnl_dir.mkdir(parents=True, exist_ok=True)

    # Resume: skip per-file aggregation if temp files already exist
    existing_tmp = sorted(tmp_pnl_dir.glob("pnl_*.parquet"))
    if len(existing_tmp) >= len(compact_files):
        log.info("pnl_tmp_exists", files=len(existing_tmp), skip_generation=True)
    else:
        # Clean partial results and regenerate
        for old in existing_tmp:
            old.unlink()

        for i, f in enumerate(compact_files):
            df = pl.read_parquet(
                f,
                columns=[
                    "condition_id",
                    "asset_id",
                    "side",
                    "price",
                    "size",
                    "amount_usd",
                    "fee_usd",
                    "maker",
                    "taker",
                    "timestamp",
                ],
            )
            # Filter to resolved markets only
            df = df.filter(pl.col("condition_id").is_in(resolved_set))
            if df.height == 0:
                continue

            # Map outcome, token_index, and settlement
            df = df.with_columns(
                pl.col("asset_id")
                .replace_strict(outcome_map, default=None)
                .alias("outcome"),
                pl.col("asset_id")
                .replace_strict(token_index_map, default=None)
                .alias("token_index"),
            )
            df = df.filter(pl.col("outcome").is_not_null())
            df = df.with_columns(
                pl.col("condition_id")
                .replace_strict(winner_map, default="")
                .alias("winner_outcome"),
            )
            df = df.with_columns(
                pl.when(pl.col("outcome") == pl.col("winner_outcome"))
                .then(1.0)
                .otherwise(0.0)
                .alias("spt"),
            )

            # YES-side price: price directly for YES token, 1-price for NO token
            df = df.with_columns(
                pl.when(pl.col("token_index") == 0)
                .then(pl.col("price"))
                .otherwise(1.0 - pl.col("price"))
                .alias("yes_price"),
            )
            # Volume-weighted price numerator for wavg computation
            df = df.with_columns(
                (pl.col("yes_price") * pl.col("amount_usd")).alias("yes_price_x_vol"),
            )

            # Pre-compute signed columns for maker and taker sides
            buy = pl.col("side") == "BUY"
            is_yes_token = pl.col("token_index") == 0
            df = df.with_columns(
                pl.when(buy)
                .then(pl.col("size"))
                .otherwise(-pl.col("size"))
                .alias("m_signed_tok"),
                pl.when(buy)
                .then(-pl.col("amount_usd"))
                .otherwise(pl.col("amount_usd"))
                .alias("m_signed_usd"),
                pl.when(buy)
                .then(-pl.col("size"))
                .otherwise(pl.col("size"))
                .alias("t_signed_tok"),
                pl.when(buy)
                .then(pl.col("amount_usd"))
                .otherwise(-pl.col("amount_usd"))
                .alias("t_signed_usd"),
            )
            # YES-side signed tokens (token_index=0 is affirmative/YES)
            df = df.with_columns(
                pl.when(is_yes_token)
                .then(pl.col("m_signed_tok"))
                .otherwise(0.0)
                .alias("m_yes_tok"),
                pl.when(is_yes_token)
                .then(pl.col("t_signed_tok"))
                .otherwise(0.0)
                .alias("t_yes_tok"),
            )

            common_cols = [
                "condition_id", "fee_usd", "spt", "amount_usd",
                "timestamp", "yes_price_x_vol",
            ]

            # Create maker rows
            maker_df = df.select(
                pl.col("maker").alias("trader"),
                *common_cols,
                pl.col("m_signed_tok").alias("signed_tokens"),
                pl.col("m_signed_usd").alias("signed_usd"),
                pl.col("m_yes_tok").alias("yes_tok"),
            )

            # Create taker rows
            taker_df = df.filter(pl.col("taker").is_not_null()).select(
                pl.col("taker").alias("trader"),
                *common_cols,
                pl.col("t_signed_tok").alias("signed_tokens"),
                pl.col("t_signed_usd").alias("signed_usd"),
                pl.col("t_yes_tok").alias("yes_tok"),
            )

            both = pl.concat([maker_df, taker_df])

            agg = both.group_by(["trader", "condition_id"]).agg(
                pl.col("signed_tokens").sum().alias("net_tokens"),
                pl.col("signed_usd").sum().alias("net_usd_flow"),
                pl.col("fee_usd").sum().alias("total_fees"),
                pl.col("spt").first().alias("spt"),
                pl.col("amount_usd").sum().alias("market_volume"),
                pl.len().alias("trade_count"),
                pl.col("timestamp").min().alias("first_trade"),
                pl.col("timestamp").max().alias("last_trade"),
                pl.col("yes_tok").sum().alias("net_yes_tokens"),
                pl.col("yes_price_x_vol").sum().alias("yes_price_x_vol"),
            )
            agg.write_parquet(tmp_pnl_dir / f"pnl_{i:04d}.parquet")

            if (i + 1) % 20 == 0:
                log.info("pnl_progress", files=i + 1, total=len(compact_files))

    # Final re-aggregate via DuckDB (handles out-of-core natively)
    tmp_files = sorted(tmp_pnl_dir.glob("pnl_*.parquet"))
    log.info("pnl_combining", tmp_files=len(tmp_files))

    import duckdb

    db_path = str(DERIVED_DIR / "_pnl_agg.duckdb")
    conn = duckdb.connect(db_path)
    # Set temp directory and memory limit for out-of-core aggregation
    conn.execute(f"SET temp_directory='{DERIVED_DIR / '_duckdb_tmp'}'")
    conn.execute("SET memory_limit='4GB'")
    tmp_glob = str(tmp_pnl_dir / "pnl_*.parquet")
    pnl_df = conn.execute(f"""
        SELECT
            trader,
            condition_id,
            (SUM(net_tokens) * ANY_VALUE(spt) + SUM(net_usd_flow) - SUM(total_fees))
                AS market_pnl,
            SUM(market_volume) AS market_volume,
            CAST(SUM(trade_count) AS UINTEGER) AS trade_count,
            MIN(first_trade) AS first_trade,
            MAX(last_trade) AS last_trade,
            SUM(net_yes_tokens) AS net_yes_tokens,
            SUM(yes_price_x_vol) / NULLIF(SUM(market_volume), 0)
                AS wavg_yes_entry_price
        FROM read_parquet('{tmp_glob}')
        GROUP BY trader, condition_id
    """).pl()
    conn.close()
    # Cleanup DuckDB files
    Path(db_path).unlink(missing_ok=True)
    shutil.rmtree(DERIVED_DIR / "_duckdb_tmp", ignore_errors=True)

    pnl_df.write_parquet(DERIVED_DIR / "trader_market_pnl.parquet", compression="zstd")
    log.info("pnl_complete", rows=pnl_df.height, elapsed=f"{time.monotonic() - t_pnl:.1f}s")

    # Cleanup temp files
    shutil.rmtree(tmp_pnl_dir)

    # --- 3c. maker_volume_fractions.parquet ---
    log.info("computing_mvf")
    t_mvf = time.monotonic()

    compact_glob = str(COMPACT_DIR / "compact_*.parquet")
    resolved_path = str(DERIVED_DIR / "markets_resolved.parquet")
    mvf_out = DERIVED_DIR / "maker_volume_fractions.parquet"

    conn = duckdb.connect(str(DERIVED_DIR / "_mvf.duckdb"))
    conn.execute(f"SET temp_directory='{DERIVED_DIR / '_duckdb_tmp'}'")
    conn.execute("SET memory_limit='8GB'")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute("SET threads=4")

    mvf_df = conn.execute(f"""
        WITH resolved AS (
            SELECT condition_id FROM read_parquet('{resolved_path}')
        ),
        trades AS (
            SELECT maker, taker, amount_usd, condition_id
            FROM read_parquet('{compact_glob}')
            WHERE condition_id IN (SELECT condition_id FROM resolved)
        ),
        maker_vol AS (
            SELECT maker AS trader, SUM(amount_usd) AS maker_volume
            FROM trades
            GROUP BY maker
        ),
        taker_vol AS (
            SELECT taker AS trader, SUM(amount_usd) AS taker_volume
            FROM trades
            WHERE taker IS NOT NULL
            GROUP BY taker
        )
        SELECT
            COALESCE(m.trader, t.trader) AS trader,
            COALESCE(m.maker_volume, 0) AS maker_volume,
            COALESCE(t.taker_volume, 0) AS taker_volume,
            COALESCE(m.maker_volume, 0) + COALESCE(t.taker_volume, 0) AS total_volume,
            COALESCE(m.maker_volume, 0)
                / (COALESCE(m.maker_volume, 0) + COALESCE(t.taker_volume, 0)) AS mvf
        FROM maker_vol m
        FULL OUTER JOIN taker_vol t ON m.trader = t.trader
    """).pl()
    conn.close()

    Path(str(DERIVED_DIR / "_mvf.duckdb")).unlink(missing_ok=True)
    shutil.rmtree(DERIVED_DIR / "_duckdb_tmp", ignore_errors=True)

    mvf_df.write_parquet(mvf_out, compression="zstd")
    log.info("mvf_complete", traders=mvf_df.height, elapsed=f"{time.monotonic() - t_mvf:.1f}s")

    elapsed = time.monotonic() - t0
    log.info("derived_complete", elapsed=f"{elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Step 4: Market prices (pure Polars)
# ---------------------------------------------------------------------------


def step_prices() -> None:
    """Extract YES price timeseries from compact trades via DuckDB (out-of-core sort)."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)

    log.info("prices_start")
    t0 = time.monotonic()

    token_map_path = METADATA_DIR / "token_map.parquet"
    compact_glob = str(COMPACT_DIR / "compact_*.parquet")

    import duckdb

    db_path = str(DERIVED_DIR / "_prices.duckdb")
    conn = duckdb.connect(db_path)
    conn.execute(f"SET temp_directory='{DERIVED_DIR / '_duckdb_tmp'}'")
    conn.execute("SET memory_limit='12GB'")
    conn.execute("SET preserve_insertion_order=false")

    out_path = DERIVED_DIR / "market_prices.parquet"
    # Write without global sort to avoid OOM on 83M+ rows.
    # Compact files are already sorted by (condition_id, timestamp), so output is
    # locally sorted and has good row-group statistics for predicate pushdown.
    conn.execute(f"""
        COPY (
            SELECT
                t.condition_id,
                epoch(t.timestamp) AS timestamp,
                CASE WHEN tm.token_index = 0 THEN t.price ELSE 1.0 - t.price END AS yes_price
            FROM read_parquet('{compact_glob}') t
            JOIN read_parquet('{token_map_path}') tm ON t.asset_id = tm.asset_id
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 500000)
    """)

    # Get stats
    count = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()[0]
    n_markets = conn.execute(
        f"SELECT COUNT(DISTINCT condition_id) FROM read_parquet('{out_path}')"
    ).fetchone()[0]
    conn.close()

    Path(db_path).unlink(missing_ok=True)
    shutil.rmtree(DERIVED_DIR / "_duckdb_tmp", ignore_errors=True)

    log.info(
        "prices_complete",
        rows=count,
        markets=n_markets,
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
            step_derived()
        elif step == "prices":
            step_prices()

    elapsed = time.monotonic() - t0
    log.info("build_complete", steps=steps, elapsed=f"{elapsed:.1f}s")


if __name__ == "__main__":
    main()
