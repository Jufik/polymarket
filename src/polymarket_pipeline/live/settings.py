"""Live pipeline configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the live sync pipeline.

    All values can be overridden via environment variables with PM_ prefix.
    Example: PM_REDPANDA_URL=redpanda:9092
    """

    model_config = SettingsConfigDict(env_prefix="PM_", env_file=".env", extra="ignore")

    # Redpanda
    redpanda_url: str = "localhost:19092"

    # Alchemy (required, no default — contains API key)
    alchemy_ws_url: str

    # Goldsky Subgraph (recovery)
    subgraph_url: str = (
        "https://api.goldsky.com/api/public/"
        "project_cl6mb8i9h0003e201j6li0diw/"
        "subgraphs/orderbook-subgraph/0.0.1/gn"
    )

    # ClickHouse
    ch_host: str = "localhost"
    ch_port: int = 18123
    ch_database: str = "polymarket"

    # PostgreSQL
    pg_dsn: str = ""  # Set via PM_PG_DSN env var or .env file

    # Quality thresholds
    quality_check_interval_s: int = 900
    source_liveness_timeout_s: int = 30
    volume_drop_red_pct: float = 0.10
    enrichment_ratio_min: float = 0.80

    # Recovery
    gap_threshold_s: int = 600
    recovery_timeout_s: int = 300  # Max time for in-app startup recovery

    # Dashboard
    dashboard_refresh_s: int = 5
    dashboard_port: int = 8099

    # RTDS redundant connection pool
    rtds_pool_size: int = 2
    rtds_rotation_interval_s: int = 300

    # Mempool monitor (Rust PyO3 sidecar)
    mempool_enabled: bool = False
    mempool_listen_port: int = 30304

    # Pending block poller (free RPC, ~1-2s early trade detection)
    # Racing multiple endpoints catches ~2x more txs than single endpoint
    pending_block_enabled: bool = False
    pending_block_rpc_ws_urls: str = "wss://polygon-bor-rpc.publicnode.com,wss://polygon.drpc.org"
    pending_block_poll_interval_s: float = 0.5

    # Batching (ClickHouse consumer)
    ch_batch_size: int = 100
    ch_flush_interval_s: float = 1.0

    # CLOB orderbook ingestor
    clob_orderbook_enabled: bool = False
    clob_orderbook_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # CLOB API (execution)
    clob_api_url: str = "https://clob.polymarket.com"
    clob_api_key: str = ""
    clob_api_secret: str = ""
    clob_api_passphrase: str = ""

    # Self-protection
    degraded_grace_s: float = 300.0  # 5 minutes before DEGRADED -> RED

    # Position limits
    max_position_usd: float = 100.0
    max_total_exposure_usd: float = 500.0

    # Logging
    log_file: str = ""  # Empty means console only, e.g. "logs/pipeline.jsonl"
