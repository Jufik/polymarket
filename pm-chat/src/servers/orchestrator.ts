/**
 * Orchestrator MCP server: strategy and stage lifecycle tools.
 *
 * Provides tools to read exploration trees, run/review/generate stages,
 * compare stages, and set directions. Pure-TS tools read files directly;
 * lifecycle tools delegate to Python bridge helpers.
 */
import {
  tool,
  createSdkMcpServer,
} from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython, STRATEGIES_ROOT } from "../bridge.js";
import { resolve } from "node:path";
import { readFileSync, existsSync } from "node:fs";

// ---------------------------------------------------------------------------
// Helper: read a JSON file, return parsed or null
// ---------------------------------------------------------------------------
function readJsonSafe(filePath: string): unknown | null {
  if (!existsSync(filePath)) return null;
  return JSON.parse(readFileSync(filePath, "utf-8"));
}

function readTextSafe(filePath: string): string | null {
  if (!existsSync(filePath)) return null;
  return readFileSync(filePath, "utf-8");
}

// ---------------------------------------------------------------------------
// Tool: listStrategies
// ---------------------------------------------------------------------------
const listStrategies = tool(
  "listStrategies",
  "List all available strategy directories.",
  {},
  async () => {
    try {
      const strategies = await callPython(
        "polymarket_pipeline.cli.bridge",
        "list_strategies",
        {}
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(strategies, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to list strategies: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: readTree
// ---------------------------------------------------------------------------
const readTree = tool(
  "readTree",
  "Read the exploration tree (exploration_tree.json) for a strategy. " +
    "Returns the full tree structure with all stages.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
  },
  async ({ strategy }) => {
    try {
      const treePath = resolve(STRATEGIES_ROOT, strategy, "exploration_tree.json");
      const tree = readJsonSafe(treePath);
      if (tree === null) {
        return {
          content: [
            {
              type: "text" as const,
              text: `Tree not found at ${treePath}`,
            },
          ],
          isError: true,
        };
      }
      return {
        content: [{ type: "text" as const, text: JSON.stringify(tree, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to read tree: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: readState
// ---------------------------------------------------------------------------
const readState = tool(
  "readState",
  "Read orchestrator state and research brief for a strategy. " +
    "Returns orchestrator_state.json and research_brief.json.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
  },
  async ({ strategy }) => {
    try {
      const stratDir = resolve(STRATEGIES_ROOT, strategy);
      const state = readJsonSafe(resolve(stratDir, "orchestrator_state.json"));
      const brief = readJsonSafe(resolve(stratDir, "research_brief.json"));
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ state, brief }, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to read state: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: readStage
// ---------------------------------------------------------------------------
const readStage = tool(
  "readStage",
  "Read stage details: stage.py, summary.json, metrics.json, and analysis.md for a stage.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_id: z.string().describe("Stage directory name (e.g. '01a_pnl_based_skill_scoring')"),
  },
  async ({ strategy, stage_id }) => {
    try {
      const stageDir = resolve(STRATEGIES_ROOT, strategy, "stages", stage_id);
      const script = readTextSafe(resolve(stageDir, "stage.py"));
      const summary = readJsonSafe(resolve(stageDir, "outputs", "summary.json"));
      const metrics = readJsonSafe(resolve(stageDir, "outputs", "metrics.json"));
      const analysis = readTextSafe(resolve(stageDir, "analysis.md"));
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(
              {
                stage_id,
                script: script !== null ? script : undefined,
                summary: summary !== null ? summary : undefined,
                metrics: metrics !== null ? metrics : undefined,
                analysis: analysis !== null ? analysis : undefined,
              },
              null,
              2
            ),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to read stage: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: status
// ---------------------------------------------------------------------------
const status = tool(
  "status",
  "Get exploration tree status as text or mermaid diagram.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    format: z
      .enum(["text", "mermaid"])
      .default("text")
      .describe("Output format: 'text' (default) or 'mermaid'"),
  },
  async ({ strategy, format }) => {
    try {
      const text = await callPython(
        "polymarket_pipeline.cli.bridge",
        "tree_status",
        { strategy, format }
      );
      return {
        content: [{ type: "text" as const, text: text as string }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to get status: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: suggestNext
// ---------------------------------------------------------------------------
const suggestNext = tool(
  "suggestNext",
  "Rank and return top-N suggested refinements for the next exploration step.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    n: z.number().int().positive().default(3).describe("Number of suggestions (default 3)"),
  },
  async ({ strategy, n }) => {
    try {
      const suggestions = await callPython(
        "polymarket_pipeline.cli.bridge",
        "suggest_next",
        { strategy, n }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(suggestions, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to suggest next: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: compareStages
// ---------------------------------------------------------------------------
const compareStages = tool(
  "compareStages",
  "Side-by-side comparison of multiple stages: metrics, analysis, and status.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_ids: z
      .array(z.string())
      .min(1)
      .describe("List of stage IDs to compare"),
  },
  async ({ strategy, stage_ids }) => {
    try {
      const comparison = await callPython(
        "polymarket_pipeline.cli.bridge",
        "compare_stages",
        { strategy, stage_ids }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(comparison, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to compare stages: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: runStage
// ---------------------------------------------------------------------------
const runStage = tool(
  "runStage",
  "Execute a stage script synchronously. This runs the stage.py for the given stage. " +
    "May take several minutes for complex stages.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_id: z.string().describe("Stage ID to run (e.g. '01a_pnl_based_skill_scoring')"),
  },
  async ({ strategy, stage_id }) => {
    try {
      const result = await callPython(
        "polymarket_pipeline.cli.bridge",
        "run_stage_bridge",
        { strategy, stage_id },
        { timeoutMs: 600_000 } // 10 minute timeout
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Stage run failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: reviewStage
// ---------------------------------------------------------------------------
const reviewStage = tool(
  "reviewStage",
  "Have Claude review a completed stage. Produces analysis with insights, " +
    "concerns, data quality issues, and proposed refinements.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_id: z.string().describe("Stage ID to review"),
  },
  async ({ strategy, stage_id }) => {
    try {
      const result = await callPython(
        "polymarket_pipeline.cli.bridge",
        "review_stage_bridge",
        { strategy, stage_id },
        { timeoutMs: 300_000 } // 5 minute timeout
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Stage review failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: generateStage
// ---------------------------------------------------------------------------
const generateStage = tool(
  "generateStage",
  "Generate a new child stage from a parent's proposed refinement. " +
    "Creates the stage directory, generates stage.py via Claude.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    parent_id: z.string().describe("Parent stage ID"),
    refinement_name: z
      .string()
      .describe("Name of the refinement to generate (must match a proposed refinement)"),
  },
  async ({ strategy, parent_id, refinement_name }) => {
    try {
      const result = await callPython(
        "polymarket_pipeline.cli.bridge",
        "generate_stage_bridge",
        { strategy, parent_id, refinement_name },
        { timeoutMs: 300_000 } // 5 minute timeout
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Stage generation failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: setDirection
// ---------------------------------------------------------------------------
const setDirection = tool(
  "setDirection",
  "Override the branch recommendation for a stage. " +
    "Valid directions: continue, pivot, converge, reject.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_id: z.string().describe("Stage ID to update"),
    direction: z
      .enum(["continue", "pivot", "converge", "reject"])
      .describe("New branch recommendation"),
  },
  async ({ strategy, stage_id, direction }) => {
    try {
      const result = await callPython(
        "polymarket_pipeline.cli.bridge",
        "set_direction",
        { strategy, stage_id, direction }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to set direction: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Server factory
// ---------------------------------------------------------------------------
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
