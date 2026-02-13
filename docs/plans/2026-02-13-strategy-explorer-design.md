# Strategy Explorer — Operationalization Design

**Date:** 2026-02-13
**Status:** Approved

## Summary

Make the `strategy_explorer/` scaffold operational by folding it into the existing `polymarket_pipeline` package, wiring it to the live ClickHouse/PostgreSQL infrastructure, adding MLflow tracking, and replacing the raw Anthropic client with the Claude Agent SDK.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Package structure | Fold into `polymarket_pipeline.exploration` | Reuse existing sinks, models, connections. One install. |
| Notebooks | Plain Python scripts with `run()` contract | Simpler than marimo, runnable in CI/agents, no special runtime. |
| Experiment tracking | MLflow from day 1 | Docker service, one experiment per strategy, one run per stage. |
| Claude integration | Claude Agent SDK (`claude-agent-sdk`) | Agent gets ClickHouse as a tool, can explore interactively. Replaces raw `anthropic.messages.create`. |
| Model | `claude-opus-4-6` | Default for all agent operations. |
| Data access | Thin ClickHouse wrapper, SQL does the work | Joins/aggregates pushed to ClickHouse. Polars only for prototyping. Graduated features become materialized views. |

## 1. Package Structure

```
src/polymarket_pipeline/
├── ...existing modules...
├── exploration/
│   ├── __init__.py
│   ├── tree.py              # Exploration tree models (from strategy_explorer/tree.py)
│   ├── agent.py             # Claude Agent SDK integration (revised)
│   ├── data.py              # Thin ClickHouse query wrapper
│   ├── tracking.py          # MLflow integration
│   └── templates/
│       └── stage_template.py
├── cli/
│   ├── ...existing...
│   └── explore.py           # pm-explore CLI (from strategy_explorer/cli.py)
```

Stage data lives at repo root:

```
strategies/
└── skilled_traders/
    ├── exploration_tree.json
    └── stages/
        ├── 00_initial/
        │   ├── stage.py
        │   ├── analysis.md
        │   └── outputs/
        └── 01a_high_volume/
            ├── stage.py
            └── outputs/
```

## 2. Data Access Layer (`exploration/data.py`)

Thin wrapper — ClickHouse does all heavy lifting (joins, aggregates, filters). No pre-built Python query methods.

```python
class ExplorationDataSource:
    """Read-only ClickHouse client for exploration."""

    def __init__(self, host="localhost", port=8123, database="polymarket"):
        self._client = clickhouse_connect.get_client(...)

    def query_df(self, sql: str, params=None) -> pl.DataFrame:
        """Run SQL, return Polars DataFrame."""

    def query_raw(self, sql: str, params=None) -> list[dict]:
        """Run SQL, return list of dicts."""
```

Available ClickHouse tables:
- `trades_raw` / `trades` (view) — 246M+ trades, ReplacingMergeTree
- `events` — PostgreSQL engine, 197K rows
- `markets` — PostgreSQL engine, 471K rows
- `tags` — PostgreSQL engine, 4.8K rows
- `event_tags` — PostgreSQL engine
- `token_market_map` — PostgreSQL engine, 942K rows

Graduation path: when an exploration discovers a useful aggregate, promote it to a ClickHouse materialized view — not a Python helper.

## 3. MLflow Tracking

### Docker Service

Added to `docker-compose.yml`:

```yaml
mlflow:
  image: ghcr.io/mlflow/mlflow:v2.19.0
  ports:
    - "5000:5000"
  volumes:
    - mlflow_data:/mlflow
  command: >
    mlflow server --host 0.0.0.0
    --backend-store-uri sqlite:///mlflow/mlflow.db
    --default-artifact-root /mlflow/artifacts
```

### Tracker (`exploration/tracking.py`)

```python
class ExplorationTracker:
    def __init__(self, tracking_uri="http://localhost:5000"):
        mlflow.set_tracking_uri(tracking_uri)

    def get_or_create_experiment(self, strategy_name: str) -> str:
        """One MLflow experiment per strategy."""

    def log_stage(self, stage, metrics, outputs_dir) -> str:
        """Log a completed stage as an MLflow run.
        Tags: stage_id, parent_id, depth, refinement_type, hypothesis
        Params: filter_conditions, analysis_parameters
        Metrics: sample_size, sharpe_ratio, win_rate, etc.
        Artifacts: outputs/*.json, outputs/*.parquet, analysis.md
        Returns mlflow_run_id."""
```

## 4. Stage Scripts

Each stage is a plain Python script with a standard contract:

```python
"""Stage: 00_initial — Initial Trader Segmentation"""
from pathlib import Path
from polymarket_pipeline.exploration.data import ExplorationDataSource
from polymarket_pipeline.exploration.tree import StageMetrics

STAGE_ID = "00_initial"

def run(strategy_root: Path, outputs_dir: Path) -> dict:
    """Execute this stage. Returns outputs_summary dict."""
    db = ExplorationDataSource()

    # 1. Query ClickHouse (heavy lifting in SQL)
    df = db.query_df("""
        SELECT maker, count() as trade_count, ...
        FROM polymarket.trades FINAL
        WHERE ...
        GROUP BY maker HAVING trade_count >= 10
    """)

    # 2. Exploratory transforms in Polars (not productized yet)
    df = df.with_columns(...)

    # 3. Save outputs
    df.write_parquet(outputs_dir / "traders_segmented.parquet")

    # 4. Return summary
    metrics = StageMetrics(sample_size=len(df), ...)
    return {"stage_id": STAGE_ID, "metrics": metrics.model_dump(), ...}

if __name__ == "__main__":
    from pathlib import Path
    root = Path(__file__).parent.parent.parent
    out = Path(__file__).parent / "outputs"
    out.mkdir(exist_ok=True)
    run(root, out)
```

## 5. Claude Agent (Agent SDK)

Replace raw `anthropic` client with `claude-agent-sdk`. The agent gets custom in-process MCP tools:

### Tools

| Tool | Description |
|------|-------------|
| `query_clickhouse` | Run read-only SQL against ClickHouse, returns tabular result |
| `get_schema` | Inspect ClickHouse table schema (columns, types, engine) |
| `read_file` | Read a stage output file (JSON, Parquet summary) |
| `write_file` | Write a generated stage script |
| `log_metrics` | Log metrics to MLflow |

### Agent Definitions

| Agent | Tools | Purpose |
|-------|-------|---------|
| `reviewer` | `query_clickhouse`, `get_schema`, `read_file` | Analyze completed stage outputs, validate findings against live data, produce `ClaudeAnalysis` |
| `generator` | `query_clickhouse`, `get_schema`, `write_file` | Generate stage scripts with correct SQL by inspecting actual schema |
| `explorer` | `query_clickhouse`, `get_schema`, `log_metrics` | Free-form exploration, suggest next steps |

### Key Advantages Over Raw API

- Interactive data exploration — reviewer runs follow-up queries to validate
- Schema-aware generation — generator inspects actual columns before writing SQL
- No context pre-packaging — agents pull what they need via tools
- In-process MCP — no subprocess overhead
- Built-in cost tracking via `ResultMessage`

### Output Contract

All agents return structured JSON parsed into existing Pydantic models (`ClaudeAnalysis`, `ProposedRefinement`). The models from `tree.py` are unchanged.

## 6. CLI (`cli/explore.py`)

Registered in root `pyproject.toml` as `pm-explore = "polymarket_pipeline.cli.explore:app"`.

Commands (unchanged from scaffold):

| Command | Action |
|---------|--------|
| `init <name> --desc --hypothesis` | Create strategy dir + root stage |
| `run <strategy> <stage_id>` | Call stage's `run()` function, log to MLflow |
| `review <strategy> <stage_id>` | Load outputs, invoke reviewer agent, save analysis |
| `generate <strategy> <parent_id> <refinement>` | Invoke generator agent, create new stage script |
| `status <strategy> [--mermaid]` | Print exploration tree |
| `suggest <strategy> [-n N]` | Rank pending refinements |

`viz` command removed — MLflow UI at `:5000` covers experiment comparison.

## 7. Dependencies

Added to root `pyproject.toml`:

```toml
[project.optional-dependencies]
exploration = [
    "claude-agent-sdk",
    "mlflow>=2.19.0",
    "typer>=0.14.0",
    "rich>=13.9.0",
    "polars>=1.15.0",
]
```

Existing deps already cover: `clickhouse-connect`, `pydantic`, `structlog`.

Removed from scaffold: `marimo`, `streamlit`, `vectorbt`, `scipy`, `scikit-learn`, `pyarrow` (stage scripts can add per-stage deps as needed).

## What Gets Deleted

The `strategy_explorer/` directory is removed after its contents are migrated:
- `tree.py` → `src/polymarket_pipeline/exploration/tree.py`
- `agent.py` → rewritten using Agent SDK at `src/polymarket_pipeline/exploration/agent.py`
- `cli.py` → adapted at `src/polymarket_pipeline/cli/explore.py`
- `skilled_traders_00_initial.py` → rewritten as plain script template
- `pyproject.toml` → merged into root
- `README.md` → content folded into project docs
