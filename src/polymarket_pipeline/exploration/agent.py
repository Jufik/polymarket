"""Claude Agent SDK integration for strategy exploration.

Three agent roles:
- reviewer: analyze completed stage outputs against live data
- generator: produce new stage scripts with correct ClickHouse SQL
- explorer: free-form exploration, suggest next steps
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.prompts.registry import PromptRegistry
from polymarket_pipeline.exploration.tree import (
    BranchRecommendation,
    ClaudeAnalysis,
    DataQualityIssue,
    ExplorationContext,
    ExplorationStage,
    ExplorationTree,
    ProposedRefinement,
    RefinementType,
)

# ---------------------------------------------------------------------------
# MCP Tools (in-process, no subprocess overhead)
# ---------------------------------------------------------------------------

_db: ExplorationDataSource | None = None
_db_lock = threading.Lock()


def _get_db() -> ExplorationDataSource:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:  # double-checked locking
                _db = ExplorationDataSource()
    return _db


@tool(
    "query_clickhouse",
    "Run a read-only SQL query against ClickHouse polymarket database. "
    "Returns up to 100 rows as text. Use FINAL on trades_raw for dedup. "
    "Available tables: trades_raw/trades (246M trades), events, markets, "
    "tags, event_tags, token_market_map (all PG engine tables).",
    {"sql": str},
)
async def query_clickhouse(args: dict[str, Any]) -> dict[str, Any]:
    sql = args["sql"].strip()
    # Safety: block mutations
    first_word = sql.split()[0].upper() if sql else ""
    if first_word in ("INSERT", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "UPDATE"):
        return {
            "content": [{"type": "text", "text": f"Error: {first_word} queries are not allowed."}],
            "is_error": True,
        }
    try:
        db = _get_db()
        rows = db.query_raw(sql)
        # Truncate large results
        if len(rows) > 100:
            text = json.dumps(rows[:100], default=str, indent=2)
            text += f"\n... ({len(rows)} total rows, showing first 100)"
        else:
            text = json.dumps(rows, default=str, indent=2)
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Query error: {e}"}], "is_error": True}


@tool(
    "get_schema",
    "Get column names, types, and comments for a ClickHouse table.",
    {"table": str},
)
async def get_schema(args: dict[str, Any]) -> dict[str, Any]:
    db = _get_db()
    schema = db.get_schema(args["table"])
    return {"content": [{"type": "text", "text": json.dumps(schema, indent=2)}]}


@tool(
    "read_file",
    "Read a file and return its contents. For JSON/text files returns text. "
    "For Parquet files returns a summary of columns and row count.",
    {"path": str},
)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["path"])
    if not path.exists():
        return {
            "content": [{"type": "text", "text": f"File not found: {path}"}],
            "is_error": True,
        }
    if path.suffix == ".parquet":
        import polars as pl

        df = pl.read_parquet(path)
        text = (
            f"Parquet: {df.shape[0]} rows x {df.shape[1]} columns\n"
            f"Columns: {df.columns}\n"
            f"Schema: {df.schema}\n"
            f"Head:\n{df.head(10)}"
        )
        return {"content": [{"type": "text", "text": text}]}
    text = path.read_text()
    if len(text) > 50_000:
        text = text[:50_000] + "\n... (truncated)"
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "write_file",
    "Write content to a file. Creates parent directories if needed.",
    {"path": str, "content": str},
)
async def write_file(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"])
    return {
        "content": [{"type": "text", "text": f"Written {len(args['content'])} bytes to {path}"}]
    }


# ---------------------------------------------------------------------------
# MCP server and agent options
# ---------------------------------------------------------------------------

# Module-level registry cache
_registry: PromptRegistry | None = None


def _get_registry(strategy: str | None = None) -> PromptRegistry:
    """Get or create the prompt registry, loading from disk if available."""
    global _registry
    if _registry is None:
        _registry = PromptRegistry.load(strategy or "skilled_traders")
    return _registry


def set_registry(registry: PromptRegistry) -> None:
    """Set the module-level prompt registry (used by orchestrator)."""
    global _registry
    _registry = registry


# Keep legacy constants for backward compatibility
_BASE_SYSTEM = """You are an expert quantitative researcher specializing in Polymarket prediction markets.

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
- Push computation to ClickHouse SQL, not Python
"""

_REVIEWER_SYSTEM = (
    _BASE_SYSTEM
    + """
You are reviewing a completed exploration stage. Your job:
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
0.0 = redundant with prior work.
"""
)

_GENERATOR_SYSTEM = (
    _BASE_SYSTEM
    + """
You are generating a Python stage script for a strategy exploration.

The script must follow this contract:
- Define STAGE_ID as a module constant
- Define a run(strategy_root: Path, outputs_dir: Path) -> dict function
- Use ExplorationDataSource for ClickHouse queries (from polymarket_pipeline.exploration.data)
- Use StageMetrics for metrics (from polymarket_pipeline.exploration.tree)
- Save outputs to outputs_dir as Parquet and/or JSON
- Return a summary dict with stage_id, metrics, and key findings
- Include a __main__ block for standalone execution
- Push aggregation to ClickHouse SQL, use Polars only for prototyping transforms

Use get_schema to inspect actual table columns before writing SQL.
Return ONLY the Python code, no markdown wrapping.
"""
)

_EXPLORER_SYSTEM = (
    _BASE_SYSTEM
    + """
You are exploring the Polymarket dataset to discover trading opportunities.
Query the data freely, look for patterns, anomalies, and exploitable signals.
Report your findings clearly with supporting statistics.
"""
)


# ---------------------------------------------------------------------------
# MCP server and agent options
# ---------------------------------------------------------------------------


def _build_mcp_server():
    return create_sdk_mcp_server(
        name="exploration",
        version="1.0.0",
        tools=[query_clickhouse, get_schema, read_file, write_file],
    )


def _build_options(
    system_prompt: str | None = None,
    tool_names: list[str] | None = None,
    max_turns: int = 20,
    *,
    role: str | None = None,
    strategy: str | None = None,
) -> ClaudeAgentOptions:
    # New path: use registry-based prompt for a given role
    if role is not None:
        registry = _get_registry(strategy)
        prompt = registry.render_for_role(role)
    elif system_prompt is not None:
        prompt = system_prompt
    else:
        raise ValueError("Either 'role' or 'system_prompt' must be provided")
    server = _build_mcp_server()
    return ClaudeAgentOptions(
        system_prompt=prompt,
        model="claude-opus-4-6",
        mcp_servers={"exploration": server},
        allowed_tools=[f"mcp__exploration__{t}" for t in (tool_names or [])],
        max_turns=max_turns,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def review_stage(
    stage: ExplorationStage,
    strategy_root: Path,
    on_event: Callable[[dict], None] | None = None,
    context: ExplorationContext | None = None,
    tree: ExplorationTree | None = None,
) -> tuple[ClaudeAnalysis, AgentResult]:
    """Have Claude review a completed stage and produce analysis.

    Returns (analysis, agent_result) where agent_result contains the
    full transcript of all tool calls and reasoning.
    """
    outputs_dir = strategy_root / stage.outputs_path if stage.outputs_path else None

    prompt_parts = [
        f"Review exploration stage '{stage.id}' ({stage.name}).",
        f"Hypothesis: {stage.hypothesis or 'Initial exploration'}",
        f"Depth: {stage.depth}",
    ]

    if outputs_dir and outputs_dir.exists():
        prompt_parts.append(
            f"\nStage outputs are in: {outputs_dir}"
            "\nStart by reading the summary.json file, then query ClickHouse to validate."
        )

    if stage.metrics:
        prompt_parts.append(
            f"\nReported metrics: {json.dumps(stage.metrics.model_dump(exclude_none=True), default=str)}"
        )

    # Enrich with exploration context so the reviewer sees what siblings found
    if tree is not None and context is not None:
        prompt_parts.append(_build_context_section(stage, tree, context))

    prompt = "\n".join(prompt_parts)

    options = _build_options(
        role="reviewer",
        tool_names=["query_clickhouse", "get_schema", "read_file"],
        max_turns=130,
    )

    agent_result = await _run_agent(prompt, options, on_event=on_event)
    analysis = _parse_analysis(agent_result.last_text)
    return analysis, agent_result


def _build_context_section(
    stage: ExplorationStage,
    tree: ExplorationTree,
    context: ExplorationContext,
) -> str:
    """Build exploration context section for the reviewer prompt."""
    parts: list[str] = ["\n--- EXPLORATION CONTEXT ---"]

    # 1. Exploration path (root to current stage)
    path = tree.get_path_to_root(stage.id)
    if path:
        path_lines = []
        for s in path:
            conf = f" (confidence={s.analysis.confidence:.0%})" if s.analysis else ""
            rec = f", recommendation={s.analysis.branch_recommendation.value}" if s.analysis else ""
            path_lines.append(f"  {s.id}: {s.name}{conf}{rec}")
        parts.append("Exploration path (root -> current):\n" + "\n".join(path_lines))

    # 2. Sibling stage summaries (max 5)
    if stage.parent_id:
        siblings = [
            s for s in tree.get_children(stage.parent_id) if s.id != stage.id and s.analysis
        ]
        if siblings:
            sib_lines = []
            for s in siblings[:5]:
                assert s.analysis is not None
                finding = s.analysis.summary[:120] if s.analysis.summary else "N/A"
                sib_lines.append(
                    f"  {s.id} ({s.name}): confidence={s.analysis.confidence:.0%}, "
                    f"finding: {finding}"
                )
            parts.append(
                f"Sibling stages already explored under {stage.parent_id} "
                f"({len(siblings)} total):\n" + "\n".join(sib_lines)
            )

    # 3. Established facts (max 10)
    if context.established_facts:
        facts = context.established_facts[:10]
        parts.append(
            "Established facts from prior stages:\n" + "\n".join(f"  - {f}" for f in facts)
        )

    # 4. Rejected directions (max 10)
    if context.rejected_directions:
        rejected = context.rejected_directions[:10]
        parts.append(
            "Rejected directions (do NOT re-propose):\n" + "\n".join(f"  - {r}" for r in rejected)
        )

    # 5. Already-explored refinement names
    if context.executed_refinement_names:
        parts.append(
            "Refinement names already explored (propose DIFFERENT names):\n"
            + "\n".join(f"  - {n}" for n in sorted(context.executed_refinement_names))
        )

    parts.append("--- END EXPLORATION CONTEXT ---")
    return "\n\n".join(parts)


async def generate_stage_script(
    stage: ExplorationStage,
    parent: ExplorationStage | None,
    refinement: ProposedRefinement | None,
    strategy_root: Path,
    output_path: Path,
    on_event: Callable[[dict], None] | None = None,
    previous_error: str | None = None,
    previous_dq_issues: list[DataQualityIssue] | None = None,
) -> AgentResult:
    """Have Claude generate a stage script."""
    prompt_parts = [
        f"Generate a Python stage script for: {stage.name}",
        f"Stage ID: {stage.id}",
        f"Hypothesis: {stage.hypothesis or 'To be determined'}",
    ]

    if parent:
        prompt_parts.append(f"Parent stage: {parent.id} ({parent.name})")
        if parent.outputs_path:
            prompt_parts.append(f"Parent outputs at: {strategy_root / parent.outputs_path}")
        parent_script = strategy_root / (parent.script_path or f"stages/{parent.id}/stage.py")
        if parent_script.exists():
            prompt_parts.append(
                f"\nIMPORTANT: Start by reading the parent script at {parent_script} with read_file. "
                "Your script should adapt and build upon the parent's SQL and logic, "
                "applying only the specific refinement described below."
            )

    if refinement:
        prompt_parts.append(f"Refinement type: {refinement.refinement_type.value}")
        prompt_parts.append(f"Expected outcome: {refinement.expected_outcome}")
        if refinement.filter_conditions:
            prompt_parts.append(f"Filter conditions: {json.dumps(refinement.filter_conditions)}")
        if refinement.new_features:
            prompt_parts.append(f"New features to compute: {refinement.new_features}")

    if previous_error:
        prompt_parts.append(
            f"\nPREVIOUS ATTEMPT FAILED with error:\n{previous_error}\n\n"
            f"Read the existing script at {output_path}, diagnose the bug, and write a fixed version.\n"
            "Common ClickHouse pitfalls:\n"
            "- Always alias columns with table prefix after JOINs (e.g. t.condition_id AS condition_id)\n"
            "- Avoid SELECT * FROM large tables with FINAL — select only needed columns, or drop FINAL\n"
            "- Don't reuse CTE column names as outer aliases (causes nested aggregation errors)"
        )
    elif previous_dq_issues:
        issues_desc = "\n".join(
            f"- [{dq.severity.upper()}] {dq.description}"
            + (f"\n  Suggested fix: {dq.suggested_fix}" if dq.suggested_fix else "")
            + (f"\n  Affected metric: {dq.affected_metric}" if dq.affected_metric else "")
            for dq in previous_dq_issues
        )
        prompt_parts.append(
            f"\nPREVIOUS ATTEMPT had DATA QUALITY issues detected by the reviewer:\n"
            f"{issues_desc}\n\n"
            f"Read the existing script at {output_path}, diagnose the data quality problems, "
            f"and write a fixed version.\n"
            "Common data quality causes:\n"
            "- Missing FINAL keyword on ReplacingMergeTree tables (trades_raw) causing duplicates\n"
            "- Wrong JOIN keys producing fanout or zero rows\n"
            "- Filtering on NULL columns without COALESCE/isNotNull\n"
            "- Incorrect date/timestamp filtering excluding valid data\n"
            "- Not handling Nullable columns in aggregations"
        )
    else:
        prompt_parts.append(
            f"\nWrite the script to: {output_path}"
            "\nFirst inspect the ClickHouse schema with get_schema, "
            "then write the script with write_file."
        )

    prompt = "\n".join(prompt_parts)

    options = _build_options(
        role="generator",
        tool_names=["query_clickhouse", "get_schema", "read_file", "write_file"],
        max_turns=15,
    )

    return await _run_agent(prompt, options, on_event=on_event)


async def explore(prompt: str, max_turns: int = 30) -> AgentResult:
    """Free-form exploration of the dataset."""
    options = _build_options(
        role="explorer",
        tool_names=["query_clickhouse", "get_schema", "read_file"],
        max_turns=max_turns,
    )
    return await _run_agent(prompt, options)


@dataclass
class FilteredRefinement:
    """A refinement that was filtered out, with reason."""

    parent_id: str
    refinement_name: str
    reason: str


def suggest_next_stages(
    tree: ExplorationTree,
    max_suggestions: int = 3,
    context: ExplorationContext | None = None,
) -> tuple[list[tuple[ExplorationStage, ProposedRefinement]], list[FilteredRefinement]]:
    """Rank pending refinements across the tree with dedup and pruning.

    Returns (suggestions, filtered) where filtered contains refinements
    that were skipped and the reason why.
    """
    suggestions: list[tuple[float, ExplorationStage, ProposedRefinement]] = []
    filtered: list[FilteredRefinement] = []

    consumed_keys = context.consumed_refinement_keys if context else set()
    executed_names = context.executed_refinement_names if context else set()
    branch_statuses = context.branch_statuses if context else {}

    for stage in tree.get_active_stages():
        if not stage.analysis:
            continue

        # Branch pruning: skip stages whose branch is rejected or converged
        rec = branch_statuses.get(stage.id, BranchRecommendation.CONTINUE)
        if rec in (BranchRecommendation.REJECT, BranchRecommendation.CONVERGE):
            for ref in stage.analysis.proposed_refinements:
                filtered.append(
                    FilteredRefinement(
                        parent_id=stage.id,
                        refinement_name=ref.name,
                        reason=f"branch {rec.value}",
                    )
                )
            continue

        # Skip low-confidence parents
        if stage.analysis.confidence < 0.3:
            for ref in stage.analysis.proposed_refinements:
                filtered.append(
                    FilteredRefinement(
                        parent_id=stage.id,
                        refinement_name=ref.name,
                        reason=f"parent confidence {stage.analysis.confidence:.0%} < 30%",
                    )
                )
            continue

        info_gain = stage.analysis.information_gain

        for ref in stage.analysis.proposed_refinements:
            # Consumed refinement filtering: skip if already has a child
            ref_key = f"{stage.id}::{ref.name}"
            if ref_key in consumed_keys:
                filtered.append(
                    FilteredRefinement(
                        parent_id=stage.id,
                        refinement_name=ref.name,
                        reason="already consumed (child exists)",
                    )
                )
                continue

            # Global name dedup: skip if name exists anywhere as a stage name
            if ref.name in executed_names:
                filtered.append(
                    FilteredRefinement(
                        parent_id=stage.id,
                        refinement_name=ref.name,
                        reason="name already used globally",
                    )
                )
                continue

            # Path-aware scoring: lower is better
            score = (
                ref.priority
                + ref.estimated_complexity * 0.5
                - stage.analysis.confidence * 2.0
                + stage.depth * 0.5
                - info_gain * 1.0
            )
            suggestions.append((score, stage, ref))

    suggestions.sort(key=lambda x: x[0])
    return (
        [(stage, ref) for _, stage, ref in suggestions[:max_suggestions]],
        filtered,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Full result from an agent run, including transcript."""

    last_text: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def save_transcript(self, path: Path) -> None:
        """Save transcript as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.transcript, indent=2, default=str))


async def _run_agent(
    prompt: str,
    options: ClaudeAgentOptions,
    on_event: Callable[[dict], None] | None = None,
) -> AgentResult:
    """Run an agent query and capture the full transcript.

    Returns AgentResult with:
    - last_text: the final assistant message (used for parsing)
    - transcript: ordered list of all messages (text, tool calls, results)

    If *on_event* is provided it is called synchronously with each transcript
    entry dict as soon as it is appended, enabling live streaming of agent
    activity to the caller.
    """
    result = AgentResult()

    def _append(entry: dict) -> None:
        result.transcript.append(entry)
        if on_event is not None:
            on_event(entry)

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)

        async for msg in client.receive_messages():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        _append(
                            {
                                "role": "assistant",
                                "type": "text",
                                "text": block.text,
                            }
                        )
                    elif isinstance(block, ThinkingBlock):
                        _append(
                            {
                                "role": "assistant",
                                "type": "thinking",
                                "text": block.text,
                            }
                        )
                    elif isinstance(block, ToolUseBlock):
                        _append(
                            {
                                "role": "assistant",
                                "type": "tool_use",
                                "tool": block.name,
                                "tool_use_id": block.id,
                                "input": block.input,
                            }
                        )
                    elif isinstance(block, ToolResultBlock):
                        _append(
                            {
                                "role": "tool",
                                "type": "tool_result",
                                "tool_use_id": block.tool_use_id,
                                "content": block.content,
                                "is_error": block.is_error,
                            }
                        )

                # Extract last text from this message
                text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                if text_parts:
                    result.last_text = "\n".join(text_parts)

            elif isinstance(msg, ResultMessage):
                break

    return result


def _parse_analysis(response_text: str) -> ClaudeAnalysis:
    """Parse Claude's JSON response into a ClaudeAnalysis."""
    # Extract JSON from response (handle markdown wrapping)
    text = response_text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        # Find the last code block (likely the JSON)
        parts = text.split("```")
        for part in reversed(parts):
            stripped = part.strip()
            if stripped.startswith("{"):
                text = stripped
                break

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return ClaudeAnalysis(
            summary=response_text[:500],
            key_insights=["Failed to parse structured response"],
            concerns=["Agent did not return valid JSON"],
            proposed_refinements=[],
            confidence=0.0,
        )

    data = json.loads(text[start:end])

    refinements = [
        ProposedRefinement(
            name=r["name"],
            description=r["description"],
            refinement_type=RefinementType(r["refinement_type"]),
            hypothesis=r["hypothesis"],
            expected_outcome=r["expected_outcome"],
            priority=r.get("priority", 3),
            estimated_complexity=r.get("estimated_complexity", 3),
            filter_conditions=r.get("filter_conditions"),
            new_features=r.get("new_features"),
            model_config_extra=r.get("model_config_extra"),
        )
        for r in data.get("proposed_refinements", [])
    ]

    dq_issues = [
        DataQualityIssue(
            severity=dq.get("severity", "info"),
            description=dq["description"],
            affected_metric=dq.get("affected_metric"),
            suggested_fix=dq.get("suggested_fix"),
        )
        for dq in data.get("data_quality_issues", [])
    ]

    # Parse branch recommendation
    raw_rec = data.get("branch_recommendation", "continue")
    try:
        branch_rec = BranchRecommendation(raw_rec)
    except ValueError:
        branch_rec = BranchRecommendation.CONTINUE

    return ClaudeAnalysis(
        summary=data.get("summary", ""),
        key_insights=data.get("key_insights", []),
        concerns=data.get("concerns", []),
        proposed_refinements=refinements,
        data_quality_issues=dq_issues,
        confidence=data.get("confidence", 0.5),
        branch_recommendation=branch_rec,
        information_gain=float(data.get("information_gain", 1.0)),
    )
