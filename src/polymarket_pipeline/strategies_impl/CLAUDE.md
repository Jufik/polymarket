# strategies_impl/ — Concrete Strategy Implementations

Each subdirectory is a self-contained strategy with `config.py`, `strategy.py`, `providers.py`.

## Strategy Inventory

### S1: proportional_copy/
Copy trades from graded longshot-YES specialists.

- **GradedPoolProvider** — 3 filters: `min_markets` (50), `min_longshot_yes_frac` (0.15), `max_no_fraction` (0.60)
  - Longshot YES = BUY side at price < 0.50
  - Defaults are backward-compatible (0.0 / 1.0 = off)
- **ProportionalCopyStrategy** — event-driven + vectorized paths
  - Tracks first entry per (maker, condition_id)
  - `contradiction_filter`: skips markets where pool disagrees on direction
  - `sizing`: "equal" or "proportional"

### S2a: will_no/
Buy NO on binary "Will X happen?" questions in profitable niches.

- **WillMarketProvider** — regex filter on market question text
- **WillNoStrategy** — data-derived band sizing + niche keyword filtering
  - `prefer_keywords`: niche filter (default: sports draws + finance terms)
  - `avoid_keywords`: negative-edge filter (default: "reach", "hit")
  - `max_volume_usd`: volume cap (default: $1K — critical profitability filter)
  - `price_bands`: data-derived multipliers (edge increases with YES price)
  - `max_bucket`: market size filter (thin/med/thick/heavy, default: "med")
  - `dual_sided`: simultaneous BUY NO + SELL YES (splits 50/50)
  - Backtested: +11.4% ROI at 2% fee, 85.7% HR, 100% stable (17/17 rolling windows)

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
