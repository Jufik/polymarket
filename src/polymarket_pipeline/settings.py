"""Pipeline-wide configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    """Configuration for the offline data pipeline.

    All values can be overridden via environment variables with PM_ prefix.
    Example: PM_DATA_DIR=./my_data
    """

    model_config = SettingsConfigDict(env_prefix="PM_", env_file=".env", extra="ignore")

    data_dir: str = "data"
    raw_parquet_dir: str = "order_filled"
    pg_dsn: str = "postgresql://polymarket:polymarket@localhost:15432/polymarket"
    ch_host: str = "localhost"
    ch_port: int = 18123
    ch_database: str = "polymarket"

    @property
    def metadata_dir(self) -> Path:
        return Path(self.data_dir) / "metadata"

    @property
    def compact_dir(self) -> Path:
        return Path(self.data_dir) / "compact"

    @property
    def derived_dir(self) -> Path:
        return Path(self.data_dir) / "derived"

    @property
    def token_map_path(self) -> Path:
        return self.metadata_dir / "token_map.parquet"

    @property
    def markets_path(self) -> Path:
        return self.metadata_dir / "markets.parquet"
