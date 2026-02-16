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

export function loadConversationState(strategyDir: string): ConversationSnapshot | null {
  const path = resolve(strategyDir, STATE_FILE);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

export function saveConversationState(strategyDir: string, snapshot: ConversationSnapshot): void {
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
