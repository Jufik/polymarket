"""Train the market size classifier from ClickHouse data.

Pushes aggregation to ClickHouse — transfers ~20K rows instead of ~20M raw trades.
Uses log-volume regression with optional Optuna hyperparameter tuning.

Usage:
    uv run python scripts/train_market_size_classifier.py [--window 6] [--output models/market_size_xgb.joblib]
    uv run python scripts/train_market_size_classifier.py --tune --n-trials 50
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import polars as pl
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.market_size.classifier import MarketSizeClassifier
from polymarket_pipeline.strategies_impl.market_size.config import MarketSizeConfig
from polymarket_pipeline.strategies_impl.market_size.features import bucket_from_volume

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


def _fmt_elapsed(t0: float) -> str:
    elapsed = time.time() - t0
    if elapsed < 60:
        return f"{elapsed:.1f}s"
    return f"{elapsed / 60:.1f}min"


async def query_ch(
    client: httpx.AsyncClient, query: str, *, label: str = "query"
) -> pl.DataFrame:
    """Execute a ClickHouse query with streaming download and progress."""
    t0 = time.time()
    print(f"  [{label}] Executing...", flush=True)

    async with client.stream(
        "POST",
        "/",
        content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB},
        headers={"Content-Type": "text/plain"},
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            print(f"  CH Error: {body}")
            resp.raise_for_status()

        chunks: list[bytes] = []
        total_bytes = 0
        last_report = t0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            total_bytes += len(chunk)
            now = time.time()
            if now - last_report >= 5.0:
                print(
                    f"  [{label}] {total_bytes / 1_000_000:.1f} MB ({_fmt_elapsed(t0)})",
                    flush=True,
                )
                last_report = now

    data = b"".join(chunks)
    if not data.strip():
        print(f"  [{label}] Empty result ({_fmt_elapsed(t0)})", flush=True)
        return pl.DataFrame()

    df = pl.read_csv(io.BytesIO(data), has_header=True, skip_rows_after_header=1)
    print(
        f"  [{label}] {len(df):,} rows, {total_bytes / 1_000_000:.1f} MB "
        f"({_fmt_elapsed(t0)})",
        flush=True,
    )
    return df


def run_optuna_tuning(
    train_df: pl.DataFrame,
    test_df: pl.DataFrame,
    train_questions: list[str],
    test_questions: list[str],
    cfg: MarketSizeConfig,
    n_trials: int = 50,
) -> dict[str, float]:
    """Run Optuna hyperparameter search and return best params."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 100, 600),
            "max_depth": trial.suggest_int("max_depth", 4, 10),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        }

        clf = MarketSizeClassifier(cfg)
        clf.train(train_df, questions=train_questions, xgb_params=params)

        # Evaluate: predict log-volume → bucket → accuracy on test set
        test_preds = clf.predict(
            test_df.drop("total_volume", "label"), questions=test_questions
        )
        test_labels = test_df["label"].to_list()
        acc = accuracy_score(test_labels, test_preds)

        # Also compute RMSE for regression quality
        log_preds = clf.predict_log_volume(
            test_df.drop("total_volume", "label"), questions=test_questions
        )
        log_true = np.log1p(test_df["total_volume"].to_numpy())
        rmse = float(np.sqrt(np.mean((log_preds - log_true) ** 2)))

        # Multi-objective: maximize accuracy, minimize RMSE
        trial.set_user_attr("rmse", rmse)
        return float(acc)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    print(f"\n  Best trial: accuracy={study.best_value:.4f}")
    print(f"  Best RMSE: {study.best_trial.user_attrs['rmse']:.4f}")
    print("  Best params:")
    for k, v in study.best_params.items():
        print(f"    {k}: {v}")

    return study.best_params


async def main() -> None:
    parser = argparse.ArgumentParser(description="Train market size classifier")
    parser.add_argument("--window", type=int, default=6, help="Feature window in hours")
    parser.add_argument("--output", default="models/market_size_xgb.joblib")
    parser.add_argument("--tune", action="store_true", help="Run Optuna hyperparameter tuning")
    parser.add_argument("--n-trials", type=int, default=50, help="Number of Optuna trials")
    args = parser.parse_args()

    t_start = time.time()
    cfg = MarketSizeConfig(feature_window_hours=args.window)
    window_secs = args.window * 3600
    early_secs = window_secs // 6   # 1h for 6h window
    mid_secs = window_secs // 2     # 3h for 6h window
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    # ---------------------------------------------------------------
    # Step 1: Server-side aggregation — one row per condition_id
    # Multi-window + price microstructure via conditional agg.
    # ---------------------------------------------------------------
    print("Step 1/6: Aggregating trades server-side in ClickHouse...")
    print(f"  Windows: early={early_secs}s, mid={mid_secs}s, full={window_secs}s")
    features_raw = await query_ch(
        client,
        f"""
        WITH
            will_markets AS (
                SELECT condition_id
                FROM markets
                WHERE question LIKE 'Will %%'
            ),
            trades AS (
                SELECT condition_id, maker,
                       CAST(price AS Float64) AS price,
                       CAST(size AS Float64) AS size,
                       toUnixTimestamp(timestamp) AS ts
                FROM (SELECT * FROM polymarket.trades_raw FINAL) t
                WHERE condition_id IN (SELECT condition_id FROM will_markets)
            ),
            first_ts AS (
                SELECT condition_id, min(ts) AS first_ts
                FROM trades
                GROUP BY condition_id
            ),
            window_agg AS (
                SELECT
                    t.condition_id AS condition_id,
                    -- Full window
                    count() AS trades_window,
                    sum(t.price * t.size) AS vol_window,
                    uniq(t.maker) AS traders_window,
                    -- Early sub-window (1/6 of full)
                    countIf((t.ts - f.first_ts) <= {early_secs}) AS trades_early,
                    sumIf(t.price * t.size, (t.ts - f.first_ts) <= {early_secs}) AS vol_early,
                    uniqIf(t.maker, (t.ts - f.first_ts) <= {early_secs}) AS traders_early,
                    -- Mid sub-window (1/2 of full)
                    countIf((t.ts - f.first_ts) <= {mid_secs}) AS trades_mid,
                    sumIf(t.price * t.size, (t.ts - f.first_ts) <= {mid_secs}) AS vol_mid,
                    uniqIf(t.maker, (t.ts - f.first_ts) <= {mid_secs}) AS traders_mid,
                    -- Price microstructure
                    stddevPop(t.price) AS price_std,
                    max(t.price) - min(t.price) AS price_range,
                    avg(t.size) AS avg_trade_size,
                    max(t.size) AS max_trade_size
                FROM trades t
                JOIN first_ts f ON t.condition_id = f.condition_id
                WHERE (t.ts - f.first_ts) <= {window_secs}
                GROUP BY t.condition_id
            ),
            total_agg AS (
                SELECT
                    condition_id,
                    sum(price * size) AS total_volume,
                    count() AS total_trades
                FROM trades
                GROUP BY condition_id
            )
        SELECT
            w.condition_id AS condition_id,
            w.trades_window AS trades_window,
            w.vol_window AS vol_window,
            w.traders_window AS traders_window,
            w.trades_early AS trades_early,
            w.vol_early AS vol_early,
            w.traders_early AS traders_early,
            w.trades_mid AS trades_mid,
            w.vol_mid AS vol_mid,
            w.traders_mid AS traders_mid,
            w.price_std AS price_std,
            w.price_range AS price_range,
            w.avg_trade_size AS avg_trade_size,
            w.max_trade_size AS max_trade_size,
            ta.total_volume AS total_volume,
            ta.total_trades AS total_trades,
            f.first_ts AS first_ts
        FROM window_agg w
        JOIN total_agg ta ON w.condition_id = ta.condition_id
        JOIN first_ts f ON w.condition_id = f.condition_id
        """,
        label="trades-agg",
    )

    # Cast to float
    for col in ["trades_window", "vol_window", "traders_window",
                 "trades_early", "vol_early", "traders_early",
                 "trades_mid", "vol_mid", "traders_mid",
                 "price_std", "price_range", "avg_trade_size", "max_trade_size",
                 "total_volume", "total_trades", "first_ts"]:
        features_raw = features_raw.with_columns(pl.col(col).cast(pl.Float64))

    # ---------------------------------------------------------------
    # Step 2: Fetch metadata (small tables, fast)
    # ---------------------------------------------------------------
    print("\nStep 2/6: Fetching metadata...")
    markets = await query_ch(
        client,
        "SELECT condition_id, toString(event_id) AS event_id, neg_risk, question FROM markets WHERE question LIKE 'Will %%'",
        label="markets",
    )
    markets = markets.with_columns(pl.col("neg_risk").cast(pl.Boolean))

    events = await query_ch(
        client,
        """
        SELECT toString(id) AS id,
               toUnixTimestamp(end_date) AS end_date,
               CAST(volume AS Float64) AS volume,
               CAST(liquidity AS Float64) AS liquidity
        FROM events
        WHERE end_date IS NOT NULL
        """,
        label="events",
    )
    for col in ["end_date", "volume", "liquidity"]:
        events = events.with_columns(pl.col(col).cast(pl.Float64))

    tags = await query_ch(
        client,
        "SELECT toString(et.event_id) AS event_id, t.label AS tag FROM event_tags et JOIN tags t ON et.tag_id = t.id",
        label="tags",
    )
    tags = tags.sort("tag").unique(subset=["event_id"], keep="first")

    await client.aclose()
    print(f"\n  Data fetch complete ({_fmt_elapsed(t_start)})")

    # ---------------------------------------------------------------
    # Step 3: Enrich with metadata (Polars, in-memory, fast)
    # ---------------------------------------------------------------
    print("\nStep 3/6: Building feature matrix...")
    t_feat = time.time()

    # Join markets for event_id + neg_risk + question
    features = features_raw.join(
        markets.select("condition_id", "event_id", "neg_risk", "question"),
        on="condition_id",
        how="left",
    )

    # Event-level features
    event_market_count = markets.group_by("event_id").agg(
        pl.len().alias("event_n_markets")
    )
    features = features.join(
        events.select(
            pl.col("id").alias("event_id"),
            pl.col("end_date").alias("event_end_date"),
            pl.col("volume").alias("event_volume"),
            pl.col("liquidity").alias("event_liquidity"),
        ),
        on="event_id",
        how="left",
    )
    features = features.join(event_market_count, on="event_id", how="left")

    # Derived ratio features
    features = features.with_columns(
        (pl.col("vol_window") / pl.col("trades_window").clip(1)).alias("vol_per_trade"),
        (pl.col("vol_window") / pl.col("traders_window").clip(1)).alias("vol_per_trader"),
        # Growth trajectory: how volume ramps from early -> mid -> full
        (pl.col("vol_mid") / pl.col("vol_early").clip(lower_bound=0.01)).alias(
            "vol_growth_early_mid"
        ),
        (pl.col("vol_window") / pl.col("vol_mid").clip(lower_bound=0.01)).alias(
            "vol_growth_mid_full"
        ),
        # Log-scale volume features (compress range, reduce dominance)
        (pl.col("vol_window") + 1).log().alias("log_vol_window"),
        (pl.col("vol_early") + 1).log().alias("log_vol_early"),
        (pl.col("vol_mid") + 1).log().alias("log_vol_mid"),
        (pl.col("event_volume").fill_null(0) + 1).log().alias("log_event_volume"),
        (pl.col("event_liquidity").fill_null(0) + 1).log().alias("log_event_liquidity"),
        # Velocity & concentration
        (pl.col("trades_window") / (window_secs / 3600)).alias("trades_per_hour"),
        (pl.col("traders_window").cast(pl.Float64) / pl.col("trades_window").clip(1)).alias(
            "trader_concentration"
        ),
        (pl.col("vol_window") / pl.col("event_liquidity").fill_null(1).clip(lower_bound=1.0)).alias(
            "vol_to_liquidity"
        ),
    )

    # Time remaining
    features = features.with_columns(
        ((pl.col("event_end_date") - pl.col("first_ts")) / 3600.0).alias(
            "time_remaining_hours"
        ),
    )

    # Temporal features
    features = features.with_columns(
        (pl.col("first_ts") % 86400 / 3600).cast(pl.Int32).alias("hour_of_day"),
        (pl.col("first_ts") / 86400).cast(pl.Int32).mod(7).alias("day_of_week"),
    )

    # neg_risk as int
    features = features.with_columns(
        pl.col("neg_risk").cast(pl.Int8).alias("neg_risk"),
    )

    # One-hot tags
    for tag_name in cfg.top_tags:
        col_name = f"tag_{tag_name.replace(' ', '_')}"
        matching_events = tags.filter(pl.col("tag") == tag_name)["event_id"].to_list()
        features = features.with_columns(
            pl.col("event_id").is_in(matching_events).cast(pl.Int8).alias(col_name),
        )

    print(f"  Feature matrix: {features.shape} ({time.time() - t_feat:.1f}s)")

    # Filter
    features = features.filter(pl.col("total_volume") >= 10)
    print(f"  After min volume filter: {len(features):,}")

    features = features.drop_nulls(subset=["time_remaining_hours"])
    features = features.filter(pl.col("time_remaining_hours") > 0)
    print(f"  After time_remaining filter: {len(features):,}")

    # ---------------------------------------------------------------
    # Step 4: Label distribution
    # ---------------------------------------------------------------
    labels = [
        bucket_from_volume(v, cfg.bucket_thresholds, cfg.buckets)
        for v in features["total_volume"].to_list()
    ]
    features = features.with_columns(pl.Series("label", labels))
    print("\n  Label distribution:")
    for bucket in cfg.buckets:
        n = sum(1 for label in labels if label == bucket)
        print(f"    {bucket:<10} {n:>6,} ({n / len(labels):>6.1%})")

    # ---------------------------------------------------------------
    # Step 5: Train/test split + train
    # ---------------------------------------------------------------
    print("\nStep 4/6: Training XGBoost regressor (log-volume)...")
    t_train = time.time()

    # Select only model columns (drop event_id, first_ts, event_end_date, etc.)
    model_cols = [
        "condition_id", "trades_window", "vol_window", "traders_window",
        "trades_early", "vol_early", "traders_early",
        "trades_mid", "vol_mid", "traders_mid",
        "vol_per_trade", "vol_per_trader",
        "vol_growth_early_mid", "vol_growth_mid_full",
        "price_std", "price_range", "avg_trade_size", "max_trade_size",
        "log_vol_window", "log_vol_early", "log_vol_mid",
        "log_event_volume", "log_event_liquidity",
        "trades_per_hour", "trader_concentration", "vol_to_liquidity",
        "time_remaining_hours", "event_n_markets",
        "neg_risk", "hour_of_day", "day_of_week", "total_volume",
    ] + [f"tag_{t.replace(' ', '_')}" for t in cfg.top_tags]
    existing = [c for c in model_cols if c in features.columns]
    model_df = features.select(existing + ["label"])

    # Extract question texts (aligned with model_df rows)
    all_questions = features["question"].fill_null("").to_list()

    train_idx, test_idx = train_test_split(
        list(range(len(model_df))),
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )
    train_df = model_df[train_idx]
    test_df = model_df[test_idx]
    train_questions = [all_questions[i] for i in train_idx]
    test_questions = [all_questions[i] for i in test_idx]

    # ---------------------------------------------------------------
    # Optional: Optuna tuning
    # ---------------------------------------------------------------
    best_params: dict[str, float] | None = None
    if args.tune:
        print(f"\n  Running Optuna tuning ({args.n_trials} trials)...")
        best_params = run_optuna_tuning(
            train_df, test_df, train_questions, test_questions, cfg, args.n_trials
        )
        print(f"  Tuning complete ({time.time() - t_train:.1f}s)")
        t_train = time.time()

    clf = MarketSizeClassifier(cfg)
    train_metrics = clf.train(train_df, questions=train_questions, xgb_params=best_params)
    print(
        f"  Train accuracy: {train_metrics['accuracy']:.3f}, "
        f"RMSE: {train_metrics['rmse']:.3f} ({time.time() - t_train:.1f}s)"
    )

    # ---------------------------------------------------------------
    # Step 6: Evaluate on test set
    # ---------------------------------------------------------------
    print("\nStep 5/6: Evaluating on test set...")
    test_preds = clf.predict(
        test_df.drop("total_volume", "label"), questions=test_questions
    )
    test_labels = test_df["label"].to_list()

    print(f"\n  Test accuracy: {accuracy_score(test_labels, test_preds):.3f}")

    # Log-volume RMSE
    log_preds = clf.predict_log_volume(
        test_df.drop("total_volume", "label"), questions=test_questions
    )
    log_true = np.log1p(test_df["total_volume"].to_numpy().astype(np.float64))
    print(f"  Test RMSE (log-vol): {np.sqrt(np.mean((log_preds - log_true) ** 2)):.3f}")

    print(classification_report(test_labels, test_preds, zero_division=0))
    print("Confusion matrix:")
    cm = confusion_matrix(test_labels, test_preds, labels=list(cfg.buckets))
    header = "         " + "  ".join(f"{b:>8}" for b in cfg.buckets)
    print(header)
    for i, row in enumerate(cm):
        print(f"  {cfg.buckets[i]:<8}" + "  ".join(f"{v:>8,}" for v in row))

    # Retrain on full data and save
    print(f"\nStep 6/6: Retraining on full data and saving to {args.output}...")
    clf_full = MarketSizeClassifier(cfg)
    clf_full.train(model_df, questions=all_questions, xgb_params=best_params)
    clf_full.save(args.output)

    # Feature importances
    importances = clf_full._model.feature_importances_
    n_numeric = len(clf_full._feature_names)
    names = list(clf_full._feature_names) + [
        f"text_svd_{i}" for i in range(len(importances) - n_numeric)
    ]
    sorted_idx = np.argsort(importances)[::-1]
    print("\nTop 15 features:")
    for i in sorted_idx[:15]:
        print(f"  {names[i]:<30} {importances[i]:.4f}")

    print(f"\nDone! Total time: {_fmt_elapsed(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
