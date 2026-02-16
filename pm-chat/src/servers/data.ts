/**
 * Data MCP server: ClickHouse queries, Parquet reading, schema info.
 *
 * Exposes five tools that delegate to Python bridge helpers for actual
 * data operations (ClickHouse connections, Polars reads, etc.).
 */
import {
  tool,
  createSdkMcpServer,
} from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython, STRATEGIES_ROOT } from "../bridge.js";
import { resolve } from "node:path";
import { readdirSync } from "node:fs";

/** Regex that catches SQL mutation keywords at the start of a statement. Used to block writes. */
export const SQL_MUTATION_PATTERN =
  /^\s*(INSERT|DELETE|DROP|ALTER|CREATE|TRUNCATE|UPDATE|REPLACE)\b/i;

// ---------------------------------------------------------------------------
// Tool: queryClickhouse
// ---------------------------------------------------------------------------
const queryClickhouse = tool(
  "queryClickhouse",
  "Execute a read-only SQL query against ClickHouse and return results as JSON rows. " +
    "Mutations (INSERT, DROP, ALTER, etc.) are blocked.",
  {
    sql: z.string().describe("The SQL query to execute (SELECT/SHOW/DESCRIBE only)"),
  },
  async ({ sql }) => {
    if (SQL_MUTATION_PATTERN.test(sql)) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Blocked: mutation detected in SQL. Only read queries are allowed.`,
          },
        ],
        isError: true,
      };
    }
    try {
      const rows = await callPython(
        "polymarket_pipeline.cli.bridge",
        "query_clickhouse",
        { sql }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(rows, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `ClickHouse query failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: getSchema
// ---------------------------------------------------------------------------
const getSchema = tool(
  "getSchema",
  "Get column names, types, and comments for a ClickHouse table.",
  {
    table: z.string().describe("Table name (e.g. 'trades_raw')"),
  },
  async ({ table }) => {
    try {
      const schema = await callPython(
        "polymarket_pipeline.cli.bridge",
        "get_schema",
        { table }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(schema, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Schema fetch failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: readParquet
// ---------------------------------------------------------------------------
const readParquet = tool(
  "readParquet",
  "Read rows from a Parquet file. Returns JSON array of row dicts. " +
    "Use for inspecting stage outputs or data files.",
  {
    path: z.string().describe("Absolute or project-relative path to .parquet file"),
    columns: z
      .array(z.string())
      .optional()
      .describe("Subset of columns to read (default: all)"),
    n_rows: z
      .number()
      .int()
      .positive()
      .default(100)
      .describe("Max rows to return (default 100)"),
  },
  async ({ path, columns, n_rows }) => {
    try {
      const rows = await callPython(
        "polymarket_pipeline.cli.bridge",
        "read_parquet",
        { path, columns: columns ?? null, n_rows }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(rows, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Parquet read failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: readStageOutputs
// ---------------------------------------------------------------------------
const readStageOutputs = tool(
  "readStageOutputs",
  "List output files for a strategy stage. Returns filenames in the outputs/ directory.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_id: z
      .string()
      .describe("Stage directory name (e.g. '01a_pnl_based_skill_scoring')"),
  },
  async ({ strategy, stage_id }) => {
    try {
      const outputsDir = resolve(
        STRATEGIES_ROOT,
        strategy,
        "stages",
        stage_id,
        "outputs"
      );
      const files = readdirSync(outputsDir);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(
              { stage: `${strategy}/${stage_id}`, files },
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
            text: `Failed to list stage outputs: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: describeDf
// ---------------------------------------------------------------------------
const describeDf = tool(
  "describeDf",
  "Describe a Parquet file: shape, dtypes, null counts, and summary statistics.",
  {
    path: z.string().describe("Absolute or project-relative path to .parquet file"),
  },
  async ({ path }) => {
    try {
      const desc = await callPython(
        "polymarket_pipeline.cli.bridge",
        "describe_parquet",
        { path }
      );
      return {
        content: [{ type: "text" as const, text: JSON.stringify(desc, null, 2) }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Describe failed: ${(err as Error).message}`,
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
export function createDataServer() {
  return createSdkMcpServer({
    name: "data",
    version: "0.1.0",
    tools: [queryClickhouse, getSchema, readParquet, readStageOutputs, describeDf],
  });
}
