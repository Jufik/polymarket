"""Derived table computation: PnL, MVF, resolved markets, and price timeseries.

Extracts the derived/prices steps from scripts/build_data.py into reusable
functions parameterized by PipelineSettings.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import polars as pl
import structlog

from polymarket_pipeline.settings import PipelineSettings

log: structlog.stdlib.BoundLogger = structlog.get_logger()


def compute_derived(settings: PipelineSettings) -> None:
    """Compute PnL, MVF, and resolved markets.

    Uses per-file Polars aggregation then DuckDB for final out-of-core
    re-aggregation.

    Outputs (all written to ``settings.derived_dir``):
      - ``markets_resolved.parquet``
      - ``trader_market_pnl.parquet``
      - ``maker_volume_fractions.parquet``
    """
    derived_dir = settings.derived_dir
    metadata_dir = settings.metadata_dir
    compact_dir = settings.compact_dir

    derived_dir.mkdir(parents=True, exist_ok=True)

    log.info("derived_start")
    t0 = time.monotonic()

    # ------------------------------------------------------------------
    # Load metadata
    # ------------------------------------------------------------------
    markets = pl.read_parquet(metadata_dir / "markets.parquet")
    token_map = pl.read_parquet(metadata_dir / "token_map.parquet")

    compact_files = sorted(compact_dir.glob("compact_*.parquet"))
    if not compact_files:
        log.error("no_compact_files", hint="Run --step compact first")
        raise SystemExit(1)

    log.info("loading_compact", files=len(compact_files))

    # ------------------------------------------------------------------
    # 3a. markets_resolved.parquet
    # ------------------------------------------------------------------
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
    yes_tokens = (
        token_map.filter(
            (pl.col("token_index") == 0) & (pl.col("winner") == True)  # noqa: E712
        )
        .select("condition_id")
        .with_columns(pl.lit(True).alias("yes_won"))
    )
    resolved = resolved.join(yes_tokens, on="condition_id", how="left").with_columns(
        pl.col("yes_won").fill_null(False)
    )
    resolved.write_parquet(derived_dir / "markets_resolved.parquet", compression="zstd")
    log.info(
        "markets_resolved_complete",
        rows=resolved.height,
        yes_won=int(resolved["yes_won"].sum()),
        no_won=int((~resolved["yes_won"]).sum()),
    )

    # ------------------------------------------------------------------
    # 3b. trader_market_pnl.parquet
    # ------------------------------------------------------------------
    _compute_pnl(
        derived_dir=derived_dir,
        compact_files=compact_files,
        resolved=resolved,
        token_map=token_map,
    )

    # ------------------------------------------------------------------
    # 3c. maker_volume_fractions.parquet
    # ------------------------------------------------------------------
    _compute_mvf(
        derived_dir=derived_dir,
        compact_dir=compact_dir,
    )

    elapsed = time.monotonic() - t0
    log.info("derived_complete", elapsed=f"{elapsed:.1f}s")


# ======================================================================
# Internal helpers
# ======================================================================


def _compute_pnl(
    *,
    derived_dir: Path,
    compact_files: list[Path],
    resolved: pl.DataFrame,
    token_map: pl.DataFrame,
) -> None:
    """Per-file Polars aggregation followed by DuckDB re-aggregation."""
    import duckdb

    log.info("computing_pnl")
    t_pnl = time.monotonic()

    resolved_set: set[str] = set(resolved["condition_id"].to_list())
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
    tmp_pnl_dir = derived_dir / "_tmp_pnl"
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
            _aggregate_pnl_file(
                idx=i,
                path=f,
                resolved_set=resolved_set,
                outcome_map=outcome_map,
                token_index_map=token_index_map,
                winner_map=winner_map,
                tmp_pnl_dir=tmp_pnl_dir,
            )
            if (i + 1) % 20 == 0:
                log.info("pnl_progress", files=i + 1, total=len(compact_files))

    # Final re-aggregate via DuckDB (handles out-of-core natively)
    tmp_files = sorted(tmp_pnl_dir.glob("pnl_*.parquet"))
    log.info("pnl_combining", tmp_files=len(tmp_files))

    db_path = str(derived_dir / "_pnl_agg.duckdb")
    conn = duckdb.connect(db_path)
    # Set temp directory and memory limit for out-of-core aggregation
    conn.execute(f"SET temp_directory='{derived_dir / '_duckdb_tmp'}'")
    conn.execute("SET memory_limit='4GB'")
    tmp_glob = str(tmp_pnl_dir / "pnl_*.parquet")
    pnl_df: pl.DataFrame = conn.execute(f"""
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
    shutil.rmtree(derived_dir / "_duckdb_tmp", ignore_errors=True)

    pnl_df.write_parquet(derived_dir / "trader_market_pnl.parquet", compression="zstd")
    log.info("pnl_complete", rows=pnl_df.height, elapsed=f"{time.monotonic() - t_pnl:.1f}s")

    # Cleanup temp files
    shutil.rmtree(tmp_pnl_dir)


def _aggregate_pnl_file(
    *,
    idx: int,
    path: Path,
    resolved_set: set[str],
    outcome_map: dict[str, str],
    token_index_map: dict[str, int],
    winner_map: dict[str, str],
    tmp_pnl_dir: Path,
) -> None:
    """Aggregate a single compact parquet file into per-trader-market PnL."""
    df = pl.read_parquet(
        path,
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
        return

    # Map outcome, token_index, and settlement
    df = df.with_columns(
        pl.col("asset_id").replace_strict(outcome_map, default=None).alias("outcome"),
        pl.col("asset_id").replace_strict(token_index_map, default=None).alias("token_index"),
    )
    df = df.filter(pl.col("outcome").is_not_null())
    df = df.with_columns(
        pl.col("condition_id").replace_strict(winner_map, default="").alias("winner_outcome"),
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
        pl.when(buy).then(pl.col("size")).otherwise(-pl.col("size")).alias("m_signed_tok"),
        pl.when(buy)
        .then(-pl.col("amount_usd"))
        .otherwise(pl.col("amount_usd"))
        .alias("m_signed_usd"),
        pl.when(buy).then(-pl.col("size")).otherwise(pl.col("size")).alias("t_signed_tok"),
        pl.when(buy)
        .then(pl.col("amount_usd"))
        .otherwise(-pl.col("amount_usd"))
        .alias("t_signed_usd"),
    )
    # YES-side signed tokens (token_index=0 is affirmative/YES)
    df = df.with_columns(
        pl.when(is_yes_token).then(pl.col("m_signed_tok")).otherwise(0.0).alias("m_yes_tok"),
        pl.when(is_yes_token).then(pl.col("t_signed_tok")).otherwise(0.0).alias("t_yes_tok"),
    )

    common_cols = [
        "condition_id",
        "fee_usd",
        "spt",
        "amount_usd",
        "timestamp",
        "yes_price_x_vol",
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
    agg.write_parquet(tmp_pnl_dir / f"pnl_{idx:04d}.parquet")


def _compute_mvf(
    *,
    derived_dir: Path,
    compact_dir: Path,
) -> None:
    """Compute maker volume fractions via DuckDB out-of-core query."""
    import duckdb

    log.info("computing_mvf")
    t_mvf = time.monotonic()

    compact_glob = str(compact_dir / "compact_*.parquet")
    resolved_path = str(derived_dir / "markets_resolved.parquet")
    mvf_out = derived_dir / "maker_volume_fractions.parquet"

    conn = duckdb.connect(str(derived_dir / "_mvf.duckdb"))
    conn.execute(f"SET temp_directory='{derived_dir / '_duckdb_tmp'}'")
    conn.execute("SET memory_limit='8GB'")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute("SET threads=4")

    mvf_df: pl.DataFrame = conn.execute(f"""
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

    Path(str(derived_dir / "_mvf.duckdb")).unlink(missing_ok=True)
    shutil.rmtree(derived_dir / "_duckdb_tmp", ignore_errors=True)

    mvf_df.write_parquet(mvf_out, compression="zstd")
    log.info("mvf_complete", traders=mvf_df.height, elapsed=f"{time.monotonic() - t_mvf:.1f}s")


def compute_prices(settings: PipelineSettings) -> None:
    """Extract YES price timeseries from compact trades via DuckDB (out-of-core sort).

    Output (written to ``settings.derived_dir``):
      - ``market_prices.parquet``
    """
    import duckdb

    derived_dir = settings.derived_dir
    compact_dir = settings.compact_dir
    metadata_dir = settings.metadata_dir

    derived_dir.mkdir(parents=True, exist_ok=True)

    log.info("prices_start")
    t0 = time.monotonic()

    token_map_path = metadata_dir / "token_map.parquet"
    compact_glob = str(compact_dir / "compact_*.parquet")

    db_path = str(derived_dir / "_prices.duckdb")
    conn = duckdb.connect(db_path)
    conn.execute(f"SET temp_directory='{derived_dir / '_duckdb_tmp'}'")
    conn.execute("SET memory_limit='12GB'")
    conn.execute("SET preserve_insertion_order=false")

    out_path = derived_dir / "market_prices.parquet"
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
    row_result = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()
    count: int = row_result[0] if row_result is not None else 0

    market_result = conn.execute(
        f"SELECT COUNT(DISTINCT condition_id) FROM read_parquet('{out_path}')"
    ).fetchone()
    n_markets: int = market_result[0] if market_result is not None else 0

    conn.close()

    Path(db_path).unlink(missing_ok=True)
    shutil.rmtree(derived_dir / "_duckdb_tmp", ignore_errors=True)

    log.info(
        "prices_complete",
        rows=count,
        markets=n_markets,
        elapsed=f"{time.monotonic() - t0:.1f}s",
    )
