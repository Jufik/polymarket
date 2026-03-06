---
name: research-knowledge
description: Load, parse, and enrich the research knowledge base before any quantitative research, strategy exploration, or backtesting task. Use PROACTIVELY when context involves trading strategies, market data analysis, or simulation.
---

# Research Knowledge Orchestrator

## When to Use

ALWAYS invoke this skill before:
- Exploring a new trading signal or strategy
- Running any backtest (vectorized or tick-by-tick)
- Analyzing market data or trader behavior
- Comparing simulation results across methods
- Implementing strategy code that touches positions, resolution, or consensus
- Reviewing or interpreting backtest results

## Phase 1: Load Knowledge (Parallel Agent Dispatch)

Use the `Agent` tool with `subagent_type=Explore` to load knowledge categories in parallel.
Dispatch **one agent per relevant category** — do not load sequentially.

```
# Dispatch pattern: one parallel message with multiple Agent calls
Agent(subagent_type="Explore", prompt="Read all .md files in research/knowledge/pitfalls/ ...")
Agent(subagent_type="Explore", prompt="Read all .md files in research/knowledge/data/ ...")
Agent(subagent_type="Explore", prompt="Read all .md files in research/knowledge/execution/ ...")
```

### Category Selection Matrix

| Your Task | Load Categories | Priority Entries |
|-----------|----------------|-----------------|
| Signal discovery | `data/` + `pitfalls/` + `signals/` | base_rates, vectorized_vs_tick |
| Backtest (vectorized) | `pitfalls/` + `execution/` | vectorized_vs_tick, hold_time_capital |
| Backtest (tick-by-tick) | `pitfalls/` + `execution/` | sell_is_exit, consensus_dedup, position_settlement |
| Strategy implementation | `pitfalls/` + `execution/` + `signals/` | All critical entries |
| Result interpretation | `pitfalls/` + `data/` | vectorized_vs_tick, base_rates |
| New research session | ALL categories | Start with critical entries |

### Critical Entries (Always Load First)

These prevent the costliest mistakes. Each has an admonition severity:

1. `pitfalls/vectorized_vs_tick.md` — **[CRITICAL]** Vectorized is 20-40pp optimistic
2. `pitfalls/sell_is_exit.md` — **[WARNING]** SELL is directional but ambiguous (exit vs split-entry) — test include/exclude
3. `pitfalls/consensus_dedup.md` — **[CRITICAL]** Count unique traders, not events
4. `data/resolution_mechanics.md` — **[CRITICAL]** Use asset_id, never strings
5. `execution/hold_time_capital.md` — **[WARNING]** Long markets kill capital efficiency
6. `execution/position_settlement.md` — **[WARNING]** Must settle mid-sim to free capital

## Phase 2: Parse Admonitions

Knowledge entries use GitHub-flavored admonitions for severity-tagged takeaways.
When reading entries, **extract and surface all admonitions to the main context**.

### Admonition Format

```markdown
> [!CRITICAL]
> One-line rule that MUST be followed. Violating this invalidates results.

> [!WARNING]
> Important caveat. Ignoring this leads to misleading but not catastrophically wrong results.

> [!TIP]
> Optimization or best practice. Improves quality but not strictly required.

> [!NOTE]
> Context or background. Helpful for understanding but no action needed.
```

### Severity Semantics

| Level | Meaning | Agent Action |
|-------|---------|--------------|
| `CRITICAL` | Violating this produces garbage results | **STOP** if violated. Fix before proceeding. |
| `WARNING` | Results will be biased or misleading | Flag to user. Apply correction if possible. |
| `TIP` | Quality improvement opportunity | Apply when practical. Note in output. |
| `NOTE` | Background context | Absorb. No action required. |

### Parsing Rules

When loading a knowledge entry:
1. Extract ALL `> [!LEVEL]` blocks from the entry
2. Group by severity: CRITICAL first, then WARNING, TIP, NOTE
3. Surface CRITICAL and WARNING admonitions in your response to the user
4. Check CRITICAL admonitions against your current approach — if any are violated, flag immediately

### Cross-Reference Tags

Each knowledge entry ends with a `## Tags` section. Use tags to find related entries:
- If you load an entry tagged `consensus`, also check entries tagged `dedup` or `signal-quality`
- If you load an entry tagged `capital`, also check `hold-time` and `position-lifecycle`
- If you load an entry tagged `critical`, it contains a CRITICAL admonition — always load it

## Phase 3: Knowledge Enrichment (During Research)

As you work through a research task, **actively detect when new knowledge should be captured**.

### Surprise Detection Heuristics

Flag a finding for knowledge capture when ANY of these triggers fire:

| Trigger | Example | Action |
|---------|---------|--------|
| **Result contradicts expectation** | "YES HR 45% but base rate is 38%" | Capture as `data/` or `signals/` entry |
| **Result contradicts existing knowledge** | "Vectorized matched tick-by-tick" | Update existing entry or add caveat |
| **Magnitude surprise** | "Filter removed 72% of trades" | Capture the filter's impact |
| **New failure mode** | "Strategy breaks on multi-outcome markets" | Capture as `pitfalls/` entry |
| **Performance cliff** | "HR drops from 87% to 34% with one parameter change" | Capture parameter sensitivity |
| **Data quality issue** | "15% of resolved markets have no winner" | Capture as `data/` entry |
| **Cross-strategy insight** | "Capital constraint applies to all copy strategies" | Generalize and capture in `execution/` |

### Enrichment Workflow

When a surprise is detected:

1. **Check existing knowledge**: Does an entry already cover this? Search by tags.
2. **If exists**: Update the existing entry with new evidence or a new admonition.
3. **If new**: Create a new entry following the template below.
4. **Cross-reference**: Add tags that link to related entries.
5. **Notify**: Tell the user what you captured and why.

### New Entry Template

```markdown
# Title (Concise, Descriptive)

> **TL;DR**: One sentence. What did we learn?

> [!CRITICAL or WARNING or TIP]
> The key actionable takeaway from this finding.

## Finding

What we learned (2-5 sentences). Include concrete numbers.

## Evidence

SQL query or Python script that proves this.
Reference `queries/<name>.sql` for reusable CH queries.

## Impact

How this affects strategy design or simulation accuracy.
Bullet list of concrete actions.

## Related

- `pitfalls/related_entry.md` — how it connects
- `data/another_entry.md` — shared context

## Tags

`tag1`, `tag2`, `tag3`
```

### SQL Query Capture

When a CH query produces a surprising or reusable result:
1. Save it to `research/knowledge/queries/<descriptive_name>.sql`
2. Add a header comment: `-- Description, Usage, Expected result`
3. Reference it from the knowledge entry's Evidence section

## Phase 4: Agent Orchestration

### Integration with `/research` Pipeline

When used within the `/research` pipeline, knowledge is loaded ONCE by the Lead orchestrator
in Phase 0 and passed as `{KNOWLEDGE_CONTEXT}` to all subsequent agent prompts. Individual
agents do NOT need to re-load knowledge — they receive it in their dispatch prompt.

The parallel loading pattern below is primarily for **standalone use** (e.g., by
`quant-research-strategist` for ad-hoc exploration outside the formal pipeline).

### Integration with quant-research-strategist (standalone)

The `quant-research-strategist` agent should invoke this skill at the START of any standalone research session. The orchestration flow:

```
quant-research-strategist (main)
  ├── Phase 1: Dispatch parallel Explore agents to load knowledge
  │     ├── Agent: load pitfalls/ (always)
  │     ├── Agent: load data/ (always)
  │     ├── Agent: load execution/ (if backtest)
  │     └── Agent: load signals/ (if signal research)
  ├── Phase 2: Parse admonitions, surface CRITICAL/WARNING
  ├── [main research work happens here]
  ├── Phase 3: Detect surprises, enrich knowledge
  │     ├── Update existing entries (Edit tool)
  │     └── Create new entries (Write tool)
  └── Phase 4: Summary — list new/updated knowledge entries
```

### Parallel Loading Pattern

For a typical research session, dispatch 2-3 Explore agents simultaneously:

```python
# Pseudocode for agent orchestration
# All three agents launch in ONE message (parallel)

Agent("Explore", "Read research/knowledge/pitfalls/*.md. "
      "Extract all > [!CRITICAL] and > [!WARNING] admonitions. "
      "Return a structured list: {file, severity, message}")

Agent("Explore", "Read research/knowledge/data/*.md. "
      "Extract all admonitions and the TL;DR from each entry. "
      "Return: {file, tldr, admonitions[]}")

Agent("Explore", "Read research/knowledge/execution/*.md. "
      "Extract all admonitions and key parameters/thresholds. "
      "Return: {file, tldr, admonitions[], parameters[]}")
```

### Knowledge Validation (Post-Research)

After completing a research task, validate knowledge consistency:

1. **No contradictions**: New findings should not silently contradict existing entries
2. **Freshness**: If an entry references temp tables (`_tmp_*`), flag it as needing a durable query
3. **Completeness**: Every CRITICAL pitfall should have a corresponding check in the strategy code
4. **Cross-links**: Related entries should reference each other via `## Related` sections

## Research Workflow (Updated)

```
1. LOAD KNOWLEDGE (parallel agents)
   ├── Dispatch Explore agents by category
   ├── Parse admonitions from loaded entries
   └── Surface CRITICAL/WARNING to user

2. DISCOVER (vectorized, cheap)
   ├── CH SQL parameter sweeps
   ├── Use knowledge/queries/ for base queries
   ├── Report results as UPPER BOUNDS
   └── Flag surprises → Phase 3 enrichment

3. VALIDATE (tick-by-tick, expensive)
   ├── ReplayRunner with real trades
   ├── Apply ALL loaded CRITICAL admonitions
   ├── Compare with vectorized — expect 20-40pp degradation
   └── Flag surprises → Phase 3 enrichment

4. CAPTURE (knowledge enrichment)
   ├── Create/update knowledge entries for new findings
   ├── Add reusable SQL to queries/
   ├── Cross-reference with existing entries
   └── Report what was captured to user

5. DEPLOY (production code)
   ├── Strategy protocol implementation
   ├── Provider with CH-backed features
   ├── TOML config + CLI registration
   └── Verify all CRITICAL admonitions are addressed in code
```
