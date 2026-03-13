"""Config section schemas — strict Pydantic models for all dynamic config."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pm_core.types import ExecutionMode


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    mode: ExecutionMode = ExecutionMode.PAPER_DEV
    capital_usd: float = Field(default=500.0, ge=0, le=100_000)
    max_position_usd: float = Field(default=100.0, ge=0, le=50_000)
    max_open_positions: int = Field(default=20, ge=1, le=500)
    cooldown_s: float = Field(default=30.0, ge=0, le=3600)
    features: list[str] = []
    params: dict[str, Any] = {}


class CLOBWSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_slots: int = Field(default=4, ge=1, le=20)
    assets_per_slot: int = Field(default=500, ge=50, le=2000)
    redundancy: int = Field(default=1, ge=1, le=3)
    stale_timeout_s: float = Field(default=120, ge=10, le=600)
    rotation_s: float = Field(default=300, ge=60, le=3600)


class RTDSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pool_size: int = Field(default=2, ge=1, le=5)
    rotation_s: float = Field(default=300, ge=60, le=3600)
    dedup_ttl_s: float = Field(default=600, ge=60, le=3600)


class RPCConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publish_workers: int = Field(default=8, ge=1, le=32)
    dedup_ttl_s: float = Field(default=60, ge=10, le=600)
    stale_timeout_s: float = Field(default=120, ge=30, le=600)


class QualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_liveness_timeout_s: float = Field(default=30, ge=5, le=300)
    volume_drop_red_pct: float = Field(default=10, ge=1, le=50)
    degraded_grace_s: float = Field(default=120, ge=30, le=900)
    enrichment_ratio_min: float = Field(default=0.8, ge=0.5, le=1.0)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_total_exposure_usd: float = Field(default=5000, ge=0, le=100_000)
    patient_timeout_s: float = Field(default=30, ge=5, le=300)
    fee_pct: float = Field(default=0.0, ge=0.0, le=0.05)


class LifecycleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reconcile_interval_s: float = Field(default=300, ge=60, le=3600)
    archive_after_days: int = Field(default=30, ge=7, le=365)
    resolution_poll_interval_s: float = Field(default=60, ge=10, le=600)
