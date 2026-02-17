"""Brainstorm agent data models.

Structured outputs from the brainstorm agent: learnings, platform knowledge,
escalation triggers, and the living research brief.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class StrategyLearning(BaseModel):
    """A finding from exploration that informs future stages."""

    id: str  # snake_case unique key
    finding: str  # What was learned
    evidence_stage_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5  # 0-1
    enforcement: Literal["hard_constraint", "soft_hint", "observation"] = "observation"


class PlatformKnowledge(BaseModel):
    """A technical insight about the data platform (ClickHouse, schema, etc)."""

    category: Literal["sql_pitfall", "schema_quirk", "performance", "data_quality"]
    description: str
    correct_pattern: str | None = None
    wrong_pattern: str | None = None
    discovered_in_stage: str = ""


class EscalationTrigger(BaseModel):
    """A condition that warrants human attention."""

    trigger_type: Literal[
        "pivot_needed",  # Fundamental approach failing
        "contradiction_found",  # Two stages disagree
        "convergence_reached",  # No more info gain
        "novel_discovery",  # Unexpected finding needs human judgment
    ]
    severity: Literal["pause", "notify"]
    description: str
    evidence: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)
    question_for_human: str = ""


class BrainstormResult(BaseModel):
    """Output from a single brainstorm agent run."""

    new_learnings: list[StrategyLearning] = Field(default_factory=list)
    platform_knowledge: list[PlatformKnowledge] = Field(default_factory=list)
    escalation_triggers: list[EscalationTrigger] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    brief_update: str = ""

    def has_pause_triggers(self) -> bool:
        """Check if any triggers require pausing."""
        return any(t.severity == "pause" for t in self.escalation_triggers)

    def has_notify_triggers(self) -> bool:
        """Check if any triggers are notification-only."""
        return any(t.severity == "notify" for t in self.escalation_triggers)


class StrategyBrief(BaseModel):
    """Living document maintained by the brainstorm agent.

    Persisted to strategies/{name}/research_brief.json.
    """

    strategy: str
    learnings: list[StrategyLearning] = Field(default_factory=list)
    platform_knowledge: list[PlatformKnowledge] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def merge_result(self, result: BrainstormResult) -> None:
        """Merge a brainstorm result into the brief."""
        existing_ids = {ln.id for ln in self.learnings}

        for learning in result.new_learnings:
            if learning.id in existing_ids:
                # Update existing
                for i, existing in enumerate(self.learnings):
                    if existing.id == learning.id:
                        self.learnings[i] = learning
                        break
            else:
                self.learnings.append(learning)

        # Append new platform knowledge (dedup by description)
        existing_descs = {pk.description for pk in self.platform_knowledge}
        for pk in result.platform_knowledge:
            if pk.description not in existing_descs:
                self.platform_knowledge.append(pk)

        # Merge open questions (dedup)
        existing_q = set(self.open_questions)
        for q in result.open_questions:
            if q not in existing_q:
                self.open_questions.append(q)

        self.updated_at = datetime.utcnow()

    def get_hard_constraints(self) -> list[StrategyLearning]:
        """Return learnings enforced as hard constraints."""
        return [ln for ln in self.learnings if ln.enforcement == "hard_constraint"]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> StrategyBrief:
        return cls.model_validate_json(path.read_text())
