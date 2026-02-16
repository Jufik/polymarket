# Conversational Exploration Shell (`pm-chat`)

**Date**: 2026-02-16
**Status**: Design approved, pending implementation

## Problem

The current exploration orchestrator is batch-oriented: `pm-explore orchestrate` runs autonomously (generate -> run -> review -> repeat), with human interaction limited to escalation pauses on critical DQ issues or brainstorm triggers. There is no way to:

- Ask questions about a strategy's current state mid-exploration
- Drill into a specific stage's outputs and validate findings interactively
- Run ad-hoc SQL or Polars transforms during exploration
- Redirect exploration based on human insight without editing code
- Create reusable tools on the fly from ad-hoc analyses

## Solution

A TypeScript CLI (`pm-chat`) built on the Claude Agent SDK V2 that wraps the existing Python exploration engine via MCP tools. The Python engine stays unchanged -- the TS layer adds a conversational interface on top.

## Architecture

```
                         +-------------------------+
                         |       pm-chat CLI       |
                         |   (TypeScript / Node)   |
                         |                         |
                         |  readline prompt loop   |
                         |  + terminal formatting  |
                         +-----------+-------------+
                                     |
                    +----------------v-----------------+
                    |   Claude Agent SDK V2 Session    |
                    |   (opus for orchestration,       |
                    |    sonnet for data/code tasks)   |
                    |                                  |
                    |  systemPrompt =                  |
                    |    platform_prompt.json           |
                    |  + strategy_prompt.json           |
                    |  + research_brief.json            |
                    |  + conversation role              |
                    |  + conversation_state (on resume) |
                    +--------+-----------+-------------+
                             |           |
              +--------------+     +-----+------+
              |                    |            |
     +--------v------+  +---------v---+  +-----v--------+
     | orchestrator  |  |    data     |  |    code      |
     | MCP server    |  | MCP server  |  | MCP server   |
     +-------+-------+  +------+------+  +------+-------+
             |                 |                |
             | subprocess      | CH HTTP API    | subprocess
             v                 v                v
     +----------------------------------------------+
     |        Python exploration engine             |
     |  lifecycle / components / agent / tree       |
     +----------------------------------------------+
```

### Design Decisions

1. **Hybrid TS frontend, Python backend**: Keeps proven Python computation engine untouched. TS provides the Claude Agent SDK's V2 session management, custom MCP tools, hooks, subagents, and streaming.

2. **Three MCP servers**: Separate concerns (orchestration vs data vs code). Each server's tools are independently composable -- the agent can use data tools without orchestration tools for pure analysis.

3. **Python bridge via subprocess**: `bridge.py` provides JSON-in/JSON-out dispatch to existing Python functions. No HTTP server, no extra process -- just `uv run python -m polymarket_pipeline.cli.bridge`.

4. **Runtime tool expansion**: The `register_tool` tool lets the agent create new MCP tools backed by Python handlers. Tools persist across hot session resumes and are saved to disk for cold restarts.

## MCP Tool Catalog

### `orchestrator` server -- Strategy & stage lifecycle

| Tool | Inputs | Description |
|------|--------|-------------|
| `read_tree` | `strategy` | Full exploration tree JSON |
| `read_state` | `strategy` | Orchestrator state + research brief |
| `status` | `strategy, format?: "text"\|"mermaid"` | Tree visualization |
| `read_stage` | `strategy, stage_id` | Stage details: script, outputs, analysis, transcript, metrics |
| `run_stage` | `strategy, stage_id` | Execute stage script via `lifecycle.run_stage()` |
| `review_stage` | `strategy, stage_id` | Claude review via `lifecycle.review_stage_lifecycle()` |
| `generate_stage` | `strategy, parent_id, refinement_name` | Create child stage via `lifecycle.generate_stage_lifecycle()` |
| `suggest_next` | `strategy, n?` | Rank top-N suggested refinements |
| `compare_stages` | `strategy, stage_ids[]` | Side-by-side metrics/insights comparison |
| `set_direction` | `strategy, stage_id, direction` | Override branch recommendation (continue/pivot/converge/reject) |
| `list_strategies` | (none) | List all strategies in `strategies/` |

### `data` server -- Direct data access

| Tool | Inputs | Description |
|------|--------|-------------|
| `query_clickhouse` | `sql` | Read-only SQL against ClickHouse (max 10K rows). Blocks mutations. |
| `get_schema` | `table` | Column names, types, comments for a ClickHouse table |
| `read_parquet` | `path, columns?, n_rows?` | Read Parquet file as JSON via Python/Polars |
| `read_stage_outputs` | `strategy, stage_id, file` | Read a specific output file |
| `describe_df` | `strategy, stage_id, file` | Parquet describe: shape, dtypes, null counts, quantiles |

### `code` server -- Ad-hoc computation & tool expansion

| Tool | Inputs | Description |
|------|--------|-------------|
| `run_python` | `code, timeout_s?` | Execute Python with access to polars, numpy, ExplorationDataSource, components |
| `edit_stage_script` | `strategy, stage_id, code` | Overwrite a stage's `stage.py` |
| `create_component` | `name, code` | Write new reusable component to `exploration/components/` |
| `register_tool` | `name, description, input_schema, python_handler` | Create a new MCP tool at runtime backed by a Python handler |

### Python bridge

All subprocess tools call through a single dispatcher:

```typescript
async function callPython(
  module: string, func: string, args: Record<string, unknown>
): Promise<string> {
  const proc = spawn("uv", [
    "run", "python", "-m", "polymarket_pipeline.cli.bridge",
    "--module", module, "--func", func, "--args", JSON.stringify(args)
  ]);
  // collect stdout, handle stderr, enforce timeout
}
```

`bridge.py` is the only new Python file. It dispatches to existing functions:

```python
# polymarket_pipeline/cli/bridge.py
import json, sys, importlib, asyncio

def main():
    args = parse_args()
    mod = importlib.import_module(args.module)
    func = getattr(mod, args.func)
    result = func(**json.loads(args.args))
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    json.dump(result, sys.stdout, default=str)

if __name__ == "__main__":
    main()
```

## Subagents

| Agent | Model | Tools | When used |
|-------|-------|-------|-----------|
| `analyst` | sonnet | `data.*` only | Focused data queries: "What's the Sharpe for pure takers?" |
| `reviewer` | opus | `orchestrator.read_*`, `data.*` | Deep stage analysis: "Review stage 01b" |
| `coder` | sonnet | `code.*`, `data.*` | Script generation: "Write a stage that filters by MVF < 0.10" |

The parent conversation agent (opus) decides when to delegate to subagents based on their descriptions.

## Session & State Persistence

### Hot tier -- SDK session resume (< hours)

```
Session started -> session.sessionId saved to strategies/{name}/chat_session.json
User types "exit" -> session.close(), sessionId persisted
User returns -> unstable_v2_resumeSession(savedId)
  -> Full conversation context restored
```

### Cold tier -- Disk snapshot (across days/restarts)

At each turn boundary (or explicit `/save`), extract structured state:

```json
// strategies/{name}/conversation_state.json
{
  "session_id": "abc-123",
  "strategy": "skilled_traders",
  "last_active": "2026-02-16T10:30:00Z",
  "focus": {
    "stage_id": "02a_taker_only_skill_scoring",
    "mode": "analysis"
  },
  "decision_log": [
    "Rejected stage 01e_bot_and_mm_filter -- filter is gameable",
    "Asked to focus on pure takers (MVF < 0.10)",
    "Created ad-hoc tool: compute_rolling_sharpe"
  ],
  "registered_tools": [
    {
      "name": "compute_rolling_sharpe",
      "description": "Rolling 30-day Sharpe ratio per trader",
      "python_handler": "strategies/skilled_traders/_tools/rolling_sharpe.py"
    }
  ],
  "pending_questions": [
    "Should we include hybrid traders (MVF 10-50%) in the next cohort?"
  ]
}
```

On cold restart, the conversation state is injected into the system prompt so Claude picks up context even without conversation history.

### Resume logic

```
pm-chat skilled_traders
  |
  +-- chat_session.json exists AND session_id fresh?
  |     YES -> unstable_v2_resumeSession(id) [hot]
  |     NO  -> conversation_state.json exists?
  |              YES -> createSession() with state in systemPrompt [cold]
  |              NO  -> createSession() fresh [new]
```

## System Prompt Composition

The SDK's `systemPrompt` is built by composing existing prompt layers:

```typescript
const systemPrompt = {
  type: "preset" as const,
  preset: "claude_code" as const,
  append: [
    readJson("strategies/_shared/platform_prompt.json").content,
    readJson(`strategies/${strategy}/strategy_prompt.json`).content,
    readJson(`strategies/${strategy}/research_brief.json`)?.summary ?? "",
    CONVERSATION_ROLE,           // interactive mode instructions
    coldStateContext ?? "",       // conversation_state.json on cold resume
  ].join("\n\n---\n\n")
};
```

The **CONVERSATION_ROLE** prompt:

```
You are an interactive exploration assistant for the "{strategy}" strategy.
You have tools to read the exploration tree, run stages, query ClickHouse,
execute Python, and create new tools at runtime.

INTERACTION MODES:
- Strategy overview: tree status, suggest next steps, compare branches
- Stage deep-dive: read outputs, validate findings, re-run with tweaks
- Data exploration: ad-hoc SQL, Polars transforms, statistical tests
- Tool creation: build reusable components from ad-hoc analyses

RULES:
- ALWAYS read the current tree state before proposing actions.
- ALWAYS confirm before running stages (they cost time + API budget).
- When showing data, prefer concise tables over raw JSON.
- Respect all MUST/PREFER constraints from the strategy prompt.
- Use proven SQL components (compute_market_pnl, etc.) over hand-rolled SQL.
```

This reuses the existing `PromptRegistry` content -- no prompt duplication.

## Hooks

```typescript
hooks: {
  PreToolUse: [
    {
      // Confirm before running/generating stages (expensive operations)
      toolNames: [
        "mcp__orchestrator__run_stage",
        "mcp__orchestrator__generate_stage",
        "mcp__orchestrator__review_stage"
      ],
      callback: async (input) => ({
        hookSpecificOutput: {
          permissionDecision: "ask",
          permissionDecisionReason: `About to ${input.tool_name}: ${input.tool_input?.stage_id ?? input.tool_input?.refinement_name}`
        }
      })
    },
    {
      // Block mutation SQL
      toolNames: ["mcp__data__query_clickhouse"],
      callback: async (input) => {
        const sql = (input.tool_input?.sql ?? "").toUpperCase();
        if (/\b(INSERT|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b/.test(sql)) {
          return {
            hookSpecificOutput: {
              permissionDecision: "deny",
              permissionDecisionReason: "Mutation queries are blocked"
            }
          };
        }
        return { hookSpecificOutput: { permissionDecision: "allow" } };
      }
    }
  ],
  PostToolUse: [
    {
      // Log all tool executions for audit trail
      callback: async (input) => {
        appendToLog(strategy, {
          tool: input.tool_name,
          input: input.tool_input,
          timestamp: new Date().toISOString()
        });
        return {};
      }
    }
  ]
}
```

## Project Layout

```
pm-chat/                              # New TypeScript package
  package.json                        # deps: @anthropic-ai/claude-agent-sdk, zod, chalk
  tsconfig.json
  src/
    index.ts                          # CLI entry: args, session create/resume, REPL loop
    session.ts                        # Session lifecycle, state snapshot save/load
    prompt.ts                         # Load prompt JSONs -> systemPrompt composition
    bridge.ts                         # Python subprocess bridge (uv run python -m ...)
    render.ts                         # Terminal formatting: tables, mermaid, markdown
    servers/
      orchestrator.ts                 # Orchestrator MCP server definition
      data.ts                         # Data MCP server definition
      code.ts                         # Code MCP server definition
    tools/
      registry.ts                     # Dynamic tool registry (register_tool impl)

src/polymarket_pipeline/cli/
  bridge.py                           # NEW: JSON dispatcher for TS -> Python calls
```

## Runtime Tool Expansion (`register_tool`)

The agent can create new tools at runtime:

1. Agent writes Python handler to `strategies/{name}/_tools/{tool_name}.py`
2. Agent calls `register_tool` with name, description, schema, handler path
3. `registry.ts` creates a new `tool()` definition and adds it to the code MCP server
4. Tool is immediately available for use in the current session
5. Registration is saved to `conversation_state.json` for cold restart persistence

On cold restart, all registered tools from `conversation_state.json` are re-registered before the session starts.

## Cost Controls

- `maxTurns: 50` per session send (prevents runaway agent)
- `maxBudgetUsd: 10.0` per session (configurable via CLI flag)
- Subagents use sonnet by default (cheaper than opus)
- Hooks require confirmation before expensive operations (run/review/generate)
- `run_python` has a 60s default timeout

## Out of Scope (Future)

- Web UI (Approach C) -- build later by replacing readline with WebSocket server
- Multi-user collaboration
- Automated background exploration (keep using `pm-explore orchestrate` for that)
- Integration with the brainstorm agent (for now, brainstorm runs via the existing orchestrator)
