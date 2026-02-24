"""MLflow integration for exploration stage tracking."""

from __future__ import annotations

import json
from pathlib import Path

import mlflow

from polymarket_pipeline.exploration.tree import (
    ClaudeAnalysis,
    ExplorationStage,
    StageMetrics,
)


class ExplorationTracker:
    """Tracks exploration stages in MLflow.

    One MLflow experiment per strategy. One run per stage.
    """

    def __init__(self, tracking_uri: str = "http://localhost:5050") -> None:
        mlflow.set_tracking_uri(tracking_uri)

    def get_or_create_experiment(self, strategy_name: str) -> str:
        """Get or create an MLflow experiment for a strategy."""
        experiment = mlflow.get_experiment_by_name(strategy_name)
        if experiment:
            return experiment.experiment_id
        return mlflow.create_experiment(strategy_name)

    def log_stage(
        self,
        strategy_name: str,
        stage: ExplorationStage,
        metrics: StageMetrics,
        outputs_dir: Path,
    ) -> str:
        """Log a completed stage as an MLflow run.

        Returns the mlflow_run_id.
        """
        experiment_id = self.get_or_create_experiment(strategy_name)

        with mlflow.start_run(
            experiment_id=experiment_id,
            run_name=f"{stage.id} — {stage.name}",
        ) as run:
            # Tags
            mlflow.set_tags(
                {
                    "stage_id": stage.id,
                    "stage_name": stage.name,
                    "depth": str(stage.depth),
                    "parent_id": stage.parent_id or "root",
                    "refinement_type": (
                        stage.refinement_type.value if stage.refinement_type else "root"
                    ),
                    "hypothesis": stage.hypothesis or "",
                    "status": stage.status.value,
                }
            )

            # Metrics
            metrics_dict = metrics.model_dump(exclude_none=True)
            custom = metrics_dict.pop("custom", {})
            # confidence_interval is a tuple, not loggable as metric
            metrics_dict.pop("confidence_interval", None)

            for key, value in metrics_dict.items():
                if isinstance(value, (int, float)):
                    mlflow.log_metric(key, value)

            for key, value in custom.items():
                if isinstance(value, (int, float)):
                    mlflow.log_metric(f"custom_{key}", value)

            # Params (filter conditions, analysis parameters from stage config)
            if stage.analysis:
                for ref in stage.analysis.proposed_refinements:
                    if ref.filter_conditions:
                        mlflow.log_param(
                            f"ref_{ref.name}_filters",
                            json.dumps(ref.filter_conditions),
                        )

            # Artifacts
            if outputs_dir.exists():
                for artifact in outputs_dir.iterdir():
                    if artifact.is_file() and artifact.stat().st_size < 50_000_000:
                        mlflow.log_artifact(str(artifact))

            return run.info.run_id

    def log_analysis(
        self,
        strategy_name: str,
        stage: ExplorationStage,
        analysis: ClaudeAnalysis,
        analysis_path: Path,
    ) -> None:
        """Log Claude analysis to an existing stage run."""
        if not stage.mlflow_run_id:
            return

        with mlflow.start_run(run_id=stage.mlflow_run_id):
            mlflow.set_tag("analysis_confidence", str(analysis.confidence))
            mlflow.set_tag("num_proposed_refinements", str(len(analysis.proposed_refinements)))
            mlflow.log_metric("analysis_confidence", analysis.confidence)

            if analysis_path.exists():
                mlflow.log_artifact(str(analysis_path))
