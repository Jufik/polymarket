"""Autonomous strategy exploration orchestrator.

Drives the init -> run -> review -> generate -> run loop end-to-end,
pausing only when the reviewer detects critical data quality issues.
"""

from __future__ import annotations

import json
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from polymarket_pipeline.exploration.agent import suggest_next_stages
from polymarket_pipeline.exploration.lifecycle import (
    GenerateResult,
    LifecycleCallback,
    ReviewResult,
    RunResult,
    fix_dq_and_retry,
    generate_stage_lifecycle,
    load_orchestrator_state,
    load_tree,
    review_stage_lifecycle,
    run_stage_with_retry,
    save_orchestrator_state,
    save_tree,
)
from polymarket_pipeline.exploration.tree import (
    ExplorationStage,
    ExplorationTree,
    OrchestratorState,
    ProposedRefinement,
    StageStatus,
)


# ---------------------------------------------------------------------------
# Rich console callback
# ---------------------------------------------------------------------------


class RichOrchestratorCallback:
    """Rich console output for orchestrator events."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def on_info(self, message: str) -> None:
        self.console.print(f"[dim]{message}[/dim]")

    def on_success(self, message: str) -> None:
        self.console.print(f"[green]{message}[/green]")

    def on_warning(self, message: str) -> None:
        self.console.print(f"[yellow]{message}[/yellow]")

    def on_error(self, message: str) -> None:
        self.console.print(f"[red]{message}[/red]")

    def on_stage_start(self, stage: ExplorationStage) -> None:
        self.console.print(
            Panel(
                f"[cyan]Running stage:[/cyan] {stage.id} ({stage.name})\n"
                f"Hypothesis: {stage.hypothesis or 'N/A'}",
                title="Stage Execution",
            )
        )

    def on_stage_complete(self, stage: ExplorationStage) -> None:
        metrics_str = ""
        if stage.metrics and stage.metrics.sample_size:
            metrics_str = f"\nSample size: {stage.metrics.sample_size:,}"
        self.console.print(
            f"[green]Stage completed:[/green] {stage.id}{metrics_str}"
        )

    def on_data_quality_alert(self, stage: ExplorationStage, issues: list) -> None:
        issues_text = "\n".join(
            f"  [red]CRITICAL:[/red] {dq.description}" for dq in issues
        )
        self.console.print(
            Panel(
                f"[red bold]Data quality issues detected in {stage.id}![/red bold]\n\n"
                f"{issues_text}\n\n"
                "[yellow]Orchestrator is PAUSING. Fix the pipeline issues above,\n"
                "then resume with: pm-explore orchestrate <strategy> --resume[/yellow]",
                title="Data Quality Alert",
                border_style="red",
            )
        )

    def on_retry(self, stage: ExplorationStage, attempt: int, error: str) -> None:
        truncated = error[:200] + "..." if len(error) > 200 else error
        self.console.print(
            f"[yellow]Retry {attempt}: fixing script after error...[/yellow]"
        )
        self.console.print(f"[dim]{truncated}[/dim]")

    def on_dq_retry(self, stage: ExplorationStage, attempt: int, issues: list) -> None:
        issues_text = "\n".join(
            f"  - {dq.description}" for dq in issues
        )
        self.console.print(
            Panel(
                f"[yellow bold]DQ fix attempt {attempt} for {stage.id}[/yellow bold]\n\n"
                f"Issues to fix:\n{issues_text}\n\n"
                "[dim]Regenerating script with DQ feedback...[/dim]",
                title="Data Quality Self-Heal",
                border_style="yellow",
            )
        )

    def on_agent_event(self, entry: dict) -> None:
        """Print a live-streamed agent transcript entry."""
        role = entry.get("role", "?")
        etype = entry.get("type", "?")

        if role == "assistant" and etype == "text":
            text = entry["text"]
            if len(text) > 300:
                text = text[:300] + "..."
            self.console.print(f"  [cyan]Claude:[/cyan] {text}")

        elif role == "assistant" and etype == "thinking":
            text = entry["text"]
            if len(text) > 200:
                text = text[:200] + "..."
            self.console.print(f"  [dim]Thinking: {text}[/dim]")

        elif role == "assistant" and etype == "tool_use":
            tool_name = entry.get("tool", "?")
            tool_input = entry.get("input", {})
            input_preview = json.dumps(tool_input, default=str)
            if len(input_preview) > 200:
                input_preview = input_preview[:200] + "..."
            self.console.print(f"  [yellow]Tool:[/yellow] {tool_name}({input_preview})")

        elif role == "tool" and etype == "tool_result":
            content = entry.get("content", "")
            is_error = entry.get("is_error", False)
            if isinstance(content, str):
                preview = content[:200] + "..." if len(content) > 200 else content
            elif isinstance(content, list):
                texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                preview = "\n".join(texts)[:200]
            else:
                preview = str(content)[:200]
            style = "red" if is_error else "green"
            self.console.print(f"  [{style}]Result:[/{style}] {preview}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class StrategyOrchestrator:
    """Autonomous exploration loop.

    Main loop:
      1. Check for unreviewed COMPLETED stages -> review them first
      2. suggest_next_stages(tree) -> pick highest priority refinement
      3. Check depth limit
      4. generate_stage_lifecycle() -> create child stage + script
      5. run_stage_with_retry() -> execute the script (auto-fix on failure)
      6. review_stage_lifecycle() -> Claude reviews results
      7. IF critical data_quality_issues -> PAUSE, save state, return
      8. stages_completed++, loop back to step 1

    Stop conditions: max_stages reached, max_depth reached, no suggestions left.
    """

    def __init__(
        self,
        strategy: str,
        max_depth: int = 3,
        max_stages: int = 10,
        callback: LifecycleCallback | None = None,
        resume: bool = False,
        max_dq_retries: int = 2,
    ) -> None:
        self.strategy = strategy
        self.max_depth = max_depth
        self.max_stages = max_stages
        self.callback = callback or RichOrchestratorCallback()
        self.resume = resume
        self.max_dq_retries = max_dq_retries

    async def run(self) -> OrchestratorState:
        """Execute the autonomous loop. Returns final state."""
        tree = load_tree(self.strategy)
        state = load_orchestrator_state(self.strategy) or OrchestratorState()

        # Handle resume
        if self.resume and state.paused:
            self.callback.on_info(
                f"Resuming from pause (was at stage: {state.paused_at_stage})"
            )
            # Transition paused stage back to REVIEWING
            if state.paused_at_stage:
                paused_stage = tree.get_stage(state.paused_at_stage)
                if paused_stage and paused_stage.status == StageStatus.PAUSED:
                    paused_stage.status = StageStatus.REVIEWING
                    save_tree(self.strategy, tree)
            state.paused = False
            state.pause_reason = None
            state.paused_at_stage = None
            save_orchestrator_state(self.strategy, state)
        elif state.paused:
            self.callback.on_warning(
                f"Strategy is paused: {state.pause_reason}\n"
                "Use --resume to continue."
            )
            return state

        # Main loop
        while state.stages_completed < self.max_stages:
            tree = load_tree(self.strategy)

            # Step 1: Review any unreviewed COMPLETED stages
            reviewed_something = await self._review_pending(tree, state)
            if reviewed_something and state.paused:
                return state

            # Step 2: Get suggestions
            suggestions = suggest_next_stages(tree, max_suggestions=1)
            if not suggestions:
                self.callback.on_info("No more refinement suggestions available.")
                break

            parent, refinement = suggestions[0]

            # Step 3: Check depth limit
            if parent.depth + 1 > self.max_depth:
                self.callback.on_info(
                    f"Max depth ({self.max_depth}) reached at {parent.id}."
                )
                break

            # Step 4: Generate
            self.callback.on_info(
                f"\n--- Iteration {state.stages_completed + 1}/{self.max_stages} ---"
            )
            gen_result = await generate_stage_lifecycle(
                strategy=self.strategy,
                tree=tree,
                parent=parent,
                refinement=refinement,
                callback=self.callback,
            )
            if not gen_result.success:
                self.callback.on_error(f"Generation failed, stopping: {gen_result.error}")
                break

            # Reload tree after generation modified it
            tree = load_tree(self.strategy)
            new_stage = tree.get_stage(gen_result.stage.id)
            if not new_stage:
                self.callback.on_error("Generated stage not found in tree")
                break

            # Step 5: Run (with self-healing retry)
            run_result = await run_stage_with_retry(
                strategy=self.strategy,
                tree=tree,
                stage=new_stage,
                parent=parent,
                refinement=refinement,
                callback=self.callback,
                max_retries=3,
            )
            if not run_result.success:
                self.callback.on_error(f"Run failed after retries, stopping: {run_result.error}")
                break

            # Reload tree after run modified it
            tree = load_tree(self.strategy)
            new_stage = tree.get_stage(gen_result.stage.id)
            if not new_stage:
                break

            # Step 6: Review
            review_result = await review_stage_lifecycle(
                strategy=self.strategy,
                tree=tree,
                stage=new_stage,
                callback=self.callback,
            )

            # Step 7: Check for critical DQ issues -> attempt self-healing
            if review_result.has_critical_dq_issues:
                if self.max_dq_retries > 0 and review_result.analysis:
                    critical_issues = [
                        dq for dq in review_result.analysis.data_quality_issues
                        if dq.severity == "critical"
                    ]
                    tree = load_tree(self.strategy)
                    new_stage = tree.get_stage(gen_result.stage.id) or new_stage
                    review_result = await fix_dq_and_retry(
                        strategy=self.strategy,
                        tree=tree,
                        stage=new_stage,
                        parent=parent,
                        refinement=refinement,
                        dq_issues=critical_issues,
                        callback=self.callback,
                        max_dq_retries=self.max_dq_retries,
                    )

                # Still has critical DQ after retries (or retries disabled) -> PAUSE
                if review_result.has_critical_dq_issues:
                    tree = load_tree(self.strategy)
                    paused_stage = tree.get_stage(new_stage.id)
                    if paused_stage:
                        paused_stage.status = StageStatus.PAUSED
                        save_tree(self.strategy, tree)

                    state.paused = True
                    state.pause_reason = "Critical data quality issues detected"
                    state.paused_at_stage = new_stage.id
                    save_orchestrator_state(self.strategy, state)
                    return state

            if not review_result.success:
                self.callback.on_error(f"Review failed, stopping: {review_result.error}")
                break

            # Step 8: Track progress
            state.stages_completed += 1
            state.completed_stage_ids.append(new_stage.id)
            save_orchestrator_state(self.strategy, state)

        self.callback.on_success(
            f"\nOrchestrator finished: {state.stages_completed} stages completed."
        )
        self._print_summary(state)
        return state

    async def _review_pending(
        self,
        tree: ExplorationTree,
        state: OrchestratorState,
    ) -> bool:
        """Review any COMPLETED stages that haven't been reviewed yet."""
        reviewed = False
        for stage in tree.stages.values():
            if stage.status == StageStatus.COMPLETED and stage.analysis is None:
                self.callback.on_info(f"Found unreviewed stage: {stage.id}")
                review_result = await review_stage_lifecycle(
                    strategy=self.strategy,
                    tree=tree,
                    stage=stage,
                    callback=self.callback,
                )
                reviewed = True

                if review_result.has_critical_dq_issues:
                    # Attempt DQ self-healing
                    if self.max_dq_retries > 0 and review_result.analysis:
                        critical_issues = [
                            dq for dq in review_result.analysis.data_quality_issues
                            if dq.severity == "critical"
                        ]
                        # Look up parent and refinement for regeneration
                        parent_stage = (
                            tree.get_stage(stage.parent_id) if stage.parent_id else None
                        )
                        stage_refinement = self._find_refinement(parent_stage, stage)
                        tree = load_tree(self.strategy)
                        stage = tree.get_stage(stage.id) or stage
                        review_result = await fix_dq_and_retry(
                            strategy=self.strategy,
                            tree=tree,
                            stage=stage,
                            parent=parent_stage,
                            refinement=stage_refinement,
                            dq_issues=critical_issues,
                            callback=self.callback,
                            max_dq_retries=self.max_dq_retries,
                        )

                    if review_result.has_critical_dq_issues:
                        tree = load_tree(self.strategy)
                        paused_stage = tree.get_stage(stage.id)
                        if paused_stage:
                            paused_stage.status = StageStatus.PAUSED
                            save_tree(self.strategy, tree)

                        state.paused = True
                        state.pause_reason = "Critical data quality issues detected"
                        state.paused_at_stage = stage.id
                        save_orchestrator_state(self.strategy, state)
                        return True
        return reviewed

    @staticmethod
    def _find_refinement(
        parent: ExplorationStage | None,
        stage: ExplorationStage,
    ) -> ProposedRefinement | None:
        """Find the refinement that produced *stage* from *parent*'s analysis."""
        if not parent or not parent.analysis:
            return None
        for ref in parent.analysis.proposed_refinements:
            if ref.name == stage.name:
                return ref
        return None

    def _print_summary(self, state: OrchestratorState) -> None:
        """Print a summary table of the orchestrator run."""
        if not isinstance(self.callback, RichOrchestratorCallback):
            return

        console = self.callback.console
        table = Table(title="Orchestrator Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value")
        table.add_row("Stages completed", str(state.stages_completed))
        table.add_row("Stage IDs", ", ".join(state.completed_stage_ids) or "none")
        table.add_row("Paused", str(state.paused))
        if state.pause_reason:
            table.add_row("Pause reason", state.pause_reason)
        console.print(table)
