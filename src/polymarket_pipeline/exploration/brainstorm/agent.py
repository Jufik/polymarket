"""Brainstorm agent — async research advisor.

Runs post-review, doesn't block exploration. Analyzes completed stages,
consolidates learnings, evolves prompts, and decides when to escalate.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from polymarket_pipeline.exploration.brainstorm.models import (
    BrainstormResult,
    EscalationTrigger,
    PlatformKnowledge,
    StrategyBrief,
    StrategyLearning,
)
from polymarket_pipeline.exploration.tree import (
    ExplorationStage,
    ExplorationTree,
)

STRATEGIES_ROOT = Path("strategies")


class BrainstormAgent:
    """Async research advisor. Runs post-review, doesn't block exploration.

    Analyzes completed stage results, detects patterns across the tree,
    and produces structured updates to the research brief and prompts.
    """

    def __init__(self, strategy: str) -> None:
        self.strategy = strategy
        self.brief = self._load_brief()

    def _load_brief(self) -> StrategyBrief:
        path = STRATEGIES_ROOT / self.strategy / "research_brief.json"
        if path.exists():
            return StrategyBrief.load(path)
        return StrategyBrief(strategy=self.strategy)

    def _save_brief(self) -> None:
        path = STRATEGIES_ROOT / self.strategy / "research_brief.json"
        self.brief.save(path)

    async def analyze_completed_stage(
        self,
        stage: ExplorationStage,
        tree: ExplorationTree,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> BrainstormResult:
        """Analyze a completed stage and produce structured output.

        1. Read stage analysis + outputs
        2. Compare with existing learnings — contradictions? confirmations?
        3. Identify new learnings (auto-promote if confidence >= 80%)
        4. Detect escalation triggers
        5. Update research_brief.json
        6. Return triggers + prompt evolution updates
        """
        from polymarket_pipeline.exploration.agent import (
            _build_options,
            _run_agent,
        )

        strategy_path = STRATEGIES_ROOT / self.strategy

        # Build prompt with full context
        prompt_parts = [
            f"Analyze completed stage '{stage.id}' ({stage.name}).",
            f"Hypothesis: {stage.hypothesis or 'N/A'}",
        ]

        if stage.analysis:
            prompt_parts.append(f"\nStage analysis summary: {stage.analysis.summary}")
            prompt_parts.append(f"Confidence: {stage.analysis.confidence:.0%}")
            prompt_parts.append(
                f"Branch recommendation: {stage.analysis.branch_recommendation.value}"
            )
            if stage.analysis.key_insights:
                prompt_parts.append(
                    "Key insights:\n" + "\n".join(f"  - {i}" for i in stage.analysis.key_insights)
                )
            if stage.analysis.concerns:
                prompt_parts.append(
                    "Concerns:\n" + "\n".join(f"  - {c}" for c in stage.analysis.concerns)
                )

        if stage.outputs_path:
            prompt_parts.append(f"\nStage outputs at: {strategy_path / stage.outputs_path}")

        # Include existing brief context
        if self.brief.learnings:
            prompt_parts.append(
                "\nExisting learnings:\n"
                + "\n".join(
                    f"  [{ln.enforcement}] {ln.finding} (confidence={ln.confidence:.0%})"
                    for ln in self.brief.learnings
                )
            )

        # Include tree context (siblings, path)
        path_to_root = tree.get_path_to_root(stage.id)
        if path_to_root:
            prompt_parts.append("\nExploration path: " + " -> ".join(s.name for s in path_to_root))

        # Count completed stages
        completed = [s for s in tree.stages.values() if s.analysis is not None]
        prompt_parts.append(f"\nTotal completed stages: {len(completed)}")

        prompt = "\n".join(prompt_parts)

        options = _build_options(
            role="brainstorm",
            tool_names=["query_clickhouse", "get_schema", "read_file"],
            max_turns=20,
            strategy=self.strategy,
        )

        agent_result = await _run_agent(prompt, options, on_event=on_event)
        result = self._parse_brainstorm_result(agent_result.last_text)

        # Update brief with results
        self.brief.merge_result(result)
        self._save_brief()

        return result

    def _parse_brainstorm_result(self, response_text: str) -> BrainstormResult:
        """Parse Claude's JSON response into a BrainstormResult."""
        text = response_text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            parts = text.split("```")
            for part in reversed(parts):
                stripped = part.strip()
                if stripped.startswith("{"):
                    text = stripped
                    break

        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return BrainstormResult(
                brief_update="Failed to parse brainstorm response",
            )

        try:
            data = json.loads(text[start:end])
        except json.JSONDecodeError:
            return BrainstormResult(
                brief_update="Failed to parse brainstorm JSON",
            )

        learnings = [StrategyLearning(**item) for item in data.get("new_learnings", [])]

        platform = [PlatformKnowledge(**pk) for pk in data.get("platform_knowledge", [])]

        triggers = [EscalationTrigger(**t) for t in data.get("escalation_triggers", [])]

        return BrainstormResult(
            new_learnings=learnings,
            platform_knowledge=platform,
            escalation_triggers=triggers,
            open_questions=data.get("open_questions", []),
            brief_update=data.get("brief_update", ""),
        )
