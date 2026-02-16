import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdirSync, rmSync, existsSync } from "node:fs";
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
