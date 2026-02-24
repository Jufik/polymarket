"""Human interaction — CLI-based escalation.

When the brainstorm agent detects something significant, pause with
a concise CLI brief. Human approves/redirects/stops.
"""

from __future__ import annotations

from typing import Literal

from rich.console import Console
from rich.panel import Panel

from polymarket_pipeline.exploration.brainstorm.models import (
    BrainstormResult,
    StrategyBrief,
)
from polymarket_pipeline.exploration.tree import ExplorationTree


class HumanDecision:
    """Result of a human interaction prompt."""

    def __init__(
        self,
        action: Literal["continue", "redirect", "stop"],
        custom_input: str | None = None,
        selected_direction: str | None = None,
    ) -> None:
        self.action = action
        self.custom_input = custom_input
        self.selected_direction = selected_direction


class ResearchBriefRenderer:
    """Renders the research brief and escalation triggers for CLI display."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render_escalation(
        self,
        result: BrainstormResult,
        tree: ExplorationTree,
        brief: StrategyBrief,
    ) -> None:
        """Display the full escalation panel in the terminal."""
        # Find the trigger
        pause_triggers = [t for t in result.escalation_triggers if t.severity == "pause"]
        if not pause_triggers:
            return

        trigger = pause_triggers[0]

        # Header
        completed = sum(1 for s in tree.stages.values() if s.analysis is not None)
        hard_constraints = len(brief.get_hard_constraints())
        observations = len(brief.learnings) - hard_constraints

        header = (
            f"STAGES RUN: {completed} completed\n"
            f"LEARNINGS: {hard_constraints} hard constraints, {observations} observations"
        )

        # Key findings
        findings_lines: list[str] = []
        for learning in brief.learnings[:8]:
            tag = "hard" if learning.enforcement == "hard_constraint" else learning.enforcement
            findings_lines.append(f"  [{tag}] {learning.finding}")

        findings_text = "\n".join(findings_lines) if findings_lines else "  (none yet)"

        # Trigger details
        trigger_text = f"TRIGGER: {trigger.description}\n\nEVIDENCE: {', '.join(trigger.evidence)}"

        # Proposed actions
        actions_lines: list[str] = []
        for i, action in enumerate(trigger.proposed_actions):
            letter = chr(ord("a") + i)
            actions_lines.append(f"  [{letter}] {action}")

        actions_text = "\n".join(actions_lines) if actions_lines else "  (no proposals)"

        # Question
        question = trigger.question_for_human or "How should we proceed?"

        # Compose panel
        body = (
            f"{header}\n\n"
            f"KEY FINDINGS:\n{findings_text}\n\n"
            f"{trigger_text}\n\n"
            f"PROPOSED DIRECTIONS:\n{actions_text}\n\n"
            f"> {question}"
        )

        self.console.print(
            Panel(
                body,
                title=f"[bold red]EXPLORATION PAUSED: {trigger.trigger_type}[/bold red]",
                border_style="red",
                width=80,
            )
        )

    def render_notification(self, result: BrainstormResult) -> None:
        """Display a one-liner notification for non-blocking triggers."""
        for trigger in result.escalation_triggers:
            if trigger.severity == "notify":
                self.console.print(
                    f"[yellow]NOTICE:[/yellow] {trigger.description} ({trigger.trigger_type})"
                )


class HumanInteraction:
    """CLI-based human escalation handler."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self.renderer = ResearchBriefRenderer(self.console)

    async def prompt(
        self,
        result: BrainstormResult,
        tree: ExplorationTree,
        brief: StrategyBrief,
    ) -> HumanDecision:
        """Display the escalation and get human input.

        Returns HumanDecision with action and optional direction.
        """
        self.renderer.render_escalation(result, tree, brief)

        # Get the proposed actions for prompt
        pause_triggers = [t for t in result.escalation_triggers if t.severity == "pause"]
        if not pause_triggers:
            return HumanDecision(action="continue")

        trigger = pause_triggers[0]
        n_actions = len(trigger.proposed_actions)
        valid_letters = [chr(ord("a") + i) for i in range(n_actions)]
        valid_choices = valid_letters + ["custom", "stop"]

        prompt_text = f"Your choice ({'/'.join(valid_letters)}/custom/stop): "

        while True:
            try:
                choice = self.console.input(prompt_text).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return HumanDecision(action="stop")

            if choice == "stop":
                return HumanDecision(action="stop")
            elif choice == "custom":
                try:
                    custom = self.console.input("Enter custom direction: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return HumanDecision(action="stop")
                return HumanDecision(
                    action="redirect",
                    custom_input=custom,
                )
            elif choice in valid_letters:
                idx = ord(choice) - ord("a")
                return HumanDecision(
                    action="redirect",
                    selected_direction=trigger.proposed_actions[idx],
                )
            else:
                self.console.print(
                    f"[red]Invalid choice. Enter one of: {', '.join(valid_choices)}[/red]"
                )

    def notify(self, result: BrainstormResult) -> None:
        """Show non-blocking notifications."""
        self.renderer.render_notification(result)
