"""Live pipeline configuration via environment variables."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the live sync pipeline.

    All values can be overridden via environment variables with PM_ prefix.
    Example: PM_REDPANDA_URL=redpanda:9092
    """

    model_config = SettingsConfigDict(env_prefix="PM_", env_file=".env", extra="ignore")

    # Redpanda
    redpanda_url: str = "localhost:19092"

    # Polygon RPC WebSocket (eth_subscribe logs for OrderFilled events)
    # Comma-separated list of WS URLs — races all endpoints for redundancy
    # Override via PM_RPC_WS_URLS (or legacy PM_RPC_WS_URL / PM_ALCHEMY_WS_URL)
    rpc_ws_urls: str = Field(
        default="wss://polygon-bor-rpc.publicnode.com,wss://polygon.drpc.org,wss://polygon.gateway.tenderly.co",
        validation_alias=AliasChoices("PM_RPC_WS_URLS", "PM_RPC_WS_URL", "PM_ALCHEMY_WS_URL"),
    )

    # On-chain resolution detection via UMA CTF Adapter QuestionResolved events
    resolution_rpc_enabled: bool = True

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

    # Broker address as seen from ClickHouse (Docker-internal network)
    ch_kafka_broker_list: str = "redpanda:9092"

    # PostgreSQL
    pg_dsn: str = ""  # Set via PM_PG_DSN env var or .env file

    # Quality thresholds
    quality_initial_delay_s: int = 60
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

    # Token map refresh (periodic re-sync from APIs + PG reload).
    # 60s keeps PG metadata fresh for time-sensitive strategies (S3: 5-min window).
    token_map_refresh_interval_s: int = 60

    # Redis orderbook cache
    redis_url: str = "redis://localhost:6379/0"
    redis_orderbook_enabled: bool = False
    redis_orderbook_ttl_s: int = 300

    # CLOB orderbook ingestor
    clob_orderbook_enabled: bool = False
    clob_orderbook_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    clob_markets_events_topic: str = "markets.events"
    clob_orderbook_max_connections: int = (
        20  # max targeted WS (500 assets each); event loop supports ~24 total
    )

    # Exchange feed (cryptofeed → 1s bars → Kafka)
    exchange_feed_enabled: bool = False
    exchange_feed_symbols: str = "BTC-USDT"  # comma-separated
    exchange_feed_exchanges: str = "BINANCE,OKX,KRAKEN,BYBIT"  # comma-separated
    exchange_feed_topic: str = "exchange.bars"
    exchange_feed_bar_interval_s: int = 1
    exchange_feed_flush_delay_s: int = 2

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

    # Shared memory orderbook (zero-copy hot path for strategy reads)
    shmem_enabled: bool = False
    shmem_slots: int = 4096

    # Logging
    log_file: str = ""  # Empty means console only, e.g. "logs/pipeline.jsonl"
