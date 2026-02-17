"""Stage pipeline composition.

Allows new stages to be composed from reusable components
instead of 900-line standalone scripts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polymarket_pipeline.exploration.components.base import (
    ComponentConfig,
    ComponentHook,
    ComponentResult,
)
from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics


@dataclass
class PipelineStep:
    """A component step in the pipeline."""

    name: str
    component_fn: Callable[
        [ExplorationDataSource, ComponentConfig, Path, dict[str, ComponentResult] | None],
        ComponentResult,
    ]
    config: ComponentConfig
    depends_on: list[str] = field(default_factory=list)


@dataclass
class HookStep:
    """A custom hook step injected between component steps."""

    name: str
    hook_fn: ComponentHook
    depends_on: list[str] = field(default_factory=list)


class StagePipeline:
    """Compose a stage from reusable component steps.

    Executes steps in dependency order, passing prior results forward.
    Returns the same contract as existing hand-written stages:
    ``{"stage_id": ..., "metrics": ..., "findings": ...}``
    """

    def __init__(self, stage_id: str, steps: list[PipelineStep | HookStep]) -> None:
        self.stage_id = stage_id
        self.steps = steps

    def run(self, strategy_root: Path, outputs_dir: Path) -> dict[str, Any]:
        """Execute all steps and return summary dict."""
        db = ExplorationDataSource()
        outputs_dir.mkdir(parents=True, exist_ok=True)

        results: dict[str, ComponentResult] = {}
        execution_order = self._resolve_order()

        for step in execution_order:
            if isinstance(step, PipelineStep):
                step_result = step.component_fn(db, step.config, outputs_dir, results)
            else:
                hook_result = step.hook_fn(db, results, outputs_dir)
                if hook_result is None:
                    continue
                step_result = hook_result

            results[step_result.name] = step_result

        return self._build_summary(results)

    def _resolve_order(self) -> list[PipelineStep | HookStep]:
        """Topological sort of steps by depends_on."""
        step_map = {s.name: s for s in self.steps}
        visited: set[str] = set()
        order: list[PipelineStep | HookStep] = []

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            step = step_map[name]
            for dep in step.depends_on:
                if dep in step_map:
                    visit(dep)
            order.append(step)

        for step in self.steps:
            visit(step.name)

        return order

    def _build_summary(self, results: dict[str, ComponentResult]) -> dict[str, Any]:
        """Build the standard stage summary from component results."""
        # Merge all metrics
        all_metrics: dict[str, Any] = {}
        all_findings: dict[str, Any] = {}

        for _name, result in results.items():
            all_metrics.update(result.metrics)
            # Add dataframe summaries to findings
            for df_name, df in result.dataframes.items():
                if hasattr(df, "__len__"):
                    all_findings[f"{df_name}_count"] = len(df)

        # Extract StageMetrics-compatible fields
        stage_metrics = StageMetrics(
            sample_size=all_metrics.get("traders_passing_filters"),
            unique_traders=all_metrics.get(
                "consistent_count",
                all_metrics.get("skilled_count"),
            ),
            unique_markets=all_metrics.get("resolved_market_count"),
            custom={k: v for k, v in all_metrics.items()},
        )

        return {
            "stage_id": self.stage_id,
            "metrics": stage_metrics.model_dump(exclude_none=True),
            "findings": all_findings,
        }
