# Research Knowledge Base

Structured, reusable findings from quantitative research on Polymarket data.
Every entry is backed by a reproducible SQL query or Python script.

## Structure

```
knowledge/
├── data/          # Data characteristics, base rates, distributions
├── signals/       # Alpha signals and features (what works, what doesn't)
├── pitfalls/      # Known biases, loopholes, things that look good but aren't
├── execution/     # Position lifecycle, slippage, capital, compounding
└── queries/       # Reusable SQL snippets (referenced by knowledge entries)
```

## Entry Format

Each `.md` file follows this structure:

```markdown
# Title

> **TL;DR**: One-sentence summary of the finding.

> [!CRITICAL]
> Actionable rule. Violating this invalidates results.

> [!WARNING]
> Important caveat. Ignoring this leads to misleading results.

> [!TIP]
> Best practice. Improves quality but not strictly required.

## Finding

What we learned (2-5 sentences). Include concrete numbers.

## Evidence

SQL query or script reference that proves this finding.
Use `queries/<name>.sql` for reusable CH queries.

## Impact

How this affects strategy design or simulation accuracy.

## Related

- `category/entry.md` — how it connects

## Tags

`tag1`, `tag2`, `tag3`
```

### Admonition Severity Levels

| Level | When to Use | Agent Behavior |
|-------|------------|----------------|
| `[!CRITICAL]` | Violating this produces garbage results | **STOP** and fix before proceeding |
| `[!WARNING]` | Results biased or misleading if ignored | Flag to user, apply correction |
| `[!TIP]` | Quality improvement | Apply when practical |
| `[!NOTE]` | Background context | Absorb, no action needed |

Entries MUST have at least one admonition (CRITICAL or WARNING minimum for pitfalls/).
Data entries should have at least a WARNING or TIP.

### Cross-References

Every entry should have a `## Related` section linking to entries that share context.
Use tags for discovery: if entries share 2+ tags, they should cross-reference each other.

## How Agents Use This

The `research-knowledge` skill orchestrates knowledge loading via parallel agents.

### Loading Flow

1. **Parallel dispatch**: Explore agents load categories simultaneously
2. **Admonition parsing**: Extract `> [!LEVEL]` blocks from each entry
3. **Surface critical rules**: CRITICAL and WARNING admonitions shown to user
4. **Cross-reference check**: Follow `## Related` links for connected context

### When to Load (by task type)

| Task | Required Categories | Priority Entries |
|------|-------------------|-----------------|
| Signal discovery | `data/` + `pitfalls/` + `signals/` | base_rates, vectorized_vs_tick |
| Vectorized backtest | `pitfalls/` + `execution/` | vectorized_vs_tick, hold_time_capital |
| Tick-by-tick replay | `pitfalls/` + `execution/` | All CRITICAL entries |
| Strategy implementation | ALL | All CRITICAL entries |
| Result interpretation | `pitfalls/` + `data/` | vectorized_vs_tick, base_rates |

## Adding New Knowledge

### When to Capture

A finding is worth capturing when:
- A query result **surprises** you (contradicts expectation or existing knowledge)
- A filter has **outsized impact** (removes >30% of data, changes HR by >10pp)
- You discover a new **failure mode** or edge case
- A finding applies to **multiple strategies** (generalize it)

### Checklist

1. Create `.md` file in the appropriate category
2. Include at least one admonition (CRITICAL/WARNING for pitfalls, WARNING/TIP for others)
3. Include the SQL/script that produced the finding in Evidence section
4. Save reusable SQL to `queries/<name>.sql` with header comment
5. Add `## Related` links to connected entries
6. Tag it for discoverability
7. If the finding invalidates an existing entry, **update** it instead of creating a duplicate

### SQL Query Header Format

```sql
-- Description of what this query computes.
-- Usage: when/why to use this query.
-- Result: expected output description.
-- Parameters: {param_name} placeholders if any.
```

**Rule**: If you ran a query and the result surprised you, it's worth capturing.
