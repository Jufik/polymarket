"""Prompt registry — load, compose, persist, evolve.

Manages the three-layer prompt system:
- Platform layer: shared across strategies (DB pitfalls, schema quirks)
- Strategy layer: per-strategy hard constraints from learnings
- Role layer: per agent role (reviewer, generator, explorer, brainstorm)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polymarket_pipeline.exploration.prompts.layers import PromptLayer, PromptStack

STRATEGIES_ROOT = Path("strategies")
SHARED_DIR = STRATEGIES_ROOT / "_shared"

# ---------------------------------------------------------------------------
# Default role prompts (extracted from agent.py static strings)
# ---------------------------------------------------------------------------

_BASE_PLATFORM = """\
You are an expert quantitative researcher \
specializing in Polymarket prediction markets.

Key facts about the data:
- ClickHouse database 'polymarket' with 246M+ trades in trades_raw (ReplacingMergeTree)
- Use FINAL keyword when querying trades_raw for dedup correctness
- PostgreSQL engine tables in ClickHouse: events, markets, tags, event_tags, token_market_map
- Amounts are in USDC (Float32). Prices are 0-1 (Float32).
- maker/taker are wallet addresses (Nullable String)
- condition_id links trades to markets. asset_id links to token_market_map.
- trades span 2022-11-21 to 2026-01-26

Principles:
- Statistical rigor: consider sample sizes, significance, multiple comparisons
- Avoid overfitting: be skeptical of patterns that may not generalize
- Practical focus: consider transaction costs, latency, market impact
- Push computation to ClickHouse SQL, not Python"""

_REVIEWER_ROLE = """You are reviewing a completed exploration stage. Your job:
1. Read the stage outputs (summary.json, any Parquet files)
2. Query ClickHouse to validate or challenge the findings
3. Separately classify issues as DATA QUALITY vs STATISTICAL concerns
4. Propose 3-5 refinement directions

DATA QUALITY issues are problems with the underlying data pipeline that may
invalidate results. These block further exploration until fixed:
- critical: Ingestion gaps, missing populations, schema errors, broken joins,
  zero-row results from tables that should have data
- warning: Partial data coverage, stale timestamps, minor schema mismatches
- info: Data quirks noted but not blocking

STATISTICAL concerns are methodology issues that inform refinements but don't
block exploration: sample size, bias, overfitting risk, multiple comparisons.
Put these in "concerns" as before.

IMPORTANT: Your FINAL message must contain a single JSON object and nothing else.
Do not include any commentary, markdown, or explanation in your final message.
All reasoning should happen in earlier messages while you use tools.

The JSON must match this exact structure:
{
    "summary": "2-3 sentence summary of validation results and key findings",
    "key_insights": ["insight1", "insight2", ...],
    "concerns": ["concern1", "concern2", ...],
    "data_quality_issues": [
        {
            "severity": "critical|warning|info",
            "description": "What is wrong with the data",
            "affected_metric": "Which metric is affected (optional)",
            "suggested_fix": "How to fix the pipeline (optional)"
        }
    ],
    "proposed_refinements": [
        {
            "name": "short_snake_case_name",
            "description": "What this tests",
            "refinement_type": "filter|feature|model|parameter|hypothesis|ensemble",
            "hypothesis": "Specific falsifiable hypothesis",
            "expected_outcome": "What success looks like",
            "priority": 1,
            "estimated_complexity": 3,
            "filter_conditions": {},
            "new_features": [],
            "model_config_extra": {}
        }
    ],
    "confidence": 0.75,
    "branch_recommendation": "continue",
    "information_gain": 0.8
}

You MUST include at least 3 proposed refinements. Priority is 1-5 (1=highest).
The "data_quality_issues" array may be empty if there are no data issues.

branch_recommendation values:
- "continue": More refinements along this direction are promising
- "pivot": This approach is failing, suggest a fundamentally different direction
- "converge": Findings are stable, no more refinements needed on this branch
- "reject": Hypothesis is definitively rejected, stop exploring this branch

information_gain (0.0-1.0): How much genuinely NEW information this stage produced
vs what was already known from parent/sibling stages. 1.0 = entirely novel findings,
0.0 = redundant with prior work."""

_GENERATOR_ROLE = """You are generating a Python stage script for a strategy exploration.

The script must follow this contract:
- Define STAGE_ID as a module constant
- Define a run(strategy_root: Path, outputs_dir: Path) -> dict function
- Use ExplorationDataSource for ClickHouse queries (from polymarket_pipeline.exploration.data)
- Use StageMetrics for metrics (from polymarket_pipeline.exploration.tree)
- Save outputs to outputs_dir as Parquet and/or JSON
- Return a summary dict with stage_id, metrics, and key findings
- Include a __main__ block for standalone execution

## ARCHITECTURE RULE: Components + Polars (MANDATORY)

**ALWAYS use reusable SQL components for standard patterns.** ClickHouse v24.8 has
CTE scope/resolver bugs that cause ambiguous identifiers, scope leaking, and
unresolvable references in hand-rolled multi-CTE queries. The components avoid these.

Pattern: ONE SQL query via component, then analytics in Polars.

```python
from polymarket_pipeline.exploration.components.pnl import PnLConfig, compute_market_pnl
from polymarket_pipeline.exploration.data import ExplorationDataSource

def run(strategy_root, outputs_dir):
    db = ExplorationDataSource()
    # 1. Heavy SQL via proven component (one query, battle-tested)
    result = compute_market_pnl(db, PnLConfig(perspective="taker_only"), outputs_dir)
    df = result.dataframes["trader_market_pnl"]
    # 2. All complex logic in Polars (no CTE bugs possible)
    df_filtered = df.filter(...).group_by(...).agg(...)
```

## Available Components (import from polymarket_pipeline.exploration.components):

| Function | What it does |
|----------|-------------|
| compute_market_pnl(db, PnLConfig, dir) | Per-trader per-market PnL → ComponentResult |
| compute_trader_aggregates(db, PnLConfig, dir) | Trader-level PnL features → ComponentResult |
| query_resolved_markets(db, cfg, dir) | Resolved markets identification → ComponentResult |
| temporal_consistency(db, cfg, dir, prior) | Multi-period consistency test → ComponentResult |
| compute_mvf(db, cfg, dir) | Maker volume fraction → ComponentResult |
| resolved_markets_cte(cfg) | Returns SQL CTE string (for composition) |
| positions_cte(cfg) | Returns SQL CTE string (taker_only/maker_only/both) |

## When to hand-roll SQL:
Only for queries NOT covered by components (orderflow, category analysis, convergence
timing). Keep hand-rolled queries simple: max 1-2 CTEs, no CTE-to-CTE JOINs.
If a query needs more than 2 CTEs, split into multiple queries and join in Polars.

## ClickHouse SQL rules (if hand-rolling):
- ALWAYS wrap FINAL in subquery: FROM (SELECT * FROM trades_raw FINAL WHERE ...) t
- NEVER use WHERE x IN (SELECT FROM CTE) inside another CTE
- NEVER chain 3+ JOINs where tables share column names (esp. condition_id)
- Use Float64 for all financial aggregation

Use get_schema to inspect actual table columns before writing SQL.
Return ONLY the Python code, no markdown wrapping."""

_EXPLORER_ROLE = """You are exploring the Polymarket dataset to discover trading opportunities.
Query the data freely, look for patterns, anomalies, and exploitable signals.
Report your findings clearly with supporting statistics."""

_BRAINSTORM_ROLE = """You are a research advisor monitoring an ongoing strategy exploration.
Your job is NOT to run analyses. Instead:
1. Review completed stage results across the full exploration tree
2. Detect confirmations, contradictions, and patterns across branches
3. Classify findings: hard_constraint (>= 80% confidence, confirmed 2+ stages),
   soft_hint (60-80%), observation (< 60%)
4. Identify ClickHouse/SQL mistakes the generator keeps making -> platform knowledge
5. Flag when human input is needed (pivots, contradictions, convergence)

IMPORTANT: Your FINAL message must be a single JSON object with this structure:
{
    "new_learnings": [
        {
            "id": "snake_case_key",
            "finding": "What was learned",
            "evidence_stage_ids": ["01a_...", "02b_..."],
            "confidence": 0.85,
            "enforcement": "hard_constraint|soft_hint|observation"
        }
    ],
    "platform_knowledge": [
        {
            "category": "sql_pitfall|schema_quirk|performance|data_quality",
            "description": "What the issue is",
            "correct_pattern": "The right way to do it",
            "wrong_pattern": "The wrong pattern to avoid",
            "discovered_in_stage": "01b_..."
        }
    ],
    "escalation_triggers": [
        {
            "trigger_type": "pivot_needed|contradiction_found|convergence_reached|novel_discovery",
            "severity": "pause|notify",
            "description": "What happened",
            "evidence": ["stage_id_1", "stage_id_2"],
            "proposed_actions": ["action1", "action2"],
            "question_for_human": "What should we do about X?"
        }
    ],
    "open_questions": ["question1", "question2"],
    "brief_update": "2-3 sentence summary of what changed"
}"""


class PromptRegistry:
    """Registry that loads, composes, and persists prompt layers."""

    def __init__(
        self,
        platform_layer: PromptLayer | None = None,
        strategy_layer: PromptLayer | None = None,
        role_layers: dict[str, PromptLayer] | None = None,
    ) -> None:
        self.platform_layer = platform_layer or PromptLayer(
            name="platform",
            content=_BASE_PLATFORM,
        )
        self.strategy_layer = strategy_layer or PromptLayer(
            name="strategy",
            content="",
        )
        self.role_layers = role_layers or {
            "reviewer": PromptLayer(name="reviewer", content=_REVIEWER_ROLE),
            "generator": PromptLayer(name="generator", content=_GENERATOR_ROLE),
            "explorer": PromptLayer(name="explorer", content=_EXPLORER_ROLE),
            "brainstorm": PromptLayer(name="brainstorm", content=_BRAINSTORM_ROLE),
        }

    def render_for_role(self, role: str) -> str:
        """Compose platform + strategy + role layers into a single prompt."""
        stack = PromptStack()
        stack.add_layer(self.platform_layer)
        if self.strategy_layer.content.strip():
            stack.add_layer(self.strategy_layer)
        role_layer = self.role_layers.get(role)
        if role_layer:
            stack.add_layer(role_layer)
        return stack.render()

    def evolve_platform(self, entries: list[dict[str, Any]]) -> None:
        """Append new platform knowledge entries to the platform layer."""
        additions: list[str] = []
        for entry in entries:
            desc = entry.get("description", "")
            correct = entry.get("correct_pattern", "")
            wrong = entry.get("wrong_pattern", "")
            parts = [f"- {desc}"]
            if correct:
                parts.append(f"  Correct: {correct}")
            if wrong:
                parts.append(f"  Wrong: {wrong}")
            additions.append("\n".join(parts))

        if additions:
            separator = "\n\nPLATFORM KNOWLEDGE (learned from exploration):\n"
            self.platform_layer = PromptLayer(
                name="platform",
                content=self.platform_layer.content + separator + "\n".join(additions),
                source="brainstorm",
                version=self.platform_layer.version + 1,
            )

    def evolve_strategy(self, learnings: list[dict[str, Any]]) -> None:
        """Append hard constraints from strategy learnings."""
        constraints: list[str] = []
        for learning in learnings:
            enforcement = learning.get("enforcement", "observation")
            finding = learning.get("finding", "")
            if enforcement == "hard_constraint":
                constraints.append(f"MUST: {finding}")
            elif enforcement == "soft_hint":
                constraints.append(f"PREFER: {finding}")

        if constraints:
            separator = "\n\nSTRATEGY CONSTRAINTS (from exploration learnings):\n"
            self.strategy_layer = PromptLayer(
                name="strategy",
                content=self.strategy_layer.content + separator + "\n".join(constraints),
                source="brainstorm",
                version=self.strategy_layer.version + 1,
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, strategy: str) -> None:
        """Save platform and strategy prompts to disk."""
        # Platform prompt (shared)
        shared_dir = SHARED_DIR
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "platform_prompt.json").write_text(
            self.platform_layer.model_dump_json(indent=2)
        )

        # Strategy prompt
        strategy_dir = STRATEGIES_ROOT / strategy
        strategy_dir.mkdir(parents=True, exist_ok=True)
        (strategy_dir / "strategy_prompt.json").write_text(
            self.strategy_layer.model_dump_json(indent=2)
        )

    @classmethod
    def load(cls, strategy: str) -> PromptRegistry:
        """Load from disk, falling back to defaults."""
        platform_layer = None
        strategy_layer = None

        platform_path = SHARED_DIR / "platform_prompt.json"
        if platform_path.exists():
            data = json.loads(platform_path.read_text())
            platform_layer = PromptLayer(**data)

        strategy_path = STRATEGIES_ROOT / strategy / "strategy_prompt.json"
        if strategy_path.exists():
            data = json.loads(strategy_path.read_text())
            strategy_layer = PromptLayer(**data)

        return cls(
            platform_layer=platform_layer,
            strategy_layer=strategy_layer,
        )
