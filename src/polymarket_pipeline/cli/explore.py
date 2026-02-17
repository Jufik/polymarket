"""CLI for strategy exploration.

Commands:
    init        Initialize a new strategy exploration
    run         Run a stage script
    review      Have Claude review a completed stage
    generate    Generate script for a refinement
    status      Show exploration tree status
    suggest     Get ranked suggestions for next steps
    orchestrate Run autonomous exploration loop

Usage:
    uv run pm-explore init skilled_traders --desc "..." --hypothesis "..."
    uv run pm-explore run skilled_traders 00_initial
    uv run pm-explore review skilled_traders 00_initial
    uv run pm-explore generate skilled_traders 00_initial high_volume_traders
    uv run pm-explore status skilled_traders
    uv run pm-explore suggest skilled_traders
    uv run pm-explore orchestrate skilled_traders --max-stages 3
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree as RichTree

from polymarket_pipeline.exploration.tree import (
    ExplorationTree,
    StageMetrics,
    StageStatus,
    create_refinement_stage,
    create_root_stage,
    render_analysis_markdown,
)

app = typer.Typer(
    name="pm-explore",
    help="Polymarket Strategy Exploration CLI",
)
console = Console()

STRATEGIES_ROOT = Path("strategies")


def _get_strategy_path(name: str) -> Path:
    return STRATEGIES_ROOT / name


def _load_tree(name: str) -> ExplorationTree:
    tree_path = _get_strategy_path(name) / "exploration_tree.json"
    if not tree_path.exists():
        console.print(f"[red]Strategy '{name}' not found[/red]")
        raise typer.Exit(1)
    return ExplorationTree.load(tree_path)


def _save_tree(name: str, tree: ExplorationTree) -> None:
    tree.save(_get_strategy_path(name) / "exploration_tree.json")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: str = typer.Argument(..., help="Strategy name (e.g., skilled_traders)"),
    description: str = typer.Option(..., "--desc", "-d", help="Strategy description"),
    hypothesis: str = typer.Option(..., "--hypothesis", "-H", help="Initial hypothesis"),
) -> None:
    """Initialize a new strategy exploration."""
    strategy_path = _get_strategy_path(name)

    if strategy_path.exists():
        console.print(f"[red]Strategy '{name}' already exists[/red]")
        raise typer.Exit(1)

    # Create directory structure
    strategy_path.mkdir(parents=True)
    (strategy_path / "stages").mkdir()

    # Create root stage
    root = create_root_stage(name, description, hypothesis)
    root.script_path = f"stages/{root.id}/stage.py"
    root.outputs_path = f"stages/{root.id}/outputs"
    root.analysis_path = f"stages/{root.id}/analysis.md"

    # Create tree
    tree = ExplorationTree(strategy_name=name, description=description)
    tree.add_stage(root)
    _save_tree(name, tree)

    # Create stage directory
    stage_dir = strategy_path / "stages" / root.id
    stage_dir.mkdir(parents=True)
    (stage_dir / "outputs").mkdir()

    # Copy template
    template_path = (
        Path(__file__).parent.parent / "exploration" / "templates" / "stage_template.py"
    )
    if template_path.exists():
        content = template_path.read_text()
        content = content.replace("{stage_id}", root.id)
        content = content.replace("{stage_name}", root.name)
        content = content.replace("{hypothesis}", hypothesis)
        (stage_dir / "stage.py").write_text(content)

    console.print(
        Panel(
            f"[green]Strategy '{name}' initialized![/green]\n\n"
            f"Location: {strategy_path}\n"
            f"Root script: {stage_dir / 'stage.py'}\n\n"
            f"Next steps:\n"
            f"1. Edit the script: [cyan]{stage_dir / 'stage.py'}[/cyan]\n"
            f"2. Run the stage: [cyan]pm-explore run {name} {root.id}[/cyan]\n"
            f"3. Review with Claude: [cyan]pm-explore review {name} {root.id}[/cyan]",
            title="Strategy Initialized",
        )
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command()
def run(
    strategy: str = typer.Argument(..., help="Strategy name"),
    stage_id: str = typer.Argument(..., help="Stage ID to run"),
) -> None:
    """Run a stage script."""
    tree = _load_tree(strategy)
    stage = tree.get_stage(stage_id)

    if not stage:
        console.print(f"[red]Stage '{stage_id}' not found[/red]")
        raise typer.Exit(1)

    strategy_path = _get_strategy_path(strategy)
    script_path = strategy_path / (stage.script_path or f"stages/{stage_id}/stage.py")

    if not script_path.exists():
        console.print(f"[red]Script not found: {script_path}[/red]")
        raise typer.Exit(1)

    outputs_dir = strategy_path / (stage.outputs_path or f"stages/{stage_id}/outputs")
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Update status
    stage.status = StageStatus.RUNNING
    stage.started_at = datetime.utcnow()
    _save_tree(strategy, tree)

    console.print(f"[cyan]Running stage: {stage.name}[/cyan]")

    try:
        # Import and run the stage script
        spec = importlib.util.spec_from_file_location(f"stage_{stage_id}", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "run"):
            console.print("[red]Stage script missing run() function[/red]")
            stage.status = StageStatus.FAILED
            _save_tree(strategy, tree)
            raise typer.Exit(1)

        outputs_summary = module.run(strategy_path, outputs_dir)

        # Save summary
        with open(outputs_dir / "summary.json", "w") as f:
            json.dump(outputs_summary, f, indent=2, default=str)

        # Extract metrics if returned
        if "metrics" in outputs_summary:
            metrics_data = outputs_summary["metrics"]
            if isinstance(metrics_data, dict):
                stage.metrics = StageMetrics(**metrics_data)
                with open(outputs_dir / "metrics.json", "w") as f:
                    json.dump(metrics_data, f, indent=2)

        stage.status = StageStatus.COMPLETED
        stage.completed_at = datetime.utcnow()
        console.print(f"[green]Stage completed: {stage.name}[/green]")

        # Log to MLflow
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
            console.print(f"[dim]MLflow run: {run_id}[/dim]")
        except Exception as e:
            console.print(f"[yellow]MLflow logging skipped: {e}[/yellow]")

    except typer.Exit:
        raise
    except Exception as e:
        stage.status = StageStatus.FAILED
        console.print(f"[red]Stage failed: {e}[/red]")

    _save_tree(strategy, tree)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


@app.command()
def review(
    strategy: str = typer.Argument(..., help="Strategy name"),
    stage_id: str = typer.Argument(..., help="Stage ID to review"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream transcript to console"),
) -> None:
    """Have Claude review a completed stage and generate analysis."""
    tree = _load_tree(strategy)
    stage = tree.get_stage(stage_id)

    if not stage:
        console.print(f"[red]Stage '{stage_id}' not found[/red]")
        raise typer.Exit(1)

    if stage.status != StageStatus.COMPLETED:
        console.print(
            f"[yellow]Warning: Stage status is '{stage.status.value}', not 'completed'[/yellow]"
        )

    strategy_path = _get_strategy_path(strategy)

    console.print("[cyan]Sending to Claude for review...[/cyan]")

    from polymarket_pipeline.exploration.agent import review_stage

    analysis, agent_result = asyncio.run(review_stage(stage, strategy_path))

    # Save full transcript
    stage_dir = strategy_path / "stages" / stage_id
    transcript_path = stage_dir / "transcript.json"
    agent_result.save_transcript(transcript_path)
    console.print(f"[dim]Transcript saved: {transcript_path} ({len(agent_result.transcript)} entries)[/dim]")

    # Print transcript summary if verbose
    if verbose:
        _print_transcript(agent_result.transcript)

    # Save analysis
    stage.analysis = analysis
    stage.status = StageStatus.REVIEWING

    analysis_path = strategy_path / (stage.analysis_path or f"stages/{stage_id}/analysis.md")
    analysis_md = render_analysis_markdown(stage, analysis, tree)
    analysis_path.write_text(analysis_md)

    # Log analysis to MLflow
    try:
        from polymarket_pipeline.exploration.tracking import ExplorationTracker

        tracker = ExplorationTracker()
        tracker.log_analysis(strategy, stage, analysis, analysis_path)
    except Exception as e:
        console.print(f"[yellow]MLflow analysis logging skipped: {e}[/yellow]")

    _save_tree(strategy, tree)

    # Display results
    console.print(
        Panel(
            f"[green]Analysis complete![/green]\n\n"
            f"Summary: {analysis.summary}\n\n"
            f"Confidence: {analysis.confidence:.0%}\n\n"
            f"Proposed Refinements: {len(analysis.proposed_refinements)}\n\n"
            f"Full analysis: {analysis_path}",
            title=f"Review: {stage.name}",
        )
    )

    table = Table(title="Proposed Refinements")
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Priority")
    table.add_column("Description")

    for ref in analysis.proposed_refinements:
        desc = ref.description
        if len(desc) > 60:
            desc = desc[:57] + "..."
        table.add_row(ref.name, ref.refinement_type.value, str(ref.priority), desc)

    console.print(table)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@app.command()
def generate(
    strategy: str = typer.Argument(..., help="Strategy name"),
    parent_id: str = typer.Argument(..., help="Parent stage ID"),
    refinement_name: str = typer.Argument(..., help="Refinement name to generate"),
) -> None:
    """Generate a new stage script from a proposed refinement."""
    tree = _load_tree(strategy)
    parent = tree.get_stage(parent_id)

    if not parent:
        console.print(f"[red]Parent stage '{parent_id}' not found[/red]")
        raise typer.Exit(1)

    if not parent.analysis:
        console.print("[red]Parent stage has no analysis. Run 'review' first.[/red]")
        raise typer.Exit(1)

    # Find refinement
    refinement = None
    for ref in parent.analysis.proposed_refinements:
        if ref.name.lower() == refinement_name.lower():
            refinement = ref
            break

    if not refinement:
        console.print(f"[red]Refinement '{refinement_name}' not found[/red]")
        console.print("Available refinements:")
        for ref in parent.analysis.proposed_refinements:
            console.print(f"  - {ref.name}")
        raise typer.Exit(1)

    # Create new stage
    existing_children = tree.get_children(parent_id)
    index = chr(ord("a") + len(existing_children))

    new_stage = create_refinement_stage(parent, refinement, index)
    new_stage.script_path = f"stages/{new_stage.id}/stage.py"
    new_stage.outputs_path = f"stages/{new_stage.id}/outputs"
    new_stage.analysis_path = f"stages/{new_stage.id}/analysis.md"

    tree.add_stage(new_stage)

    # Create directories
    strategy_path = _get_strategy_path(strategy)
    stage_dir = strategy_path / "stages" / new_stage.id
    stage_dir.mkdir(parents=True)
    (stage_dir / "outputs").mkdir()

    console.print("[cyan]Generating script with Claude...[/cyan]")

    from polymarket_pipeline.exploration.agent import generate_stage_script

    asyncio.run(
        generate_stage_script(
            stage=new_stage,
            parent=parent,
            refinement=refinement,
            strategy_root=strategy_path,
            output_path=stage_dir / "stage.py",
        )
    )

    _save_tree(strategy, tree)

    console.print(
        Panel(
            f"[green]Stage generated![/green]\n\n"
            f"Script: {stage_dir / 'stage.py'}\n"
            f"Hypothesis: {refinement.hypothesis}\n\n"
            f"Next steps:\n"
            f"1. Review the script: [cyan]{stage_dir / 'stage.py'}[/cyan]\n"
            f"2. Run the stage: [cyan]pm-explore run {strategy} {new_stage.id}[/cyan]",
            title=f"Generated: {new_stage.name}",
        )
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    strategy: str = typer.Argument(..., help="Strategy name"),
    mermaid: bool = typer.Option(False, "--mermaid", "-m", help="Output Mermaid diagram"),
) -> None:
    """Show the exploration tree status."""
    tree = _load_tree(strategy)

    if mermaid:
        console.print(tree.to_mermaid())
        return

    console.print(f"\n[bold]{tree.strategy_name}[/bold] - {tree.description}\n")

    def _build_tree(stage_id: str, rich_tree: RichTree) -> None:
        stage = tree.get_stage(stage_id)
        if not stage:
            return

        colors = {
            "pending": "yellow",
            "running": "blue",
            "completed": "green",
            "failed": "red",
            "reviewing": "magenta",
            "archived": "dim",
            "paused": "bright_red",
        }
        color = colors.get(stage.status.value, "white")
        label = f"[{color}]{stage.id}[/{color}] - {stage.name} [{stage.status.value}]"

        if stage.metrics and stage.metrics.sample_size:
            label += f" (n={stage.metrics.sample_size:,})"

        branch = rich_tree.add(label) if stage_id != tree.root_id else rich_tree
        for child_id in stage.children_ids:
            _build_tree(child_id, branch)

    if tree.root_id:
        root = tree.get_stage(tree.root_id)
        rich_tree = RichTree(f"[green]{root.id}[/green] - {root.name}")
        for child_id in root.children_ids:
            _build_tree(child_id, rich_tree)
        console.print(rich_tree)

    console.print()
    table = Table(title="Stage Summary")
    table.add_column("Status", style="cyan")
    table.add_column("Count")

    status_counts = Counter(s.status.value for s in tree.stages.values())
    for s, count in sorted(status_counts.items()):
        table.add_row(s, str(count))

    console.print(table)


# ---------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------


@app.command()
def suggest(
    strategy: str = typer.Argument(..., help="Strategy name"),
    n: int = typer.Option(3, "--n", "-n", help="Number of suggestions"),
    show_filtered: bool = typer.Option(
        False, "--show-filtered", help="Show consumed/rejected/converged refinements"
    ),
) -> None:
    """Get ranked suggestions for what to explore next."""
    from polymarket_pipeline.exploration.agent import suggest_next_stages
    from polymarket_pipeline.exploration.tree import build_exploration_context

    tree = _load_tree(strategy)
    context = build_exploration_context(tree)
    suggestions, filtered = suggest_next_stages(tree, max_suggestions=n, context=context)

    if not suggestions:
        console.print("[yellow]No suggestions. Complete and review more stages first.[/yellow]")
        if filtered and show_filtered:
            _print_filtered(filtered)
        return

    console.print(
        Panel(
            "[bold]Suggested Next Steps[/bold]\n\n"
            "Ranked by priority, complexity, and exploration context:",
            title="Suggestions",
        )
    )

    table = Table()
    table.add_column("#", style="cyan")
    table.add_column("Parent")
    table.add_column("Refinement")
    table.add_column("Priority")
    table.add_column("Command")

    for i, (parent, ref) in enumerate(suggestions, 1):
        table.add_row(
            str(i),
            parent.id,
            ref.name,
            str(ref.priority),
            f"pm-explore generate {strategy} {parent.id} {ref.name}",
        )

    console.print(table)

    if show_filtered and filtered:
        _print_filtered(filtered)


def _print_filtered(filtered: list) -> None:
    """Print table of filtered refinements."""
    table = Table(title="Filtered Refinements")
    table.add_column("Parent", style="dim")
    table.add_column("Refinement", style="dim")
    table.add_column("Reason", style="yellow")

    for f in filtered:
        table.add_row(f.parent_id, f.refinement_name, f.reason)

    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_transcript(transcript: list[dict]) -> None:
    """Print a readable summary of the agent transcript."""
    console.print("\n[bold]--- Agent Transcript ---[/bold]\n")
    for i, entry in enumerate(transcript):
        role = entry.get("role", "?")
        etype = entry.get("type", "?")

        if role == "assistant" and etype == "text":
            text = entry["text"]
            if len(text) > 300:
                text = text[:300] + "..."
            console.print(f"[cyan]Claude:[/cyan] {text}\n")

        elif role == "assistant" and etype == "thinking":
            text = entry["text"]
            if len(text) > 200:
                text = text[:200] + "..."
            console.print(f"[dim]Thinking: {text}[/dim]\n")

        elif role == "assistant" and etype == "tool_use":
            tool_name = entry["tool"]
            tool_input = entry.get("input", {})
            input_preview = json.dumps(tool_input, default=str)
            if len(input_preview) > 200:
                input_preview = input_preview[:200] + "..."
            console.print(f"[yellow]Tool call:[/yellow] {tool_name}({input_preview})\n")

        elif role == "tool" and etype == "tool_result":
            content = entry.get("content", "")
            is_error = entry.get("is_error", False)
            if isinstance(content, str):
                preview = content[:200] + "..." if len(content) > 200 else content
            elif isinstance(content, list):
                # MCP-style content blocks
                texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                preview = "\n".join(texts)[:200]
            else:
                preview = str(content)[:200]
            style = "red" if is_error else "green"
            console.print(f"[{style}]Result:[/{style}] {preview}\n")

    console.print("[bold]--- End Transcript ---[/bold]\n")


# ---------------------------------------------------------------------------
# orchestrate
# ---------------------------------------------------------------------------


@app.command()
def orchestrate(
    strategy: str = typer.Argument(..., help="Strategy name"),
    max_depth: int = typer.Option(3, "--max-depth", help="Maximum tree depth"),
    max_stages: int = typer.Option(10, "--max-stages", help="Maximum stages to run"),
    resume: bool = typer.Option(False, "--resume", help="Resume from paused state"),
    max_dq_retries: int = typer.Option(
        2, "--max-dq-retries", help="Max DQ self-heal attempts before pausing (0 to disable)"
    ),
    parallel: int = typer.Option(
        1, "--parallel", "-p", help="Concurrent pipelines (1 = serial)"
    ),
    max_children: int = typer.Option(
        15, "--max-children", help="Max completed children per parent before convergence"
    ),
) -> None:
    """Run autonomous exploration loop.

    Iteratively generates, runs, and reviews stages until a stop condition
    is met (max stages, max depth, no suggestions) or a critical data quality
    issue is detected, which causes a pause.

    Exit code 2 indicates the orchestrator paused due to data quality issues.
    """
    from polymarket_pipeline.exploration.lifecycle import load_orchestrator_state
    from polymarket_pipeline.exploration.orchestrator import (
        RichOrchestratorCallback,
        StrategyOrchestrator,
    )

    # Check for paused state when not resuming
    if not resume:
        existing_state = load_orchestrator_state(strategy)
        if existing_state and existing_state.paused:
            console.print(
                f"[yellow]Strategy '{strategy}' is paused: "
                f"{existing_state.pause_reason}[/yellow]\n"
                f"Use --resume to continue, or the state will be reset."
            )

    callback = RichOrchestratorCallback(console)
    orchestrator = StrategyOrchestrator(
        strategy=strategy,
        max_depth=max_depth,
        max_stages=max_stages,
        callback=callback,
        resume=resume,
        max_dq_retries=max_dq_retries,
        parallel=parallel,
        max_children=max_children,
    )

    mode = f"parallel ({parallel} pipelines)" if parallel > 1 else "serial"
    console.print(
        Panel(
            f"[bold]Autonomous Exploration[/bold]\n\n"
            f"Strategy: {strategy}\n"
            f"Max depth: {max_depth}\n"
            f"Max stages: {max_stages}\n"
            f"Max DQ retries: {max_dq_retries}\n"
            f"Max children/parent: {max_children}\n"
            f"Mode: {mode}\n"
            f"Resume: {resume}",
            title="Orchestrator Starting",
        )
    )

    state = asyncio.run(orchestrator.run())

    if state.paused:
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
