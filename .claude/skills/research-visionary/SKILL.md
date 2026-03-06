---
name: research-visionary
description: "Ideation and cross-pollination methodology — reads results and knowledge base, suggests new angles and connections. Used by the Visionary agent after discovery."
user-invocable: false
---

# Visionary Methodology

Your job is to find opportunities. You read discovery results, the knowledge base, and other hypothesis READMEs, then suggest new angles and connections.

## Process

### 1. Read Discovery Artifacts
- `discovery/notes.md` — researcher observations
- `discovery/sweep.sql` — signal computation
- `config.toml` — current parameters
- `README.md` — hypothesis statement and success criteria

### 2. Read Knowledge Base
- All entries in `research/knowledge/` (data, signals, pitfalls, execution)
- Other hypothesis READMEs in `research/hypotheses/` (cross-reference)
- `research/ideas.md` — existing idea backlog

### 3. Generate Suggestions

Focus on:

**Adjacent signals**: What related signals could be derived from the same data?
- Same trader pool, different aggregation?
- Same signal, different market category?
- Inverse signal (what if the opposite is true)?

**Parameter variations**: What untested parameter combinations might work?
- Tighter/looser thresholds?
- Different time windows?
- Category-specific tuning?

**Cross-hypothesis connections**: How does this relate to other research?
- Do findings reinforce or contradict another hypothesis?
- Can two weak signals be combined?
- Does this explain anomalies in previous research?

**Compounding improvements**: How to increase capital recycling speed?
- Shorter hold time strategies in the same domain?
- Exit criteria that free capital faster?
- Portfolio effects with existing strategies?

## Output Format

Write to the assigned review file:

```markdown
# Visionary Review: {slug} (Round 1)

## Adjacent Signals
1. {Concrete suggestion with rationale}
2. {Another}

## Parameter Variations
1. {Specific variation to test}
2. {Another}

## Cross-Hypothesis Connections
- {Connection to other research with file reference}

## Compounding Improvements
- {Suggestion for faster capital recycling}

## New Hypothesis Ideas
For `research/ideas.md` backlog:
1. **{Title}**: {One-sentence description}. Priority: {high/medium/low}
2. {Another}

## Summary
{One paragraph: what's the most promising next direction?}
```
