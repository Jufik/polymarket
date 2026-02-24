"""Parallel exploration pipeline execution.

Runs N generate/run/review pipelines concurrently via asyncio.gather.
All tree mutations go through TreeManager with a threading.Lock to
prevent lost updates when multiple pipelines save concurrently.

Serial mode (parallel=1) bypasses this module entirely.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from polymarket_pipeline.exploration.lifecycle import (
    LifecycleCallback,
    SilentCallback,
    get_strategy_path,
)
from polymarket_pipeline.exploration.tree import (
    ClaudeAnalysis,
    DataQualityIssue,
    ExplorationStage,
    ExplorationTree,
    ProposedRefinement,
    StageMetrics,
    StageStatus,
    create_refinement_stage,
    render_analysis_markdown,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# TreeManager — thread-safe load/save with atomic update
# ---------------------------------------------------------------------------


class TreeManager:
    """Thread-safe wrapper for ExplorationTree I/O.

    Uses a threading.Lock (not asyncio.Lock) because _execute_script
    runs in a real OS thread via asyncio.to_thread.
    """

    def __init__(self, strategy: str) -> None:
        self.strategy = strategy
        self._lock = threading.Lock()
        self._tree_path = get_strategy_path(strategy) / "exploration_tree.json"

    def load(self) -> ExplorationTree:
        with self._lock:
            return ExplorationTree.load(self._tree_path)

    def save(self, tree: ExplorationTree) -> None:
        with self._lock:
            tree.save(self._tree_path)

    def update_atomic(
        self,
        fn: Callable[[ExplorationTree], Any],
    ) -> Any:
        """Load tree, apply *fn*, save, return fn's result.

        The entire load-modify-save cycle runs under the lock.
        """
        with self._lock:
            tree = ExplorationTree.load(self._tree_path)
            result = fn(tree)
            tree.save(self._tree_path)
            return result


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Outcome of a single parallel pipeline."""

    stage_id: str
    success: bool
    has_critical_dq: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# _execute_script — sync, runs in thread via asyncio.to_thread
# ---------------------------------------------------------------------------


def _execute_script(
    tree_mgr: TreeManager,
    stage_id: str,
    callback: LifecycleCallback,
) -> None:
    """Execute a stage script with all tree mutations going through TreeManager.

    This is a sync function intended to run in asyncio.to_thread.
    """
    strategy_path = get_strategy_path(tree_mgr.strategy)

    # 1. Read paths from tree
    def _get_paths(tree: ExplorationTree) -> tuple[Path, Path]:
        stage = tree.stages[stage_id]
        script = strategy_path / (stage.script_path or f"stages/{stage_id}/stage.py")
        outputs = strategy_path / (stage.outputs_path or f"stages/{stage_id}/outputs")
        return script, outputs

    script_path, outputs_dir = tree_mgr.update_atomic(_get_paths)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # 2. Set status=RUNNING
    def _set_running(tree: ExplorationTree) -> None:
        stage = tree.stages[stage_id]
        stage.status = StageStatus.RUNNING
        stage.started_at = datetime.utcnow()

    tree_mgr.update_atomic(_set_running)

    if not script_path.exists():

        def _set_failed_missing(tree: ExplorationTree) -> None:
            tree.stages[stage_id].status = StageStatus.FAILED

        tree_mgr.update_atomic(_set_failed_missing)
        raise FileNotFoundError(f"Script not found: {script_path}")

    # 3. Import and run module.run()
    spec = importlib.util.spec_from_file_location(f"stage_{stage_id}", script_path)
    if spec is None or spec.loader is None:

        def _set_failed_spec(tree: ExplorationTree) -> None:
            tree.stages[stage_id].status = StageStatus.FAILED

        tree_mgr.update_atomic(_set_failed_spec)
        raise ImportError(f"Cannot load spec for {script_path}")

    mod_name = f"stage_{stage_id}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module  # Python 3.14: dataclass needs this
    spec.loader.exec_module(module)

    if not hasattr(module, "run"):

        def _set_failed_norun(tree: ExplorationTree) -> None:
            tree.stages[stage_id].status = StageStatus.FAILED

        tree_mgr.update_atomic(_set_failed_norun)
        raise AttributeError("Stage script missing run() function")

    outputs_summary: dict[str, Any] = module.run(strategy_path, outputs_dir)

    # Save summary.json
    with open(outputs_dir / "summary.json", "w") as f:
        json.dump(outputs_summary, f, indent=2, default=str)

    # 4. Set status=COMPLETED, save metrics
    def _set_completed(tree: ExplorationTree) -> None:
        stage = tree.stages[stage_id]
        stage.status = StageStatus.COMPLETED
        stage.completed_at = datetime.utcnow()

        if "metrics" in outputs_summary:
            metrics_data = outputs_summary["metrics"]
            if isinstance(metrics_data, dict):
                stage.metrics = StageMetrics(**metrics_data)
                with open(outputs_dir / "metrics.json", "w") as mf:
                    json.dump(metrics_data, mf, indent=2)

    tree_mgr.update_atomic(_set_completed)
    callback.on_stage_complete(tree_mgr.load().stages[stage_id])

    # 5. MLflow logging (best-effort)
    try:
        from polymarket_pipeline.exploration.tracking import ExplorationTracker

        def _log_mlflow(tree: ExplorationTree) -> None:
            stage = tree.stages[stage_id]
            tracker = ExplorationTracker()
            run_id = tracker.log_stage(
                strategy_name=tree_mgr.strategy,
                stage=stage,
                metrics=stage.metrics or StageMetrics(),
                outputs_dir=outputs_dir,
            )
            stage.mlflow_run_id = run_id

        tree_mgr.update_atomic(_log_mlflow)
    except Exception as e:
        callback.on_warning(f"MLflow logging skipped: {e}")


# ---------------------------------------------------------------------------
# _run_single_pipeline — one full generate -> run -> review pipeline
# ---------------------------------------------------------------------------


async def _run_single_pipeline(
    tree_mgr: TreeManager,
    parent_id: str,
    refinement: ProposedRefinement,
    index: str,
    callback: LifecycleCallback,
    max_retries: int = 3,
    max_dq_retries: int = 2,
) -> PipelineResult:
    """Execute one full pipeline for a single refinement.

    Steps:
      1. Create stage (atomic)
      2. Generate script (async Claude API)
      3. Run with retry (thread + async regeneration)
      4. Review (async Claude API)
      5. DQ self-heal if needed
    """
    from polymarket_pipeline.exploration.agent import (
        generate_stage_script,
        review_stage,
    )

    strategy_path = get_strategy_path(tree_mgr.strategy)
    stage_id: str = ""

    try:
        # Step 1: Create stage atomically
        def _create_stage(tree: ExplorationTree) -> str:
            parent = tree.stages[parent_id]
            new_stage = create_refinement_stage(parent, refinement, index, tree=tree)
            new_stage.script_path = f"stages/{new_stage.id}/stage.py"
            new_stage.outputs_path = f"stages/{new_stage.id}/outputs"
            new_stage.analysis_path = f"stages/{new_stage.id}/analysis.md"
            tree.add_stage(new_stage)

            # Create directories
            stage_dir = strategy_path / "stages" / new_stage.id
            stage_dir.mkdir(parents=True, exist_ok=True)
            (stage_dir / "outputs").mkdir(exist_ok=True)

            return new_stage.id

        stage_id = tree_mgr.update_atomic(_create_stage)
        callback.on_info(f"[parallel] Created stage: {stage_id}")

        # Step 2: Generate script (async Claude API — no lock needed)
        tree_snapshot = tree_mgr.load()
        stage = tree_snapshot.stages[stage_id]
        parent_stage = tree_snapshot.stages[parent_id]
        script_path = strategy_path / (stage.script_path or f"stages/{stage_id}/stage.py")

        callback.on_info(f"[parallel] Generating script for: {stage.name}")
        await generate_stage_script(
            stage=stage,
            parent=parent_stage,
            refinement=refinement,
            strategy_root=strategy_path,
            output_path=script_path,
            on_event=callback.on_agent_event,
        )

        # Step 3: Run with retry
        for attempt in range(1, max_retries + 1):
            try:
                await asyncio.to_thread(
                    _execute_script,
                    tree_mgr,
                    stage_id,
                    callback,
                )
                break  # success
            except Exception as run_err:
                error_str = str(run_err)

                def _record_error(
                    tree: ExplorationTree,
                    err: str = error_str,
                    att: int = attempt,
                ) -> None:
                    s = tree.stages[stage_id]
                    s.last_error = err
                    s.run_attempts = att
                    s.status = StageStatus.PENDING

                tree_mgr.update_atomic(_record_error)

                if attempt >= max_retries:
                    callback.on_error(
                        f"[parallel] Stage {stage_id} failed after "
                        f"{max_retries} attempts: {error_str}"
                    )
                    return PipelineResult(
                        stage_id=stage_id,
                        success=False,
                        error=error_str,
                    )

                callback.on_retry(
                    tree_mgr.load().stages[stage_id],
                    attempt,
                    error_str,
                )

                # Regenerate script with error context
                tree_snapshot = tree_mgr.load()
                stage = tree_snapshot.stages[stage_id]
                parent_stage = tree_snapshot.stages[parent_id]
                await generate_stage_script(
                    stage=stage,
                    parent=parent_stage,
                    refinement=refinement,
                    strategy_root=strategy_path,
                    output_path=script_path,
                    on_event=callback.on_agent_event,
                    previous_error=error_str,
                )

        # Step 4: Review (async Claude API)
        tree_snapshot = tree_mgr.load()
        stage = tree_snapshot.stages[stage_id]
        callback.on_info(f"[parallel] Reviewing stage: {stage.name}")

        from polymarket_pipeline.exploration.tree import build_exploration_context

        context = build_exploration_context(tree_snapshot)
        analysis, agent_result = await review_stage(
            stage,
            strategy_path,
            on_event=callback.on_agent_event,
            context=context,
            tree=tree_snapshot,
        )

        # Save transcript
        stage_dir = strategy_path / "stages" / stage_id
        agent_result.save_transcript(stage_dir / "transcript.json")

        # Save analysis atomically
        def _save_analysis(tree: ExplorationTree) -> None:
            s = tree.stages[stage_id]
            s.analysis = analysis
            s.status = StageStatus.REVIEWING

            analysis_path = strategy_path / (s.analysis_path or f"stages/{stage_id}/analysis.md")
            analysis_md = render_analysis_markdown(s, analysis, tree)
            analysis_path.write_text(analysis_md)

        tree_mgr.update_atomic(_save_analysis)

        # MLflow analysis logging (best-effort)
        try:
            from polymarket_pipeline.exploration.tracking import ExplorationTracker

            def _log_analysis_mlflow(tree: ExplorationTree) -> None:
                s = tree.stages[stage_id]
                tracker = ExplorationTracker()
                analysis_path = strategy_path / (
                    s.analysis_path or f"stages/{stage_id}/analysis.md"
                )
                tracker.log_analysis(tree_mgr.strategy, s, analysis, analysis_path)

            tree_mgr.update_atomic(_log_analysis_mlflow)
        except Exception as e:
            callback.on_warning(f"MLflow analysis logging skipped: {e}")

        # Step 5: Check for critical DQ issues -> self-heal
        critical_issues = [dq for dq in analysis.data_quality_issues if dq.severity == "critical"]

        if critical_issues:
            callback.on_data_quality_alert(
                tree_mgr.load().stages[stage_id],
                critical_issues,
            )
            has_critical = await _dq_self_heal(
                tree_mgr=tree_mgr,
                stage_id=stage_id,
                parent_id=parent_id,
                refinement=refinement,
                dq_issues=critical_issues,
                callback=callback,
                max_dq_retries=max_dq_retries,
            )
            if has_critical:
                return PipelineResult(
                    stage_id=stage_id,
                    success=True,
                    has_critical_dq=True,
                )

        callback.on_success(
            f"[parallel] Pipeline complete: {stage_id} (confidence={analysis.confidence:.0%})"
        )
        return PipelineResult(stage_id=stage_id, success=True)

    except Exception as exc:
        callback.on_error(f"[parallel] Pipeline failed for {stage_id or 'unknown'}: {exc}")
        return PipelineResult(
            stage_id=stage_id or "unknown",
            success=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# DQ self-heal loop (within a single pipeline)
# ---------------------------------------------------------------------------


async def _dq_self_heal(
    tree_mgr: TreeManager,
    stage_id: str,
    parent_id: str,
    refinement: ProposedRefinement,
    dq_issues: list[DataQualityIssue],
    callback: LifecycleCallback,
    max_dq_retries: int = 2,
) -> bool:
    """Attempt to fix critical DQ issues. Returns True if still has critical DQ."""
    from polymarket_pipeline.exploration.agent import (
        generate_stage_script,
        review_stage,
    )
    from polymarket_pipeline.exploration.tree import build_exploration_context

    strategy_path = get_strategy_path(tree_mgr.strategy)
    current_issues = dq_issues

    for attempt in range(1, max_dq_retries + 1):
        callback.on_dq_retry(
            tree_mgr.load().stages[stage_id],
            attempt,
            current_issues,
        )

        # 1. Regenerate script with DQ context
        tree_snapshot = tree_mgr.load()
        stage = tree_snapshot.stages[stage_id]
        parent_stage = tree_snapshot.stages[parent_id]
        script_path = strategy_path / (stage.script_path or f"stages/{stage_id}/stage.py")

        await generate_stage_script(
            stage=stage,
            parent=parent_stage,
            refinement=refinement,
            strategy_root=strategy_path,
            output_path=script_path,
            on_event=callback.on_agent_event,
            previous_dq_issues=current_issues,
        )

        # 2. Reset stage
        def _reset_for_dq(tree: ExplorationTree, att: int = attempt) -> None:
            s = tree.stages[stage_id]
            s.status = StageStatus.PENDING
            s.analysis = None
            s.dq_fix_attempts = att

        tree_mgr.update_atomic(_reset_for_dq)

        # 3. Re-run
        try:
            await asyncio.to_thread(
                _execute_script,
                tree_mgr,
                stage_id,
                callback,
            )
        except Exception as e:
            callback.on_error(f"[parallel] DQ re-run failed: {e}")
            return True  # still has DQ

        # 4. Re-review
        tree_snapshot = tree_mgr.load()
        stage = tree_snapshot.stages[stage_id]
        context = build_exploration_context(tree_snapshot)

        analysis, agent_result = await review_stage(
            stage,
            strategy_path,
            on_event=callback.on_agent_event,
            context=context,
            tree=tree_snapshot,
        )

        stage_dir = strategy_path / "stages" / stage_id
        agent_result.save_transcript(stage_dir / "transcript.json")

        def _save_dq_analysis(
            tree: ExplorationTree,
            a: ClaudeAnalysis = analysis,
        ) -> None:
            s = tree.stages[stage_id]
            s.analysis = a
            s.status = StageStatus.REVIEWING
            ap = strategy_path / (s.analysis_path or f"stages/{stage_id}/analysis.md")
            ap.write_text(render_analysis_markdown(s, a, tree))

        tree_mgr.update_atomic(_save_dq_analysis)

        # 5. Check if fixed
        new_critical = [dq for dq in analysis.data_quality_issues if dq.severity == "critical"]
        if not new_critical:
            return False  # fixed

        current_issues = new_critical

    return True  # exhausted retries, still has DQ


# ---------------------------------------------------------------------------
# run_parallel_batch — public entry point
# ---------------------------------------------------------------------------


async def run_parallel_batch(
    tree_mgr: TreeManager,
    suggestions: list[tuple[ExplorationStage, ProposedRefinement]],
    callback: LifecycleCallback | None = None,
    max_retries: int = 3,
    max_dq_retries: int = 2,
) -> list[PipelineResult]:
    """Run N pipelines concurrently via asyncio.gather.

    Each pipeline gets a unique index letter for stage ID generation.
    return_exceptions=True ensures one crash doesn't cancel others.
    """
    cb = callback or SilentCallback()

    # Assign index letters based on existing children count
    tree = tree_mgr.load()
    tasks: list[Any] = []

    for i, (parent, refinement) in enumerate(suggestions):
        existing_children = tree.get_children(parent.id)
        index = chr(ord("a") + len(existing_children) + i)

        tasks.append(
            _run_single_pipeline(
                tree_mgr=tree_mgr,
                parent_id=parent.id,
                refinement=refinement,
                index=index,
                callback=cb,
                max_retries=max_retries,
                max_dq_retries=max_dq_retries,
            )
        )

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[PipelineResult] = []
    for raw in raw_results:
        if isinstance(raw, PipelineResult):
            results.append(raw)
        elif isinstance(raw, BaseException):
            cb.on_error(f"[parallel] Pipeline exception: {raw}")
            results.append(PipelineResult(stage_id="unknown", success=False, error=str(raw)))
        else:
            results.append(
                PipelineResult(stage_id="unknown", success=False, error="Unexpected result")
            )

    return results
