"""Shared types for exploration components."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class ComponentConfig(BaseModel):
    """Base config for all components. Frozen and serializable."""

    model_config = ConfigDict(frozen=True)


class ComponentResult(BaseModel):
    """Result from a component execution step."""

    name: str
    dataframes: dict[str, Any] = {}  # name -> pl.DataFrame (not serialized)
    metrics: dict[str, Any] = {}  # name -> scalar value
    artifacts: dict[str, Path] = {}  # name -> saved file path

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ComponentHook(Protocol):
    """Custom logic injected between pipeline steps."""

    def __call__(
        self,
        db: Any,  # ExplorationDataSource
        prior_results: dict[str, ComponentResult],
        outputs_dir: Path,
    ) -> ComponentResult | None: ...
