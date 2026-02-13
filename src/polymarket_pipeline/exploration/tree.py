"""Exploration tree model.

Tracks the DAG of strategy refinements. Each node represents a stage
in the exploration process, with parent-child relationships forming
the refinement tree.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEWING = "reviewing"
    ARCHIVED = "archived"
    PAUSED = "paused"


class RefinementType(str, Enum):
    FILTER = "filter"
    FEATURE = "feature"
    MODEL = "model"
    PARAMETER = "parameter"
    HYPOTHESIS = "hypothesis"
    ENSEMBLE = "ensemble"


class StageMetrics(BaseModel):
    """Metrics captured for a stage."""

    sample_size: int | None = None
    unique_traders: int | None = None
    unique_markets: int | None = None
    date_range_days: int | None = None

    sharpe_ratio: float | None = None
    total_return: float | None = None
    win_rate: float | None = None
    max_drawdown: float | None = None
    profit_factor: float | None = None

    p_value: float | None = None
    effect_size: float | None = None
    confidence_interval: tuple[float, float] | None = None

    custom: dict[str, Any] = Field(default_factory=dict)


class ProposedRefinement(BaseModel):
    """A refinement direction proposed by Claude."""

    name: str
    description: str
    refinement_type: RefinementType
    hypothesis: str
    expected_outcome: str
    priority: int = Field(ge=1, le=5)
    estimated_complexity: int = Field(ge=1, le=5)

    filter_conditions: dict[str, Any] | None = None
    new_features: list[str] | None = None
    model_config_extra: dict[str, Any] | None = None


class DataQualityIssue(BaseModel):
    """A data quality issue detected during review."""

    severity: str = Field(description="critical, warning, or info")
    description: str
    affected_metric: str | None = None
    suggested_fix: str | None = None


class ClaudeAnalysis(BaseModel):
    """Claude's analysis of a completed stage."""

    summary: str
    key_insights: list[str]
    concerns: list[str]
    proposed_refinements: list[ProposedRefinement]
    data_quality_issues: list[DataQualityIssue] = Field(default_factory=list)
    confidence: float
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class ExplorationStage(BaseModel):
    """A single stage in the exploration tree."""

    id: str
    name: str
    description: str

    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    depth: int = 0

    refinement_type: RefinementType | None = None
    hypothesis: str | None = None

    status: StageStatus = StageStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    metrics: StageMetrics | None = None
    analysis: ClaudeAnalysis | None = None
    mlflow_run_id: str | None = None

    script_path: str | None = None
    config_path: str | None = None
    outputs_path: str | None = None
    analysis_path: str | None = None

    last_error: str | None = None
    run_attempts: int = 0
    dq_fix_attempts: int = 0


class OrchestratorState(BaseModel):
    """Persisted state for the autonomous orchestrator loop."""

    paused: bool = False
    pause_reason: str | None = None
    paused_at_stage: str | None = None
    stages_completed: int = 0
    completed_stage_ids: list[str] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> OrchestratorState:
        return cls.model_validate_json(path.read_text())


class ExplorationTree(BaseModel):
    """The complete exploration tree for a strategy."""

    strategy_name: str
    description: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    mlflow_experiment_id: str | None = None

    stages: dict[str, ExplorationStage] = Field(default_factory=dict)
    root_id: str | None = None

    def add_stage(self, stage: ExplorationStage) -> None:
        self.stages[stage.id] = stage
        if stage.parent_id is None:
            self.root_id = stage.id
        elif stage.parent_id in self.stages:
            parent = self.stages[stage.parent_id]
            if stage.id not in parent.children_ids:
                parent.children_ids.append(stage.id)
        self.updated_at = datetime.utcnow()

    def get_stage(self, stage_id: str) -> ExplorationStage | None:
        return self.stages.get(stage_id)

    def get_children(self, stage_id: str) -> list[ExplorationStage]:
        stage = self.stages.get(stage_id)
        if not stage:
            return []
        return [self.stages[cid] for cid in stage.children_ids if cid in self.stages]

    def get_path_to_root(self, stage_id: str) -> list[ExplorationStage]:
        path = []
        current_id: str | None = stage_id
        while current_id:
            stage = self.stages.get(current_id)
            if not stage:
                break
            path.append(stage)
            current_id = stage.parent_id
        return list(reversed(path))

    def get_leaves(self) -> list[ExplorationStage]:
        return [s for s in self.stages.values() if not s.children_ids]

    def get_active_stages(self) -> list[ExplorationStage]:
        return [
            s
            for s in self.stages.values()
            if s.status in (StageStatus.COMPLETED, StageStatus.REVIEWING)
            and s.analysis is not None
        ]

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        status_styles = {
            StageStatus.PENDING: ":::pending",
            StageStatus.RUNNING: ":::running",
            StageStatus.COMPLETED: ":::completed",
            StageStatus.FAILED: ":::failed",
            StageStatus.REVIEWING: ":::reviewing",
            StageStatus.ARCHIVED: ":::archived",
            StageStatus.PAUSED: ":::paused",
        }

        for stage in self.stages.values():
            style = status_styles.get(stage.status, "")
            label = f'{stage.id}["{stage.name}"]{style}'
            lines.append(f"    {label}")
            for child_id in stage.children_ids:
                child = self.stages.get(child_id)
                if child and child.refinement_type:
                    lines.append(
                        f"    {stage.id} -->|{child.refinement_type.value}| {child_id}"
                    )
                else:
                    lines.append(f"    {stage.id} --> {child_id}")

        lines.extend(
            [
                "",
                "    classDef pending fill:#f9f,stroke:#333",
                "    classDef running fill:#ff9,stroke:#333",
                "    classDef completed fill:#9f9,stroke:#333",
                "    classDef failed fill:#f99,stroke:#333",
                "    classDef reviewing fill:#99f,stroke:#333",
                "    classDef archived fill:#999,stroke:#333",
                "    classDef paused fill:#f90,stroke:#333",
            ]
        )
        return "\n".join(lines)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> ExplorationTree:
        return cls.model_validate_json(path.read_text())


def create_root_stage(
    strategy_name: str,
    description: str,
    hypothesis: str,
) -> ExplorationStage:
    return ExplorationStage(
        id="00_initial",
        name=f"Initial {strategy_name.replace('_', ' ').title()}",
        description=description,
        hypothesis=hypothesis,
        depth=0,
    )


def create_refinement_stage(
    parent: ExplorationStage,
    refinement: ProposedRefinement,
    index: str,
) -> ExplorationStage:
    stage_id = f"{parent.depth + 1:02d}{index}_{refinement.name.lower().replace(' ', '_')}"
    return ExplorationStage(
        id=stage_id,
        name=refinement.name,
        description=refinement.description,
        parent_id=parent.id,
        depth=parent.depth + 1,
        refinement_type=refinement.refinement_type,
        hypothesis=refinement.hypothesis,
    )


def render_analysis_markdown(
    stage: ExplorationStage,
    analysis: ClaudeAnalysis,
    tree: ExplorationTree,
) -> str:
    """Render a ClaudeAnalysis as Markdown."""
    path_to_root = tree.get_path_to_root(stage.id)
    path_str = " -> ".join(s.name for s in path_to_root)

    refinements_md = []
    for i, ref in enumerate(analysis.proposed_refinements, 1):
        refinements_md.append(
            f"### {i}. {ref.name}\n"
            f"**Type:** {ref.refinement_type.value} | "
            f"**Priority:** {ref.priority}/5 | "
            f"**Complexity:** {ref.estimated_complexity}/5\n\n"
            f"{ref.description}\n\n"
            f"**Hypothesis:** {ref.hypothesis}\n\n"
            f"**Expected Outcome:** {ref.expected_outcome}\n"
        )

    dq_md = ""
    if analysis.data_quality_issues:
        dq_md = "\n## Data Quality Issues\n\n"
        for issue in analysis.data_quality_issues:
            icon = {"critical": "!!!", "warning": "!!", "info": "i"}.get(
                issue.severity, "?"
            )
            dq_md += f"- **[{icon}] {issue.severity.upper()}**: {issue.description}\n"
            if issue.affected_metric:
                dq_md += f"  - Affected metric: {issue.affected_metric}\n"
            if issue.suggested_fix:
                dq_md += f"  - Suggested fix: {issue.suggested_fix}\n"

    return (
        f"# Analysis: {stage.name}\n\n"
        f"**Stage ID:** `{stage.id}`\n"
        f"**Path:** {path_str}\n"
        f"**Generated:** {analysis.generated_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"**Confidence:** {analysis.confidence:.0%}\n\n"
        f"## Summary\n\n{analysis.summary}\n\n"
        f"## Key Insights\n\n"
        + "".join(f"- {insight}\n" for insight in analysis.key_insights)
        + f"\n## Concerns\n\n"
        + "".join(f"- {concern}\n" for concern in analysis.concerns)
        + dq_md
        + f"\n## Proposed Refinements\n\n"
        + "\n".join(refinements_md)
        + f"\n## Exploration Tree\n\n```mermaid\n{tree.to_mermaid()}\n```\n"
    )
