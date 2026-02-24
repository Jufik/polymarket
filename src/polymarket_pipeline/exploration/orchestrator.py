"""Autonomous strategy exploration orchestrator.

Drives the init -> run -> review -> generate -> run loop end-to-end,
pausing only when the reviewer detects critical data quality issues.
"""

from __future__ import annotations

import asyncio
import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from polymarket_pipeline.exploration.agent import set_registry, suggest_next_stages
from polymarket_pipeline.exploration.brainstorm.agent import BrainstormAgent
from polymarket_pipeline.exploration.brainstorm.models import BrainstormResult
from polymarket_pipeline.exploration.human import HumanInteraction
from polymarket_pipeline.exploration.lifecycle import (
    LifecycleCallback,
    fix_dq_and_retry,
    generate_stage_lifecycle,
    load_orchestrator_state,
    load_tree,
    review_stage_lifecycle,
    run_stage_with_retry,
    save_orchestrator_state,
    save_tree,
)
from polymarket_pipeline.exploration.parallel import (
    TreeManager,
    run_parallel_batch,
)
from polymarket_pipeline.exploration.prompts.registry import PromptRegistry
from polymarket_pipeline.exploration.tree import (
    BranchRecommendation,
    ExplorationContext,
    ExplorationStage,
    ExplorationTree,
    OrchestratorState,
    ProposedRefinement,
    StageStatus,
    build_exploration_context,
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
        self.console.print(f"[green]Stage completed:[/green] {stage.id}{metrics_str}")

    def on_data_quality_alert(self, stage: ExplorationStage, issues: list) -> None:
        issues_text = "\n".join(f"  [red]CRITICAL:[/red] {dq.description}" for dq in issues)
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
        self.console.print(f"[yellow]Retry {attempt}: fixing script after error...[/yellow]")
        self.console.print(f"[dim]{truncated}[/dim]")

    def on_dq_retry(self, stage: ExplorationStage, attempt: int, issues: list) -> None:
        issues_text = "\n".join(f"  - {dq.description}" for dq in issues)
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
# Convergence detection
# ---------------------------------------------------------------------------

DEFAULT_MAX_CHILDREN_PER_PARENT = 15
MIN_INFO_GAIN_THRESHOLD = 0.2
CONVERGENCE_WINDOW = 3  # number of recent leaves to check


def check_convergence(
    tree: ExplorationTree,
    context: ExplorationContext,
    max_children_per_parent: int = DEFAULT_MAX_CHILDREN_PER_PARENT,
) -> tuple[bool, str]:
    """Check whether exploration should stop.

    Returns (should_stop, reason).
    """
    # 1. All branches rejected or converged
    if context.branch_statuses:
        active_branches = [
            sid
            for sid, rec in context.branch_statuses.items()
            if rec not in (BranchRecommendation.REJECT, BranchRecommendation.CONVERGE)
        ]
        if not active_branches:
            return True, "All branches are rejected or converged"

    # 2. Diminishing returns: last N leaf stages averaged < threshold info gain
    leaves = tree.get_leaves()
    analyzed_leaves = [s for s in leaves if s.analysis is not None]
    if len(analyzed_leaves) >= CONVERGENCE_WINDOW:
        recent = analyzed_leaves[-CONVERGENCE_WINDOW:]
        avg_gain = sum(s.analysis.information_gain for s in recent if s.analysis) / len(recent)
        if avg_gain < MIN_INFO_GAIN_THRESHOLD:
            return True, (
                f"Diminishing returns: last {CONVERGENCE_WINDOW} leaves averaged "
                f"{avg_gain:.0%} information gain (threshold: {MIN_INFO_GAIN_THRESHOLD:.0%})"
            )

    # 3. Runaway branching: any parent has >= max_children_per_parent completed children
    for stage in tree.stages.values():
        children = tree.get_children(stage.id)
        completed_children = [
            c for c in children if c.status in (StageStatus.COMPLETED, StageStatus.REVIEWING)
        ]
        if len(completed_children) >= max_children_per_parent:
            return True, (
                f"Runaway branching: {stage.id} has {len(completed_children)} "
                f"completed children (limit: {max_children_per_parent})"
            )

    return False, ""


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
        parallel: int = 1,
        max_children: int = DEFAULT_MAX_CHILDREN_PER_PARENT,
    ) -> None:
        self.strategy = strategy
        self.max_depth = max_depth
        self.max_stages = max_stages
        self.callback = callback or RichOrchestratorCallback()
        self.resume = resume
        self.max_dq_retries = max_dq_retries
        self.parallel = max(1, parallel)
        self.max_children = max_children

        # New: prompt registry, brainstorm agent, human interaction
        self.registry = PromptRegistry.load(strategy)
        set_registry(self.registry)
        self.brainstorm = BrainstormAgent(strategy)
        self.human = HumanInteraction()
        self._pending_brainstorm: asyncio.Task[BrainstormResult] | None = None

    async def run(self) -> OrchestratorState:
        """Execute the autonomous loop. Returns final state."""
        tree = load_tree(self.strategy)
        state = load_orchestrator_state(self.strategy) or OrchestratorState()

        # Handle resume
        if self.resume and state.paused:
            self.callback.on_info(f"Resuming from pause (was at stage: {state.paused_at_stage})")
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
                f"Strategy is paused: {state.pause_reason}\nUse --resume to continue."
            )
            return state

        # Bootstrap: if root stage is PENDING, run + review it first
        tree = load_tree(self.strategy)
        bootstrapped = await self._bootstrap_root(tree, state)
        if bootstrapped and state.paused:
            return state

        if self.parallel > 1:
            return await self._run_parallel(state)
        return await self._run_serial(state)

    # ------------------------------------------------------------------
    # Bootstrap: auto-run root stage if not yet analyzed
    # ------------------------------------------------------------------

    async def _bootstrap_root(
        self,
        tree: ExplorationTree,
        state: OrchestratorState,
    ) -> bool:
        """If the root stage has no analysis, run and review it automatically.

        Uses run_stage_with_retry for auto-healing SQL/execution errors,
        and fix_dq_and_retry for data quality issues.

        Returns True if bootstrap was performed (root was pending).
        """
        if not tree.root_id:
            return False

        root = tree.get_stage(tree.root_id)
        needs_bootstrap = root.status in (StageStatus.PENDING, StageStatus.FAILED) or (
            root.status == StageStatus.COMPLETED and root.analysis is None
        )
        if not root or not needs_bootstrap:
            return False

        # Reset to PENDING if previously failed
        if root.status == StageStatus.FAILED:
            root.status = StageStatus.PENDING
            root.last_error = None
            root.run_attempts = 0
            save_tree(self.strategy, tree)

        self.callback.on_info(f"Root stage '{root.id}' needs bootstrap — running with auto-fix...")

        # Step 1: Run with auto-healing (LLM rewrites script on failure)
        run_result = await run_stage_with_retry(
            strategy=self.strategy,
            tree=tree,
            stage=root,
            parent=None,
            refinement=None,
            callback=self.callback,
            max_retries=3,
        )
        if not run_result.success:
            self.callback.on_error(f"Root stage failed after retries: {run_result.error}")
            state.paused = True
            state.pause_reason = f"Root stage failed after retries: {run_result.error}"
            state.paused_at_stage = root.id
            save_orchestrator_state(self.strategy, state)
            return True

        # Step 2: Review (generates analysis with proposed refinements)
        tree = load_tree(self.strategy)
        root = tree.get_stage(tree.root_id) or root
        context = build_exploration_context(tree)

        review_result = await review_stage_lifecycle(
            strategy=self.strategy,
            tree=tree,
            stage=root,
            callback=self.callback,
            context=context,
        )

        # Step 3: Auto-fix DQ issues if any
        if review_result.has_critical_dq_issues:
            self.callback.on_warning("Root stage has critical DQ issues — attempting auto-fix...")
            critical_issues = [
                dq
                for dq in (
                    review_result.analysis.data_quality_issues if review_result.analysis else []
                )
                if dq.severity == "critical"
            ]
            tree = load_tree(self.strategy)
            root = tree.get_stage(tree.root_id) or root
            review_result = await fix_dq_and_retry(
                strategy=self.strategy,
                tree=tree,
                stage=root,
                parent=None,
                refinement=None,
                dq_issues=critical_issues,
                callback=self.callback,
                max_dq_retries=self.max_dq_retries,
            )
            if review_result.has_critical_dq_issues:
                state.paused = True
                state.pause_reason = "Critical DQ issues in root after auto-fix"
                state.paused_at_stage = root.id
                save_orchestrator_state(self.strategy, state)
                return True

        state.stages_completed += 1
        state.completed_stage_ids.append(root.id)
        save_orchestrator_state(self.strategy, state)

        self.callback.on_success(f"Root stage '{root.id}' bootstrapped successfully.")
        return True

    # ------------------------------------------------------------------
    # Serial loop (unchanged from original)
    # ------------------------------------------------------------------

    async def _run_serial(self, state: OrchestratorState) -> OrchestratorState:
        """Serial execution loop with brainstorm agent and human escalation."""
        while state.stages_completed < self.max_stages:
            tree = load_tree(self.strategy)

            # Check for completed brainstorm from previous iteration
            if self._pending_brainstorm is not None and self._pending_brainstorm.done():
                brainstorm_result = self._pending_brainstorm.result()
                self._pending_brainstorm = None
                self._apply_brainstorm(brainstorm_result)
                if brainstorm_result.has_pause_triggers():
                    tree = load_tree(self.strategy)
                    decision = await self.human.prompt(
                        brainstorm_result,
                        tree,
                        self.brainstorm.brief,
                    )
                    if decision.action == "stop":
                        self.callback.on_info("Human chose to stop exploration.")
                        break
                    elif decision.action == "redirect":
                        self.callback.on_info(
                            f"Human redirected: {decision.selected_direction or decision.custom_input}"
                        )
                elif brainstorm_result.has_notify_triggers():
                    self.human.notify(brainstorm_result)

            # Build exploration context for this iteration
            context = build_exploration_context(tree)

            # Step 1: Review any unreviewed COMPLETED stages
            reviewed_something = await self._review_pending(tree, state, context)
            if reviewed_something and state.paused:
                return state

            # Rebuild context after reviews may have added analysis
            tree = load_tree(self.strategy)
            context = build_exploration_context(tree)

            # Check convergence before picking next refinement
            should_stop, reason = check_convergence(
                tree,
                context,
                self.max_children,
            )
            if should_stop:
                self.callback.on_info(f"Convergence detected: {reason}")
                break

            # Step 2: Get suggestions (with context-aware filtering)
            suggestions, filtered = suggest_next_stages(
                tree,
                max_suggestions=1,
                context=context,
            )
            if filtered:
                self.callback.on_info(
                    f"Filtered {len(filtered)} refinements: "
                    + ", ".join(f"{f.refinement_name} ({f.reason})" for f in filtered[:5])
                )
            if not suggestions:
                self.callback.on_info("No more refinement suggestions available.")
                break

            parent, refinement = suggestions[0]

            # Step 3: Check depth limit
            if parent.depth + 1 > self.max_depth:
                self.callback.on_info(f"Max depth ({self.max_depth}) reached at {parent.id}.")
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

            # Step 6: Review (with exploration context)
            tree = load_tree(self.strategy)
            context = build_exploration_context(tree)
            new_stage = tree.get_stage(gen_result.stage.id)
            if not new_stage:
                break
            review_result = await review_stage_lifecycle(
                strategy=self.strategy,
                tree=tree,
                stage=new_stage,
                callback=self.callback,
                context=context,
            )

            # Step 6b: Launch brainstorm agent async (non-blocking)
            if review_result.success and new_stage.analysis:
                tree = load_tree(self.strategy)
                self._pending_brainstorm = asyncio.create_task(
                    self.brainstorm.analyze_completed_stage(new_stage, tree)
                )

            # Step 7: Check for critical DQ issues -> attempt self-healing
            if review_result.has_critical_dq_issues:
                if self.max_dq_retries > 0 and review_result.analysis:
                    critical_issues = [
                        dq
                        for dq in review_result.analysis.data_quality_issues
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

    # ------------------------------------------------------------------
    # Parallel loop
    # ------------------------------------------------------------------

    async def _run_parallel(self, state: OrchestratorState) -> OrchestratorState:
        """Parallel execution loop using asyncio.gather."""
        tree_mgr = TreeManager(self.strategy)

        while state.stages_completed < self.max_stages:
            tree = tree_mgr.load()
            context = build_exploration_context(tree)

            # Review any pending stages (serial — reuses existing method)
            reviewed_something = await self._review_pending(tree, state, context)
            if reviewed_something and state.paused:
                return state

            # Rebuild context after reviews
            tree = tree_mgr.load()
            context = build_exploration_context(tree)

            # Check convergence
            should_stop, reason = check_convergence(
                tree,
                context,
                self.max_children,
            )
            if should_stop:
                self.callback.on_info(f"Convergence detected: {reason}")
                break

            # Determine batch size
            remaining = self.max_stages - state.stages_completed
            batch_size = min(self.parallel, remaining)

            # Get N suggestions
            suggestions, filtered = suggest_next_stages(
                tree,
                max_suggestions=batch_size,
                context=context,
            )
            if filtered:
                self.callback.on_info(
                    f"Filtered {len(filtered)} refinements: "
                    + ", ".join(f"{f.refinement_name} ({f.reason})" for f in filtered[:5])
                )
            if not suggestions:
                self.callback.on_info("No more refinement suggestions available.")
                break

            # Filter by depth limit
            valid_suggestions = [
                (parent, ref) for parent, ref in suggestions if parent.depth + 1 <= self.max_depth
            ]
            if not valid_suggestions:
                self.callback.on_info(f"Max depth ({self.max_depth}) reached for all suggestions.")
                break

            self.callback.on_info(
                f"\n--- Parallel batch: {len(valid_suggestions)} pipelines "
                f"(stages {state.stages_completed + 1}-"
                f"{state.stages_completed + len(valid_suggestions)}"
                f"/{self.max_stages}) ---"
            )

            # Run batch
            results = await run_parallel_batch(
                tree_mgr=tree_mgr,
                suggestions=valid_suggestions,
                callback=self.callback,
                max_retries=3,
                max_dq_retries=self.max_dq_retries,
            )

            # Process results
            any_paused = False
            for result in results:
                if result.success and not result.has_critical_dq:
                    state.stages_completed += 1
                    state.completed_stage_ids.append(result.stage_id)
                elif result.has_critical_dq:
                    self.callback.on_warning(f"Stage {result.stage_id} has critical DQ issues.")

                    # Pause the stage
                    def _pause_stage(
                        tree: ExplorationTree,
                        sid: str = result.stage_id,
                    ) -> None:
                        s = tree.get_stage(sid)
                        if s:
                            s.status = StageStatus.PAUSED

                    tree_mgr.update_atomic(_pause_stage)
                    any_paused = True
                elif not result.success:
                    self.callback.on_error(f"Pipeline failed: {result.stage_id} — {result.error}")

            save_orchestrator_state(self.strategy, state)

            if any_paused:
                state.paused = True
                state.pause_reason = "Critical data quality issues in parallel batch"
                save_orchestrator_state(self.strategy, state)
                return state

        self.callback.on_success(
            f"\nOrchestrator finished: {state.stages_completed} stages completed."
        )
        self._print_summary(state)
        return state

    async def _review_pending(
        self,
        tree: ExplorationTree,
        state: OrchestratorState,
        context: ExplorationContext | None = None,
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
                    context=context,
                )
                reviewed = True

                if review_result.has_critical_dq_issues:
                    # Attempt DQ self-healing
                    if self.max_dq_retries > 0 and review_result.analysis:
                        critical_issues = [
                            dq
                            for dq in review_result.analysis.data_quality_issues
                            if dq.severity == "critical"
                        ]
                        # Look up parent and refinement for regeneration
                        parent_stage = tree.get_stage(stage.parent_id) if stage.parent_id else None
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

    def _apply_brainstorm(self, result: BrainstormResult) -> None:
        """Apply brainstorm results: evolve prompts, log learnings."""
        if result.platform_knowledge:
            self.registry.evolve_platform([pk.model_dump() for pk in result.platform_knowledge])
            self.callback.on_info(
                f"Brainstorm: added {len(result.platform_knowledge)} platform knowledge entries"
            )

        if result.new_learnings:
            self.registry.evolve_strategy([entry.model_dump() for entry in result.new_learnings])
            hard = [
                entry for entry in result.new_learnings if entry.enforcement == "hard_constraint"
            ]
            if hard:
                self.callback.on_info(f"Brainstorm: {len(hard)} new hard constraints")

        # Persist updated prompts
        self.registry.save(self.strategy)
        set_registry(self.registry)

        if result.brief_update:
            self.callback.on_info(f"Brainstorm: {result.brief_update}")

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
