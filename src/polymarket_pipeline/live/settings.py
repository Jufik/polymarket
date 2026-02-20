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
    pg_dsn: str = "postgresql://polymarket:polymarket@localhost:15432/polymarket"

    # Quality thresholds
    quality_check_interval_s: int = 900
    source_liveness_timeout_s: int = 30
    volume_drop_warn_pct: float = 0.50
    volume_drop_red_pct: float = 0.10
    enrichment_ratio_min: float = 0.80

    # Recovery
    gap_threshold_s: int = 600

    # Dashboard
    dashboard_refresh_s: int = 5
    dashboard_port: int = 8099

    # Batching (ClickHouse consumer)
    ch_batch_size: int = 100
    ch_flush_interval_s: float = 1.0
