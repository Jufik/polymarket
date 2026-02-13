"""Shared lifecycle functions for strategy exploration.

Extracts duplicated logic from CLI commands into reusable functions
that can be called by both the CLI and the autonomous orchestrator.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from polymarket_pipeline.exploration.tree import (
    ClaudeAnalysis,
    ExplorationStage,
    ExplorationTree,
    OrchestratorState,
    ProposedRefinement,
    StageMetrics,
    StageStatus,
    create_refinement_stage,
    render_analysis_markdown,
)

STRATEGIES_ROOT = Path("strategies")


# ---------------------------------------------------------------------------
# Lifecycle callback protocol
# ---------------------------------------------------------------------------


class LifecycleCallback(Protocol):
    """Pluggable output interface for lifecycle events."""

    def on_info(self, message: str) -> None: ...
    def on_success(self, message: str) -> None: ...
    def on_warning(self, message: str) -> None: ...
    def on_error(self, message: str) -> None: ...
    def on_stage_start(self, stage: ExplorationStage) -> None: ...
    def on_stage_complete(self, stage: ExplorationStage) -> None: ...
    def on_data_quality_alert(self, stage: ExplorationStage, issues: list) -> None: ...
    def on_agent_event(self, entry: dict) -> None: ...
    def on_retry(self, stage: ExplorationStage, attempt: int, error: str) -> None: ...


class SilentCallback:
    """No-op callback for non-interactive use."""

    def on_info(self, message: str) -> None:
        pass

    def on_success(self, message: str) -> None:
        pass

    def on_warning(self, message: str) -> None:
        pass

    def on_error(self, message: str) -> None:
        pass

    def on_stage_start(self, stage: ExplorationStage) -> None:
        pass

    def on_stage_complete(self, stage: ExplorationStage) -> None:
        pass

    def on_data_quality_alert(self, stage: ExplorationStage, issues: list) -> None:
        pass

    def on_agent_event(self, entry: dict) -> None:
        pass

    def on_retry(self, stage: ExplorationStage, attempt: int, error: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    success: bool
    stage: ExplorationStage
    error: str | None = None


@dataclass
class ReviewResult:
    success: bool
    stage: ExplorationStage
    analysis: ClaudeAnalysis | None = None
    has_critical_dq_issues: bool = False
    error: str | None = None


@dataclass
class GenerateResult:
    success: bool
    stage: ExplorationStage
    error: str | None = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_strategy_path(name: str) -> Path:
    return STRATEGIES_ROOT / name


def load_tree(name: str) -> ExplorationTree:
    tree_path = get_strategy_path(name) / "exploration_tree.json"
    if not tree_path.exists():
        raise FileNotFoundError(f"Strategy '{name}' not found at {tree_path}")
    return ExplorationTree.load(tree_path)


def save_tree(name: str, tree: ExplorationTree) -> None:
    tree.save(get_strategy_path(name) / "exploration_tree.json")


def load_orchestrator_state(name: str) -> OrchestratorState | None:
    path = get_strategy_path(name) / "orchestrator_state.json"
    if not path.exists():
        return None
    return OrchestratorState.load(path)


def save_orchestrator_state(name: str, state: OrchestratorState) -> None:
    state.last_updated = datetime.utcnow()
    state.save(get_strategy_path(name) / "orchestrator_state.json")


# ---------------------------------------------------------------------------
# Run stage
# ---------------------------------------------------------------------------


def run_stage(
    strategy: str,
    tree: ExplorationTree,
    stage: ExplorationStage,
    callback: LifecycleCallback | None = None,
) -> RunResult:
    """Execute a stage script, save outputs, update tree state."""
    cb = callback or SilentCallback()
    strategy_path = get_strategy_path(strategy)
    script_path = strategy_path / (stage.script_path or f"stages/{stage.id}/stage.py")

    if not script_path.exists():
        cb.on_error(f"Script not found: {script_path}")
        return RunResult(success=False, stage=stage, error=f"Script not found: {script_path}")

    outputs_dir = strategy_path / (stage.outputs_path or f"stages/{stage.id}/outputs")
    outputs_dir.mkdir(parents=True, exist_ok=True)

    stage.status = StageStatus.RUNNING
    stage.started_at = datetime.utcnow()
    save_tree(strategy, tree)

    cb.on_stage_start(stage)

    try:
        spec = importlib.util.spec_from_file_location(f"stage_{stage.id}", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "run"):
            stage.status = StageStatus.FAILED
            save_tree(strategy, tree)
            cb.on_error(f"Stage script missing run() function")
            return RunResult(success=False, stage=stage, error="Missing run() function")

        outputs_summary = module.run(strategy_path, outputs_dir)

        # Save summary
        with open(outputs_dir / "summary.json", "w") as f:
            json.dump(outputs_summary, f, indent=2, default=str)

        # Extract metrics
        if "metrics" in outputs_summary:
            metrics_data = outputs_summary["metrics"]
            if isinstance(metrics_data, dict):
                stage.metrics = StageMetrics(**metrics_data)
                with open(outputs_dir / "metrics.json", "w") as f:
                    json.dump(metrics_data, f, indent=2)

        stage.status = StageStatus.COMPLETED
        stage.completed_at = datetime.utcnow()
        save_tree(strategy, tree)

        cb.on_stage_complete(stage)

        # MLflow logging (best-effort)
        try:
            from polymarket_pipeline.exploration.tracking import ExplorationTracker

            tracker = ExplorationTracker()
            run_id = tracker.log_stage(
                strategy_name=strategy,
                stage=stage,
                metrics=stage.metrics or StageMetrics(),
                outputs_dir=outputs_dir,
            )
            stage.mlflow_run_id = run_id
            save_tree(strategy, tree)
            cb.on_info(f"MLflow run: {run_id}")
        except Exception as e:
            cb.on_warning(f"MLflow logging skipped: {e}")

        return RunResult(success=True, stage=stage)

    except Exception as e:
        stage.status = StageStatus.FAILED
        save_tree(strategy, tree)
        cb.on_error(f"Stage failed: {e}")
        return RunResult(success=False, stage=stage, error=str(e))


# ---------------------------------------------------------------------------
# Run stage with retry (self-healing)
# ---------------------------------------------------------------------------


async def run_stage_with_retry(
    strategy: str,
    tree: ExplorationTree,
    stage: ExplorationStage,
    parent: ExplorationStage | None,
    refinement: ProposedRefinement | None,
    callback: LifecycleCallback | None = None,
    max_retries: int = 3,
) -> RunResult:
    """Run a stage, and on failure regenerate the script with error context.

    Loop:
      1. run_stage()
      2. On failure: save error, increment run_attempts, regenerate script
      3. Reset stage to PENDING, loop back to 1
      4. After max_retries, return the final failure
    """
    cb = callback or SilentCallback()
    strategy_path = get_strategy_path(strategy)

    for attempt in range(1, max_retries + 1):
        # Reload tree to get latest state
        tree = load_tree(strategy)
        stage = tree.get_stage(stage.id) or stage

        result = run_stage(strategy, tree, stage, callback=cb)

        if result.success:
            return result

        # Record the error on the stage
        stage.last_error = result.error
        stage.run_attempts = attempt
        save_tree(strategy, tree)

        if attempt >= max_retries:
            cb.on_error(
                f"Stage {stage.id} failed after {max_retries} attempts: {result.error}"
            )
            return result

        # Notify about retry
        cb.on_retry(stage, attempt, result.error or "Unknown error")

        # Regenerate the script with error context
        from polymarket_pipeline.exploration.agent import generate_stage_script

        script_path = strategy_path / (stage.script_path or f"stages/{stage.id}/stage.py")
        await generate_stage_script(
            stage=stage,
            parent=parent,
            refinement=refinement,
            strategy_root=strategy_path,
            output_path=script_path,
            on_event=cb.on_agent_event,
            previous_error=result.error,
        )

        # Reset stage to PENDING so run_stage picks it up again
        tree = load_tree(strategy)
        stage = tree.get_stage(stage.id) or stage
        stage.status = StageStatus.PENDING
        save_tree(strategy, tree)

    # Should not reach here, but satisfy type checker
    return RunResult(success=False, stage=stage, error="Max retries exhausted")


# ---------------------------------------------------------------------------
# Review stage
# ---------------------------------------------------------------------------


async def review_stage_lifecycle(
    strategy: str,
    tree: ExplorationTree,
    stage: ExplorationStage,
    callback: LifecycleCallback | None = None,
) -> ReviewResult:
    """Run Claude review, save transcript and analysis, check DQ issues."""
    cb = callback or SilentCallback()
    strategy_path = get_strategy_path(strategy)

    cb.on_info(f"Reviewing stage: {stage.name}")

    try:
        from polymarket_pipeline.exploration.agent import review_stage

        analysis, agent_result = await review_stage(
            stage, strategy_path, on_event=cb.on_agent_event,
        )

        # Save transcript
        stage_dir = strategy_path / "stages" / stage.id
        transcript_path = stage_dir / "transcript.json"
        agent_result.save_transcript(transcript_path)
        cb.on_info(f"Transcript saved ({len(agent_result.transcript)} entries)")

        # Save analysis
        stage.analysis = analysis
        stage.status = StageStatus.REVIEWING

        analysis_path = strategy_path / (
            stage.analysis_path or f"stages/{stage.id}/analysis.md"
        )
        analysis_md = render_analysis_markdown(stage, analysis, tree)
        analysis_path.write_text(analysis_md)

        # MLflow logging (best-effort)
        try:
            from polymarket_pipeline.exploration.tracking import ExplorationTracker

            tracker = ExplorationTracker()
            tracker.log_analysis(strategy, stage, analysis, analysis_path)
        except Exception as e:
            cb.on_warning(f"MLflow analysis logging skipped: {e}")

        save_tree(strategy, tree)

        # Check for critical data quality issues
        critical_issues = [
            dq for dq in analysis.data_quality_issues if dq.severity == "critical"
        ]
        if critical_issues:
            cb.on_data_quality_alert(stage, critical_issues)
            return ReviewResult(
                success=True,
                stage=stage,
                analysis=analysis,
                has_critical_dq_issues=True,
            )

        cb.on_success(f"Review complete: confidence={analysis.confidence:.0%}")
        return ReviewResult(success=True, stage=stage, analysis=analysis)

    except Exception as e:
        cb.on_error(f"Review failed: {e}")
        return ReviewResult(success=False, stage=stage, error=str(e))


# ---------------------------------------------------------------------------
# Generate stage
# ---------------------------------------------------------------------------


async def generate_stage_lifecycle(
    strategy: str,
    tree: ExplorationTree,
    parent: ExplorationStage,
    refinement: ProposedRefinement,
    callback: LifecycleCallback | None = None,
) -> GenerateResult:
    """Create a child stage, generate its script via Claude, save tree."""
    cb = callback or SilentCallback()
    strategy_path = get_strategy_path(strategy)

    # Determine index letter
    existing_children = tree.get_children(parent.id)
    index = chr(ord("a") + len(existing_children))

    new_stage = create_refinement_stage(parent, refinement, index)
    new_stage.script_path = f"stages/{new_stage.id}/stage.py"
    new_stage.outputs_path = f"stages/{new_stage.id}/outputs"
    new_stage.analysis_path = f"stages/{new_stage.id}/analysis.md"

    tree.add_stage(new_stage)

    # Create directories
    stage_dir = strategy_path / "stages" / new_stage.id
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "outputs").mkdir(exist_ok=True)

    cb.on_info(f"Generating script for: {new_stage.name}")

    try:
        from polymarket_pipeline.exploration.agent import generate_stage_script

        await generate_stage_script(
            stage=new_stage,
            parent=parent,
            refinement=refinement,
            strategy_root=strategy_path,
            output_path=stage_dir / "stage.py",
            on_event=cb.on_agent_event,
        )

        save_tree(strategy, tree)
        cb.on_success(f"Generated stage: {new_stage.id}")
        return GenerateResult(success=True, stage=new_stage)

    except Exception as e:
        cb.on_error(f"Generation failed: {e}")
        return GenerateResult(success=False, stage=new_stage, error=str(e))
