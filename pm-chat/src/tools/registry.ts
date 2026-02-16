/**
 * ToolRegistry: runtime-expandable tool registration for the Code MCP server.
 *
 * Allows the agent to create new tools during a conversation. Each registered
 * tool stores a reference to a Python handler file that is invoked via the
 * bridge when the tool is called.
 *
 * Supports serialize() for persistence and loadFrom() for cold restart restore.
 */
import { tool } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { callPython } from "../bridge.js";
import type { SdkMcpToolDefinition, AnyZodRawShape } from "@anthropic-ai/claude-agent-sdk";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Serialized form of a registered tool (JSON-safe). */
export interface RegisteredToolDef {
  name: string;
  description: string;
  /** Simple JSON schema map: { paramName: { type: "string" | "number" | "boolean" } } */
  inputSchema: Record<string, { type: string; description?: string }>;
  /** Absolute path to the Python handler file. */
  pythonHandler: string;
}

// ---------------------------------------------------------------------------
// Schema conversion: simple JSON type descriptors -> Zod shapes
// ---------------------------------------------------------------------------

function jsonTypeToZod(typeName: string): z.ZodTypeAny {
  switch (typeName) {
    case "string":
      return z.string();
    case "number":
      return z.number();
    case "boolean":
      return z.boolean();
    case "array":
      return z.array(z.unknown());
    case "object":
      return z.record(z.unknown());
    default:
      return z.unknown();
  }
}

function buildZodShape(
  schema: Record<string, { type: string; description?: string }>
): AnyZodRawShape {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const [key, def] of Object.entries(schema)) {
    let field = jsonTypeToZod(def.type);
    if (def.description) {
      field = field.describe(def.description);
    }
    shape[key] = field;
  }
  return shape;
}

// ---------------------------------------------------------------------------
// ToolRegistry
// ---------------------------------------------------------------------------

export class ToolRegistry {
  private definitions: RegisteredToolDef[] = [];
  private sdkTools: SdkMcpToolDefinition<AnyZodRawShape>[] = [];

  /** Register a new tool. Throws if name is already registered. */
  register(def: RegisteredToolDef): void {
    if (this.definitions.some((d) => d.name === def.name)) {
      throw new Error(`Tool '${def.name}' is already registered`);
    }
    this.definitions.push(def);
    this.sdkTools.push(this.buildSdkTool(def));
  }

  /** Get all registered SDK tool definitions (for MCP server). */
  getTools(): SdkMcpToolDefinition<AnyZodRawShape>[] {
    return this.sdkTools;
  }

  /** Serialize all tool definitions for JSON persistence. */
  serialize(): RegisteredToolDef[] {
    return [...this.definitions];
  }

  /** Restore from serialized data. Replaces any existing tools. */
  loadFrom(defs: RegisteredToolDef[]): void {
    this.definitions = [];
    this.sdkTools = [];
    for (const def of defs) {
      this.definitions.push(def);
      this.sdkTools.push(this.buildSdkTool(def));
    }
  }

  // -----------------------------------------------------------------------
  // Private
  // -----------------------------------------------------------------------

  private buildSdkTool(
    def: RegisteredToolDef
  ): SdkMcpToolDefinition<AnyZodRawShape> {
    const zodShape = buildZodShape(def.inputSchema);
    return tool(
      def.name,
      def.description,
      zodShape,
      async (args) => {
        try {
          const result = await callPython(
            "polymarket_pipeline.cli.bridge",
            "run_registered_tool",
            { handler_path: def.pythonHandler, args }
          );
          return {
            content: [
              { type: "text" as const, text: JSON.stringify(result, null, 2) },
            ],
          };
        } catch (err) {
          return {
            content: [
              {
                type: "text" as const,
                text: `Tool '${def.name}' failed: ${(err as Error).message}`,
              },
            ],
            isError: true,
          };
        }
      }
    );
  }
}
