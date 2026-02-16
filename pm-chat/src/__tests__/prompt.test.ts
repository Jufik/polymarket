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

  it("includes open research questions from brief", () => {
    const prompt = buildSystemPrompt("skilled_traders");
    expect(prompt.append).toContain("OPEN RESEARCH QUESTIONS");
    expect(prompt.append).toContain("trade timing");
  });

  it("includes focus context when provided", () => {
    const prompt = buildSystemPrompt("skilled_traders", {
      focus: { stage_id: "01a_pnl_based_skill_scoring", mode: "deep-dive" },
    });
    expect(prompt.append).toContain("01a_pnl_based_skill_scoring");
    expect(prompt.append).toContain("deep-dive");
  });

  it("includes registered tools when provided", () => {
    const prompt = buildSystemPrompt("skilled_traders", {
      registered_tools: [{ name: "my_tool", description: "does things" }],
    });
    expect(prompt.append).toContain("my_tool");
    expect(prompt.append).toContain("does things");
  });

  it("handles missing strategy gracefully", () => {
    const prompt = buildSystemPrompt("nonexistent_strategy_xyz");
    // Should still include platform prompt and role
    expect(prompt.append).toContain("CLICKHOUSE SCHEMA");
    expect(prompt.append).toContain("interactive exploration assistant");
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
