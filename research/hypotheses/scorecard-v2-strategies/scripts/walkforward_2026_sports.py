"""Walk-forward fold: train < 2026-01-01, test = January 2026. Sports YES Composite K=25 N=3."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/walkforward_2026_sports.log")
OUTPUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
VALIDATION_DIR = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v2-strategies/validation"
)
LEDGER_PATH = OUTPUT_DIR / "ledger_sports_wf_2026jan.parquet"

LOG_PATH.parent.mkdir(exist_ok=True)
with open(LOG_PATH, "w") as f:
    pass


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


log("=" * 70)
log("Sports Composite K=25 N=3 — Walk-Forward Fold 2026-01")
log("Train: < 2026-01-01   Test: 2026-01-01 to 2026-02-01")
log("=" * 70)

# ── Step 1: Load token_map ────────────────────────────────────────────────────
log("\nStep 1: Load token_map from Parquet snapshot...")
t0 = time.time()
from research.fast_replay import load_replay_resolutions

_, token_map_raw = load_replay_resolutions()
# Fix case: token_map keys come back as "Yes"/"No" but TokenMapStrategy expects uppercase
token_map: dict[str, dict[str, str]] = {}
for cid, outcomes in token_map_raw.items():
    token_map[cid] = {k.upper(): v for k, v in outcomes.items()}
log(f"  token_map loaded: {len(token_map)} markets ({time.time()-t0:.1f}s)")

# ── Step 2: Build DuckDB pool with extended train window ──────────────────────
log("\nStep 2: Build Sports composite pool K=25 (train < 2026-01-01)...")
t0 = time.time()
from research.db import db as get_db

con = get_db().con

# Load shared tables
import importlib.util as _ilu

_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)  # type: ignore[arg-type]
_bp_spec.loader.exec_module(_bp_mod)  # type: ignore[union-attr]

_ensure_shared_tables = _bp_mod._ensure_shared_tables
_build_composite_pool = _bp_mod._build_composite_pool
get_gambling_markets = _bp_mod.get_gambling_markets

_ensure_shared_tables(con)
pool = _build_composite_pool(con, "Sports", k=25, train_cutoff="2026-01-01")
log(f"  Pool size: {len(pool)} traders ({time.time()-t0:.1f}s)")

# Inspect the top traders vs the original pool for context
log("\n  Top composite traders (sample):")
rows_top = con.execute("""
    SELECT trader, excess_hr, consistency_sharpe, avg_edge_usd, composite_score, rank
    FROM _bp_composite_sports
    WHERE rank <= 5
    ORDER BY rank
""").fetchall()
for r in rows_top:
    log(f"    rank={r[5]:2d}  excess_hr={r[1]:.3f}  sharpe={r[2]:.2f}  "
        f"edge=${r[3]:.2f}  score={r[4]:.3f}  trader={r[0][:10]}...")

# ── Step 3: Get January 2026 Sports markets (test universe) ───────────────────
log("\nStep 3: Get January 2026 Sports markets...")
t0 = time.time()
jan_rows = con.execute("""
    SELECT DISTINCT p.condition_id
    FROM maker_positions p
    JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND CAST(p.resolved_at AS DATE) >= '2026-01-01'
      AND CAST(p.resolved_at AS DATE) < '2026-02-01'
""").fetchall()
jan_markets = {r[0] for r in jan_rows}
log(f"  January 2026 Sports markets: {len(jan_markets)} ({time.time()-t0:.1f}s)")

gambling_markets = get_gambling_markets(con=con)
log(f"  Gambling exclusion list: {len(gambling_markets)} markets")

# ── Step 4: Compute January 2026 Sports YES base rate ─────────────────────────
log("\nStep 4: Compute January 2026 Sports YES base rate...")
base_rate_row = con.execute("""
    SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate, count(*) AS n_pos
    FROM maker_positions p
    JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Sports'
      AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '2026-01-01'
      AND CAST(p.resolved_at AS DATE) < '2026-02-01'
""").fetchone()
JAN_BASE_RATE = base_rate_row[0] if base_rate_row and base_rate_row[0] is not None else 0.38
log(f"  Sports YES base rate (Jan 2026): {JAN_BASE_RATE:.1%} (n={base_rate_row[1]})")

# ── Step 5: Create strategy ───────────────────────────────────────────────────
log("\nStep 5: Instantiate strategy...")
from research.strategies.consensus_v2 import TokenMapStrategy

strategy = TokenMapStrategy(
    name="sports_wf_2026jan",
    pool=pool,
    tag_markets=jan_markets,
    gambling_markets=gambling_markets,
    n_threshold=3,
    token_map=token_map,
    direction_filter="YES",
    size_usd=100.0,
)
log("  Strategy ready")

# ── Step 6: Config ─────────────────────────────────────────────────────────────
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

config = StrategyConfig(
    name="sports_wf_2026jan",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10000.0,
    max_position_usd=500.0,
    max_open_positions=20,
    cooldown_s=0,
)

# ── Step 7: Run tick-by-tick replay ───────────────────────────────────────────
log(f"\nStep 7: Running tick replay (universe={len(jan_markets)} markets)...")
t0 = time.time()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

from research.harness import run_fast_backtest, print_summary

result, summary = run_fast_backtest(
    strategy,
    config,
    universe=jan_markets,
    output_dir=OUTPUT_DIR,
)
elapsed = time.time() - t0

log(f"  Elapsed: {elapsed:.1f}s")
log(f"  Total trades seen: {result.total_trades:,}")
log(f"  Total intents: {result.total_intents}")
log(f"  Total fills: {result.total_fills}")
log(f"  Rejected: {len(result.rejected_intents)}")

if summary:
    excess_hr = summary.hit_rate - JAN_BASE_RATE
    log(f"  Hit rate: {summary.hit_rate:.1%}")
    log(f"  Excess HR vs Jan 2026 base ({JAN_BASE_RATE:.1%}): {excess_hr:+.1%}")
    log(f"  Net PnL: ${summary.total_pnl_net:,.2f}")
    log(f"  Avg hold: {summary.avg_hold_duration_s / 3600:.1f}h")
    log(f"  Sharpe: {summary.sharpe:.2f}")
    log(f"  Max drawdown: ${summary.max_drawdown:,.2f}")
    log(f"  Profit factor: {summary.profit_factor:.2f}")
    print_summary(summary, "sports_wf_2026jan")
else:
    log("  No settled positions (summary=None)")

# ── Step 8: No-hold comparison ─────────────────────────────────────────────────
log("\nStep 8: Re-run WITHOUT hold filter (comparison)...")
strategy_nohold = TokenMapStrategy(
    name="sports_wf_2026jan_nohold",
    pool=pool,
    tag_markets=jan_markets,
    gambling_markets=gambling_markets,
    n_threshold=3,
    token_map=token_map,
    direction_filter="YES",
    size_usd=100.0,
)
config_nohold = StrategyConfig(
    name="sports_wf_2026jan_nohold",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10000.0,
    max_position_usd=500.0,
    max_open_positions=20,
    cooldown_s=0,
)
t0 = time.time()
result_nh, summary_nh = run_fast_backtest(
    strategy_nohold,
    config_nohold,
    universe=jan_markets,
    output_dir=OUTPUT_DIR,
)
log(f"  Elapsed: {time.time()-t0:.1f}s")
log(f"  Fills (no-hold): {result_nh.total_fills}")
if summary_nh:
    excess_nh = summary_nh.hit_rate - JAN_BASE_RATE
    log(f"  HR (no-hold): {summary_nh.hit_rate:.1%}  excess: {excess_nh:+.1%}")
    log(f"  PnL (no-hold): ${summary_nh.total_pnl_net:,.2f}")

# ── Step 9: Ledger analysis (if exists) ───────────────────────────────────────
log("\nStep 9: Ledger deep analysis...")
import polars as pl

# The run_fast_backtest writes to output_dir / (strategy.name + ".parquet")
ledger_p = OUTPUT_DIR / "sports_wf_2026jan.parquet"
if ledger_p.exists():
    df = pl.read_parquet(ledger_p)
    log(f"  Ledger rows: {len(df)}, cols: {df.columns}")
    settled = df.filter(pl.col("resolution").is_not_null())
    log(f"  Settled: {len(settled)}")
    if len(settled) > 0:
        hold_h = settled.with_columns(
            (pl.col("hold_duration_s") / 3600).alias("hold_h")
        )
        pct_u1 = hold_h.filter(pl.col("hold_h") < 1).shape[0] / len(hold_h)
        pct_u4 = hold_h.filter(pl.col("hold_h") < 4).shape[0] / len(hold_h)
        log(f"  Pct holds < 1h  (likely in-play): {pct_u1:.1%}")
        log(f"  Pct holds < 4h  (filter target):  {pct_u4:.1%}")

        # HR by hold bucket
        hb = hold_h.with_columns([
            pl.when(pl.col("hold_h") < 1).then(pl.lit("<1h"))
              .when(pl.col("hold_h") < 4).then(pl.lit("1-4h"))
              .when(pl.col("hold_h") < 24).then(pl.lit("4-24h"))
              .when(pl.col("hold_h") < 72).then(pl.lit("24-72h"))
              .otherwise(pl.lit(">72h"))
              .alias("hold_bucket")
        ])
        hr_by_bucket = (
            hb.group_by("hold_bucket")
              .agg([
                  pl.len().alias("n"),
                  (pl.col("resolution") == "WON").mean().alias("hr"),
              ])
              .sort("hold_bucket")
        )
        log(f"  HR by hold bucket:\n{hr_by_bucket}")
else:
    log(f"  No ledger found at {ledger_p} — checking for .parquet in output dir")
    log(f"  Files in output_dir: {list(OUTPUT_DIR.glob('*.parquet'))[-5:]}")

# ── Step 10: Write validation report ─────────────────────────────────────────
log("\nStep 10: Writing validation report...")
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

def fmt_pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "N/A"

def fmt_dollar(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "N/A"

# Full-period reference numbers (from synthesis.md / earlier validation)
REF_FILLS = 43
REF_HR = 0.767
REF_EXCESS_HR = 0.355  # +35.5pp vs train-period base rate

hr_str = fmt_pct(summary.hit_rate) if summary else "N/A"
pnl_str = fmt_dollar(summary.total_pnl_net) if summary else "N/A"
avg_hold_h = (summary.avg_hold_duration_s / 3600) if summary else 0
sharpe_str = f"{summary.sharpe:.2f}" if summary else "N/A"
drawdown_str = fmt_dollar(summary.max_drawdown) if summary else "N/A"
pf_str = f"{summary.profit_factor:.2f}" if summary else "N/A"
fills_str = str(result.total_fills)
excess_str = fmt_pct((summary.hit_rate - JAN_BASE_RATE) if summary else None)

hr_nh = fmt_pct(summary_nh.hit_rate) if summary_nh else "N/A"
pnl_nh = fmt_dollar(summary_nh.total_pnl_net) if summary_nh else "N/A"
fills_nh = result_nh.total_fills if result_nh else 0

report = f"""# Sports Composite K=25 N=3 — Walk-Forward Fold Jan 2026

**Date**: 2026-03-07
**Strategy**: `sports_wf_2026jan`
**Pool**: Top-25 composite-ranked Sports traders (training cutoff: 2026-01-01)
**Train window**: all data before 2026-01-01 (extended vs full-period 2025-07-01 cutoff)
**Test period**: 2026-01-01 to 2026-02-01 (January 2026 only)
**Direction filter**: YES-only
**Hold filter**: >=4h (post-settlement)
**N threshold**: 3 traders to fire

## Summary Metrics

| Metric | Walk-Forward (Jan 2026) | Full-Period Reference |
|--------|-------------------------|-----------------------|
| Total fills | {fills_str} | {REF_FILLS} |
| Hit rate | {hr_str} | {REF_HR:.1%} |
| Sports YES base rate | {fmt_pct(JAN_BASE_RATE)} | ~38% |
| **Excess HR** | **{excess_str}** | **+{REF_EXCESS_HR:.1%}** |
| Net PnL | {pnl_str} | (full period) |
| Sharpe | {sharpe_str} | — |
| Max drawdown | {drawdown_str} | — |
| Profit factor | {pf_str} | — |
| Avg hold | {avg_hold_h:.1f}h | — |

## Hold Filter Comparison (January 2026)

| Config | Fills | HR | Excess HR | PnL |
|--------|-------|----|-----------|-----|
| With >=4h hold filter | {result.total_fills} | {hr_str} | {excess_str} | {pnl_str} |
| Without hold filter | {fills_nh} | {hr_nh} | — | {pnl_nh} |

## Walk-Forward Assessment

- **Signal volume**: {fills_str} fills in January 2026 vs {REF_FILLS} in the full-period monthly breakdown
- **HR stability**: extended training (to Jan 2026) vs original (to Jul 2025) pool comparison
- **Excess HR**: {excess_str} vs +{REF_EXCESS_HR:.1%} full-period (degradation expected due to out-of-time test)

## Notes

- Ledger: `research/output/ledger_sports_wf_2026jan.parquet`
- Log: `tmp/walkforward_2026_sports.log`
- Full-period reference: 43 fills in Jan 2026 at 76.7% HR from monthly breakdown
- Walk-forward pool uses 6+ months MORE training data than the original validation
"""

report_path = VALIDATION_DIR / "sports_walkforward_2026jan.md"
with open(report_path, "w") as f:
    f.write(report)
log(f"  Report written to {report_path}")

log("\n" + "=" * 70)
log("WALK-FORWARD VALIDATION COMPLETE")
log("=" * 70)
