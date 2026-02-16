import { describe, it, expect } from "vitest";
import { callPython } from "../bridge.js";
import { buildSystemPrompt } from "../prompt.js";
import { createDataServer } from "../servers/data.js";
import { createOrchestratorServer } from "../servers/orchestrator.js";
import { createCodeServer } from "../servers/code.js";
import { ToolRegistry } from "../tools/registry.js";
import {
  loadConversationState,
  saveConversationState,
  loadSessionId,
  saveSessionId,
} from "../session.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

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
  });

  it("system prompt includes all layers", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.type).toBe("preset");
    expect(prompt.preset).toBe("claude_code");
    expect(typeof prompt.append).toBe("string");
    // Should contain conversation role content
    expect(prompt.append).toContain("interactive exploration assistant");
  });

  it("system prompt includes strategy-specific content when available", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    // Should have some strategy-related content (if strategy_prompt.json exists)
    expect(prompt.append.length).toBeGreaterThan(100);
  });

  it("all three MCP servers create successfully", () => {
    const registry = new ToolRegistry();
    const dataServer = createDataServer();
    const orchestratorServer = createOrchestratorServer();
    const codeServer = createCodeServer(registry);

    expect(dataServer).toBeDefined();
    expect(dataServer.type).toBe("sdk");
    expect(dataServer.name).toBe("data");

    expect(orchestratorServer).toBeDefined();
    expect(orchestratorServer.type).toBe("sdk");
    expect(orchestratorServer.name).toBe("orchestrator");

    expect(codeServer).toBeDefined();
    expect(codeServer.type).toBe("sdk");
    expect(codeServer.name).toBe("code");
  });

  it("tool registry round-trips through serialize/loadFrom", () => {
    const registry = new ToolRegistry();
    registry.register({
      name: "test_tool",
      description: "A test tool",
      inputSchema: { query: { type: "string", description: "test param" } },
      pythonHandler: "/tmp/test_handler.py",
    });

    const serialized = registry.serialize();
    expect(serialized).toHaveLength(1);
    expect(serialized[0]!.name).toBe("test_tool");

    const registry2 = new ToolRegistry();
    registry2.loadFrom(serialized);
    expect(registry2.getTools()).toHaveLength(1);
    expect(registry2.serialize()[0]!.name).toBe("test_tool");
  });

  it("session state round-trips through save/load", () => {
    const tmpDir = mkdtempSync(join(tmpdir(), "pm-chat-test-"));
    try {
      // Initially no state
      expect(loadConversationState(tmpDir)).toBeNull();
      expect(loadSessionId(tmpDir)).toBeNull();

      // Save state
      saveConversationState(tmpDir, {
        session_id: "test-session-123",
        strategy: "skilled_traders",
        last_active: new Date().toISOString(),
        decision_log: ["decided to use PnL scoring"],
        registered_tools: [],
        pending_questions: ["What about maker/taker split?"],
      });
      saveSessionId(tmpDir, "test-session-123");

      // Load state
      const loaded = loadConversationState(tmpDir);
      expect(loaded).not.toBeNull();
      expect(loaded!.session_id).toBe("test-session-123");
      expect(loaded!.strategy).toBe("skilled_traders");
      expect(loaded!.decision_log).toContain("decided to use PnL scoring");

      const sessionId = loadSessionId(tmpDir);
      expect(sessionId).toBe("test-session-123");
    } finally {
      rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});
