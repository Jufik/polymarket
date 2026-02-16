#!/usr/bin/env node
/**
 * pm-chat: Interactive REPL for Polymarket strategy exploration.
 *
 * Wires together:
 * - Python bridge (callPython)
 * - Three MCP servers (data, orchestrator, code)
 * - System prompt composition
 * - Session state persistence
 * - Claude Agent SDK query API for multi-turn conversations
 */
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { existsSync } from "node:fs";

import {
  query,
  type Options,
  type SDKMessage,
  type McpServerConfig,
  type AgentDefinition,
} from "@anthropic-ai/claude-agent-sdk";

import { PROJECT_ROOT, STRATEGIES_ROOT } from "./bridge.js";
import { buildSystemPrompt, type ConversationState } from "./prompt.js";
import {
  loadConversationState,
  saveConversationState,
  loadSessionId,
  saveSessionId,
  type ConversationSnapshot,
} from "./session.js";
import { createDataServer } from "./servers/data.js";
import { createOrchestratorServer } from "./servers/orchestrator.js";
import { createCodeServer } from "./servers/code.js";
import { ToolRegistry, type RegisteredToolDef } from "./tools/registry.js";

// ---------------------------------------------------------------------------
// Configuration from environment
// ---------------------------------------------------------------------------
const MODEL = process.env["PM_CHAT_MODEL"] ?? "claude-opus-4-6";
const MAX_TURNS = Number(process.env["PM_CHAT_MAX_TURNS"] ?? "50");
const MAX_BUDGET_USD = Number(process.env["PM_CHAT_MAX_BUDGET"] ?? "10.0");

// ---------------------------------------------------------------------------
// CLI argument parsing
// ---------------------------------------------------------------------------
function parseArgs(): { strategy: string; resume: boolean } {
  const args = process.argv.slice(2);
  let strategy: string | undefined;
  let resume = false;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i]!;
    if (arg === "--resume" || arg === "-r") {
      resume = true;
    } else if (arg === "--help" || arg === "-h") {
      printUsage();
      process.exit(0);
    } else if (!arg.startsWith("-")) {
      strategy = arg;
    }
  }

  if (!strategy) {
    console.error("Error: strategy name is required.\n");
    printUsage();
    process.exit(1);
  }

  // Validate strategy directory exists
  const stratDir = resolve(STRATEGIES_ROOT, strategy);
  if (!existsSync(stratDir)) {
    console.error(`Error: strategy directory not found: ${stratDir}`);
    process.exit(1);
  }

  return { strategy, resume };
}

function printUsage(): void {
  console.log(`Usage: pm-chat <strategy> [--resume]

Arguments:
  strategy       Strategy name (e.g. 'skilled_traders')

Options:
  --resume, -r   Resume previous session for this strategy
  --help, -h     Show this help message

Environment:
  PM_CHAT_MODEL       Model to use (default: claude-opus-4-6)
  PM_CHAT_MAX_TURNS   Max conversation turns (default: 50)
  PM_CHAT_MAX_BUDGET  Max budget in USD (default: 10.0)
  ANTHROPIC_API_KEY   API key for Claude

Commands (during chat):
  /save           Save conversation state
  /status         Show exploration tree status
  /quit, /exit    Exit the REPL`);
}

// ---------------------------------------------------------------------------
// Build SDK options for each query
// ---------------------------------------------------------------------------
function buildQueryOptions(
  strategy: string,
  coldState: ConversationState | null,
  registry: ToolRegistry,
  mcpServers: Record<string, McpServerConfig>,
  continueConversation: boolean,
  sessionId?: string | null,
): Options {
  const systemPrompt = buildSystemPrompt(strategy, coldState);

  const agents: Record<string, AgentDefinition> = {
    analyst: {
      description:
        "Data analyst agent for querying ClickHouse, reading Parquet files, and performing statistical analysis.",
      model: "sonnet",
      prompt:
        `You are a data analyst for the "${strategy}" Polymarket strategy. ` +
        "Use the data MCP tools to query ClickHouse, read Parquet files, and inspect schemas. " +
        "Always use proven SQL components over hand-rolled CTEs. " +
        "Present findings as concise tables.",
      mcpServers: ["data"],
    },
    reviewer: {
      description:
        "Senior reviewer agent for evaluating stage results and orchestrating exploration decisions.",
      model: "opus",
      prompt:
        `You are a senior reviewer for the "${strategy}" Polymarket strategy. ` +
        "Evaluate stage outputs critically. Look for statistical validity issues, " +
        "data leakage, overfitting, and flawed methodology. " +
        "Use orchestrator tools to read stages and data tools to verify claims.",
      mcpServers: ["orchestrator", "data"],
    },
    coder: {
      description:
        "Code generation agent for writing stage scripts, Python analysis, and creating reusable components.",
      model: "sonnet",
      prompt:
        `You are a coder for the "${strategy}" Polymarket strategy. ` +
        "Write Python stage scripts, create reusable components, and register new tools. " +
        "Follow project conventions: use proven SQL components + Polars, " +
        "never hand-roll multi-CTE chains (>2 CTEs).",
      mcpServers: ["code", "data"],
    },
  };

  const opts: Options = {
    model: MODEL,
    systemPrompt,
    maxTurns: MAX_TURNS,
    maxBudgetUsd: MAX_BUDGET_USD,
    permissionMode: "default",
    mcpServers,
    agents,
    cwd: PROJECT_ROOT,
    includePartialMessages: true,
  };

  if (continueConversation) {
    opts.continue = true;
  }

  if (sessionId) {
    opts.resume = sessionId;
  }

  return opts;
}

// ---------------------------------------------------------------------------
// Message rendering
// ---------------------------------------------------------------------------
function renderMessage(msg: SDKMessage): void {
  switch (msg.type) {
    case "assistant": {
      for (const block of msg.message.content) {
        if (block.type === "text") {
          process.stdout.write(block.text);
        }
      }
      break;
    }
    case "stream_event": {
      const event = msg.event;
      if (event.type === "content_block_delta") {
        const delta = event.delta;
        if ("text" in delta && typeof delta.text === "string") {
          process.stdout.write(delta.text);
        }
      }
      break;
    }
    case "result": {
      console.log(); // newline after streaming
      if (msg.subtype === "success") {
        console.log(
          `\n[Cost: $${msg.total_cost_usd.toFixed(4)} | Turns: ${msg.num_turns}]`
        );
      } else {
        console.log(`\n[Session ended: ${msg.subtype}]`);
        if ("errors" in msg && msg.errors.length > 0) {
          for (const err of msg.errors) {
            console.error(`  Error: ${err}`);
          }
        }
      }
      break;
    }
    case "system": {
      if (msg.subtype === "init") {
        console.log(`[Connected: model=${msg.model}, tools=${msg.tools.length}]`);
      }
      break;
    }
    default:
      // Ignore tool_progress, auth_status, etc.
      break;
  }
}

// ---------------------------------------------------------------------------
// Save state helper
// ---------------------------------------------------------------------------
function doSave(
  strategyDir: string,
  strategy: string,
  sessionId: string | null,
  decisionLog: string[],
  registry: ToolRegistry,
  pendingQuestions: string[],
): void {
  const snapshot: ConversationSnapshot = {
    session_id: sessionId ?? "unknown",
    strategy,
    last_active: new Date().toISOString(),
    decision_log: decisionLog,
    registered_tools: registry.serialize(),
    pending_questions: pendingQuestions,
  };
  saveConversationState(strategyDir, snapshot);
  if (sessionId) {
    saveSessionId(strategyDir, sessionId);
  }
  console.log("[State saved]");
}

// ---------------------------------------------------------------------------
// Main REPL
// ---------------------------------------------------------------------------
async function main(): Promise<void> {
  const { strategy, resume } = parseArgs();
  const strategyDir = resolve(STRATEGIES_ROOT, strategy);

  console.log(`pm-chat: Strategy "${strategy}"`);
  console.log(`  Model: ${MODEL}`);
  console.log(`  Max turns: ${MAX_TURNS}`);
  console.log(`  Max budget: $${MAX_BUDGET_USD}`);
  console.log();

  // ---- Load state ----
  const coldState = loadConversationState(strategyDir);
  const savedSessionId = resume ? loadSessionId(strategyDir) : null;

  if (coldState) {
    console.log("[Loaded previous session state]");
    if (coldState.decision_log?.length) {
      console.log(`  Decisions: ${coldState.decision_log.length}`);
    }
    if (coldState.registered_tools?.length) {
      console.log(`  Custom tools: ${coldState.registered_tools.length}`);
    }
  }

  // ---- Build tool registry ----
  const registry = new ToolRegistry();
  if (coldState?.registered_tools?.length) {
    // Cast from JSON-deserialized shape to RegisteredToolDef[]
    registry.loadFrom(coldState.registered_tools as RegisteredToolDef[]);
  }

  // ---- Create MCP servers ----
  const dataServer = createDataServer();
  const orchestratorServer = createOrchestratorServer();
  const codeServer = createCodeServer(registry);

  const mcpServers: Record<string, McpServerConfig> = {
    data: dataServer,
    orchestrator: orchestratorServer,
    code: codeServer,
  };

  // ---- Conversation state ----
  const decisionLog: string[] = coldState?.decision_log ?? [];
  const pendingQuestions: string[] = coldState?.pending_questions ?? [];
  let currentSessionId: string | null = savedSessionId;
  let isFirstMessage = true;

  // ---- Readline interface ----
  const rl = createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: "\npm-chat> ",
  });

  // ---- Handle /commands ----
  function handleCommand(cmd: string): boolean {
    const trimmed = cmd.trim().toLowerCase();
    if (trimmed === "/save") {
      doSave(
        strategyDir,
        strategy,
        currentSessionId,
        decisionLog,
        registry,
        pendingQuestions,
      );
      return true;
    }
    if (trimmed === "/status") {
      console.log("[Fetching tree status...]");
      // Fire and forget async operation, print when done
      import("./bridge.js").then(({ callPython: cp }) =>
        cp("polymarket_pipeline.cli.bridge", "tree_status", {
          strategy,
          format: "text",
        }).then(
          (result) => {
            console.log(result as string);
            rl.prompt();
          },
          (err) => {
            console.error(`Status failed: ${(err as Error).message}`);
            rl.prompt();
          },
        )
      );
      return true;
    }
    if (trimmed === "/quit" || trimmed === "/exit") {
      console.log("[Saving state before exit...]");
      doSave(
        strategyDir,
        strategy,
        currentSessionId,
        decisionLog,
        registry,
        pendingQuestions,
      );
      rl.close();
      return true;
    }
    return false;
  }

  // ---- Process a user message through the SDK ----
  async function processMessage(userInput: string): Promise<void> {
    const opts = buildQueryOptions(
      strategy,
      coldState,
      registry,
      mcpServers,
      !isFirstMessage && !savedSessionId,
      isFirstMessage && savedSessionId ? savedSessionId : undefined,
    );

    const q = query({ prompt: userInput, options: opts });

    try {
      for await (const msg of q) {
        renderMessage(msg);

        // Capture session ID from init message
        if (msg.type === "system" && msg.subtype === "init") {
          currentSessionId = msg.session_id;
        }

        // Track session ID from result
        if (msg.type === "result") {
          currentSessionId = msg.session_id;
        }
      }
    } catch (err) {
      console.error(`\n[Error: ${(err as Error).message}]`);
    }

    isFirstMessage = false;
  }

  // ---- REPL loop ----
  console.log(
    'Type your message and press Enter. Commands: /save, /status, /quit'
  );
  rl.prompt();

  rl.on("line", (line: string) => {
    const input = line.trim();
    if (!input) {
      rl.prompt();
      return;
    }

    // Handle slash commands
    if (input.startsWith("/")) {
      if (handleCommand(input)) {
        if (input.trim().toLowerCase() !== "/status") {
          rl.prompt();
        }
        return;
      }
      // Unknown command, treat as regular message
      console.log(`[Unknown command: ${input}. Sending as message.]`);
    }

    // Process through SDK
    processMessage(input).then(() => {
      rl.prompt();
    });
  });

  rl.on("close", () => {
    console.log("\n[Session ended]");
    process.exit(0);
  });

  // Graceful shutdown
  process.on("SIGINT", () => {
    console.log("\n[Interrupted. Saving state...]");
    doSave(
      strategyDir,
      strategy,
      currentSessionId,
      decisionLog,
      registry,
      pendingQuestions,
    );
    process.exit(0);
  });
}

main().catch((err: unknown) => {
  console.error(`Fatal error: ${(err as Error).message}`);
  process.exit(1);
});
