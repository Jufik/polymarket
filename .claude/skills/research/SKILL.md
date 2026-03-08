---
name: research
description: "Orchestrate multi-agent quantitative research. Use when the user describes a trading hypothesis, asks to explore a strategy idea, or invokes /research {slug}. Creates hypothesis folder, spawns team, manages review rounds and manual gates."
user-invocable: true
argument-hint: "[hypothesis-slug]"
---

# Research Pipeline Orchestrator (Lead)

You are the Lead. You orchestrate the research pipeline — you do NOT do research yourself.
You dispatch agents, manage gates, synthesize reviews, and present decisions to the user.

<HARD-GATE>
NEVER skip the manual validation gate. Vectorized results are UPPER BOUNDS.
Present them labeled as such and WAIT for the user to decide before dispatching tick-by-tick.
</HARD-GATE>

## Phase 0: Bootstrap

### 0a. Load Knowledge

Dispatch in parallel:

```python
Agent(subagent_type="Explore", prompt="""
  Read ALL .md files in research/knowledge/pitfalls/ and research/knowledge/data/
  and research/knowledge/execution/.
  Extract every > [!CRITICAL] and > [!WARNING] admonition.
  Return structured: {file, severity, message} for each admonition.
  Also return the TL;DR from each entry.
""", description="Load knowledge base")

Agent(subagent_type="Explore", prompt="""
  Read research/ideas.md (the strategy idea backlog).
  Return: queued ideas (with priority), in-progress tracks, tested results.
""", description="Load idea backlog")
```

After agents return:
1. Surface all CRITICAL and WARNING admonitions to the user
2. If no hypothesis provided: present top 3 queued ideas from backlog
3. **Store the collected admonitions as `KNOWLEDGE_CONTEXT`** — a structured block:

```markdown
## Knowledge Context
### CRITICAL
- [pitfalls/vectorized_vs_tick.md]: Vectorized 20-40pp optimistic vs tick-by-tick
- [pitfalls/consensus_dedup.md]: Count unique traders, not trade events
- [data/resolution_mechanics.md]: Use asset_id, never string matching
- [data/tag_base_rates.md]: Tag-specific base rates vary 9-73% YES
- ... (all CRITICAL admonitions)
### WARNING
- [pitfalls/sell_is_exit.md]: SELL is directional but ambiguous — test both
- [pitfalls/split_position_blind_spot.md]: 12% of maker positions have corrupted PnL
- ... (all WARNING admonitions)
```

**This block is included in EVERY subsequent agent dispatch prompt.** This eliminates
redundant knowledge loading by individual agents.

### 0b. Create Hypothesis Folder

If the user provides a slug (or you derive one from the hypothesis):

```bash
cp -r research/hypotheses/_template research/hypotheses/{slug}
```

Update `README.md` with hypothesis title, date, category.

## Phase 1: Frame Hypothesis

Structure the raw idea. Present to user for confirmation:

```markdown
### Hypothesis: {title}

**Signal**: What we're testing
**Thesis**: Why it should work (economic intuition)
**Test**: CH SQL approach
**Success**: Excess HR > {X}pp, positive PnL
**Compounding angle**: Expected hold time and throughput
**Knowledge flags**: {relevant CRITICAL/WARNING admonitions}
```

Wait for user confirmation before dispatching.

### Pre-mortem Check

After user confirms the hypothesis framing, dispatch a quick Skeptic review on the FRAMING
(not discovery artifacts — those don't exist yet):

```python
Agent(
    agent="skeptic",
    team="research-{slug}",
    description="Pre-mortem: {slug}",
    prompt="""
    Quick pre-mortem on this hypothesis framing (NOT a full review).

    {hypothesis framing from Phase 1}

    {KNOWLEDGE_CONTEXT}

    Check ONLY:
    1. Does the hypothesis contradict any CRITICAL knowledge entries?
    2. Is the test approach fundamentally flawed?
    3. Are there obvious confounders the framing misses?

    Write a brief (< 200 words) assessment to: research/hypotheses/{slug}/reviews/premortem.md
    If CRITICAL issues found, flag them. Otherwise, greenlight.
    """,
)
```

If Skeptic flags CRITICAL issues: present to user, refine hypothesis before proceeding.
If greenlit: proceed to Phase 1.5.

## Phase 1.5: Data Recon

Before any sweep, dispatch Researcher for a quick data reconnaissance.
**Use DuckDB** (instant queries on pre-loaded Parquet snapshot) for recon — no CH needed.

```python
Agent(
    agent="researcher",
    team="research-{slug}",
    description="Data recon: {slug}",
    prompt="""
    TASK: Quick data recon for hypothesis. Run these 5 DuckDB queries and report results.
    DO NOT run sweeps or create notebooks. Just recon.

    {KNOWLEDGE_CONTEXT}

    Hypothesis: {signal}, {thesis}, {test_approach}
    Target tags/categories: {tags}

    Use DuckDB (from research.db import db; d = db()):

    1. **Universe size**: How many resolved markets match the target tags?
       Count condition_ids in markets_resolved with event_tags JOIN.

    2. **Tag-specific base rates**: What are YES/NO win rates for the target tags?
       Compute from markets_resolved (not the global 38/62).

    3. **Table health**: Are Parquet snapshot tables populated?
       d.status() shows row counts for all loaded tables and views.

    4. **Classification status**: Classifications live in CH only.
       If the hypothesis needs classifications, check CH:
       clickhouse_connect to 192.168.0.148:18123, SELECT label, count(*) FROM trader_classifications FINAL GROUP BY label.
       If not needed, skip.

    5. **Time coverage**: What date range has data for the target tags?
       Query trades view for earliest/latest timestamps + trade count (filtered by universe).

    RETURN: Structured report with GO / NO-GO recommendation.
    NO-GO if: universe < 50 resolved markets, data coverage < 6 months,
    or required tables are empty/missing.
    """,
)
```

### Go/No-Go Gate

Present recon results to user:

```markdown
## Data Recon: {slug}
- Universe: {N} resolved markets ({tag})
- Base rates: YES {X}%, NO {Y}% (vs global 38%/62%)
- Split-corrected tables: {populated / empty}
- Classifications available: {labels} | Missing: {needed labels}
- Coverage: {start} to {end} ({N} months)
- **Recommendation**: GO / NO-GO ({reason})
```

If NO-GO: suggest refinement or alternative hypothesis. Wait for user decision.
If GO: proceed to Phase 2.

## Phase 2: Discover

### Create team and dispatch Researcher:

```python
TeamCreate(name="research-{slug}")

Agent(
    agent="researcher",
    team="research-{slug}",
    description="Discovery: {hypothesis_title}",
    prompt="""
    TASK: Vectorized discovery for this hypothesis.

    {KNOWLEDGE_CONTEXT}

    Hypothesis folder: research/hypotheses/{slug}/
    Signal: {signal}
    Thesis: {thesis}
    Test approach: {test_approach}
    Success criteria: {criteria}
    Tag-specific base rates: YES {X}%, NO {Y}% (from data recon)
    Universe size: {N} markets (from data recon)

    You will invoke the research-discover skill for methodology.
    Write artifacts to research/hypotheses/{slug}/discovery/
    Fill in research/hypotheses/{slug}/config.toml with strategy parameters.

    IMPORTANT:
    - Run BOTH SELL variants (BUY-only + directional) — mandatory dual-test
    - Write discovery/results.json with structured output
    - Include sensitivity analysis for top-3 combos
    - If sanity combo fails (HR below base rate): abort early with NO-GO

    RETURN (in your final message):
    - Top 5 parameter combos by compounding_score (BOTH variants)
    - Vectorized HR and PnL (labeled UPPER BOUND)
    - Sensitivity analysis summary
    - Hold time distribution
    - Universe size (trades/month)
    - Spawned ideas (for backlog)
    - Surprising findings (for knowledge capture)

    DO NOT run tick-by-tick replay. Discovery only.
    """,
)
```

Wait for Researcher to complete.

### Early Abort Handling

If Researcher returns with `"verdict": "no_signal"` from the sanity combo:
- Present the no-signal finding to user
- Suggest: refine parameters, try different tags, or abandon
- Skip Review Round 1 — no artifacts to review
- If user says abandon: go to Phase 6 (Capture & Close) with rejection

## Phase 3: Review Round 1

### Read Structured Output

Read `research/hypotheses/{slug}/discovery/results.json` for structured metrics.
Use this for the gate presentation table instead of free-text parsing from agent messages.

### Dispatch all four reviewers in parallel:

```python
Agent(
    agent="skeptic",
    team="research-{slug}",
    description="R1 Skeptic: {slug}",
    prompt="""
    Review discovery artifacts for hypothesis: {slug}
    Folder: research/hypotheses/{slug}/
    Read discovery/results.json for structured metrics.

    {KNOWLEDGE_CONTEXT}

    Write review to: research/hypotheses/{slug}/reviews/round1_skeptic.md
    """,
)

Agent(
    agent="visionary",
    team="research-{slug}",
    description="R1 Visionary: {slug}",
    prompt="""
    Review discovery artifacts for hypothesis: {slug}
    Folder: research/hypotheses/{slug}/
    Read discovery/results.json for structured metrics.

    {KNOWLEDGE_CONTEXT}

    Write review to: research/hypotheses/{slug}/reviews/round1_visionary.md
    """,
)

Agent(
    agent="challenger",
    team="research-{slug}",
    description="R1 Challenger: {slug}",
    prompt="""
    Review discovery artifacts for hypothesis: {slug}
    Folder: research/hypotheses/{slug}/
    Read discovery/results.json for structured metrics.

    {KNOWLEDGE_CONTEXT}

    Write review to: research/hypotheses/{slug}/reviews/round1_challenger.md
    """,
)

Agent(
    agent="architect",
    team="research-{slug}",
    description="R1 Config review: {slug}",
    prompt="""
    Review config.toml for hypothesis: {slug}
    Path: research/hypotheses/{slug}/config.toml
    Verify harness config correctness BEFORE validation run.

    {KNOWLEDGE_CONTEXT}

    Check: executor=realistic, settlement=true, resolution_source=asset_id,
    bootstrap_hours sufficient, walk-forward config reasonable.

    Write observations to: research/hypotheses/{slug}/reviews/round1_architect.md
    """,
)
```

After all four reviews arrive:
1. Read all review files
2. Check for `> [!CRITICAL]` blocking issues
3. If blocked: route feedback to Researcher, go back to Phase 2
4. If clear: present discovery results with review summary to user

### Discovery Results Presentation

```markdown
## Discovery Results: {title}

> [!WARNING] These are UPPER BOUNDS. Expect 20-40pp degradation in tick-by-tick.

### BUY-only variant
| Params | Vec HR | Excess HR | Edge/trade | Hold (d) | Compounding | Trades/mo | Fragile? |
|--------|--------|-----------|------------|----------|-------------|-----------|----------|
| ...top 5... |

### Directional SELL variant
| Params | Vec HR | Excess HR | Edge/trade | Hold (d) | Compounding | Trades/mo | Fragile? |
|--------|--------|-----------|------------|----------|-------------|-----------|----------|
| ...top 5... |

SELL sensitivity: {X}pp difference between variants

**Expected realistic range**: HR {vec - 40pp} to {vec - 20pp}, PnL x0.3 to x0.5

**Reviewer summaries**:
- Skeptic: {one-liner}
- Visionary: {one-liner}
- Challenger: {one-liner}
- Architect: {config assessment}

### What next?
1. **Validate** — dispatch tick-by-tick replay (~2min/month)
2. **Refine** — adjust parameters, re-run discovery
3. **Park** — save to idea backlog with context
4. **Abandon** — capture anti-knowledge, close
```

<HARD-GATE>
Wait for explicit user decision. NEVER auto-proceed to validation.
</HARD-GATE>

## Phase 4: Validate

Only after user says "validate":

### Dispatch Researcher for tick-by-tick validation:
```python
Agent(
    agent="researcher",
    team="research-{slug}",
    description="Validate: {slug}",
    prompt="""
    TASK: Tick-by-tick validation for hypothesis: {slug}

    {KNOWLEDGE_CONTEXT}

    Hypothesis folder: research/hypotheses/{slug}/
    Selected parameters: {params from Phase 3}
    Vectorized results: HR={vec_hr}, PnL={vec_pnl} (UPPER BOUND)
    SELL variant selected: {buy_only | directional}

    You will invoke the research-validate skill for methodology.

    Use the fast replay infrastructure:
    - `run_fast_backtest()` from research/harness.py (simplest, fully sync)
    - Or `SyncReplayRunner` from research/sync_replay.py (more control)
    - Data: Parquet snapshot in data/research/ (Polars predicate pushdown)
    - No asyncio needed — everything runs synchronously

    Write artifacts to research/hypotheses/{slug}/validation/

    RETURN:
    - Validated metrics (HR, PnL, Sharpe, drawdown, compounding_score)
    - Vectorized vs tick comparison table
    - Knowledge captures
    - Verdict: exploitable / marginal / none
    """,
)
```

Wait for Researcher to complete.

### Degradation Check

If degradation > 40pp: auto-dispatch sim-fidelity-auditor:
```python
Agent(
    agent="sim-fidelity-auditor",
    team="research-{slug}",
    description="Diagnose degradation: {slug}",
    prompt="""
    Tick-by-tick validation for {slug} shows {X}pp degradation (expected 20-40pp).
    Diagnose the cause. Mode B: Reactive Diagnosis.

    {KNOWLEDGE_CONTEXT}

    Vectorized HR: {vec_hr}%
    Tick-by-tick HR: {tick_hr}%
    Folder: research/hypotheses/{slug}/
    """,
)
```

## Phase 5: Review Round 2

Dispatch Engineer + Challenger + Skeptic in parallel:

```python
Agent(
    agent="engineer",
    team="research-{slug}",
    description="R2 Engineer: {slug}",
    prompt="""
    Review validation results for hypothesis: {slug}
    Folder: research/hypotheses/{slug}/

    {KNOWLEDGE_CONTEXT}

    Write review to: research/hypotheses/{slug}/reviews/round2_engineer.md
    """,
)

Agent(
    agent="challenger",
    team="research-{slug}",
    description="R2 Challenger: {slug}",
    prompt="""
    Review validation results for hypothesis: {slug}
    Folder: research/hypotheses/{slug}/

    {KNOWLEDGE_CONTEXT}

    Write review to: research/hypotheses/{slug}/reviews/round2_challenger.md
    """,
)

Agent(
    agent="skeptic",
    team="research-{slug}",
    description="R2 Skeptic: {slug}",
    prompt="""
    Review validation results for hypothesis: {slug}
    Folder: research/hypotheses/{slug}/

    {KNOWLEDGE_CONTEXT}

    Write review to: research/hypotheses/{slug}/reviews/round2_skeptic.md
    """,
)
```

### Manual Gate

Read all round 2 reviews. Synthesize into gate summary using `gate-summary.md` template.

Present to user:

```markdown
## Gate Decision: {slug}

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | X% | Y% | -Zpp |
| Sharpe | X | Y | -Z% |
| Avg Edge | $X | $Y | -Z% |
| Compounding | X | Y | -Z% |

**Reviewer consensus**:
- Skeptic: {summary}
- Challenger: {summary}
- Engineer: {summary}

**Recommendation**: {promote to paper_dev / iterate / reject}

What would you like to do?
1. **Promote** — move config to paper trading
2. **Iterate** — back to discovery with feedback
3. **Reject** — capture anti-knowledge, close
```

<HARD-GATE>
Wait for user decision. NEVER auto-promote.
</HARD-GATE>

## Phase 6: Capture & Close

### If promoting:
1. Update `README.md` with final scores and decision
2. Extract knowledge from discovery/validation → merge into `research/knowledge/`
3. Update `research/ideas.md`: move hypothesis to Tested section
4. Config is ready for `pm-strategy run --config research/hypotheses/{slug}/config.toml`

### If rejecting:
1. Update `README.md` status to `rejected` with rationale
2. **Fill in the Anti-Knowledge section** in README.md (MANDATORY):
   - Signal tested, why it failed, conditions for revisiting, generalizable lesson
3. If the failure mode is generalizable: create a knowledge entry in `research/knowledge/`
4. Add spawned ideas to `research/ideas.md` backlog

### If iterating:
1. Route reviewer feedback to Researcher
2. Go back to Phase 2 with refined parameters

### Cleanup:
```python
TeamDelete(name="research-{slug}")
```

## Red Flags (Stop and Ask User)

- Track agent returns HR > 95% → likely data leakage
- Validation shows 0 fills → settlement bug, flag to Architect
- Degradation > 40pp → auto-dispatch sim-fidelity-auditor
- Degradation < 10pp → suspicious, likely look-ahead bias
- Compounding score negative → no edge, present honestly
- Agent stuck on CH errors → surface to user
- Knowledge contradiction → investigate before proceeding
- Researcher returns NO-GO from sanity combo → present to user, don't auto-abandon
