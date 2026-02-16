/**
 * Code MCP server: Python execution, stage editing, component creation,
 * and runtime tool registration.
 *
 * Provides four built-in tools plus any dynamically registered tools
 * from the ToolRegistry.
 */
import {
  tool,
  createSdkMcpServer,
} from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython, PROJECT_ROOT, STRATEGIES_ROOT } from "../bridge.js";
import { resolve, dirname } from "node:path";
import { writeFileSync, mkdirSync } from "node:fs";
import type { ToolRegistry } from "../tools/registry.js";

// ---------------------------------------------------------------------------
// Tool: runPython
// ---------------------------------------------------------------------------
const runPython = tool(
  "runPython",
  "Execute Python code in a subprocess and return stdout/stderr. " +
    "Code runs with uv in the project environment. Use for ad-hoc analysis, " +
    "data exploration, or testing snippets.",
  {
    code: z.string().describe("Python code to execute"),
    timeout_seconds: z
      .number()
      .int()
      .positive()
      .default(60)
      .describe("Max execution time in seconds (default 60)"),
  },
  async ({ code, timeout_seconds }) => {
    try {
      const result = await callPython(
        "polymarket_pipeline.cli.bridge",
        "run_python_code",
        { code },
        { timeoutMs: timeout_seconds * 1000 }
      );
      return {
        content: [{ type: "text" as const, text: result as string }],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Python execution failed: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: editStageScript
// ---------------------------------------------------------------------------
const editStageScript = tool(
  "editStageScript",
  "Write or overwrite the stage.py script for a strategy stage. " +
    "Creates the stage directory and outputs/ subdirectory if they don't exist.",
  {
    strategy: z.string().describe("Strategy name (e.g. 'skilled_traders')"),
    stage_id: z
      .string()
      .describe("Stage directory name (e.g. '01a_pnl_based_skill_scoring')"),
    code: z.string().describe("Full Python source code for stage.py"),
  },
  async ({ strategy, stage_id, code }) => {
    try {
      const stageDir = resolve(STRATEGIES_ROOT, strategy, "stages", stage_id);
      const outputsDir = resolve(stageDir, "outputs");
      mkdirSync(outputsDir, { recursive: true });

      const scriptPath = resolve(stageDir, "stage.py");
      writeFileSync(scriptPath, code, "utf-8");

      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ success: true, path: scriptPath }, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to write stage script: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool: createComponent
// ---------------------------------------------------------------------------
const createComponent = tool(
  "createComponent",
  "Write a new reusable component to the exploration/components/ directory. " +
    "Components are Python modules with shared SQL builders and analytics logic.",
  {
    name: z
      .string()
      .describe("Component filename without .py extension (e.g. 'my_component')"),
    code: z.string().describe("Full Python source code for the component"),
  },
  async ({ name, code }) => {
    try {
      const componentsDir = resolve(
        PROJECT_ROOT,
        "src",
        "polymarket_pipeline",
        "exploration",
        "components"
      );
      mkdirSync(componentsDir, { recursive: true });

      const filePath = resolve(componentsDir, `${name}.py`);
      writeFileSync(filePath, code, "utf-8");

      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ success: true, path: filePath }, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Failed to create component: ${(err as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Tool factory: registerTool (needs registry reference)
// ---------------------------------------------------------------------------
function makeRegisterTool(registry: ToolRegistry) {
  return tool(
    "registerTool",
    "Register a new tool at runtime. Writes a Python handler file to the " +
      "_tools/ directory and registers it so it becomes immediately available " +
      "as an MCP tool for subsequent calls.",
    {
      name: z.string().describe("Tool name (snake_case)"),
      description: z.string().describe("Human-readable tool description"),
      input_schema: z
        .record(
          z.object({
            type: z.string().describe("Parameter type: string, number, boolean, array, object"),
            description: z.string().optional().describe("Parameter description"),
          })
        )
        .describe("Input parameters as { paramName: { type, description? } }"),
      handler_code: z
        .string()
        .describe(
          "Python source code for the handler. Must define a run(**kwargs) function."
        ),
    },
    async ({ name, description, input_schema, handler_code }) => {
      try {
        // Write handler to _tools/ directory
        const toolsDir = resolve(PROJECT_ROOT, "_tools");
        mkdirSync(toolsDir, { recursive: true });
        const handlerPath = resolve(toolsDir, `${name}.py`);
        writeFileSync(handlerPath, handler_code, "utf-8");

        // Register in the runtime registry
        registry.register({
          name,
          description,
          inputSchema: input_schema,
          pythonHandler: handlerPath,
        });

        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify(
                {
                  success: true,
                  tool_name: name,
                  handler_path: handlerPath,
                  message: `Tool '${name}' registered. It is now available for use.`,
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
              text: `Failed to register tool: ${(err as Error).message}`,
            },
          ],
          isError: true,
        };
      }
    }
  );
}

// ---------------------------------------------------------------------------
// Server factory
// ---------------------------------------------------------------------------
export function createCodeServer(registry: ToolRegistry) {
  return createSdkMcpServer({
    name: "code",
    version: "0.1.0",
    tools: [
      runPython,
      editStageScript,
      createComponent,
      makeRegisterTool(registry),
      ...registry.getTools(),
    ],
  });
}
