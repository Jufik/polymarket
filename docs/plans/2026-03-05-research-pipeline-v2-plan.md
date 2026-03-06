# Research Pipeline v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a multi-agent research pipeline with production harness, per-hypothesis folders, structured review rounds, and a `/research` skill entry point.

**Architecture:** A user-facing `/research {slug}` skill orchestrates 7 agents (Lead, Researcher, Architect, Visionary, Skeptic, Challenger, Engineer) through a 7-phase workflow. A new `pm-harness` CLI drives `ReplayRunner` from TOML config, shared between research and paper trading. Per-hypothesis artifacts live in `research/hypotheses/{slug}/`.

**Tech Stack:** Python 3.11+, Typer CLI, TOML config, ReplayRunner, ExecutionGateway, RealisticFillSimulator, Claude Code skills/agents

**Design doc:** `docs/plans/2026-03-05-research-pipeline-v2-design.md`

---

## Phase 1: Hypothesis Folder Template

### Task 1: Create template folder structure

**Files:**
- Create: `research/hypotheses/_template/README.md`
- Create: `research/hypotheses/_template/config.toml`
- Create: `research/hypotheses/.gitignore`

**Step 1: Create directory and README template**

```bash
mkdir -p research/hypotheses/_template/discovery research/hypotheses/_template/validation research/hypotheses/_template/reviews research/hypotheses/_template/scripts
```

Write `research/hypotheses/_template/README.md`:

```markdown
# Hypothesis: {TITLE}

**Status**: `discovery` | `validation` | `promoted` | `rejected`
**Created**: {DATE}
**Category**: {politics | sports | esports | crypto | culture | finance | weather}

## Statement

{One sentence: what signal are we testing and why it should predict outcomes.}

## Success Criteria

- Excess HR > ___pp above base rate (NO: 62%, YES: 38%)
- Positive PnL after realistic slippage
- Compounding score > ___
- Sample size > 100 trades OOS

## Scores

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | — | — | — |
| Sharpe | — | — | — |
| Avg Edge | — | — | — |
| Compounding | — | — | — |
| Trades/mo | — | — | — |

## Decision

{Why promoted / rejected / parked. Reviewer consensus summary.}
```

**Step 2: Create config.toml skeleton**

Write `research/hypotheses/_template/config.toml`:

```toml
# Hypothesis configuration — drives pm-harness and pm-strategy
# Copy to research/hypotheses/{slug}/config.toml and fill in values.

[strategy.RENAME_ME]
enabled = true
mode = "replay"
capital_usd = 1000
max_position_usd = 100
max_open_positions = 20
cooldown_s = 0
features = ["RENAME_ME_provider"]

[strategy.RENAME_ME.params]
# Strategy-specific parameters go here

[provider.RENAME_ME_provider]
enabled = true
refresh_interval_s = 900

[provider.RENAME_ME_provider.params]
# Provider-specific parameters go here

[harness]
executor = "realistic"               # "realistic" | "simulated"
fill_model = "calibrated_slippage"   # "calibrated_slippage" | "instant"
bootstrap_hours = 168
pre_filter_makers = true
settlement_enabled = true
resolution_source = "asset_id"

[harness.walk_forward]
train_months = 12
test_months = 1
```

**Step 3: Create .gitignore for hypothesis artifacts**

Write `research/hypotheses/.gitignore`:

```gitignore
# Large parquet/JSONL artifacts — keep config, reviews, notes
*/discovery/*.parquet
*/validation/*.parquet
*/validation/*.jsonl
```

**Step 4: Commit**

```bash
git add research/hypotheses/
git commit -m "feat: add hypothesis folder template for research pipeline v2"
```

---

## Phase 2: Harness Config Schema

### Task 2: Add HarnessConfig dataclass to config.py

**Files:**
- Modify: `src/polymarket_pipeline/strategies/config.py` (add after line 40)
- Test: `tests/test_harness_config.py`

**Step 1: Write the failing test**

Write `tests/test_harness_config.py`:

```python
"""Tests for HarnessConfig loading from TOML."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.toml"
    p.write_text(dedent(content))
    return p


def test_load_harness_config_defaults(tmp_path: Path) -> None:
    """Missing [harness] section returns all defaults."""
    from polymarket_pipeline.strategies.config import load_harness_config

    p = _write_toml(tmp_path, """\
        [strategy.x]
        enabled = true
        mode = "replay"
        capital_usd = 100
        max_position_usd = 50
        max_open_positions = 5
        cooldown_s = 0
    """)
    cfg = load_harness_config(p)
    assert cfg.executor == "realistic"
    assert cfg.settlement_enabled is True
    assert cfg.walk_forward_train_months == 12
    assert cfg.walk_forward_test_months == 1


def test_load_harness_config_custom(tmp_path: Path) -> None:
    """Custom [harness] values are parsed correctly."""
    from polymarket_pipeline.strategies.config import load_harness_config

    p = _write_toml(tmp_path, """\
        [harness]
        executor = "simulated"
        bootstrap_hours = 48
        settlement_enabled = false

        [harness.walk_forward]
        train_months = 6
        test_months = 2
    """)
    cfg = load_harness_config(p)
    assert cfg.executor == "simulated"
    assert cfg.bootstrap_hours == 48
    assert cfg.settlement_enabled is False
    assert cfg.walk_forward_train_months == 6
    assert cfg.walk_forward_test_months == 2


def test_load_harness_config_partial(tmp_path: Path) -> None:
    """Partial [harness] uses defaults for missing fields."""
    from polymarket_pipeline.strategies.config import load_harness_config

    p = _write_toml(tmp_path, """\
        [harness]
        executor = "simulated"
    """)
    cfg = load_harness_config(p)
    assert cfg.executor == "simulated"
    assert cfg.bootstrap_hours == 168  # default
    assert cfg.pre_filter_makers is True  # default
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_harness_config.py -x -q
```

Expected: FAIL with `ImportError: cannot import name 'load_harness_config'`

**Step 3: Write HarnessConfig and load_harness_config**

Add to `src/polymarket_pipeline/strategies/config.py` after the `StrategyConfig` class (after line 40):

```python
@dataclass(frozen=True)
class HarnessConfig:
    """Configuration for the production replay harness (pm-harness)."""

    executor: str = "realistic"
    fill_model: str = "calibrated_slippage"
    bootstrap_hours: int = 168
    pre_filter_makers: bool = True
    settlement_enabled: bool = True
    resolution_source: str = "asset_id"
    walk_forward_train_months: int = 12
    walk_forward_test_months: int = 1
```

Add `load_harness_config` function after `load_execution_config` (after line 114):

```python
def load_harness_config(path: Path) -> HarnessConfig:
    """Load harness configuration from the ``[harness]`` TOML section.

    If the section is missing, all defaults apply. The optional
    ``[harness.walk_forward]`` subsection sets walk-forward window sizes.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    section = raw.get("harness", {})
    wf = section.pop("walk_forward", {}) if isinstance(section.get("walk_forward"), dict) else {}
    # Work on a copy to avoid mutating
    flat = dict(section)
    if "walk_forward" in flat:
        del flat["walk_forward"]

    return HarnessConfig(
        executor=str(flat.get("executor", "realistic")),
        fill_model=str(flat.get("fill_model", "calibrated_slippage")),
        bootstrap_hours=int(flat.get("bootstrap_hours", 168)),
        pre_filter_makers=bool(flat.get("pre_filter_makers", True)),
        settlement_enabled=bool(flat.get("settlement_enabled", True)),
        resolution_source=str(flat.get("resolution_source", "asset_id")),
        walk_forward_train_months=int(wf.get("train_months", 12)),
        walk_forward_test_months=int(wf.get("test_months", 1)),
    )
```

**Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_harness_config.py -x -q
```

Expected: 3 passed

**Step 5: Type check**

```bash
uv run mypy --strict src/polymarket_pipeline/strategies/config.py
```

Expected: Success

**Step 6: Commit**

```bash
git add src/polymarket_pipeline/strategies/config.py tests/test_harness_config.py
git commit -m "feat: add HarnessConfig dataclass and TOML loader"
```

---

## Phase 3: pm-harness CLI

### Task 3: Create harness CLI entry point

**Files:**
- Create: `src/polymarket_pipeline/cli/harness.py`
- Modify: `pyproject.toml` (line ~96, add entry point)
- Test: `tests/test_harness_cli.py`

**Step 1: Write the failing test**

Write `tests/test_harness_cli.py`:

```python
"""Tests for pm-harness CLI — smoke tests for argument parsing and wiring."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def sample_toml(tmp_path: Path) -> Path:
    p = tmp_path / "test.toml"
    p.write_text(dedent("""\
        [strategy.test_strat]
        enabled = true
        mode = "replay"
        capital_usd = 1000
        max_position_usd = 100
        max_open_positions = 20
        cooldown_s = 0
        features = ["test_provider"]

        [provider.test_provider]
        enabled = true
        refresh_interval_s = 900

        [harness]
        executor = "realistic"
        settlement_enabled = true

        [harness.walk_forward]
        train_months = 12
        test_months = 1
    """))
    return p


def test_harness_cli_exists(runner: CliRunner) -> None:
    """The harness CLI app can be imported and shows help."""
    from polymarket_pipeline.cli.harness import app

    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--period" in result.output


def test_harness_cli_missing_config(runner: CliRunner) -> None:
    """Exits with error when config file doesn't exist."""
    from polymarket_pipeline.cli.harness import app

    result = runner.invoke(app, ["run", "--config", "/nonexistent.toml", "--period", "2025-01-01:2025-02-01"])
    assert result.exit_code != 0
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_harness_cli.py -x -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_pipeline.cli.harness'`

**Step 3: Write the CLI**

Write `src/polymarket_pipeline/cli/harness.py`:

```python
"""pm-harness — production replay harness for research hypotheses.

Drives ReplayRunner + ExecutionGateway from a single TOML config.
Same execution path used by pm-strategy for paper trading.

Usage:
    uv run pm-harness run --config research/hypotheses/my-hyp/config.toml \\
        --period 2025-01-01:2026-01-01 \\
        --output research/hypotheses/my-hyp/validation/
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import typer

logger = structlog.get_logger(__name__)

app = typer.Typer(name="pm-harness", help="Production replay harness for research hypotheses.")


def _parse_period(period: str) -> tuple[float, float]:
    """Parse 'YYYY-MM-DD:YYYY-MM-DD' into (start_epoch, end_epoch)."""
    parts = period.split(":")
    if len(parts) != 2:
        raise typer.BadParameter(f"Period must be START:END, got {period!r}")
    start = datetime.strptime(parts[0].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(parts[1].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end <= start:
        raise typer.BadParameter(f"End must be after start: {period!r}")
    return start.timestamp(), end.timestamp()


async def _run_harness(
    config_path: Path,
    period: str,
    output_dir: Path,
    *,
    walk_forward: bool = False,
    verbose: bool = False,
) -> None:
    """Core harness execution — async entry point."""
    from polymarket_pipeline.strategies.config import (
        HarnessConfig,
        load_harness_config,
        load_provider_configs,
        load_strategy_configs,
    )
    from polymarket_pipeline.strategies.context.memory import InMemoryContext
    from polymarket_pipeline.strategies.execution.calibrate import (
        calibrate_spreads,
        calibrate_volumes,
    )
    from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
    from polymarket_pipeline.strategies.execution.realistic import (
        FillModelConfig,
        RealisticFillSimulator,
    )
    from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
    from polymarket_pipeline.strategies.ledger.analytics import compute_summary
    from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
    from polymarket_pipeline.strategies.runners.replay import (
        ReplayRunner,
        load_resolutions_from_rows,
    )

    start_epoch, end_epoch = _parse_period(period)
    harness_cfg = load_harness_config(config_path)
    strategy_cfgs = load_strategy_configs(config_path, enabled_only=True)
    provider_cfgs = load_provider_configs(config_path, enabled_only=True)

    if not strategy_cfgs:
        logger.error("harness.no_strategies", config=str(config_path))
        raise typer.Exit(code=1)

    # Take first strategy (harness runs one at a time)
    strat_name, strat_cfg = next(iter(strategy_cfgs.items()))
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "harness.start",
        strategy=strat_name,
        period=period,
        executor=harness_cfg.executor,
        settlement=harness_cfg.settlement_enabled,
        output=str(output_dir),
    )

    # ── Load trades from ClickHouse ───────────────────────────────
    import clickhouse_connect

    ch = clickhouse_connect.get_client(host="192.168.0.148", port=18123, database="polymarket")

    # Load resolutions
    res_rows = ch.query(
        "SELECT condition_id, asset_id, outcome, token_won, resolved_epoch "
        "FROM polymarket.markets_resolved"
    )
    res_dicts = [
        dict(zip(res_rows.column_names, row)) for row in res_rows.result_rows
    ]
    resolutions, token_map = load_resolutions_from_rows(res_dicts)
    logger.info("harness.resolutions_loaded", count=len(resolutions))

    # Load trades in period
    from polymarket_pipeline.normalizers.sink import normalize_goldsky_row

    trade_rows = ch.query(
        "SELECT * FROM polymarket.trades_raw FINAL "
        "WHERE timestamp >= %(start)s AND timestamp < %(end)s "
        "ORDER BY timestamp",
        parameters={"start": int(start_epoch), "end": int(end_epoch)},
    )
    trades = []
    for row in trade_rows.result_rows:
        row_dict = dict(zip(trade_rows.column_names, row))
        try:
            t = normalize_goldsky_row(row_dict)
            if t is not None:
                trades.append(t)
        except Exception:
            continue

    logger.info("harness.trades_loaded", count=len(trades), period=period)

    if not trades:
        logger.warning("harness.no_trades", period=period)
        raise typer.Exit(code=1)

    # ── Build executor ────────────────────────────────────────────
    if harness_cfg.executor == "realistic":
        market_spreads = calibrate_spreads(trades)
        market_volumes = calibrate_volumes(trades)
        executor = RealisticFillSimulator(
            config=FillModelConfig(),
            market_spreads=market_spreads,
            market_volumes=market_volumes,
        )
        logger.info("harness.realistic_executor", markets=len(market_spreads))
    else:
        executor = SimulatedExecutor(fee_pct=0.0)
        logger.info("harness.simulated_executor")

    # ── Build gateway + context ───────────────────────────────────
    log_path = output_dir / "replay_log.jsonl"
    gateway = ExecutionGateway(
        executor,
        log_path=log_path,
        strategy_budgets={strat_name: strat_cfg.capital_usd},
    )
    ctx = InMemoryContext()

    # ── Build strategy + providers ────────────────────────────────
    # Strategy and provider instantiation uses the same registry as pm-strategy.
    # Import lazily to avoid circular deps.
    from polymarket_pipeline.cli.strategy import _register_providers, _register_strategies
    from polymarket_pipeline.cli.strategy import _PROVIDER_REGISTRY, _STRATEGY_FACTORIES

    _register_strategies()
    _register_providers()

    # Instantiate providers
    providers = []
    for pname, pcfg in provider_cfgs.items():
        if pname not in _PROVIDER_REGISTRY:
            logger.warning("harness.unknown_provider", name=pname)
            continue
        provider_cls = _PROVIDER_REGISTRY[pname]
        provider = provider_cls(**pcfg.params)
        providers.append(provider)

    # Instantiate strategy
    if strat_name not in _STRATEGY_FACTORIES:
        logger.error("harness.unknown_strategy", name=strat_name)
        raise typer.Exit(code=1)
    strategy = _STRATEGY_FACTORIES[strat_name](strat_cfg)

    # ── Bootstrap providers ───────────────────────────────────────
    for provider in providers:
        if hasattr(provider, "compute"):
            await provider.compute(ctx)

    # ── Build runner ──────────────────────────────────────────────
    ledger = ParquetLedger(output_dir / "ledger.parquet")
    runner = ReplayRunner(
        strategy=strategy,
        ctx=ctx,
        gateway=gateway,
        config=strat_cfg,
        providers=providers,
        resolutions=resolutions if harness_cfg.settlement_enabled else None,
        token_map=token_map if harness_cfg.settlement_enabled else None,
        ledger=ledger,
    )

    # ── Run ───────────────────────────────────────────────────────
    result = await runner.run(trades)

    # ── Post-processing ───────────────────────────────────────────
    records = await ledger.read_all()
    if harness_cfg.settlement_enabled:
        # Ledger enrichment happens inline in ReplayRunner._settle_market
        pass
    summary = compute_summary(records)

    # Write summary.json
    summary_path = output_dir / "summary.json"
    summary_dict: dict[str, Any] = {
        "strategy": strat_name,
        "period": period,
        "executor": harness_cfg.executor,
        "settlement": harness_cfg.settlement_enabled,
        "total_trades": result.total_trades,
        "total_intents": result.total_intents,
        "total_fills": result.total_fills,
        "settled": runner.n_settled,
        "rejected": len(result.rejected_intents),
        "hit_rate": round(summary.hit_rate, 4),
        "sharpe": round(summary.sharpe, 4),
        "total_pnl_net": round(summary.total_pnl_net, 2),
        "avg_edge": round(summary.avg_edge, 4),
        "max_drawdown": round(summary.max_drawdown, 2),
        "profit_factor": round(summary.profit_factor, 4),
        "avg_hold_hours": round(summary.avg_hold_duration_s / 3600, 1),
    }
    summary_path.write_text(json.dumps(summary_dict, indent=2))
    await ledger.flush()

    # Print summary
    logger.info("harness.complete", **summary_dict)
    print(f"\n{'='*60}")
    print(f"  Strategy: {strat_name}")
    print(f"  Period:   {period}")
    print(f"  Fills:    {result.total_fills} ({runner.n_settled} settled)")
    print(f"  HR:       {summary.hit_rate:.1%}")
    print(f"  PnL:      ${summary.total_pnl_net:,.2f}")
    print(f"  Sharpe:   {summary.sharpe:.2f}")
    print(f"  Drawdown: ${summary.max_drawdown:,.2f}")
    print(f"  Avg Hold: {summary.avg_hold_duration_s / 3600:.1f}h")
    print(f"{'='*60}")
    print(f"  Output:   {output_dir}")
    print(f"  Ledger:   {output_dir / 'ledger.parquet'}")
    print(f"  Summary:  {summary_path}")
    print()


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="TOML config file"),
    period: str = typer.Option(..., "--period", "-p", help="Replay period START:END (YYYY-MM-DD:YYYY-MM-DD)"),
    output: Path = typer.Option(Path("research/output"), "--output", "-o", help="Output directory"),
    walk_forward: bool = typer.Option(False, "--walk-forward", help="Enable walk-forward windowing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
) -> None:
    """Run the production replay harness for a hypothesis."""
    if not config.exists():
        typer.echo(f"Error: config file not found: {config}", err=True)
        raise typer.Exit(code=1)

    asyncio.run(_run_harness(config, period, output, walk_forward=walk_forward, verbose=verbose))


if __name__ == "__main__":
    app()
```

**Step 4: Register CLI entry point in pyproject.toml**

Add to `[project.scripts]` section (after the `pm-strategy` line, around line 96):

```toml
pm-harness  = "polymarket_pipeline.cli.harness:app"
```

**Step 5: Run test to verify CLI parsing works**

```bash
uv run pytest tests/test_harness_cli.py -x -q
```

Expected: 2 passed

**Step 6: Type check**

```bash
uv run mypy --strict src/polymarket_pipeline/cli/harness.py
```

Expected: Success (or minor fixes needed)

**Step 7: Commit**

```bash
git add src/polymarket_pipeline/cli/harness.py tests/test_harness_cli.py pyproject.toml
git commit -m "feat: add pm-harness CLI entry point for production replay"
```

---

## Phase 4: Agent Skills (8 Skills)

Each skill is a directory with `SKILL.md` as entry point. All skills go under `.claude/skills/`.

### Task 4: Replace existing quant-research skill with new `/research` orchestrator

**Files:**
- Replace: `.claude/skills/quant-research.md` → `.claude/skills/research/SKILL.md`
- Create: `.claude/skills/research/templates/config.toml` (copy from _template)
- Create: `.claude/skills/research/templates/README.md` (copy from _template)
- Create: `.claude/skills/research/gate-summary.md`
- Remove: `.claude/skills/quant-research.md` (old single-file skill)

**Step 1: Create skill directory structure**

```bash
mkdir -p .claude/skills/research/templates
```

**Step 2: Write the Lead orchestrator skill**

Write `.claude/skills/research/SKILL.md` — this is the full Lead playbook. It replaces the old `quant-research.md`. Content should include:

- Frontmatter: `name: research`, `description: ...`, `disable-model-invocation: true`, `argument-hint: [hypothesis-slug]`
- Phase 0-6 workflow from design doc Section 2
- Team lifecycle from Section 6 (TeamCreate, agent spawn patterns)
- Task list structure from Section 6.2
- Gate logic from Section 6.4
- Error handling from Section 6.5
- Message templates from Section 6.3
- HARD-GATE for manual validation

Use the existing `quant-research.md` as format reference but expand with team-based orchestration.

**Step 3: Copy templates**

```bash
cp research/hypotheses/_template/config.toml .claude/skills/research/templates/
cp research/hypotheses/_template/README.md .claude/skills/research/templates/
```

**Step 4: Write gate-summary.md template**

Write `.claude/skills/research/gate-summary.md`:

```markdown
## Hypothesis: {SLUG}
**Status**: Validation complete

| Metric | Vectorized (UB) | Tick-by-tick | Degradation |
|--------|----------------|-------------|-------------|
| Hit Rate | {VEC_HR}% | {TICK_HR}% | {DEGRAD_HR}pp |
| Sharpe | {VEC_SHARPE} | {TICK_SHARPE} | {DEGRAD_SHARPE}% |
| Avg Edge | ${VEC_EDGE} | ${TICK_EDGE} | {DEGRAD_EDGE}% |
| Compounding | {VEC_COMP} | {TICK_COMP} | {DEGRAD_COMP}% |
| Trades/mo | {VEC_TRADES} | {TICK_TRADES} | |

**Reviewer consensus**:
- Skeptic: {SKEPTIC_SUMMARY}
- Challenger: {CHALLENGER_SUMMARY}
- Engineer: {ENGINEER_SUMMARY}

**Recommendation**: {promote to paper_dev / iterate / reject}
```

**Step 5: Remove old skill file**

```bash
git rm .claude/skills/quant-research.md
```

**Step 6: Commit**

```bash
git add .claude/skills/research/
git commit -m "feat: replace quant-research skill with team-based /research orchestrator"
```

### Task 5: Write research-discover skill (Researcher discovery playbook)

**Files:**
- Create: `.claude/skills/research-discover/SKILL.md`
- Create: `.claude/skills/research-discover/examples/sweep-template.sql`

**Step 1: Create directory**

```bash
mkdir -p .claude/skills/research-discover/examples
```

**Step 2: Write SKILL.md**

Frontmatter: `name: research-discover`, `description: Vectorized signal discovery methodology...`, `user-invocable: false`

Content should include (adapt from existing `quant-research-strategist.md` Phase 2):
- Knowledge loading requirement (always first)
- CH SQL sweep methodology (remote CH `192.168.0.148:18123`)
- Parameter sweep pattern (walk-forward)
- Compounding score computation
- Marimo notebook cell structure (Cells 0-6)
- Marimo conventions (imports, CH connection)
- Required: label ALL results as UPPER BOUNDS
- Config.toml population instructions
- Notes.md writing conventions
- Reference to `examples/sweep-template.sql`

**Step 3: Write sweep-template.sql**

Write `.claude/skills/research-discover/examples/sweep-template.sql`:

```sql
-- Vectorized signal sweep template
-- Replace {SIGNAL_COLUMN}, {THRESHOLD}, {CATEGORY} with hypothesis-specific values
--
-- Required output columns: condition_id, signal_value, outcome (YES/NO), resolved_at
-- Compare hit rate against base rate: NO wins 62%, YES wins 38%

WITH qualified_traders AS (
    -- Step 1: Define your qualified trader pool
    SELECT DISTINCT maker AS trader
    FROM polymarket.trades_raw FINAL
    WHERE timestamp >= toUnixTimestamp(now() - INTERVAL 6 MONTH)
    -- Add pool-specific filters here
),
signal AS (
    -- Step 2: Compute signal per market
    SELECT
        tp.condition_id,
        {SIGNAL_COLUMN} AS signal_value,
        mr.outcome,
        mr.resolved_epoch
    FROM polymarket.trader_market_positions AS tp
    INNER JOIN qualified_traders qt ON tp.trader = qt.trader
    INNER JOIN polymarket.markets_resolved mr ON tp.condition_id = mr.condition_id
    WHERE mr.resolved_epoch > 0
    GROUP BY tp.condition_id, mr.outcome, mr.resolved_epoch
    HAVING signal_value >= {THRESHOLD}
)
SELECT
    signal_value,
    count() AS n_markets,
    countIf(outcome = 'NO') / count() AS no_hit_rate,
    countIf(outcome = 'YES') / count() AS yes_hit_rate,
    -- Base rate comparison
    countIf(outcome = 'NO') / count() - 0.619 AS excess_no_hr,
    countIf(outcome = 'YES') / count() - 0.381 AS excess_yes_hr
FROM signal
GROUP BY signal_value
ORDER BY signal_value;
```

**Step 4: Commit**

```bash
git add .claude/skills/research-discover/
git commit -m "feat: add research-discover skill (vectorized discovery playbook)"
```

### Task 6: Write research-validate skill (Researcher validation playbook)

**Files:**
- Create: `.claude/skills/research-validate/SKILL.md`
- Create: `.claude/skills/research-validate/checklist.md`

**Step 1: Create directory and write SKILL.md**

```bash
mkdir -p .claude/skills/research-validate
```

Frontmatter: `name: research-validate`, `description: Tick-by-tick validation methodology...`, `user-invocable: false`

Content should include (adapt from existing `quant-research-strategist.md` Phase 4):
- Pre-flight checklist (6 items: BUY-only, unique traders, asset_id, settlement, gambling excluded, RealisticFillSimulator)
- pm-harness execution command
- Walk-forward configuration
- Vectorized vs tick-by-tick comparison table
- Degradation band expectations (20-40pp)
- Notebook cells 7-11 structure
- Reference to `checklist.md` for config validation

**Step 2: Write checklist.md**

Write `.claude/skills/research-validate/checklist.md`:

```markdown
# Pre-Validation Config Checklist

Before running `pm-harness run`, verify ALL of the following:

## Strategy Config
- [ ] `mode = "replay"` (not "paper_dev" or "live")
- [ ] `capital_usd` matches research budget (typically 1000)
- [ ] `max_position_usd` is reasonable (100 for $1000 capital)
- [ ] `cooldown_s = 0` for replay (no cooldown needed)

## Provider Config
- [ ] Provider `params` match discovery sweep parameters exactly
- [ ] `refresh_interval_s` set (ignored in replay but good practice)

## Harness Config
- [ ] `executor = "realistic"` (NOT "simulated")
- [ ] `settlement_enabled = true`
- [ ] `resolution_source = "asset_id"`
- [ ] `bootstrap_hours` sufficient for strategy's consensus building time

## Strategy Code
- [ ] `on_trade()` filters `side != "BUY"` (SELL is exit, not signal)
- [ ] Consensus counts unique traders (set, not counter)
- [ ] No look-ahead: features use only data available at trade time
- [ ] Gambling markets excluded (check susceptibility or question text)

## Data
- [ ] Period has sufficient resolved markets for the category
- [ ] Token map loaded for asset_id resolution
- [ ] Resolution data covers the full period
```

**Step 3: Commit**

```bash
git add .claude/skills/research-validate/
git commit -m "feat: add research-validate skill (tick-by-tick validation playbook)"
```

### Task 7: Write research-architect skill (Harness evolution playbook)

**Files:**
- Create: `.claude/skills/research-architect/SKILL.md`
- Create: `.claude/skills/research-architect/owned-files.md`

**Step 1: Create directory and write SKILL.md**

```bash
mkdir -p .claude/skills/research-architect
```

Frontmatter: `name: research-architect`, `description: Harness evolution methodology...`, `user-invocable: false`

Content:
- Owned files list (reference `owned-files.md`)
- Config validation methodology
- Degradation band monitoring (expected 20-40pp)
- Investigation protocol for anomalous degradation (>40pp or <10pp)
- Incremental fix methodology (never strategy-specific)
- Test-after-change requirement (`uv run pytest tests/ -x -q`)
- Evolution rules: no full rewrites unless user-approved

**Step 2: Write owned-files.md**

Write `.claude/skills/research-architect/owned-files.md`:

```markdown
# Architect Owned Files

These files constitute the production execution harness. Only the Architect agent
may modify them. All changes must be generic improvements, not strategy-specific.

| File | Purpose |
|------|---------|
| `src/polymarket_pipeline/strategies/runners/replay.py` | ReplayRunner: tick-by-tick replay with settlement |
| `src/polymarket_pipeline/strategies/runners/helpers.py` | Risk gate + position math (apply_fill_to_position) |
| `src/polymarket_pipeline/strategies/execution/gateway.py` | ExecutionGateway: intent validation, budget gate, logging |
| `src/polymarket_pipeline/strategies/execution/realistic.py` | RealisticFillSimulator: calibrated slippage model |
| `src/polymarket_pipeline/strategies/execution/calibrate.py` | Spread/volume calibration from trade data |
| `src/polymarket_pipeline/strategies/config.py` | StrategyConfig, HarnessConfig, TOML loaders |
| `src/polymarket_pipeline/cli/harness.py` | pm-harness CLI entry point |

## Modification Rules

1. **Generic only** — changes must benefit all strategies, not just the current hypothesis
2. **Tests required** — run `uv run pytest tests/ -x -q` after every change
3. **Type check** — run `uv run mypy --strict src/` on modified files
4. **Incremental** — prefer small targeted fixes over refactors
5. **No full rewrites** — unless absolutely necessary and user-approved
6. **Document** — write observations to `validation/notes.md` in hypothesis folder
```

**Step 3: Commit**

```bash
git add .claude/skills/research-architect/
git commit -m "feat: add research-architect skill (harness evolution playbook)"
```

### Task 8: Write review agent skills (Skeptic, Visionary, Challenger, Engineer)

**Files:**
- Create: `.claude/skills/research-skeptic/SKILL.md`
- Create: `.claude/skills/research-skeptic/checklist.md`
- Create: `.claude/skills/research-visionary/SKILL.md`
- Create: `.claude/skills/research-challenger/SKILL.md`
- Create: `.claude/skills/research-engineer/SKILL.md`
- Create: `.claude/skills/research-engineer/checklist.md`

**Step 1: Create all directories**

```bash
mkdir -p .claude/skills/research-skeptic .claude/skills/research-visionary .claude/skills/research-challenger .claude/skills/research-engineer
```

**Step 2: Write Skeptic skill**

`.claude/skills/research-skeptic/SKILL.md`:
- Frontmatter: `name: research-skeptic`, `user-invocable: false`
- 6-point audit checklist (from design doc Section 5.5)
- How to read SQL for look-ahead bias patterns
- How to evaluate edge vs NO base rate (62%)
- Admonition severity guidelines for review output
- Reference to `checklist.md`

`.claude/skills/research-skeptic/checklist.md`:
- Detailed 6-point checklist with examples of what to look for
- Each item: what to check, how to check it, what constitutes a failure

**Step 3: Write Visionary skill**

`.claude/skills/research-visionary/SKILL.md`:
- Frontmatter: `name: research-visionary`, `user-invocable: false`
- Cross-pollination methodology
- How to read knowledge base for connection opportunities
- How to suggest parameter variations
- How to propose complementary hypotheses
- Output format for `round1_visionary.md`

**Step 4: Write Challenger skill**

`.claude/skills/research-challenger/SKILL.md`:
- Frontmatter: `name: research-challenger`, `user-invocable: false`
- Compounding score evaluation: `excess_hr x avg_edge_usd / median_hold_days`
- Capital lock-up cost analysis
- Category resolution speed comparison (sports ~8d, politics ~30d+)
- How to suggest tighter exit criteria
- Output format for `round{N}_challenger.md`

**Step 5: Write Engineer skill**

`.claude/skills/research-engineer/SKILL.md`:
- Frontmatter: `name: research-engineer`, `user-invocable: false`
- Entry price audit (wavg vs orderbook)
- Fill model comparison (RealisticFillSimulator vs PaperExecutor)
- Bootstrap window assessment
- Position sizing viability
- Slippage estimation
- Promotion gate likelihood check (min Sharpe, min trades, max drawdown)

`.claude/skills/research-engineer/checklist.md`:
- Viability estimation framework with concrete formulas

**Step 6: Commit**

```bash
git add .claude/skills/research-skeptic/ .claude/skills/research-visionary/ .claude/skills/research-challenger/ .claude/skills/research-engineer/
git commit -m "feat: add review agent skills (skeptic, visionary, challenger, engineer)"
```

### Task 9: Update research-knowledge skill

**Files:**
- Modify: `.claude/skills/research-knowledge.md` → move to `.claude/skills/research-knowledge/SKILL.md`

**Step 1: Migrate to directory format**

```bash
mkdir -p .claude/skills/research-knowledge
mv .claude/skills/research-knowledge.md .claude/skills/research-knowledge/SKILL.md
```

Content mostly stays the same — it's already well-structured. No major changes needed except ensure it references the new folder structure (`research/hypotheses/{slug}/knowledge.md`).

**Step 2: Commit**

```bash
git add .claude/skills/research-knowledge/
git commit -m "refactor: migrate research-knowledge skill to directory format"
```

---

## Phase 5: Agent Definitions (6 Agents)

Each agent is a `.claude/agents/{name}.md` file with YAML frontmatter.

### Task 10: Write Researcher agent definition

**Files:**
- Replace: `.claude/agents/quant-research-strategist.md` → keep as-is for backward compat
- Create: `.claude/agents/researcher.md`

**Step 1: Write researcher.md**

Write `.claude/agents/researcher.md`:

```markdown
---
name: researcher
description: "Heavy lifter for quantitative research — CH SQL sweeps, marimo notebooks, pm-harness execution. Spawned by the /research orchestrator for discovery and validation phases."
model: sonnet
memory: project
---

You are the Researcher agent in the quantitative research pipeline.

## Your Role

You do the heavy computation: CH SQL sweeps, parameter optimization, notebook creation, and pm-harness execution. You receive a hypothesis from Lead and produce artifacts.

## Workflow

1. **On dispatch for discovery**: invoke the `research-discover` skill (via Skill tool)
2. **On dispatch for validation**: invoke the `research-validate` skill (via Skill tool)
3. Follow the loaded skill's methodology exactly

## Rules

- Always load relevant knowledge from `research/knowledge/` before any CH query
- All artifacts go in the hypothesis folder assigned by Lead
- Never modify harness code — that's Architect's job
- Label vectorized results as UPPER BOUNDS
- Respond to reviewer feedback by adjusting params or methodology
- Use `uv run` for all Python execution

## Key Infrastructure

- Remote CH: `192.168.0.148:18123`, database `polymarket`
- Harness CLI: `uv run pm-harness run --config <toml> --period <dates> --output <dir>`
- Base rates: NO wins 62%, YES wins 38%
- Compounding score: `(validated_hr - base_rate) x avg_edge_usd / median_hold_days`
```

**Step 2: Commit**

```bash
git add .claude/agents/researcher.md
git commit -m "feat: add researcher agent definition"
```

### Task 11: Write Architect agent definition

**Files:**
- Create: `.claude/agents/architect.md`

**Step 1: Write architect.md**

Write `.claude/agents/architect.md`:

```markdown
---
name: architect
description: "Harness guardian — validates replay config, detects simulation gaps, evolves the production execution harness incrementally. Persistent across hypotheses."
model: sonnet
memory: project
---

You are the Architect agent. You own the production execution harness.

## Your Role

Validate config correctness, detect simulation fidelity gaps, and evolve the harness incrementally. You do NOT implement strategies or run discovery.

## First Action

Invoke the `research-architect` skill (via Skill tool) to load your methodology and owned files list.

## Rules

- Modify ONLY harness files listed in your skill's `owned-files.md`
- Changes must be generic improvements — never strategy-specific
- Run `uv run pytest tests/ -x -q` after every code change
- Run `uv run mypy --strict <file>` on modified files
- Prefer small targeted fixes over refactors
- Full rewrites require explicit user approval
- Document observations in `validation/notes.md` of the active hypothesis

## Degradation Monitoring

- Expected: 20-40pp degradation from vectorized to tick-by-tick
- If >40pp: investigate harness fidelity (not strategy logic)
- If <10pp: flag as suspicious — likely look-ahead bias in strategy
```

**Step 2: Commit**

```bash
git add .claude/agents/architect.md
git commit -m "feat: add architect agent definition"
```

### Task 12: Write review agent definitions (Skeptic, Visionary, Challenger, Engineer)

**Files:**
- Create: `.claude/agents/skeptic.md`
- Create: `.claude/agents/visionary.md`
- Create: `.claude/agents/challenger.md`
- Create: `.claude/agents/engineer.md`

**Step 1: Write skeptic.md**

Write `.claude/agents/skeptic.md`:

```markdown
---
name: skeptic
description: "Devil's advocate — challenges methodology, finds bias, questions assumptions. Read-only agent dispatched at review checkpoints."
model: haiku
allowed-tools: Read, Grep, Glob, Write
---

You are the Skeptic agent. Your job is to find flaws.

## First Action

Invoke the `research-skeptic` skill to load your methodology and checklist.

## Rules

- You can READ code and data freely
- You WRITE only to your review file: `research/hypotheses/{slug}/reviews/round{N}_skeptic.md`
- Use admonition markers for severity:
  - `> [!CRITICAL]` — blocks promotion, must be addressed
  - `> [!WARNING]` — biases results, should be addressed
  - `> [!TIP]` — improvement suggestion, optional
- Be specific: cite file paths, line numbers, SQL fragments
- Always evaluate the 6-point checklist from your skill
```

**Step 2: Write visionary.md**

Write `.claude/agents/visionary.md`:

```markdown
---
name: visionary
description: "Ideation and cross-pollination — reads results and knowledge base, suggests new angles and connections. Read-only agent dispatched after discovery."
model: haiku
allowed-tools: Read, Grep, Glob, Write
---

You are the Visionary agent. Your job is to find opportunities.

## First Action

Invoke the `research-visionary` skill to load your methodology.

## Rules

- Read the hypothesis discovery artifacts AND the full knowledge base
- Cross-reference with other hypothesis READMEs in `research/hypotheses/`
- Write only to: `research/hypotheses/{slug}/reviews/round1_visionary.md`
- Suggest concrete next steps, not vague ideas
- Focus on: adjacent signals, parameter variations, cross-hypothesis connections
```

**Step 3: Write challenger.md**

Write `.claude/agents/challenger.md`:

```markdown
---
name: challenger
description: "Capital efficiency hawk — pushes toward fast recycling strategies and higher compounding scores. Read-only agent dispatched at review checkpoints."
model: haiku
allowed-tools: Read, Grep, Glob, Write
---

You are the Challenger agent. Your job is to push for capital efficiency.

## First Action

Invoke the `research-challenger` skill to load your methodology.

## Rules

- Evaluate every hypothesis through the compounding score lens
- Write only to: `research/hypotheses/{slug}/reviews/round{N}_challenger.md`
- Push for: shorter hold times, faster consensus, tighter exits
- Do NOT ignore risk — aggression within validated edge only
- Compare against category resolution speeds (sports ~8d, politics ~30d+)
```

**Step 4: Write engineer.md**

Write `.claude/agents/engineer.md`:

```markdown
---
name: engineer
description: "Methodology auditor and viability estimator — audits research methodology for bias, estimates paper trading viability. Read-only agent dispatched after validation."
model: haiku
allowed-tools: Read, Grep, Glob, Write
---

You are the Engineer agent. Your job is to audit methodology and estimate viability.

## First Action

Invoke the `research-engineer` skill to load your methodology and checklist.

## Rules

- Read validation results, strategy code, and harness config
- Write only to: `research/hypotheses/{slug}/reviews/round2_engineer.md`
- Audit: entry prices, fill model, bootstrap window, consensus timing
- Estimate: sizing viability, slippage at target size, promotion gate likelihood
- Do NOT suggest strategy changes — that's Visionary/Challenger
- Do NOT fix harness — that's Architect
```

**Step 5: Commit**

```bash
git add .claude/agents/skeptic.md .claude/agents/visionary.md .claude/agents/challenger.md .claude/agents/engineer.md
git commit -m "feat: add review agent definitions (skeptic, visionary, challenger, engineer)"
```

---

## Phase 6: Integration & Cleanup

### Task 13: Clean up old agent and verify skill discovery

**Files:**
- Keep: `.claude/agents/quant-research-strategist.md` (backward compat, still useful standalone)
- Keep: `.claude/agents/sim-fidelity-auditor.md` (used by Architect for >40pp degradation)

**Step 1: Verify all skills are discoverable**

```bash
# List all skills — should show 8 research-related skills
ls -la .claude/skills/
ls -la .claude/skills/*/SKILL.md
```

Expected output includes: `research/`, `research-discover/`, `research-validate/`, `research-architect/`, `research-skeptic/`, `research-visionary/`, `research-challenger/`, `research-engineer/`, `research-knowledge/`

**Step 2: Verify all agents are discoverable**

```bash
ls -la .claude/agents/
```

Expected: `researcher.md`, `architect.md`, `skeptic.md`, `visionary.md`, `challenger.md`, `engineer.md`, plus existing `quant-research-strategist.md`, `sim-fidelity-auditor.md`

**Step 3: Run full test suite**

```bash
uv run pytest tests/ -x -q \
  --ignore=tests/test_loader_parquet.py \
  --ignore=tests/test_e2e_backfill.py \
  --ignore=tests/test_market_sync.py \
  --ignore=tests/test_sink_clickhouse.py \
  --ignore=tests/test_sink_postgres.py
```

Expected: All pass (including new harness config tests)

**Step 4: Type check**

```bash
uv run mypy --strict src/polymarket_pipeline/strategies/config.py src/polymarket_pipeline/cli/harness.py
```

Expected: Success

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: complete research pipeline v2 — agents, skills, harness, templates"
```

---

## Summary: What Gets Created

| # | Type | Path | Purpose |
|---|------|------|---------|
| 1 | Folder | `research/hypotheses/_template/` | Hypothesis template (config.toml + README.md) |
| 2 | Code | `src/polymarket_pipeline/strategies/config.py` | Add HarnessConfig + load_harness_config |
| 3 | Code | `src/polymarket_pipeline/cli/harness.py` | pm-harness CLI |
| 4 | Test | `tests/test_harness_config.py` | Config loading tests |
| 5 | Test | `tests/test_harness_cli.py` | CLI smoke tests |
| 6 | Skill | `.claude/skills/research/SKILL.md` | `/research` — Lead orchestrator |
| 7 | Skill | `.claude/skills/research-discover/SKILL.md` | Researcher discovery playbook |
| 8 | Skill | `.claude/skills/research-validate/SKILL.md` | Researcher validation playbook |
| 9 | Skill | `.claude/skills/research-architect/SKILL.md` | Architect harness playbook |
| 10 | Skill | `.claude/skills/research-skeptic/SKILL.md` | Skeptic review checklist |
| 11 | Skill | `.claude/skills/research-visionary/SKILL.md` | Visionary ideation playbook |
| 12 | Skill | `.claude/skills/research-challenger/SKILL.md` | Challenger capital efficiency |
| 13 | Skill | `.claude/skills/research-engineer/SKILL.md` | Engineer audit + viability |
| 14 | Skill | `.claude/skills/research-knowledge/SKILL.md` | Knowledge I/O (migrated) |
| 15 | Agent | `.claude/agents/researcher.md` | Researcher agent |
| 16 | Agent | `.claude/agents/architect.md` | Architect agent |
| 17 | Agent | `.claude/agents/skeptic.md` | Skeptic agent |
| 18 | Agent | `.claude/agents/visionary.md` | Visionary agent |
| 19 | Agent | `.claude/agents/challenger.md` | Challenger agent |
| 20 | Agent | `.claude/agents/engineer.md` | Engineer agent |
| 21 | Config | `pyproject.toml` | Add pm-harness entry point |
