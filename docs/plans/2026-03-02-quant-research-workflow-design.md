# Agentic Quant Research Workflow

**Date**: 2026-03-02
**Goal**: Multi-track strategy research pipeline that finds exploitable edges with rapid capital recycling, validates them tick-by-tick, and compounds knowledge across sessions.

## Design Principles

1. **Edge-first, filter later** — search all categories, apply capital efficiency post-hoc
2. **Manual validation gate** — user reviews vectorized upper bounds before expensive replay
3. **Multi-track** — 2-3 hypotheses in parallel via isolated worktree agents
4. **Idea backlog** — capture spawned ideas for future sessions
5. **Knowledge compounds** — every session enriches the knowledge base
6. **Marimo as artifact** — each hypothesis produces a self-contained notebook

## Architecture

```
User: "explore whether X predicts Y"
         │
         ▼
┌─────────────────────────────────┐
│  RESEARCH ORCHESTRATOR (skill)  │  ← main skill, runs in conversation
│  - Parses hypothesis            │
│  - Dispatches track agents      │
│  - Manages idea backlog         │
│  - Coordinates knowledge I/O    │
└────────┬───────────┬────────────┘
         │           │
    ┌────▼────┐ ┌────▼────┐
    │ Track A │ │ Track B │  ← isolated worktree agents (parallel)
    │ (hyp 1) │ │ (hyp 2) │
    └────┬────┘ └────┬────┘
         │           │
         ▼           ▼
   ┌───────────────────────┐
   │  Shared Knowledge Base │  ← research/knowledge/ (git-tracked)
   │  Idea Backlog          │  ← research/ideas.md (persistent)
   │  Marimo Notebooks      │  ← research/notebooks/ (artifacts)
   └───────────────────────┘
```

## Agent Roles

### 1. Research Orchestrator (main conversation skill)

**Responsibility**: Top-level coordination. Frames hypotheses, dispatches tracks, manages gates.

**Actions**:
- Parse user's hypothesis into structured form
- Load knowledge base (parallel Explore agents)
- Surface CRITICAL/WARNING admonitions
- Dispatch track agents (worktree-isolated)
- Manage manual validation gate (present results, await user decision)
- Coordinate idea backlog reads/writes
- Final knowledge capture + summary

### 2. Track Agent (worktree-isolated, general-purpose)

**Responsibility**: Execute one hypothesis end-to-end through discovery phase.

**Actions**:
- Create marimo notebook skeleton for the hypothesis
- Run vectorized CH SQL discovery (parameter sweeps)
- Compute capital efficiency metrics (hold time, throughput)
- Score the edge: `compounding_score = excess_hr × avg_pnl / median_hold_days`
- Report results back to orchestrator
- Capture surprising findings as knowledge draft entries

### 3. Validation Agent (foreground, after manual gate)

**Responsibility**: Run expensive tick-by-tick replay on user-approved hypotheses.

**Actions**:
- Configure ReplayRunner with RealisticFillSimulator
- Run month-by-month OOS replay with settlement
- Compare vectorized vs tick-by-tick (flag if gap > 30pp)
- Compute final metrics: HR, PnL, Sharpe, drawdown, avg hold time
- Update marimo notebook with validation results
- Capture degradation patterns as knowledge

### 4. Knowledge Agent (background, Explore-based)

**Responsibility**: Load, parse, enrich knowledge base.

**Actions**:
- Parallel-load knowledge categories at session start
- Extract and surface admonitions
- During research: detect surprises, create/update entries
- Post-research: validate consistency, cross-reference

## Workflow Phases

### Phase 0: Session Bootstrap

```
User starts conversation with hypothesis or "explore ideas"
    │
    ├── Load idea backlog (research/ideas.md)
    ├── Dispatch knowledge agents (parallel, by category)
    ├── Surface CRITICAL/WARNING admonitions
    └── If no hypothesis: present top ideas from backlog
```

### Phase 1: Hypothesis Framing

```
Raw idea: "maker volume fraction predicts resolution"
    │
    ▼
Structured Hypothesis:
    ├── Signal: maker_vol_frac (MVF) per trader per market
    ├── Thesis: high-MVF traders have better resolution prediction
    ├── Null hypothesis: MVF is uncorrelated with correctness
    ├── Test: compare HR of top-MVF vs bottom-MVF traders
    ├── Success criteria: excess HR > 10pp over base rate
    ├── Capital angle: what's the expected hold time?
    └── Knowledge check: any existing entries on MVF?
```

**Output**: Structured hypothesis shown to user for confirmation.

### Phase 2: Discovery (Vectorized, Cheap)

```
Dispatch Track Agent(s) in worktree:
    │
    ├── Create marimo notebook: research/notebooks/{hypothesis_slug}.py
    │     ├── Setup cell (CH connection, imports)
    │     ├── Hypothesis cell (structured description)
    │     ├── Base rates cell (load from knowledge)
    │     ├── Signal computation cells (CH SQL)
    │     ├── Parameter sweep cells
    │     └── Results summary cell (placeholder)
    │
    ├── Run vectorized CH SQL sweeps
    │     ├── Compute signal values across trader population
    │     ├── Grid search: thresholds × lookbacks × filters
    │     ├── Walk-forward: train on N months, test on M
    │     └── Report ALL results as UPPER BOUNDS
    │
    ├── Compute compounding score per parameter combo:
    │     compounding_score = excess_hr × avg_edge_usd / median_hold_days
    │     (higher = faster capital recycling)
    │
    ├── Flag surprises → draft knowledge entries
    │
    └── Return to orchestrator:
          ├── Best parameter combos (top 5 by compounding_score)
          ├── Vectorized HR, PnL (labeled UPPER BOUND)
          ├── Hold time distribution
          ├── Universe size (how many trades/month?)
          └── Spawned ideas (for backlog)
```

### Phase 3: Manual Gate

```
Orchestrator presents to user:
    │
    ├── Vectorized results table (UPPER BOUND label)
    ├── Compounding score ranking
    ├── Knowledge admonitions that apply
    ├── Estimated tick-by-tick degradation (from knowledge: 20-40pp)
    ├── Expected realistic performance range
    │
    └── User chooses:
          ├── "validate" → Phase 4 (tick-by-tick)
          ├── "refine"  → back to Phase 2 with adjusted params
          ├── "park"    → save to idea backlog with context
          └── "abandon" → capture why, close track
```

### Phase 4: Validation (Tick-by-Tick, Expensive)

```
Dispatch Validation Agent (foreground, needs user attention):
    │
    ├── Load trades from CH (pre-filtered by qualified makers)
    ├── Configure ReplayRunner:
    │     ├── RealisticFillSimulator (calibrated spreads)
    │     ├── MarketResolution (asset_id-based)
    │     ├── Settlement enabled (tick-by-tick capital freeing)
    │     ├── ParquetLedger for outcome tracking
    │     └── Strategy + Provider from hypothesis
    │
    ├── Run month-by-month OOS replay
    │     ├── 9-month rolling train → 1-month test
    │     ├── Track: fills, settlements, capital utilization
    │     └── Output: ledger parquet per month
    │
    ├── Compute validated metrics:
    │     ├── Tick-by-tick HR (vs vectorized HR)
    │     ├── Net PnL after slippage
    │     ├── Sharpe ratio (annualized)
    │     ├── Max drawdown
    │     ├── Avg hold time (actual, not estimated)
    │     ├── Capital utilization % (slots used / available)
    │     ├── Compounding score (validated, not upper bound)
    │     └── Degradation gap: vectorized - tick (flag if > 30pp)
    │
    ├── Update marimo notebook with validation section
    │
    └── Return to orchestrator:
          ├── Validated metrics
          ├── Vectorized vs tick comparison table
          ├── Monthly equity curve
          └── Knowledge captures (degradation patterns, new pitfalls)
```

### Phase 5: Capture & Score

```
Orchestrator:
    │
    ├── Update marimo notebook (final results section)
    │
    ├── Knowledge enrichment:
    │     ├── New findings → create entries
    │     ├── Contradictions → update existing entries
    │     ├── Reusable SQL → save to queries/
    │     └── Cross-reference related entries
    │
    ├── Score the edge for compounding potential:
    │     ├── validated_compounding_score
    │     ├── capital_efficiency_rank (vs other strategies)
    │     ├── universe_sustainability (enough trades/month?)
    │     └── implementation_complexity (simple = better)
    │
    ├── Idea backlog updates:
    │     ├── Add spawned ideas with context
    │     ├── Mark explored ideas as "tested" with result summary
    │     └── Prioritize remaining ideas by potential
    │
    └── Present final summary to user:
          ├── Edge verdict: exploitable / marginal / none
          ├── Compounding potential: high / medium / low
          ├── Next step recommendation:
          │     ├── "promote" → create strategy_impl skeleton
          │     ├── "refine" → specific parameters to adjust
          │     ├── "combine" → pair with another strategy
          │     └── "park" → save context for later
          └── Updated idea backlog (top 3 next hypotheses)
```

## Marimo Notebook Template

Each hypothesis produces a notebook with this structure:

```
Cell 0: Setup (CH connection, imports, constants)
Cell 1: Hypothesis (structured description, success criteria)
Cell 2: Knowledge Context (loaded admonitions, base rates)
Cell 3: Signal Computation (CH SQL, feature engineering)
Cell 4: Parameter Sweep (grid search, walk-forward)
Cell 5: Vectorized Results (UPPER BOUND, compounding score)
Cell 6: Capital Efficiency (hold time, throughput analysis)
--- validation gate ---
Cell 7: Tick-by-Tick Setup (ReplayRunner config)
Cell 8: Replay Results (monthly breakdown, equity curve)
Cell 9: Comparison (vectorized vs tick, degradation analysis)
Cell 10: Verdict (edge score, compounding potential, next steps)
Cell 11: Knowledge Captures (new findings, updated entries)
```

## Idea Backlog Format (`research/ideas.md`)

```markdown
# Strategy Research Idea Backlog

## Queued
- [ ] **MVF as exit signal** — high MVF traders exiting = bearish signal
  - Source: S1 exploration (2026-03-01), noticed MVF spike before resolution
  - Priority: HIGH (addresses capital recycling directly)
  - Related: signals/mvf_entry.md

## In Progress
- [~] **Consensus velocity** — speed of consensus formation predicts edge
  - Track: research/notebooks/consensus_velocity.py
  - Started: 2026-03-02

## Tested
- [x] **Hit-rate copy (S1)** — copy high-HR traders with consensus
  - Result: 87.9% HR validated, $0.94/trade, compounding_score=2.3
  - Notebook: research/notebooks/S1_hitrate_copy_exploration.py
  - Status: PROMOTED to strategies_impl/

## Parked
- [-] **Volume spike detection** — sudden volume = informed trading
  - Reason: insufficient universe (<50 trades/month after filters)
  - Revisit when: more market categories available
```

## Compounding Score Formula

```
compounding_score = (validated_hr - base_rate_hr) × avg_edge_usd / median_hold_days

Where:
  validated_hr     = tick-by-tick hit rate (not vectorized)
  base_rate_hr     = 0.381 (YES) or 0.619 (NO) depending on direction
  avg_edge_usd     = average net PnL per trade (after slippage)
  median_hold_days = median time from entry to resolution

Higher score = faster capital recycling with real edge.
```

**Interpretation**:
- `> 5.0` = excellent compounding candidate
- `1.0 - 5.0` = moderate, worth deploying
- `< 1.0` = edge exists but poor capital efficiency
- `< 0` = no edge

## Skill Files to Create

1. **`.claude/skills/quant-research.md`** — Main orchestrator skill
2. **`.claude/skills/research-track.md`** — Track agent instructions
3. **`.claude/skills/research-validate.md`** — Validation agent instructions
4. Update **`.claude/skills/research-knowledge.md`** — Already improved (admonitions, enrichment, orchestration)

## Integration Points

- **quant-research-strategist** (existing Agent type) — invoke from orchestrator for complex research
- **research-knowledge** (existing skill) — invoked at Phase 0 for knowledge loading
- **superpowers:brainstorming** — invoked if hypothesis is vague, needs structuring
- **superpowers:verification-before-completion** — invoked at Phase 5 before claiming results
