"""Parallel Parquet scanner — extract YES token prices at signal trigger times.

Scans raw Goldsky Parquet files to build a (condition_id, timestamp, yes_price)
timeseries, then performs an asof join to get the last traded YES price at each
signal trigger time.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import fastparquet
import polars as pl


def _load_token_maps(
    token_map_path: Path,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load token_market_map and build YES/NO asset -> condition_id DataFrames."""
    with open(token_map_path) as f:
        raw_map: dict[str, list[str]] = json.load(f)

    yes_rows: list[dict[str, str]] = []
    no_rows: list[dict[str, str]] = []

    for asset_id, (cid, outcome) in raw_map.items():
        if outcome == "YES":
            yes_rows.append({"token_id": asset_id, "condition_id": cid})
        else:
            no_rows.append({"token_id": asset_id, "condition_id": cid})

    yes_df = pl.DataFrame(yes_rows)
    no_df = pl.DataFrame(no_rows)
    return yes_df, no_df


def _scan_one_file(
    filepath: str,
    yes_map_path: str,
    no_map_path: str,
    target_cids: set[str] | None = None,
) -> list[dict]:
    """Scan a single Parquet file and extract YES token prices.

    Returns list of dicts with keys: condition_id, timestamp, yes_price.
    """
    yes_map = pl.read_parquet(yes_map_path)
    no_map = pl.read_parquet(no_map_path)

    try:
        fp = fastparquet.ParquetFile(filepath)
        pdf = fp.to_pandas(
            columns=[
                "timestamp",
                "maker_asset_id",
                "taker_asset_id",
                "maker_amount_filled",
                "taker_amount_filled",
            ]
        )
    except Exception:
        return []

    if len(pdf) == 0:
        return []

    df = pl.from_pandas(pdf)

    # Determine which side is token vs USDC
    df = df.with_columns(
        (pl.col("maker_asset_id").cast(pl.Utf8) == "0").alias("is_buy"),
    )

    df = df.with_columns(
        [
            pl.when(pl.col("is_buy"))
            .then(pl.col("taker_asset_id").cast(pl.Utf8))
            .otherwise(pl.col("maker_asset_id").cast(pl.Utf8))
            .alias("token_id"),
            pl.when(pl.col("is_buy"))
            .then(pl.col("maker_amount_filled"))
            .otherwise(pl.col("taker_amount_filled"))
            .alias("usdc_amount"),
            pl.when(pl.col("is_buy"))
            .then(pl.col("taker_amount_filled"))
            .otherwise(pl.col("maker_amount_filled"))
            .alias("token_amount"),
        ]
    )

    # Filter out zero-token trades
    df = df.filter(pl.col("token_amount") > 0)

    # YES token trades: price = usdc / tokens
    yes_trades = (
        df.join(yes_map, on="token_id", how="inner")
        .with_columns((pl.col("usdc_amount") / pl.col("token_amount")).alias("yes_price"))
        .select(["condition_id", "timestamp", "yes_price"])
    )

    # NO token trades: yes_price = 1 - (usdc / tokens)
    no_trades = (
        df.join(no_map, on="token_id", how="inner")
        .with_columns(
            (1.0 - pl.col("usdc_amount") / pl.col("token_amount")).alias("yes_price")
        )
        .select(["condition_id", "timestamp", "yes_price"])
    )

    combined = pl.concat([yes_trades, no_trades])

    # Filter to target condition_ids if specified
    if target_cids is not None:
        combined = combined.filter(pl.col("condition_id").is_in(list(target_cids)))

    if combined.height == 0:
        return []

    return combined.to_dicts()


def scan_parquet_prices(
    parquet_dir: Path,
    token_map_path: Path,
    target_cids: set[str] | None = None,
    max_workers: int = 6,
    cache_path: Path | None = None,
) -> pl.DataFrame:
    """Scan all Parquet files and extract YES token price timeseries.

    Parameters
    ----------
    parquet_dir
        Directory containing raw Goldsky Parquet files.
    token_map_path
        Path to token_market_map.json.
    target_cids
        If set, only extract prices for these condition_ids.
    max_workers
        Number of parallel workers.
    cache_path
        If set, save/load cache from this path.

    Returns
    -------
    pl.DataFrame
        Columns: condition_id, timestamp, yes_price
    """
    if cache_path is not None and cache_path.exists():
        print(f"[prices] Loading cached prices from {cache_path}")
        return pl.read_parquet(cache_path)

    # Prepare token map files for workers (serialize to temp parquet)
    yes_map, no_map = _load_token_maps(token_map_path)
    tmp_dir = Path("/tmp/price_scanner_maps")
    tmp_dir.mkdir(exist_ok=True)
    yes_path = tmp_dir / "yes_map.parquet"
    no_path = tmp_dir / "no_map.parquet"
    yes_map.write_parquet(yes_path)
    no_map.write_parquet(no_path)

    files = sorted(os.listdir(parquet_dir))
    filepaths = [str(parquet_dir / f) for f in files]

    print(f"[prices] Scanning {len(filepaths)} Parquet files with {max_workers} workers...")

    all_results: list[dict] = []
    completed = 0

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _scan_one_file,
                fp,
                str(yes_path),
                str(no_path),
                target_cids,
            ): fp
            for fp in filepaths
        }

        for future in as_completed(futures):
            completed += 1
            try:
                rows = future.result()
                all_results.extend(rows)
            except Exception as e:
                print(f"[prices] Error: {e}")

            if completed % 200 == 0:
                print(
                    f"[prices] {completed}/{len(filepaths)} files, "
                    f"{len(all_results):,} price records"
                )

    if not all_results:
        return pl.DataFrame(schema={"condition_id": pl.Utf8, "timestamp": pl.Float64, "yes_price": pl.Float64})

    result = pl.DataFrame(all_results)
    print(f"[prices] Total: {result.height:,} price records across {result['condition_id'].n_unique():,} markets")

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        result.write_parquet(cache_path)
        print(f"[prices] Cached to {cache_path}")

    return result


def get_market_prices_at_signals(
    price_ts: pl.DataFrame,
    signal_table: pl.DataFrame,
) -> pl.DataFrame:
    """Get the last YES token price at each signal trigger time.

    Uses an asof join: for each (condition_id, trigger_time), find the last
    price record at or before trigger_time.

    Parameters
    ----------
    price_ts
        Price timeseries: condition_id, timestamp (float), yes_price.
    signal_table
        Signal table with columns including condition_id, trigger_time (datetime).

    Returns
    -------
    pl.DataFrame
        Signal table with trigger_entry_price replaced by real market price.
        For YES signals: entry = yes_price. For NO signals: entry = 1 - yes_price.
    """
    # Convert signal trigger_time to float timestamp for joining
    signals = signal_table.with_columns(
        pl.col("trigger_time").dt.epoch("s").cast(pl.Float64).alias("trigger_ts")
    )

    # Sort price timeseries
    prices = price_ts.sort(["condition_id", "timestamp"])

    # Sort signals
    signals = signals.sort(["condition_id", "trigger_ts"])

    # Asof join: for each signal row, find the last price <= trigger_ts
    joined = signals.join_asof(
        prices,
        left_on="trigger_ts",
        right_on="timestamp",
        by="condition_id",
        strategy="backward",
    )

    # Compute entry price based on signal direction
    # YES signal: buy YES at yes_price -> entry = yes_price
    # NO signal: buy NO at (1 - yes_price) -> entry = 1 - yes_price
    joined = joined.with_columns(
        pl.when(pl.col("signal_direction") == "YES")
        .then(pl.col("yes_price"))
        .otherwise(1.0 - pl.col("yes_price"))
        .alias("market_entry_price")
    )

    # Replace trigger_entry_price with market price where available
    joined = joined.with_columns(
        pl.when(pl.col("market_entry_price").is_not_null())
        .then(pl.col("market_entry_price"))
        .otherwise(pl.col("trigger_entry_price"))
        .alias("trigger_entry_price")
    )

    # Drop helper columns
    return joined.drop(["trigger_ts", "yes_price", "market_entry_price"])
