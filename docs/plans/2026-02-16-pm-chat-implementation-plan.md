# pm-chat Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a TypeScript CLI (`pm-chat`) that provides a conversational REPL for interacting with the Polymarket exploration engine via the Claude Agent SDK V2.

**Architecture:** TypeScript Agent SDK V2 session as conversational frontend, calling into the existing Python exploration engine via three in-process MCP servers (orchestrator, data, code). Python bridge via subprocess (`uv run python -m polymarket_pipeline.cli.bridge`).

**Tech Stack:** TypeScript, Node 22, `@anthropic-ai/claude-agent-sdk`, `zod`, Python 3.14 (existing engine)

**Design Doc:** `docs/plans/2026-02-16-conversational-exploration-shell-design.md`

---

## Task 1: Initialize TypeScript Project

**Files:**
- Create: `pm-chat/package.json`
- Create: `pm-chat/tsconfig.json`
- Create: `pm-chat/.gitignore`

**Step 1: Create package.json**

```json
{
  "name": "pm-chat",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "bin": {
    "pm-chat": "./dist/index.js"
  },
  "scripts": {
    "build": "tsc",
    "dev": "tsx src/index.ts",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@anthropic-ai/claude-agent-sdk": "^0.1.58",
    "zod": "^3.24.0"
  },
  "devDependencies": {
    "tsx": "^4.19.0",
    "typescript": "^5.7.0",
    "vitest": "^3.0.0",
    "@types/node": "^22.0.0"
  }
}
```

**Step 2: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "Node16",
    "moduleResolution": "Node16",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": true,
    "sourceMap": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

**Step 3: Create .gitignore**

```
node_modules/
dist/
*.tsbuildinfo
```

**Step 4: Install dependencies**

Run: `cd pm-chat && npm install`
Expected: `node_modules/` created, `package-lock.json` generated

**Step 5: Verify TypeScript compiles**

Create a minimal `pm-chat/src/index.ts`:
```typescript
console.log("pm-chat placeholder");
```

Run: `cd pm-chat && npx tsc --noEmit`
Expected: No errors

**Step 6: Commit**

```bash
git add pm-chat/
git commit -m "feat(pm-chat): initialize TypeScript project with SDK deps"
```

---

## Task 2: Python Bridge (`bridge.py`)

The single new Python file that dispatches JSON-in/JSON-out calls from TypeScript.

**Files:**
- Create: `src/polymarket_pipeline/cli/bridge.py`
- Create: `tests/test_bridge.py`

**Step 1: Write the failing test**

```python
# tests/test_bridge.py
"""Tests for the TypeScript -> Python bridge dispatcher."""
import json
import subprocess
import sys


def _run_bridge(module: str, func: str, args: dict) -> dict:
    """Call bridge.py as subprocess, return parsed JSON output."""
    result = subprocess.run(
        [
            sys.executable, "-m", "polymarket_pipeline.cli.bridge",
            "--module", module,
            "--func", func,
            "--args", json.dumps(args),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Bridge failed: {result.stderr}"
    return json.loads(result.stdout)


def test_bridge_dispatches_sync_function():
    """Bridge can call a sync function and return JSON."""
    # json.dumps is a sync function: json.dumps({"hello": "world"})
    result = _run_bridge("json", "dumps", {"obj": {"hello": "world"}})
    assert result == {"hello": "world"}


def test_bridge_returns_error_on_missing_module():
    """Bridge returns non-zero exit code for missing module."""
    result = subprocess.run(
        [
            sys.executable, "-m", "polymarket_pipeline.cli.bridge",
            "--module", "nonexistent_module_xyz",
            "--func", "foo",
            "--args", "{}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0


def test_bridge_returns_error_on_missing_function():
    """Bridge returns non-zero exit code for missing function."""
    result = subprocess.run(
        [
            sys.executable, "-m", "polymarket_pipeline.cli.bridge",
            "--module", "json",
            "--func", "nonexistent_func_xyz",
            "--args", "{}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0


def test_bridge_handles_list_strategies():
    """Bridge can list strategy directories."""
    result = _run_bridge(
        "polymarket_pipeline.cli.bridge",
        "list_strategies",
        {},
    )
    assert isinstance(result, list)
    assert "skilled_traders" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bridge.py -x -v`
Expected: FAIL — module `polymarket_pipeline.cli.bridge` not found

**Step 3: Implement bridge.py**

```python
# src/polymarket_pipeline/cli/bridge.py
"""JSON-in/JSON-out dispatcher for TypeScript -> Python calls.

Usage:
    python -m polymarket_pipeline.cli.bridge \
        --module polymarket_pipeline.exploration.lifecycle \
        --func run_stage \
        --args '{"strategy": "skilled_traders", "stage_id": "01a_..."}'

Outputs JSON to stdout. Errors go to stderr with non-zero exit code.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path


STRATEGIES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "strategies"


def list_strategies() -> list[str]:
    """List all strategy directories."""
    return sorted(
        d.name
        for d in STRATEGIES_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    )


def read_json_file(path: str) -> dict | list | None:
    """Read and parse a JSON file."""
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def read_text_file(path: str) -> str | None:
    """Read a text file."""
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text()


def main() -> None:
    parser = argparse.ArgumentParser(description="TS->Python bridge")
    parser.add_argument("--module", required=True, help="Python module path")
    parser.add_argument("--func", required=True, help="Function name")
    parser.add_argument("--args", default="{}", help="JSON-encoded kwargs")
    args = parser.parse_args()

    try:
        # Allow calling bridge's own helpers (list_strategies, read_json_file, etc.)
        if args.module == "polymarket_pipeline.cli.bridge":
            func = globals()[args.func]
        else:
            mod = importlib.import_module(args.module)
            func = getattr(mod, args.func)

        kwargs = json.loads(args.args)
        result = func(**kwargs)

        # Handle async functions
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)

        json.dump(result, sys.stdout, default=str)
    except Exception as e:
        print(f"Bridge error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bridge.py -x -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/polymarket_pipeline/cli/bridge.py tests/test_bridge.py
git commit -m "feat(pm-chat): add Python bridge for TS->Python subprocess calls"
```

---

## Task 3: TypeScript Bridge Client

The TS side that calls `bridge.py` and parses responses.

**Files:**
- Create: `pm-chat/src/bridge.ts`
- Create: `pm-chat/src/__tests__/bridge.test.ts`

**Step 1: Write the failing test**

```typescript
// pm-chat/src/__tests__/bridge.test.ts
import { describe, it, expect } from "vitest";
import { callPython } from "../bridge.js";

describe("callPython", () => {
  it("calls a sync Python function and returns parsed JSON", async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "list_strategies",
      {}
    );
    expect(Array.isArray(result)).toBe(true);
    expect(result).toContain("skilled_traders");
  });

  it("reads a JSON file via bridge", async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "read_json_file",
      { path: "strategies/skilled_traders/exploration_tree.json" }
    );
    expect(result).toHaveProperty("strategy_name");
    expect(result.strategy_name).toBe("skilled_traders");
  });

  it("throws on missing module", async () => {
    await expect(
      callPython("nonexistent_xyz", "foo", {})
    ).rejects.toThrow();
  });

  it("throws on missing function", async () => {
    await expect(
      callPython("json", "nonexistent_xyz", {})
    ).rejects.toThrow();
  });

  it("respects timeout", async () => {
    // time.sleep(10) should exceed a 2s timeout
    await expect(
      callPython("time", "sleep", { secs: 10 }, { timeoutMs: 2000 })
    ).rejects.toThrow(/timeout|timed out/i);
  }, 5000);
});
```

**Step 2: Run test to verify it fails**

Run: `cd pm-chat && npx vitest run src/__tests__/bridge.test.ts`
Expected: FAIL — `callPython` not found

**Step 3: Implement bridge.ts**

```typescript
// pm-chat/src/bridge.ts
import { spawn } from "node:child_process";
import { resolve } from "node:path";

/** Root of the polymarket project (parent of pm-chat/) */
export const PROJECT_ROOT = resolve(import.meta.dirname, "../..");

/** Root of strategy directories */
export const STRATEGIES_ROOT = resolve(PROJECT_ROOT, "strategies");

export interface CallPythonOptions {
  timeoutMs?: number;
  cwd?: string;
}

/**
 * Call a Python function via bridge.py subprocess.
 * Returns parsed JSON output.
 */
export async function callPython(
  module: string,
  func: string,
  args: Record<string, unknown>,
  options: CallPythonOptions = {}
): Promise<unknown> {
  const { timeoutMs = 120_000, cwd = PROJECT_ROOT } = options;

  return new Promise((resolve, reject) => {
    const proc = spawn(
      "uv",
      [
        "run",
        "python",
        "-m",
        "polymarket_pipeline.cli.bridge",
        "--module",
        module,
        "--func",
        func,
        "--args",
        JSON.stringify(args),
      ],
      { cwd, stdio: ["ignore", "pipe", "pipe"] }
    );

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (data: Buffer) => {
      stdout += data.toString();
    });
    proc.stderr.on("data", (data: Buffer) => {
      stderr += data.toString();
    });

    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error(`Python bridge timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(
          new Error(
            `Python bridge exited with code ${code}: ${stderr.trim()}`
          )
        );
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error(`Failed to parse bridge output: ${stdout.slice(0, 200)}`));
      }
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Failed to spawn Python bridge: ${err.message}`));
    });
  });
}
```

**Step 4: Run tests to verify they pass**

Run: `cd pm-chat && npx vitest run src/__tests__/bridge.test.ts`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add pm-chat/src/bridge.ts pm-chat/src/__tests__/bridge.test.ts
git commit -m "feat(pm-chat): add TypeScript bridge client for Python subprocess calls"
```

---

## Task 4: Data MCP Server

The simplest MCP server — direct data access tools that don't need complex lifecycle management.

**Files:**
- Create: `pm-chat/src/servers/data.ts`
- Create: `pm-chat/src/__tests__/data-server.test.ts`

**Step 1: Write the failing test**

```typescript
// pm-chat/src/__tests__/data-server.test.ts
import { describe, it, expect } from "vitest";
import { createDataServer, SQL_MUTATION_PATTERN } from "../servers/data.js";

describe("SQL_MUTATION_PATTERN", () => {
  it("blocks INSERT statements", () => {
    expect(SQL_MUTATION_PATTERN.test("INSERT INTO foo VALUES (1)")).toBe(true);
  });
  it("blocks DROP statements", () => {
    expect(SQL_MUTATION_PATTERN.test("DROP TABLE foo")).toBe(true);
  });
  it("allows SELECT statements", () => {
    expect(SQL_MUTATION_PATTERN.test("SELECT * FROM foo")).toBe(false);
  });
  it("allows SHOW statements", () => {
    expect(SQL_MUTATION_PATTERN.test("SHOW CREATE TABLE foo")).toBe(false);
  });
  it("blocks case-insensitive", () => {
    expect(SQL_MUTATION_PATTERN.test("insert into foo values (1)")).toBe(true);
  });
});

describe("createDataServer", () => {
  it("returns an MCP server object", () => {
    const server = createDataServer();
    expect(server).toBeDefined();
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd pm-chat && npx vitest run src/__tests__/data-server.test.ts`
Expected: FAIL — `createDataServer` not found

**Step 3: Implement data.ts**

```typescript
// pm-chat/src/servers/data.ts
import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython, STRATEGIES_ROOT } from "../bridge.js";
import { resolve } from "node:path";

export const SQL_MUTATION_PATTERN =
  /\b(INSERT|DELETE|DROP|ALTER|CREATE|TRUNCATE|UPDATE|REPLACE)\b/i;

const queryClickhouse = tool(
  "query_clickhouse",
  "Execute a read-only SQL query against ClickHouse. Returns JSON rows (max 10K). Mutation queries (INSERT/DELETE/DROP/ALTER/CREATE/TRUNCATE) are blocked.",
  {
    sql: z.string().describe("SQL query to execute"),
  },
  async (args) => {
    if (SQL_MUTATION_PATTERN.test(args.sql)) {
      return {
        content: [
          {
            type: "text" as const,
            text: "ERROR: Mutation queries are blocked. Only SELECT/SHOW/DESCRIBE/WITH allowed.",
          },
        ],
      };
    }
    const limitedSql = args.sql.includes("LIMIT")
      ? args.sql
      : `${args.sql} LIMIT 10000`;
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "query_clickhouse",
      { sql: limitedSql }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const getSchema = tool(
  "get_schema",
  "Get column names, types, and comments for a ClickHouse table",
  {
    table: z.string().describe("Table name (e.g. 'trades_raw', 'markets')"),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "get_schema",
      { table: args.table }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const readParquet = tool(
  "read_parquet",
  "Read a Parquet file and return as JSON. Uses Python/Polars.",
  {
    path: z.string().describe("Path to .parquet file (relative to project root)"),
    columns: z.array(z.string()).optional().describe("Subset of columns to read"),
    n_rows: z.number().optional().describe("Max rows to read (default 100)"),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "read_parquet",
      { path: args.path, columns: args.columns, n_rows: args.n_rows ?? 100 }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const readStageOutputs = tool(
  "read_stage_outputs",
  "Read a specific output file from a stage (summary.json, metrics.json, or text files)",
  {
    strategy: z.string(),
    stage_id: z.string(),
    file: z.string().describe("Filename within outputs/ directory"),
  },
  async (args) => {
    const path = resolve(
      STRATEGIES_ROOT,
      args.strategy,
      "stages",
      args.stage_id,
      "outputs",
      args.file
    );
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "read_json_file",
      { path }
    );
    return {
      content: [
        { type: "text" as const, text: result ? JSON.stringify(result, null, 2) : "File not found" },
      ],
    };
  }
);

const describeDf = tool(
  "describe_df",
  "Describe a Parquet file: shape, dtypes, null counts, quantiles for numeric columns",
  {
    strategy: z.string(),
    stage_id: z.string(),
    file: z.string().describe("Parquet filename within outputs/ directory"),
  },
  async (args) => {
    const path = resolve(
      STRATEGIES_ROOT,
      args.strategy,
      "stages",
      args.stage_id,
      "outputs",
      args.file
    );
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "describe_parquet",
      { path }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

export function createDataServer() {
  return createSdkMcpServer({
    name: "data",
    version: "0.1.0",
    tools: [queryClickhouse, getSchema, readParquet, readStageOutputs, describeDf],
  });
}
```

**Step 4: Add Python bridge helpers for data tools**

Add to `src/polymarket_pipeline/cli/bridge.py`:

```python
def query_clickhouse(sql: str) -> list[dict]:
    """Execute read-only SQL, return list of dicts."""
    from polymarket_pipeline.exploration.data import ExplorationDataSource
    db = ExplorationDataSource()
    return db.query_raw(sql)


def get_schema(table: str) -> list[dict]:
    """Get schema for a ClickHouse table."""
    from polymarket_pipeline.exploration.data import ExplorationDataSource
    db = ExplorationDataSource()
    return db.get_schema(table)


def read_parquet(path: str, columns: list[str] | None = None, n_rows: int = 100) -> list[dict]:
    """Read Parquet file, return as list of dicts."""
    import polars as pl
    df = pl.read_parquet(path, columns=columns, n_rows=n_rows)
    return df.to_dicts()


def describe_parquet(path: str) -> dict:
    """Describe a Parquet file: shape, dtypes, null counts, quantiles."""
    import polars as pl
    df = pl.read_parquet(path)
    return {
        "shape": {"rows": df.height, "columns": df.width},
        "dtypes": {col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)},
        "null_counts": {col: df[col].null_count() for col in df.columns},
        "describe": df.describe().to_dicts(),
    }
```

**Step 5: Run tests**

Run: `cd pm-chat && npx vitest run src/__tests__/data-server.test.ts`
Expected: PASS

**Step 6: Commit**

```bash
git add pm-chat/src/servers/data.ts pm-chat/src/__tests__/data-server.test.ts src/polymarket_pipeline/cli/bridge.py
git commit -m "feat(pm-chat): add data MCP server with ClickHouse/Parquet tools"
```

---

## Task 5: Orchestrator MCP Server

Strategy and stage lifecycle tools.

**Files:**
- Create: `pm-chat/src/servers/orchestrator.ts`
- Create: `pm-chat/src/__tests__/orchestrator-server.test.ts`

**Step 1: Write the failing test**

```typescript
// pm-chat/src/__tests__/orchestrator-server.test.ts
import { describe, it, expect } from "vitest";
import { createOrchestratorServer } from "../servers/orchestrator.js";

describe("createOrchestratorServer", () => {
  it("returns an MCP server object", () => {
    const server = createOrchestratorServer();
    expect(server).toBeDefined();
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd pm-chat && npx vitest run src/__tests__/orchestrator-server.test.ts`
Expected: FAIL

**Step 3: Implement orchestrator.ts**

```typescript
// pm-chat/src/servers/orchestrator.ts
import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython, STRATEGIES_ROOT } from "../bridge.js";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

function readStrategyJson(strategy: string, filename: string): unknown {
  const path = resolve(STRATEGIES_ROOT, strategy, filename);
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf-8"));
}

const listStrategies = tool(
  "list_strategies",
  "List all strategy directories in the strategies/ folder",
  {},
  async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "list_strategies",
      {}
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const readTree = tool(
  "read_tree",
  "Read the full exploration tree for a strategy. Returns all stages with status, metrics, analysis, refinements.",
  { strategy: z.string() },
  async (args) => {
    const tree = readStrategyJson(args.strategy, "exploration_tree.json");
    return { content: [{ type: "text" as const, text: tree ? JSON.stringify(tree, null, 2) : "Tree not found" }] };
  }
);

const readState = tool(
  "read_state",
  "Read orchestrator state and research brief for a strategy",
  { strategy: z.string() },
  async (args) => {
    const state = readStrategyJson(args.strategy, "orchestrator_state.json");
    const brief = readStrategyJson(args.strategy, "research_brief.json");
    return {
      content: [
        { type: "text" as const, text: JSON.stringify({ state, brief }, null, 2) },
      ],
    };
  }
);

const readStage = tool(
  "read_stage",
  "Read full details for a specific stage: script source, outputs/summary.json, analysis.md, metrics.json",
  {
    strategy: z.string(),
    stage_id: z.string(),
  },
  async (args) => {
    const stageDir = resolve(STRATEGIES_ROOT, args.strategy, "stages", args.stage_id);
    const result: Record<string, unknown> = {};

    const scriptPath = resolve(stageDir, "stage.py");
    if (existsSync(scriptPath)) {
      result.script = readFileSync(scriptPath, "utf-8");
    }

    const summaryPath = resolve(stageDir, "outputs", "summary.json");
    if (existsSync(summaryPath)) {
      result.summary = JSON.parse(readFileSync(summaryPath, "utf-8"));
    }

    const metricsPath = resolve(stageDir, "outputs", "metrics.json");
    if (existsSync(metricsPath)) {
      result.metrics = JSON.parse(readFileSync(metricsPath, "utf-8"));
    }

    const analysisPath = resolve(stageDir, "analysis.md");
    if (existsSync(analysisPath)) {
      result.analysis = readFileSync(analysisPath, "utf-8");
    }

    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const status = tool(
  "status",
  "Show exploration tree visualization as text or Mermaid diagram",
  {
    strategy: z.string(),
    format: z.enum(["text", "mermaid"]).default("text").optional(),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "tree_status",
      { strategy: args.strategy, format: args.format ?? "text" }
    );
    return { content: [{ type: "text" as const, text: String(result) }] };
  }
);

const suggestNext = tool(
  "suggest_next",
  "Get ranked suggestions for next exploration stages based on tree state",
  {
    strategy: z.string(),
    n: z.number().default(3).optional(),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "suggest_next",
      { strategy: args.strategy, n: args.n ?? 3 }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const compareStages = tool(
  "compare_stages",
  "Side-by-side comparison of metrics, key insights, and confidence for multiple stages",
  {
    strategy: z.string(),
    stage_ids: z.array(z.string()).min(2),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "compare_stages",
      { strategy: args.strategy, stage_ids: args.stage_ids }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const runStage = tool(
  "run_stage",
  "Execute a stage script. This runs Python code and may take minutes. Confirm with the user first.",
  {
    strategy: z.string(),
    stage_id: z.string(),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "run_stage_bridge",
      { strategy: args.strategy, stage_id: args.stage_id },
      { timeoutMs: 600_000 }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const reviewStage = tool(
  "review_stage",
  "Have Claude review a completed stage's outputs. Produces analysis.md with insights and refinement proposals.",
  {
    strategy: z.string(),
    stage_id: z.string(),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "review_stage_bridge",
      { strategy: args.strategy, stage_id: args.stage_id },
      { timeoutMs: 300_000 }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const generateStage = tool(
  "generate_stage",
  "Generate a new child stage from a parent's proposed refinement. Creates stage.py script.",
  {
    strategy: z.string(),
    parent_id: z.string(),
    refinement_name: z.string(),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "generate_stage_bridge",
      {
        strategy: args.strategy,
        parent_id: args.parent_id,
        refinement_name: args.refinement_name,
      },
      { timeoutMs: 300_000 }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  }
);

const setDirection = tool(
  "set_direction",
  "Override the branch recommendation for a stage (continue, pivot, converge, or reject)",
  {
    strategy: z.string(),
    stage_id: z.string(),
    direction: z.enum(["continue", "pivot", "converge", "reject"]),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "set_direction",
      {
        strategy: args.strategy,
        stage_id: args.stage_id,
        direction: args.direction,
      }
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(result) }] };
  }
);

export function createOrchestratorServer() {
  return createSdkMcpServer({
    name: "orchestrator",
    version: "0.1.0",
    tools: [
      listStrategies,
      readTree,
      readState,
      readStage,
      status,
      suggestNext,
      compareStages,
      runStage,
      reviewStage,
      generateStage,
      setDirection,
    ],
  });
}
```

**Step 4: Add Python bridge helpers for orchestrator tools**

Append to `src/polymarket_pipeline/cli/bridge.py`:

```python
def tree_status(strategy: str, format: str = "text") -> str:
    """Get exploration tree visualization."""
    from polymarket_pipeline.exploration.lifecycle import load_tree
    tree = load_tree(strategy)
    if format == "mermaid":
        return tree.to_mermaid()
    # Text summary
    lines = []
    for stage in tree.stages.values():
        indent = "  " * stage.depth
        status_icon = {"completed": "+", "failed": "x", "running": "~",
                       "reviewing": "?", "pending": ".", "archived": "-",
                       "paused": "!"}.get(stage.status.value, "?")
        conf = ""
        if stage.analysis:
            conf = f" (conf: {stage.analysis.confidence:.0%})"
        lines.append(f"{indent}[{status_icon}] {stage.id}: {stage.name}{conf}")
    return "\n".join(lines)


def suggest_next(strategy: str, n: int = 3) -> list[dict]:
    """Rank and return top-N suggested refinements."""
    from polymarket_pipeline.exploration.lifecycle import load_tree
    from polymarket_pipeline.exploration.agent import suggest_next_stages
    from polymarket_pipeline.exploration.tree import build_exploration_context
    tree = load_tree(strategy)
    context = build_exploration_context(tree)
    suggestions, filtered = suggest_next_stages(tree, max_suggestions=n, context=context)
    return [
        {
            "parent_id": parent.id,
            "refinement_name": ref.name,
            "description": ref.description,
            "hypothesis": ref.hypothesis,
            "priority": ref.priority,
            "complexity": ref.estimated_complexity,
        }
        for parent, ref in suggestions
    ]


def compare_stages(strategy: str, stage_ids: list[str]) -> list[dict]:
    """Side-by-side comparison of stages."""
    from polymarket_pipeline.exploration.lifecycle import load_tree
    tree = load_tree(strategy)
    results = []
    for sid in stage_ids:
        stage = tree.get_stage(sid)
        if not stage:
            results.append({"stage_id": sid, "error": "not found"})
            continue
        entry: dict = {
            "stage_id": sid,
            "name": stage.name,
            "status": stage.status.value,
            "depth": stage.depth,
        }
        if stage.metrics:
            entry["metrics"] = stage.metrics.model_dump(exclude_none=True)
        if stage.analysis:
            entry["confidence"] = stage.analysis.confidence
            entry["info_gain"] = stage.analysis.information_gain
            entry["key_insights"] = stage.analysis.key_insights[:3]
            entry["branch_rec"] = stage.analysis.branch_recommendation.value if stage.analysis.branch_recommendation else None
        results.append(entry)
    return results


def run_stage_bridge(strategy: str, stage_id: str) -> dict:
    """Execute a stage script synchronously."""
    from polymarket_pipeline.exploration.lifecycle import load_tree, run_stage
    tree = load_tree(strategy)
    stage = tree.get_stage(stage_id)
    if not stage:
        return {"success": False, "error": f"Stage {stage_id} not found"}
    result = run_stage(strategy, tree, stage)
    return {"success": result.success, "error": result.error}


async def review_stage_bridge(strategy: str, stage_id: str) -> dict:
    """Claude review of a completed stage."""
    from polymarket_pipeline.exploration.lifecycle import (
        load_tree, review_stage_lifecycle,
    )
    from polymarket_pipeline.exploration.tree import build_exploration_context
    tree = load_tree(strategy)
    stage = tree.get_stage(stage_id)
    if not stage:
        return {"success": False, "error": f"Stage {stage_id} not found"}
    context = build_exploration_context(tree)
    result = await review_stage_lifecycle(strategy, tree, stage, context=context)
    resp: dict = {"success": result.success, "error": result.error}
    if result.analysis:
        resp["confidence"] = result.analysis.confidence
        resp["summary"] = result.analysis.summary
        resp["has_critical_dq"] = result.has_critical_dq_issues
    return resp


async def generate_stage_bridge(
    strategy: str, parent_id: str, refinement_name: str
) -> dict:
    """Generate a new child stage."""
    from polymarket_pipeline.exploration.lifecycle import (
        load_tree, generate_stage_lifecycle,
    )
    tree = load_tree(strategy)
    parent = tree.get_stage(parent_id)
    if not parent:
        return {"success": False, "error": f"Parent {parent_id} not found"}
    if not parent.analysis:
        return {"success": False, "error": f"Parent {parent_id} has no analysis"}
    ref = next(
        (r for r in parent.analysis.proposed_refinements if r.name == refinement_name),
        None,
    )
    if not ref:
        return {
            "success": False,
            "error": f"Refinement '{refinement_name}' not found in {parent_id}",
        }
    result = await generate_stage_lifecycle(strategy, tree, parent, ref)
    return {
        "success": result.success,
        "stage_id": result.stage.id,
        "error": result.error,
    }


def set_direction(strategy: str, stage_id: str, direction: str) -> dict:
    """Override branch recommendation for a stage."""
    from polymarket_pipeline.exploration.lifecycle import load_tree, save_tree
    from polymarket_pipeline.exploration.tree import BranchRecommendation
    tree = load_tree(strategy)
    stage = tree.get_stage(stage_id)
    if not stage:
        return {"success": False, "error": f"Stage {stage_id} not found"}
    if not stage.analysis:
        return {"success": False, "error": f"Stage {stage_id} has no analysis"}
    stage.analysis.branch_recommendation = BranchRecommendation(direction)
    save_tree(strategy, tree)
    return {"success": True, "stage_id": stage_id, "direction": direction}
```

**Step 5: Run tests**

Run: `cd pm-chat && npx vitest run src/__tests__/orchestrator-server.test.ts`
Expected: PASS

**Step 6: Commit**

```bash
git add pm-chat/src/servers/orchestrator.ts pm-chat/src/__tests__/orchestrator-server.test.ts src/polymarket_pipeline/cli/bridge.py
git commit -m "feat(pm-chat): add orchestrator MCP server with lifecycle tools"
```

---

## Task 6: Code MCP Server (with register_tool)

**Files:**
- Create: `pm-chat/src/servers/code.ts`
- Create: `pm-chat/src/tools/registry.ts`
- Create: `pm-chat/src/__tests__/code-server.test.ts`

**Step 1: Write the failing test**

```typescript
// pm-chat/src/__tests__/code-server.test.ts
import { describe, it, expect } from "vitest";
import { createCodeServer } from "../servers/code.js";
import { ToolRegistry } from "../tools/registry.js";

describe("ToolRegistry", () => {
  it("starts empty", () => {
    const registry = new ToolRegistry();
    expect(registry.getTools()).toEqual([]);
  });

  it("registers a tool and returns it", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "test_tool",
      description: "A test tool",
      inputSchema: { value: { type: "string" } },
      pythonHandler: "path/to/handler.py",
    });
    expect(registry.getTools()).toHaveLength(1);
  });

  it("serializes for persistence", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "test_tool",
      description: "A test tool",
      inputSchema: { value: { type: "string" } },
      pythonHandler: "path/to/handler.py",
    });
    const serialized = registry.serialize();
    expect(serialized).toHaveLength(1);
    expect(serialized[0].name).toBe("test_tool");
  });
});

describe("createCodeServer", () => {
  it("returns an MCP server object", () => {
    const registry = new ToolRegistry();
    const server = createCodeServer(registry);
    expect(server).toBeDefined();
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd pm-chat && npx vitest run src/__tests__/code-server.test.ts`
Expected: FAIL

**Step 3: Implement registry.ts**

```typescript
// pm-chat/src/tools/registry.ts
import { tool } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython } from "../bridge.js";
import type { SdkMcpToolDefinition } from "@anthropic-ai/claude-agent-sdk";

export interface RegisteredToolDef {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  pythonHandler: string;
}

export class ToolRegistry {
  private tools: Map<string, { def: RegisteredToolDef; mcpTool: SdkMcpToolDefinition }> =
    new Map();

  register(def: RegisteredToolDef): SdkMcpToolDefinition {
    // Build a Zod schema from the simple type definitions
    const zodShape: Record<string, z.ZodTypeAny> = {};
    for (const [key, spec] of Object.entries(def.inputSchema)) {
      const s = spec as { type: string; description?: string };
      let zType: z.ZodTypeAny;
      switch (s.type) {
        case "number":
          zType = z.number();
          break;
        case "boolean":
          zType = z.boolean();
          break;
        default:
          zType = z.string();
      }
      if (s.description) zType = zType.describe(s.description);
      zodShape[key] = zType;
    }

    const mcpTool = tool(
      def.name,
      def.description,
      zodShape,
      async (args) => {
        const result = await callPython(
          "polymarket_pipeline.cli.bridge",
          "run_registered_tool",
          { handler_path: def.pythonHandler, args }
        );
        return {
          content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    this.tools.set(def.name, { def, mcpTool });
    return mcpTool;
  }

  getTools(): SdkMcpToolDefinition[] {
    return Array.from(this.tools.values()).map((t) => t.mcpTool);
  }

  serialize(): RegisteredToolDef[] {
    return Array.from(this.tools.values()).map((t) => t.def);
  }

  loadFrom(defs: RegisteredToolDef[]): void {
    for (const def of defs) {
      this.register(def);
    }
  }
}
```

**Step 4: Implement code.ts**

```typescript
// pm-chat/src/servers/code.ts
import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython, STRATEGIES_ROOT } from "../bridge.js";
import { ToolRegistry } from "../tools/registry.js";
import { writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";

const runPython = tool(
  "run_python",
  "Execute arbitrary Python code in a subprocess. Has access to polars, numpy, ExplorationDataSource, and components. Returns stdout.",
  {
    code: z.string().describe("Python code to execute"),
    timeout_s: z.number().default(60).optional(),
  },
  async (args) => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "run_python_code",
      { code: args.code },
      { timeoutMs: (args.timeout_s ?? 60) * 1000 }
    );
    return { content: [{ type: "text" as const, text: String(result) }] };
  }
);

const editStageScript = tool(
  "edit_stage_script",
  "Overwrite a stage's stage.py with new code",
  {
    strategy: z.string(),
    stage_id: z.string(),
    code: z.string().describe("Full Python source code for stage.py"),
  },
  async (args) => {
    const path = resolve(
      STRATEGIES_ROOT,
      args.strategy,
      "stages",
      args.stage_id,
      "stage.py"
    );
    writeFileSync(path, args.code, "utf-8");
    return {
      content: [{ type: "text" as const, text: `Written ${args.code.length} bytes to ${path}` }],
    };
  }
);

const createComponent = tool(
  "create_component",
  "Write a new reusable component to exploration/components/",
  {
    name: z.string().describe("Component filename (without .py)"),
    code: z.string().describe("Full Python source code"),
  },
  async (args) => {
    const path = resolve(
      STRATEGIES_ROOT,
      "..",
      "src",
      "polymarket_pipeline",
      "exploration",
      "components",
      `${args.name}.py`
    );
    writeFileSync(path, args.code, "utf-8");
    return {
      content: [{ type: "text" as const, text: `Written component to ${path}` }],
    };
  }
);

export function createCodeServer(registry: ToolRegistry) {
  const registerTool = tool(
    "register_tool",
    "Register a new MCP tool at runtime backed by a Python handler script. The tool becomes available immediately.",
    {
      name: z.string().describe("Tool name (snake_case)"),
      description: z.string().describe("What the tool does"),
      input_schema: z
        .record(z.object({ type: z.string(), description: z.string().optional() }))
        .describe("Input parameter definitions: { param_name: { type, description } }"),
      python_handler: z.string().describe("Python handler code. Must define a run(**kwargs) function."),
      strategy: z.string().describe("Strategy to save the tool handler in"),
    },
    async (args) => {
      // Write handler to disk
      const toolsDir = resolve(STRATEGIES_ROOT, args.strategy, "_tools");
      mkdirSync(toolsDir, { recursive: true });
      const handlerPath = resolve(toolsDir, `${args.name}.py`);
      writeFileSync(handlerPath, args.python_handler, "utf-8");

      // Register in runtime registry
      registry.register({
        name: args.name,
        description: args.description,
        inputSchema: args.input_schema,
        pythonHandler: handlerPath,
      });

      return {
        content: [
          {
            type: "text" as const,
            text: `Tool "${args.name}" registered. Handler at ${handlerPath}. Available as mcp__code__${args.name}.`,
          },
        ],
      };
    }
  );

  return createSdkMcpServer({
    name: "code",
    version: "0.1.0",
    tools: [runPython, editStageScript, createComponent, registerTool, ...registry.getTools()],
  });
}
```

**Step 5: Add Python bridge helpers**

Append to `src/polymarket_pipeline/cli/bridge.py`:

```python
def run_python_code(code: str) -> str:
    """Execute Python code in a subprocess and capture stdout."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(STRATEGIES_ROOT.parent),
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]: {result.stderr}"
    if result.returncode != 0:
        output += f"\n[exit code: {result.returncode}]"
    return output


def run_registered_tool(handler_path: str, args: dict) -> object:
    """Execute a registered tool's Python handler."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tool_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run(**args)
```

**Step 6: Run tests**

Run: `cd pm-chat && npx vitest run src/__tests__/code-server.test.ts`
Expected: PASS

**Step 7: Commit**

```bash
git add pm-chat/src/servers/code.ts pm-chat/src/tools/registry.ts pm-chat/src/__tests__/code-server.test.ts src/polymarket_pipeline/cli/bridge.py
git commit -m "feat(pm-chat): add code MCP server with runtime tool registration"
```

---

## Task 7: System Prompt Composition

**Files:**
- Create: `pm-chat/src/prompt.ts`
- Create: `pm-chat/src/__tests__/prompt.test.ts`

**Step 1: Write the failing test**

```typescript
// pm-chat/src/__tests__/prompt.test.ts
import { describe, it, expect } from "vitest";
import { buildSystemPrompt, CONVERSATION_ROLE } from "../prompt.js";

describe("buildSystemPrompt", () => {
  it("includes platform prompt content", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.append).toContain("CLICKHOUSE SCHEMA");
  });

  it("includes strategy prompt content", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.append).toContain("STRATEGY CONSTRAINTS");
  });

  it("includes conversation role", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.append).toContain("interactive exploration assistant");
  });

  it("includes cold state context when provided", () => {
    const prompt = buildSystemPrompt("skilled_traders", {
      decision_log: ["rejected bot filter"],
    });
    expect(prompt.append).toContain("rejected bot filter");
  });

  it("uses claude_code preset", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.type).toBe("preset");
    expect(prompt.preset).toBe("claude_code");
  });
});

describe("CONVERSATION_ROLE", () => {
  it("mentions interaction modes", () => {
    expect(CONVERSATION_ROLE).toContain("Strategy overview");
    expect(CONVERSATION_ROLE).toContain("Stage deep-dive");
    expect(CONVERSATION_ROLE).toContain("Data exploration");
    expect(CONVERSATION_ROLE).toContain("Tool creation");
  });
});
```

**Step 2: Run to verify failure**

Run: `cd pm-chat && npx vitest run src/__tests__/prompt.test.ts`
Expected: FAIL

**Step 3: Implement prompt.ts**

```typescript
// pm-chat/src/prompt.ts
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { STRATEGIES_ROOT } from "./bridge.js";

export const CONVERSATION_ROLE = `You are an interactive exploration assistant for this Polymarket strategy.
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
- Never hand-roll multi-CTE chains (>2 CTEs). Use components + Polars.`;

function readPromptJson(path: string): string {
  if (!existsSync(path)) return "";
  try {
    const data = JSON.parse(readFileSync(path, "utf-8"));
    return data.content ?? JSON.stringify(data, null, 2);
  } catch {
    return "";
  }
}

export interface ConversationState {
  decision_log?: string[];
  focus?: { stage_id: string; mode: string };
  pending_questions?: string[];
  registered_tools?: Array<{ name: string; description: string }>;
}

export function buildSystemPrompt(
  strategy: string,
  coldState?: ConversationState | null
) {
  const parts: string[] = [];

  // Platform prompt (shared schema, SQL patterns, hard constraints)
  const platformPath = resolve(STRATEGIES_ROOT, "_shared", "platform_prompt.json");
  const platform = readPromptJson(platformPath);
  if (platform) parts.push(platform);

  // Strategy prompt (per-strategy MUST/PREFER learnings)
  const strategyPath = resolve(STRATEGIES_ROOT, strategy, "strategy_prompt.json");
  const strat = readPromptJson(strategyPath);
  if (strat) parts.push(strat);

  // Research brief summary
  const briefPath = resolve(STRATEGIES_ROOT, strategy, "research_brief.json");
  if (existsSync(briefPath)) {
    try {
      const brief = JSON.parse(readFileSync(briefPath, "utf-8"));
      if (brief.open_questions?.length) {
        parts.push(
          "OPEN RESEARCH QUESTIONS:\n" +
            brief.open_questions.map((q: { question: string }) => `- ${q.question}`).join("\n")
        );
      }
    } catch {
      // skip if malformed
    }
  }

  // Conversation role
  parts.push(CONVERSATION_ROLE.replace("this Polymarket strategy", `the "${strategy}" strategy`));

  // Cold state context (for session resume)
  if (coldState) {
    const stateLines: string[] = ["PREVIOUS SESSION CONTEXT:"];
    if (coldState.focus) {
      stateLines.push(`Last focus: stage ${coldState.focus.stage_id} (${coldState.focus.mode})`);
    }
    if (coldState.decision_log?.length) {
      stateLines.push("Decisions made:");
      for (const d of coldState.decision_log) {
        stateLines.push(`  - ${d}`);
      }
    }
    if (coldState.pending_questions?.length) {
      stateLines.push("Unanswered questions:");
      for (const q of coldState.pending_questions) {
        stateLines.push(`  - ${q}`);
      }
    }
    if (coldState.registered_tools?.length) {
      stateLines.push("Custom tools available:");
      for (const t of coldState.registered_tools) {
        stateLines.push(`  - ${t.name}: ${t.description}`);
      }
    }
    parts.push(stateLines.join("\n"));
  }

  return {
    type: "preset" as const,
    preset: "claude_code" as const,
    append: parts.join("\n\n---\n\n"),
  };
}
```

**Step 4: Run tests**

Run: `cd pm-chat && npx vitest run src/__tests__/prompt.test.ts`
Expected: PASS

**Step 5: Commit**

```bash
git add pm-chat/src/prompt.ts pm-chat/src/__tests__/prompt.test.ts
git commit -m "feat(pm-chat): add system prompt composition from existing prompt layers"
```

---

## Task 8: Session Management

**Files:**
- Create: `pm-chat/src/session.ts`
- Create: `pm-chat/src/__tests__/session.test.ts`

**Step 1: Write the failing test**

```typescript
// pm-chat/src/__tests__/session.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { writeFileSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import {
  loadConversationState,
  saveConversationState,
  loadSessionId,
  saveSessionId,
  type ConversationSnapshot,
} from "../session.js";

const TEST_DIR = resolve(import.meta.dirname, "__test_session_tmp__");

beforeEach(() => {
  mkdirSync(TEST_DIR, { recursive: true });
});

afterEach(() => {
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true });
});

describe("conversation state", () => {
  it("returns null when no state file exists", () => {
    const state = loadConversationState(TEST_DIR);
    expect(state).toBeNull();
  });

  it("round-trips state to disk", () => {
    const snapshot: ConversationSnapshot = {
      session_id: "test-123",
      strategy: "skilled_traders",
      last_active: new Date().toISOString(),
      focus: { stage_id: "01a", mode: "analysis" },
      decision_log: ["rejected bot filter"],
      registered_tools: [],
      pending_questions: [],
    };
    saveConversationState(TEST_DIR, snapshot);
    const loaded = loadConversationState(TEST_DIR);
    expect(loaded).toEqual(snapshot);
  });
});

describe("session ID", () => {
  it("returns null when no session file exists", () => {
    expect(loadSessionId(TEST_DIR)).toBeNull();
  });

  it("round-trips session ID", () => {
    saveSessionId(TEST_DIR, "abc-456");
    expect(loadSessionId(TEST_DIR)).toBe("abc-456");
  });
});
```

**Step 2: Run to verify failure**

Run: `cd pm-chat && npx vitest run src/__tests__/session.test.ts`
Expected: FAIL

**Step 3: Implement session.ts**

```typescript
// pm-chat/src/session.ts
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

export interface ConversationSnapshot {
  session_id: string;
  strategy: string;
  last_active: string;
  focus?: { stage_id: string; mode: string };
  decision_log: string[];
  registered_tools: Array<{
    name: string;
    description: string;
    inputSchema: Record<string, unknown>;
    pythonHandler: string;
  }>;
  pending_questions: string[];
}

const STATE_FILE = "conversation_state.json";
const SESSION_FILE = "chat_session.json";

export function loadConversationState(
  strategyDir: string
): ConversationSnapshot | null {
  const path = resolve(strategyDir, STATE_FILE);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

export function saveConversationState(
  strategyDir: string,
  snapshot: ConversationSnapshot
): void {
  const path = resolve(strategyDir, STATE_FILE);
  writeFileSync(path, JSON.stringify(snapshot, null, 2), "utf-8");
}

export function loadSessionId(strategyDir: string): string | null {
  const path = resolve(strategyDir, SESSION_FILE);
  if (!existsSync(path)) return null;
  try {
    const data = JSON.parse(readFileSync(path, "utf-8"));
    return data.session_id ?? null;
  } catch {
    return null;
  }
}

export function saveSessionId(strategyDir: string, sessionId: string): void {
  const path = resolve(strategyDir, SESSION_FILE);
  writeFileSync(
    path,
    JSON.stringify({ session_id: sessionId, saved_at: new Date().toISOString() }, null, 2),
    "utf-8"
  );
}
```

**Step 4: Run tests**

Run: `cd pm-chat && npx vitest run src/__tests__/session.test.ts`
Expected: PASS

**Step 5: Commit**

```bash
git add pm-chat/src/session.ts pm-chat/src/__tests__/session.test.ts
git commit -m "feat(pm-chat): add session state persistence (hot + cold)"
```

---

## Task 9: Main CLI Entry Point (REPL Loop)

**Files:**
- Create: `pm-chat/src/index.ts`

**Step 1: Implement index.ts**

This is the integration point — it wires everything together. No unit test for this file; we test it manually end-to-end.

```typescript
// pm-chat/src/index.ts
import {
  unstable_v2_createSession,
  unstable_v2_resumeSession,
} from "@anthropic-ai/claude-agent-sdk";
import { createInterface } from "node:readline/promises";
import { resolve } from "node:path";
import { STRATEGIES_ROOT } from "./bridge.js";
import { buildSystemPrompt } from "./prompt.js";
import { createDataServer } from "./servers/data.js";
import { createOrchestratorServer } from "./servers/orchestrator.js";
import { createCodeServer } from "./servers/code.js";
import { ToolRegistry } from "./tools/registry.js";
import {
  loadConversationState,
  saveConversationState,
  loadSessionId,
  saveSessionId,
  type ConversationSnapshot,
} from "./session.js";

const STRATEGY = process.argv[2];
if (!STRATEGY) {
  console.error("Usage: pm-chat <strategy_name>");
  console.error("Example: pm-chat skilled_traders");
  process.exit(1);
}

const MODEL = process.env.PM_CHAT_MODEL ?? "claude-opus-4-6";
const MAX_TURNS = parseInt(process.env.PM_CHAT_MAX_TURNS ?? "50", 10);
const MAX_BUDGET = parseFloat(process.env.PM_CHAT_MAX_BUDGET ?? "10.0");

const strategyDir = resolve(STRATEGIES_ROOT, STRATEGY);

// Load state
const coldState = loadConversationState(strategyDir);
const savedSessionId = loadSessionId(strategyDir);

// Build tool registry (restore registered tools from cold state)
const registry = new ToolRegistry();
if (coldState?.registered_tools) {
  registry.loadFrom(coldState.registered_tools);
}

// Build MCP servers
const orchestratorServer = createOrchestratorServer();
const dataServer = createDataServer();
const codeServer = createCodeServer(registry);

// Build system prompt
const systemPrompt = buildSystemPrompt(
  STRATEGY,
  savedSessionId ? null : coldState // Only inject cold state if no hot session
);

// Session options
const sessionOptions = {
  model: MODEL,
  systemPrompt,
  mcpServers: {
    orchestrator: orchestratorServer,
    data: dataServer,
    code: codeServer,
  },
  maxTurns: MAX_TURNS,
  maxBudgetUsd: MAX_BUDGET,
  permissionMode: "default" as const,
  agents: {
    analyst: {
      description:
        "Data analyst for focused ClickHouse queries and Polars transforms. Use for quick data questions.",
      prompt:
        "You are a quantitative analyst. Answer data questions using the data MCP tools. Be concise.",
      tools: [
        "mcp__data__query_clickhouse",
        "mcp__data__get_schema",
        "mcp__data__read_parquet",
        "mcp__data__describe_df",
      ],
      model: "sonnet" as const,
    },
    reviewer: {
      description:
        "Stage reviewer for deep analysis of completed stages. Use when asked to review or analyze a stage.",
      prompt:
        "You are an expert reviewer. Read stage outputs and provide detailed analysis. Use orchestrator read tools + data tools.",
      tools: [
        "mcp__orchestrator__read_tree",
        "mcp__orchestrator__read_stage",
        "mcp__orchestrator__read_state",
        "mcp__data__query_clickhouse",
        "mcp__data__get_schema",
      ],
      model: "opus" as const,
    },
    coder: {
      description:
        "Code writer for creating stage scripts, components, and ad-hoc Python. Use when asked to write or modify code.",
      prompt:
        "You are a Python developer. Write exploration stage scripts and reusable components. Use code + data tools.",
      tools: [
        "mcp__code__run_python",
        "mcp__code__edit_stage_script",
        "mcp__code__create_component",
        "mcp__code__register_tool",
        "mcp__data__query_clickhouse",
        "mcp__data__get_schema",
      ],
      model: "sonnet" as const,
    },
  },
};

async function main() {
  console.log(`\npm-chat: ${STRATEGY}`);
  console.log(`Model: ${MODEL} | Max turns: ${MAX_TURNS} | Budget: $${MAX_BUDGET}\n`);

  // Create or resume session
  let session;
  try {
    if (savedSessionId) {
      console.log(`Resuming session ${savedSessionId.slice(0, 8)}...`);
      session = await unstable_v2_resumeSession(savedSessionId, sessionOptions);
    } else {
      console.log("Starting new session...");
      session = await unstable_v2_createSession(sessionOptions);
    }
  } catch (err) {
    // Hot resume failed, fall back to new session
    console.log("Session resume failed, starting fresh...");
    session = await unstable_v2_createSession(sessionOptions);
  }

  saveSessionId(strategyDir, session.sessionId);
  console.log(`Session: ${session.sessionId.slice(0, 8)}...\n`);

  // REPL loop
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const decisionLog: string[] = coldState?.decision_log ?? [];

  // Auto-send initial message for new sessions
  let firstMessage =
    savedSessionId === null
      ? "Read the current exploration tree state and give me a brief overview."
      : null;

  try {
    while (true) {
      const userInput =
        firstMessage ?? (await rl.question("\n> "));
      firstMessage = null;

      if (!userInput.trim()) continue;
      if (userInput === "exit" || userInput === "quit") break;

      if (userInput === "/save") {
        const snapshot: ConversationSnapshot = {
          session_id: session.sessionId,
          strategy: STRATEGY,
          last_active: new Date().toISOString(),
          decision_log: decisionLog,
          registered_tools: registry.serialize(),
          pending_questions: [],
        };
        saveConversationState(strategyDir, snapshot);
        console.log("State saved.");
        continue;
      }

      // Track decisions
      if (
        userInput.toLowerCase().startsWith("reject") ||
        userInput.toLowerCase().startsWith("focus") ||
        userInput.toLowerCase().startsWith("create")
      ) {
        decisionLog.push(userInput);
      }

      // Send to Claude
      try {
        const response = await session.send(userInput);
        for await (const message of response.stream()) {
          if (message.type === "assistant") {
            // Extract text content
            const content = message.message.content;
            if (Array.isArray(content)) {
              for (const block of content) {
                if (block.type === "text") {
                  process.stdout.write(block.text);
                }
              }
            }
          }
          if (message.type === "result") {
            if (message.is_error) {
              console.error(`\n[Error: ${message.result}]`);
            }
            console.log(
              `\n[${message.num_turns} turns, $${message.total_cost_usd?.toFixed(4) ?? "?"}, ${message.duration_ms}ms]`
            );
          }
        }
      } catch (err) {
        console.error(`\nSession error: ${err}`);
      }
    }
  } finally {
    // Save state on exit
    const snapshot: ConversationSnapshot = {
      session_id: session.sessionId,
      strategy: STRATEGY,
      last_active: new Date().toISOString(),
      decision_log: decisionLog,
      registered_tools: registry.serialize(),
      pending_questions: [],
    };
    saveConversationState(strategyDir, snapshot);
    saveSessionId(strategyDir, session.sessionId);
    console.log("\nSession saved. Resume with: pm-chat " + STRATEGY);
    await session.close();
    rl.close();
  }
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
```

**Step 2: Build and verify**

Run: `cd pm-chat && npx tsc --noEmit`
Expected: No type errors

**Step 3: Commit**

```bash
git add pm-chat/src/index.ts
git commit -m "feat(pm-chat): add main REPL entry point with session management"
```

---

## Task 10: End-to-End Smoke Test

**Files:**
- Create: `pm-chat/src/__tests__/e2e.test.ts`

**Step 1: Write E2E test**

```typescript
// pm-chat/src/__tests__/e2e.test.ts
import { describe, it, expect } from "vitest";
import { callPython } from "../bridge.js";
import { buildSystemPrompt } from "../prompt.js";
import { createDataServer } from "../servers/data.js";
import { createOrchestratorServer } from "../servers/orchestrator.js";
import { createCodeServer } from "../servers/code.js";
import { ToolRegistry } from "../tools/registry.js";

describe("e2e: component wiring", () => {
  it("bridge can list strategies", async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "list_strategies",
      {}
    );
    expect(result).toContain("skilled_traders");
  });

  it("bridge can get tree status", async () => {
    const result = await callPython(
      "polymarket_pipeline.cli.bridge",
      "tree_status",
      { strategy: "skilled_traders", format: "text" }
    );
    expect(typeof result).toBe("string");
    expect(result).toContain("00_initial");
  });

  it("system prompt includes all layers", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.append).toContain("CLICKHOUSE SCHEMA");
    expect(prompt.append).toContain("STRATEGY CONSTRAINTS");
    expect(prompt.append).toContain("interactive exploration assistant");
  });

  it("all three MCP servers create successfully", () => {
    const registry = new ToolRegistry();
    expect(createDataServer()).toBeDefined();
    expect(createOrchestratorServer()).toBeDefined();
    expect(createCodeServer(registry)).toBeDefined();
  });
});
```

**Step 2: Run full test suite**

Run: `cd pm-chat && npx vitest run`
Expected: All tests PASS

**Step 3: Manual smoke test**

Run: `cd pm-chat && npx tsx src/index.ts skilled_traders`
Expected: Session starts, auto-sends "Read the current exploration tree state...", Claude responds with strategy overview.
Type `exit` to close.

**Step 4: Final commit**

```bash
git add pm-chat/src/__tests__/e2e.test.ts
git commit -m "feat(pm-chat): add e2e smoke tests for component wiring"
```

---

## Summary

| Task | What | Files | Est. Size |
|------|------|-------|-----------|
| 1 | Init TS project | `pm-chat/package.json`, `tsconfig.json` | Small |
| 2 | Python bridge | `bridge.py`, `test_bridge.py` | Medium |
| 3 | TS bridge client | `bridge.ts`, `bridge.test.ts` | Small |
| 4 | Data MCP server | `data.ts`, `data-server.test.ts` | Medium |
| 5 | Orchestrator MCP server | `orchestrator.ts`, `orchestrator-server.test.ts` | Large |
| 6 | Code MCP server + registry | `code.ts`, `registry.ts`, `code-server.test.ts` | Medium |
| 7 | System prompt composition | `prompt.ts`, `prompt.test.ts` | Small |
| 8 | Session management | `session.ts`, `session.test.ts` | Small |
| 9 | Main REPL entry | `index.ts` | Medium |
| 10 | E2E smoke test | `e2e.test.ts` | Small |
