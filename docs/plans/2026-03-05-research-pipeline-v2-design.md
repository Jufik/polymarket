# Research Pipeline v2: Agent Team Architecture

**Date**: 2026-03-05
**Goal**: Professional multi-agent research pipeline that takes hypotheses from discovery through tick-by-tick validation to paper trading, using a shared production harness and structured review rounds.
**Supersedes**: `2026-03-02-quant-research-workflow-design.md` (single orchestrator + worktree tracks)

## Design Principles

1. **Skills define HOW, agents define WHO** — lightweight agent files reference skill playbooks
2. **One execution path** — same TOML config drives research replay and paper trading
3. **Architect owns harness** — incremental evolution, never strategy-specific rework
4. **Two review rounds** — after vectorized discovery, after tick-by-tick validation
5. **Manual gate before promotion** — user decides, agents advise
6. **Knowledge compounds** — every hypothesis captures findings to `research/knowledge/`
7. **Reviewers are read-only** — they write only their review file, never modify code or data

## Agent Roster (7 Agents)

| Agent | Role | Persistence | Tools | Model |
|-------|------|-------------|-------|-------|
| **Lead** | Orchestrator | Main conversation | All | opus |
| **Researcher** | Heavy lifter (SQL, notebooks, harness) | Per-hypothesis team | All | sonnet |
| **Architect** | Harness guardian | Persistent across hypotheses | All | sonnet |
| **Visionary** | Ideation & cross-pollination | Dispatched at checkpoints | Read-only | haiku |
| **Skeptic** | Devil's advocate | Dispatched at checkpoints | Read-only | haiku |
| **Challenger** | Capital efficiency hawk | Dispatched at checkpoints | Read-only | haiku |
| **Engineer** | Methodology auditor + viability estimator | Dispatched at checkpoints | Read-only | haiku |

### Lead (Main Conversation)

Runs via `/research {slug}` skill. Manages hypothesis lifecycle:
- Creates hypothesis folder from `_template/`
- Creates team `research-{slug}`, spawns Researcher + Architect
- Dispatches review agents at checkpoints (parallel)
- Reads all review files, synthesizes go/no-go decisions
- Presents manual gate to user with metrics table
- Merges knowledge entries at capture phase

Does NOT: write SQL, run backtests, modify harness code, challenge methodology.

### Researcher

Heavy computation. Two phases, each with its own skill:
- **Discovery** (`research-discover` skill): CH SQL sweeps, marimo notebooks, config.toml population
- **Validation** (`research-validate` skill): `pm-harness run`, walk-forward windowing, result analysis

Loads relevant `research/knowledge/` entries before any CH query. Responds to reviewer feedback by adjusting params or methodology.

Does NOT: modify harness code (Architect), make promotion decisions (Lead).

### Architect

Owns the production execution harness. Persistent across hypotheses to accumulate harness evolution context.

**Reviews**: config.toml correctness before validation runs.
**Monitors**: degradation band (expected 20-40pp from vectorized). If >40pp or <10pp, investigates harness fidelity.
**Modifies** (only these files):
- `src/polymarket_pipeline/strategies/runners/replay.py`
- `src/polymarket_pipeline/strategies/runners/helpers.py`
- `src/polymarket_pipeline/strategies/execution/gateway.py`
- `src/polymarket_pipeline/strategies/execution/realistic.py`
- `src/polymarket_pipeline/strategies/execution/calibrate.py`
- `src/polymarket_pipeline/strategies/config.py`

Changes must be generic improvements. Runs `uv run pytest tests/ -x -q` after every change. Full rewrites require explicit user approval.

### Visionary

Reads discovery results + full knowledge base. Suggests new angles, connects dots across hypotheses. Writes `reviews/round1_visionary.md`.

Does NOT: challenge methodology (Skeptic), estimate viability (Engineer), push aggressiveness (Challenger).

### Skeptic

Challenges methodology, assumptions, conclusions. Mandatory 6-point checklist:
1. Look-ahead bias in SQL or feature construction?
2. Survivorship bias (only resolved markets)?
3. Edge above NO base rate (62%)?
4. Sample size sufficient (>100 trades)?
5. Walk-forward or all in-sample?
6. Degradation from vectorized → tick-by-tick in expected 20-40pp band?

Uses `> [!CRITICAL]` / `> [!WARNING]` / `> [!TIP]` admonitions for severity.

### Challenger

Pushes toward faster capital recycling:
- Evaluates compounding score: `excess_hr × avg_edge_usd / median_hold_days`
- Compares median hold time against capital lock-up cost
- Suggests tighter exit criteria, faster consensus thresholds
- Pushes for faster-resolving categories (sports ~8d vs politics ~30d+)

Does NOT ignore risk — pushes for aggression within validated edge.

### Engineer

Post-validation only (needs tick-by-tick results). Audits:
- Entry price assumptions realistic? (wavg vs actual orderbook)
- Fill model matches live execution? (RealisticFillSimulator vs PaperExecutor)
- Bootstrap window sufficient for live consensus building?
- Position sizing viable at live capital constraints?
- Expected slippage at target size?
- Promotion gate likelihood (min Sharpe, min trades, max drawdown)

Writes `reviews/round2_engineer.md` with viability assessment + promotion readiness score.

## Workflow Phases

```
Phase 0: Bootstrap ─── Load knowledge, parse admonitions
Phase 1: Frame ─────── Define hypothesis statement, scope, success criteria
Phase 2: Discover ──── Researcher runs vectorized CH SQL sweeps
Phase 3: Review R1 ─── Visionary + Skeptic + Challenger (parallel)
Phase 4: Validate ──── Researcher runs pm-harness + Architect reviews harness
Phase 5: Review R2 ─── Engineer + Challenger + Skeptic (parallel)
     ┌── Manual Gate ── User decides: promote / iterate / reject
Phase 6: Capture ───── Extract knowledge, update README, promote config
```

### Phase 0: Bootstrap
Lead loads `research/knowledge/` entries via Explore agent. Parses admonitions (CRITICAL/WARNING/TIP). Surfaces CRITICAL items that must be addressed.

### Phase 1: Frame
Lead writes `README.md` in hypothesis folder:
- Hypothesis statement (one sentence)
- Category scope (which market tags)
- Success criteria (min hit rate, min edge, min compounding score)
- Time window for analysis

### Phase 2: Discover
Researcher (in worktree) invokes `research-discover` skill, then:
- Writes CH SQL to `discovery/sweep.sql`
- Runs sweep, saves to `discovery/sweep_results.parquet`
- Creates marimo notebook at `discovery/notebook.py`
- Fills in `config.toml` with strategy parameters
- Writes observations to `discovery/notes.md`

Auto-gate to Phase 3: discovery tasks complete + `config.toml` has `[strategy]` section.

### Phase 3: Review Round 1
Lead dispatches Visionary + Skeptic + Challenger in parallel. Each reads:
- `discovery/notes.md`
- `discovery/sweep.sql`
- `config.toml`
- `research/knowledge/` (full directory)

Each writes `reviews/round1_{role}.md`.

Auto-gate to Phase 4: all 3 reviews exist + no `> [!CRITICAL]` blocking issues. If blocking issue found, Lead routes back to Researcher with feedback.

### Phase 4: Validate
Researcher invokes `research-validate` skill, then runs:
```bash
uv run pm-harness run \
  --config research/hypotheses/{slug}/config.toml \
  --period 2025-01-01:2026-01-01 \
  --output research/hypotheses/{slug}/validation/
```

Concurrently, Architect reviews config.toml correctness and monitors degradation band after results are in.

Outputs: `validation/ledger.parquet`, `validation/summary.json`, `validation/replay_log.jsonl`

Auto-gate to Phase 5: ledger + summary exist + Architect confirms degradation plausible.

### Phase 5: Review Round 2
Lead dispatches Engineer + Challenger + Skeptic in parallel. Each reads validation results + discovery context. Each writes `reviews/round2_{role}.md`.

### Manual Gate
Lead synthesizes all reviews into a metrics table and presents to user:

| Metric | Vectorized | Tick-by-tick | Degradation |
|--------|-----------|-------------|-------------|
| Hit Rate | X% | Y% | -Zpp |
| Sharpe | X | Y | -Z% |
| Avg Edge | $X | $Y | -Z% |
| Compounding | X | Y | -Z% |

Plus reviewer consensus (one-liner per reviewer) and Lead's recommendation.

User decides: **promote** (→ Phase 6), **iterate** (→ back to Phase 2), **reject** (→ capture anti-knowledge).

### Phase 6: Capture & Promote
Lead + Researcher:
- Write findings to `knowledge.md` in hypothesis folder
- Lead merges relevant entries into `research/knowledge/`
- Update `README.md` with final status + scores
- If promoting: config.toml is ready for `pm-strategy run`

## Production Harness Design

### One Execution Path

Same `ReplayRunner` + `ExecutionGateway` + `RealisticFillSimulator` pipeline for both research and paper trading. The only difference is the executor and data source:

| Mode | Executor | Data Source | Price |
|------|----------|-------------|-------|
| Research replay | RealisticFillSimulator | CH historical trades | max_price + calibrated slippage |
| Paper trading | PaperExecutor | Live Kafka + CLOB orderbook | Live best_ask/bid |

### TOML Config: `[harness]` Section

```toml
[harness]
data_source = "clickhouse"           # "clickhouse" for replay, "kafka" for paper
executor = "realistic"               # "realistic" | "paper" | "simulated"
fill_model = "calibrated_slippage"   # "calibrated_slippage" | "instant"
bootstrap_hours = 168                # Provider bootstrap window
walk_forward_train_months = 12       # Walk-forward: training window
walk_forward_test_months = 1         # Walk-forward: test window

[harness.replay]
pre_filter_makers = true             # Pre-filter trades by qualified makers in CH
settlement_enabled = true            # Mid-replay settlement (frees capital)
resolution_source = "asset_id"       # Always asset_id, never string matching
```

### CLI Entry Point

```bash
uv run pm-harness run \
  --config research/hypotheses/{slug}/config.toml \
  --period 2025-01-01:2026-01-01 \
  --output research/hypotheses/{slug}/validation/ \
  --walk-forward 12m/1m
```

Registered in `pyproject.toml` as `pm-harness = "polymarket_pipeline.cli.harness:app"`.

### Escape Hatch

Custom simulation loops are allowed (in `scripts/` subfolder) but MUST validate through production harness. Parity check: custom results must be within 5pp of harness results on same data. Architect reviews any escape hatch usage.

## Per-Hypothesis Folder Structure

```
research/hypotheses/
├── _template/                    # Copied by Lead for each new hypothesis
│   ├── config.toml               # Skeleton TOML
│   └── README.md                 # Template with placeholder sections
│
├── {slug}/                       # One per hypothesis
│   ├── config.toml               # [strategy] + [harness] + [provider] — drives pm-harness
│   ├── README.md                 # Status, statement, scores, decision rationale
│   │
│   ├── discovery/                # Phase 2 artifacts (vectorized)
│   │   ├── sweep.sql             # CH SQL
│   │   ├── sweep_results.parquet # Raw output (gitignored)
│   │   ├── notebook.py           # marimo notebook
│   │   └── notes.md              # Researcher observations
│   │
│   ├── validation/               # Phase 4 artifacts (tick-by-tick)
│   │   ├── replay_log.jsonl      # pm-harness execution log
│   │   ├── ledger.parquet        # LedgerRecord output (gitignored)
│   │   ├── summary.json          # LedgerSummary
│   │   ├── walk_forward/         # Per-window results (optional)
│   │   └── notes.md              # Researcher + Architect observations
│   │
│   ├── reviews/                  # Append-only review files
│   │   ├── round1_visionary.md
│   │   ├── round1_skeptic.md
│   │   ├── round1_challenger.md
│   │   ├── round2_engineer.md
│   │   ├── round2_challenger.md
│   │   └── round2_skeptic.md
│   │
│   ├── scripts/                  # Escape hatch (custom scripts)
│   │
│   └── knowledge.md              # Extracted findings (→ merged to research/knowledge/)
```

**Gitignore additions**:
```
research/hypotheses/*/discovery/*.parquet
research/hypotheses/*/validation/*.parquet
research/hypotheses/*/validation/*.jsonl
```

## Skills & Agent File Layout

```
.claude/
├── skills/
│   ├── research/                 # User-facing: /research {slug}
│   │   ├── SKILL.md              # Lead orchestrator playbook
│   │   ├── templates/
│   │   │   ├── config.toml       # Hypothesis config skeleton
│   │   │   └── README.md         # Hypothesis README template
│   │   └── gate-summary.md       # Manual gate presentation template
│   │
│   ├── research-discover/
│   │   ├── SKILL.md              # Discovery methodology
│   │   └── examples/
│   │       └── sweep-template.sql
│   │
│   ├── research-validate/
│   │   ├── SKILL.md              # Validation methodology
│   │   └── checklist.md          # Pre-validation config checklist
│   │
│   ├── research-architect/
│   │   ├── SKILL.md              # Harness evolution methodology
│   │   └── owned-files.md        # Canonical list of harness files
│   │
│   ├── research-skeptic/
│   │   ├── SKILL.md              # Bias detection methodology
│   │   └── checklist.md          # 6-point audit checklist
│   │
│   ├── research-visionary/
│   │   └── SKILL.md              # Ideation methodology
│   │
│   ├── research-challenger/
│   │   └── SKILL.md              # Capital efficiency methodology
│   │
│   └── research-engineer/
│       ├── SKILL.md              # Audit + viability methodology
│       └── checklist.md          # Viability estimation framework
│
├── agents/
│   ├── researcher.md             # General-purpose, invokes research-discover/validate
│   ├── architect.md              # General-purpose, invokes research-architect
│   ├── skeptic.md                # Read-only, invokes research-skeptic
│   ├── visionary.md              # Read-only, invokes research-visionary
│   ├── challenger.md             # Read-only, invokes research-challenger
│   └── engineer.md               # Read-only, invokes research-engineer
```

### Skill Frontmatter Summary

| Skill | `user-invocable` | `disable-model-invocation` | `context` | `agent` |
|-------|-------------------|---------------------------|-----------|---------|
| `research` | true | true (manual only) | — (inline, Lead) | — |
| `research-discover` | false | false | — | — |
| `research-validate` | false | false | — | — |
| `research-architect` | false | false | — | — |
| `research-skeptic` | false | false | — | — |
| `research-visionary` | false | false | — | — |
| `research-challenger` | false | false | — | — |
| `research-engineer` | false | false | — | — |

Agent skills are `user-invocable: false` — only agents invoke them. The `/research` skill is the sole user entry point.

## Orchestration Protocol

### Team Lifecycle

```
/research {slug}
  │
  Lead creates team: "research-{slug}"
  ├─ Spawns Researcher (worktree, general-purpose)
  ├─ Spawns Architect (worktree, general-purpose) — or resumes if active
  │
  │  ... phases 2-4 ...
  │
  ├─ Dispatches reviewers (foreground, read-only)
  │   └─ Each writes review file, returns summary, exits
  │
  │  ... manual gate ...
  │
  ├─ Shutdown Researcher + Architect
  └─ TeamDelete "research-{slug}"
```

### Task List Structure

Shared at `~/.claude/tasks/research-{slug}/`:

| ID | Task | Owner | Blocked By |
|----|------|-------|-----------|
| 1 | Load knowledge base | lead | — |
| 2 | Frame hypothesis | lead | — |
| 3 | Run vectorized discovery | researcher | 2 |
| 4 | Create discovery notebook | researcher | 3 |
| 5 | Review: Visionary R1 | visionary | 4 |
| 6 | Review: Skeptic R1 | skeptic | 4 |
| 7 | Review: Challenger R1 | challenger | 4 |
| 8 | Validate config.toml | architect | 4 |
| 9 | Run tick-by-tick validation | researcher | 5,6,7,8 |
| 10 | Review: Engineer R2 | engineer | 9 |
| 11 | Review: Challenger R2 | challenger | 9 |
| 12 | Review: Skeptic R2 | skeptic | 9 |
| 13 | Manual gate | lead | 10,11,12 |
| 14 | Capture knowledge | lead | 13 |

### Gate Logic

| Gate | Condition | On Failure |
|------|-----------|-----------|
| Discovery → Review R1 | `notes.md` exists + `config.toml` has `[strategy]` | Wait for Researcher |
| Review R1 → Validation | All 3 reviews exist + no `> [!CRITICAL]` | Route back to Researcher with feedback |
| Validation → Review R2 | `ledger.parquet` + `summary.json` exist + Architect confirms | Wait / dispatch sim-fidelity-auditor |
| Review R2 → Manual Gate | All 3 R2 reviews exist | Wait for reviewers |
| Manual Gate → Capture | User says "promote" | Iterate or reject |

### Error Handling

| Situation | Action |
|-----------|--------|
| CH query error / harness crash | Retry once, then present to user |
| Empty/useless review | Re-dispatch with more specific prompt (max 1 retry) |
| Architect finds harness bug | Fix + test, Researcher re-runs validation |
| Degradation > 40pp | Dispatch `sim-fidelity-auditor` agent, block until resolved |
| Degradation < 10pp | Skeptic red flag (likely look-ahead bias), route back to discovery |
| User rejects at manual gate | Capture rejection reason, ask iterate or abandon |

## Implementation Roadmap

### Phase 1: Foundation (Critical Path)

| # | Task | Output | Depends On |
|---|------|--------|-----------|
| 1 | Create hypothesis folder template | `research/hypotheses/_template/` | — |
| 2 | Create `[harness]` config schema | Extension to config.py or new HarnessConfig | — |
| 3 | Create `pm-harness run` CLI entry point | `cli/harness.py` in pyproject.toml | #2 |
| 4 | Wire harness CLI to ReplayRunner + ExecutionGateway | Working replay from TOML | #2, #3 |
| 5 | Add walk-forward windowing to harness | `--walk-forward 12m/1m` flag | #4 |

**Validation**: `uv run pm-harness run --config research/hypotheses/_template/config.toml --period 2025-06-01:2025-07-01` runs without error.

### Phase 2: Skills (Playbooks)

| # | Task | Output | Depends On |
|---|------|--------|-----------|
| 6 | Write `research/SKILL.md` (Lead orchestrator) | `.claude/skills/research/SKILL.md` + templates | #1 |
| 7 | Write `research-discover/SKILL.md` | Discovery methodology + SQL template | — |
| 8 | Write `research-validate/SKILL.md` | Validation methodology + checklist | #4 |
| 9 | Write `research-architect/SKILL.md` | Harness evolution methodology + owned files | #4 |
| 10 | Write `research-skeptic/SKILL.md` | Skeptic checklist + bias detection | — |
| 11 | Write `research-visionary/SKILL.md` | Ideation methodology | — |
| 12 | Write `research-challenger/SKILL.md` | Capital efficiency methodology | — |
| 13 | Write `research-engineer/SKILL.md` | Audit + viability methodology | #4 |

### Phase 3: Agent Definitions

| # | Task | Output | Depends On |
|---|------|--------|-----------|
| 14 | Write `researcher.md` agent | `.claude/agents/researcher.md` | #7, #8 |
| 15 | Write `architect.md` agent | `.claude/agents/architect.md` | #9 |
| 16 | Write `skeptic.md` agent | `.claude/agents/skeptic.md` | #10 |
| 17 | Write `visionary.md` agent | `.claude/agents/visionary.md` | #11 |
| 18 | Write `challenger.md` agent | `.claude/agents/challenger.md` | #12 |
| 19 | Write `engineer.md` agent | `.claude/agents/engineer.md` | #13 |

### Phase 4: Integration

| # | Task | Output | Depends On |
|---|------|--------|-----------|
| 20 | End-to-end dry run: `/research test-dry-run` | Full pipeline on known signal | #6-19 |
| 21 | Fix issues from dry run | Bug fixes | #20 |
| 22 | Run first real hypothesis | `research/hypotheses/{first-real}/` | #21 |

### Dependency Graph

```
#1 (template) ──────────────────────────┐
#2 (config schema) ─┬─ #3 (CLI) ─┬─ #4 (wire) ─┬─ #5 (walk-forward)
                    │             │              │
                    │             │  #8 (validate skill)
                    │             │  #9 (architect skill)
                    │             │  #13 (engineer skill)
                    │             │
#7 (discover skill) ┤             │
#10 (skeptic skill) ┤             │
#11 (visionary skill)┤           │
#12 (challenger skill)┘          │
                                 │
#6 (lead skill) ─────────────────┤
                                 │
#14-19 (agent defs) ─────────────┤
                                 │
#20 (dry run) ───────────────────┘
```

Phase 1 is the critical path. Skills and agents can be written in parallel once the harness exists.
