# strategies_impl/ — Concrete Strategy Implementations

Each subdirectory is a self-contained strategy with `config.py`, `strategy.py`, `providers.py`.

## Strategy Inventory

### S1: proportional_copy/
Copy trades from graded longshot-YES specialists.

- **GradedPoolProvider** — dual-mode: Polars (offline) or ClickHouse (live)
  - **Consistency mode** (preferred): `filter_consistent_traders()` (shared with S3) → longshot grading → NO cap
    - Reuses the 5-filter pipeline from `consensus_copy/consistency.py`
    - Then applies: `min_longshot_yes_frac` (0.15 in factory/TOML), `max_no_fraction` (0.60)
    - `_compute_grading_stats()`: per-trader longshot_yes_frac and no_frac from PnL data
    - `load_graded_pool_provider(data_dir, ...)` convenience for parquet-based init
  - **Legacy mode**: simple trade-count + grading from `query_trades()` (backward compat)
  - Constructor defaults are relaxed (0.0 / 1.0 = off); strict values set via factory/TOML
  - `refresh()` duck-types backend: if CH methods exist → `_refresh_from_ch()`, else → `compute()`
- **ProportionalCopyStrategy** — event-driven + vectorized paths
  - Tracks first entry per (maker, condition_id)
  - `contradiction_filter`: skips markets where pool disagrees on direction (+22% PnL)
  - `sizing`: "equal" (fixed bet) or "proportional" (scale by relative trade size, capped at `max_sizing_mult`)
  - `max_price` on intents: directional entry + `price_slippage`, capped at `max_entry_price`
  - `_TraderStats`: running average of per-trader trade sizes for proportional sizing

### S2a: will_no/
Buy NO on binary "Will X happen?" questions in profitable niches.

- **WillMarketProvider** — regex filter on market question text
- **WillNoStrategy** — data-derived band sizing + niche keyword filtering
  - `prefer_keywords`: niche filter (default: sports draws + finance terms)
  - `avoid_keywords`: negative-edge filter (default: "reach", "hit")
  - `max_volume_usd`: volume cap (default: $2K — critical profitability filter)
  - `volume_column`: which column to filter on (default: `"market_volume"` = trade-level sum(price*size))
    - **CRITICAL**: Must use trade-level volume, NOT Gamma `event_volume` (corr=0.28, 52x ratio)
    - Using Gamma event_volume with $2K cap → 110 sigs, $118/mo (destroys edge)
  - `price_bands`: 12 data-derived multipliers, 10-70% range (edge increases with YES price)
  - `max_bucket`: market size filter (thin/med/thick/heavy, default: "med")
  - `dual_sided`: simultaneous BUY NO + SELL YES (splits 50/50)
  - Implementation-validated: **+47.1% ROI** at 2% fee, 80.4% HR, ~140 sigs/month, $3,098/mo PnL

### S2b: crypto_otm_no/
Buy NO on OTM crypto price checkpoints.

- **CryptoMarketProvider** — filters by `question_pattern` + `assets` set
- **CryptoOTMNoStrategy** — tight OTM bands (yes_price 0.05–0.25)
  - `max_bucket`: optional market size filter

### S3: consensus_copy/
Copy consistency-filtered skilled traders.

- **SkilledTradersProvider** — dual-mode: Polars (offline) or ClickHouse (live)
  - 5-filter pipeline: min resolved markets, monthly profitability, MVF, consistency, median entry price
  - `filter_consistent_traders()` pure function (tested independently)
  - `refresh()` duck-types backend: if CH methods exist → `_refresh_from_ch()`, else → `compute()`
  - `load_skilled_provider(data_dir, ...)` convenience for parquet-based init
- **ConsensusCopyStrategy** — configurable direction (NO default), delay-aware

### Shared: market_size/
XGBoost volume classifier (shared feature provider).

- **MarketSizeClassifier** — XGBRegressor on log1p(volume), post-hoc bucketing
  - 34 numeric features + 20 optional TF-IDF SVD text features
  - Buckets: thin (<$1K), med ($1K-$10K), thick ($10K-$100K), heavy (>$100K)
  - Model artifact: `models/market_size_xgb.joblib` (12MB, gitignored)
- **MarketSizeProvider** — exposes `market_size_bucket` + `market_size_proba` features
- Training: `scripts/train_market_size_classifier.py` (CH aggregation → XGB → joblib)
- Validation: `scripts/validate_market_size_classifier.py` (temporal split)

## Conventions

- All configs are frozen dataclasses with sensible defaults
- Strategies must handle both event-driven (`on_trade`) and vectorized (`compute_signals`) paths
- Providers expose features via `get_features() -> dict[str, Any]`
- Feature names match between provider `.name` attribute and strategy `features` list in TOML
- Use `structlog` for all logging (strategy name in log context)
