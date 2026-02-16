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

  // Platform prompt
  const platformPath = resolve(STRATEGIES_ROOT, "_shared", "platform_prompt.json");
  const platform = readPromptJson(platformPath);
  if (platform) parts.push(platform);

  // Strategy prompt
  const strategyPath = resolve(STRATEGIES_ROOT, strategy, "strategy_prompt.json");
  const strat = readPromptJson(strategyPath);
  if (strat) parts.push(strat);

  // Research brief
  const briefPath = resolve(STRATEGIES_ROOT, strategy, "research_brief.json");
  if (existsSync(briefPath)) {
    try {
      const brief = JSON.parse(readFileSync(briefPath, "utf-8"));
      if (brief.open_questions?.length) {
        parts.push(
          "OPEN RESEARCH QUESTIONS:\n" +
            brief.open_questions
              .map((q: string | { question: string }) =>
                typeof q === "string" ? `- ${q}` : `- ${q.question}`
              )
              .join("\n")
        );
      }
    } catch {
      /* skip malformed brief */
    }
  }

  // Conversation role
  parts.push(CONVERSATION_ROLE.replace("this Polymarket strategy", `the "${strategy}" strategy`));

  // Cold state context
  if (coldState) {
    const stateLines: string[] = ["PREVIOUS SESSION CONTEXT:"];
    if (coldState.focus) {
      stateLines.push(
        `Last focus: stage ${coldState.focus.stage_id} (${coldState.focus.mode})`
      );
    }
    if (coldState.decision_log?.length) {
      stateLines.push("Decisions made:");
      for (const d of coldState.decision_log) stateLines.push(`  - ${d}`);
    }
    if (coldState.pending_questions?.length) {
      stateLines.push("Unanswered questions:");
      for (const q of coldState.pending_questions) stateLines.push(`  - ${q}`);
    }
    if (coldState.registered_tools?.length) {
      stateLines.push("Custom tools available:");
      for (const t of coldState.registered_tools)
        stateLines.push(`  - ${t.name}: ${t.description}`);
    }
    parts.push(stateLines.join("\n"));
  }

  return {
    type: "preset" as const,
    preset: "claude_code" as const,
    append: parts.join("\n\n---\n\n"),
  };
}
