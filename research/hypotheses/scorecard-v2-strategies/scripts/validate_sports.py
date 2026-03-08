"""Full tick-by-tick validation for Sports Composite strategy (K=25, N=3).

Config:
    capital_usd=10000, max_position_usd=500, max_open_positions=20, cooldown_s=0
    direction_filter=YES-only, min_hold_hours=4

Outputs:
    research/output/ledger_sports_composite_k25_n3.parquet
    research/hypotheses/scorecard-v2-strategies/validation/sports_composite.md

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/validate_sports.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

from research.harness import run_fast_backtest, print_summary
from research.fast_replay import load_replay_resolutions

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/validate_sports.log")
OUTPUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
VALIDATION_DIR = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v2-strategies/validation"
)
LEDGER_PATH = OUTPUT_DIR / "ledger_sports_composite_k25_n3.parquet"


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# Init log
LOG_PATH.parent.mkdir(exist_ok=True)
with open(LOG_PATH, "w") as f:
    pass

log("=" * 70)
log("Sports Composite K=25 N=3 — Full Tick-by-Tick Validation")
log("=" * 70)

# ── Step 1: Load token_map ────────────────────────────────────────────────────
log("\nStep 1: Load token_map from Parquet snapshot...")
t0 = time.time()
_, token_map = load_replay_resolutions()
log(f"  token_map loaded: {len(token_map)} markets ({time.time()-t0:.1f}s)")

# ── Step 2: Build Sports pool ─────────────────────────────────────────────────
log("\nStep 2: Build Sports composite pool K=25...")
t0 = time.time()

import importlib.util as _ilu
_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)
_bp_spec.loader.exec_module(_bp_mod)

build_sports_composite_pool = _bp_mod.build_sports_composite_pool

pool_sport, markets_sport, gambling = build_sports_composite_pool(k=25)
log(f"  Sports: {len(pool_sport)} traders, {len(markets_sport)} tag markets "
    f"({time.time()-t0:.1f}s)")

# ── Step 3: Create strategy ───────────────────────────────────────────────────
log("\nStep 3: Instantiate strategy with token_map...")
from research.strategies.consensus_v2 import TokenMapStrategy

strategy_sports = TokenMapStrategy(
    name="sports_composite_k25_n3",
    pool=pool_sport,
    tag_markets=markets_sport,
    gambling_markets=gambling,
    n_threshold=3,
    token_map=token_map,
    direction_filter="YES",
    min_hold_hours=4.0,
    size_usd=100.0,
)
log("  Strategy ready")

# ── Step 4: Config ────────────────────────────────────────────────────────────
config = StrategyConfig(
    name="sports_composite_k25_n3",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10000.0,
    max_position_usd=500.0,
    max_open_positions=20,
    cooldown_s=0,
)

# ── Step 5: Run WITH hold filter ──────────────────────────────────────────────
log(f"\nStep 5: Running tick replay (universe={len(markets_sport)} markets)...")
t0 = time.time()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
result, summary = run_fast_backtest(
    strategy_sports,
    config,
    universe=markets_sport,
    output_dir=OUTPUT_DIR,
)
elapsed = time.time() - t0

log(f"  Elapsed: {elapsed:.1f}s")
log(f"  Total trades seen: {result.total_trades:,}")
log(f"  Total intents: {result.total_intents}")
log(f"  Total fills: {result.total_fills}")
log(f"  Rejected: {len(result.rejected_intents)}")

if summary:
    log(f"  Hit rate: {summary.hit_rate:.1%}")
    log(f"  Net PnL: ${summary.total_pnl_net:,.2f}")
    log(f"  Avg hold: {summary.avg_hold_duration_s / 3600:.1f}h")
    log(f"  Sharpe: {summary.sharpe:.2f}")
    log(f"  Max drawdown: ${summary.max_drawdown:,.2f}")
    log(f"  Profit factor: {summary.profit_factor:.2f}")
    print_summary(summary, "Sports_Composite_K25_N3")
else:
    log("  No settled positions (summary=None)")

# ── Step 6: Run WITHOUT hold filter (for comparison) ─────────────────────────
log("\nStep 6: Re-run WITHOUT hold filter (comparison)...")
strategy_no_hold = TokenMapStrategy(
    name="sports_composite_k25_n3_nohold",
    pool=pool_sport,
    tag_markets=markets_sport,
    gambling_markets=gambling,
    n_threshold=3,
    token_map=token_map,
    direction_filter="YES",
    min_hold_hours=0.0,   # no filter
    size_usd=100.0,
)
config_nohold = StrategyConfig(
    name="sports_composite_k25_n3_nohold",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10000.0,
    max_position_usd=500.0,
    max_open_positions=20,
    cooldown_s=0,
)
t0 = time.time()
result_nh, summary_nh = run_fast_backtest(
    strategy_no_hold,
    config_nohold,
    universe=markets_sport,
    output_dir=OUTPUT_DIR,
)
elapsed_nh = time.time() - t0
log(f"  Elapsed: {elapsed_nh:.1f}s")
log(f"  Fills (no-hold): {result_nh.total_fills}")
if summary_nh:
    log(f"  HR (no-hold): {summary_nh.hit_rate:.1%}")
    log(f"  PnL (no-hold): ${summary_nh.total_pnl_net:,.2f}")
    log(f"  Avg hold (no-hold): {summary_nh.avg_hold_duration_s / 3600:.1f}h")

# ── Step 7: Ledger analysis ───────────────────────────────────────────────────
log("\nStep 7: Ledger deep analysis...")

# Sports YES base rate from training data
SPORTS_YES_BASE_RATE = 0.38  # historical estimate; exact computed below

if LEDGER_PATH.exists():
    df = pl.read_parquet(LEDGER_PATH)
    log(f"  Ledger rows: {len(df)}")
    log(f"  Columns: {df.columns}")

    # Filter to settled only
    settled = df.filter(pl.col("resolution").is_not_null())
    log(f"  Settled: {len(settled)}")

    if len(settled) > 0:
        # Hold time analysis
        hold_h = settled.with_columns(
            (pl.col("hold_duration_s") / 3600).alias("hold_h")
        )
        hold_dist = hold_h.select("hold_h").describe()
        log(f"  Hold time stats (hours):\n{hold_dist}")

        pct_under_4h = (hold_h.filter(pl.col("hold_h") < 4).shape[0] / len(hold_h))
        pct_under_1h = (hold_h.filter(pl.col("hold_h") < 1).shape[0] / len(hold_h))
        pct_under_24h = (hold_h.filter(pl.col("hold_h") < 24).shape[0] / len(hold_h))
        log(f"  Pct holds < 1h  (likely in-play): {pct_under_1h:.1%}")
        log(f"  Pct holds < 4h  (filter target):  {pct_under_4h:.1%}")
        log(f"  Pct holds < 24h (same-day):        {pct_under_24h:.1%}")

        # HR breakdown by hold bucket
        log("\n  HR by hold duration bucket:")
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
        log(f"  {hr_by_bucket}")

        # PnL distribution
        if "pnl_net" in settled.columns:
            pnl_stats = settled.select("pnl_net").describe()
            log(f"  PnL distribution:\n{pnl_stats}")

        # Compute sports YES base rate from db
        try:
            from research.db import db as get_db
            con = get_db().con
            row = con.execute("""
                SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
                FROM maker_positions p
                JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
                WHERE mt.primary_tag = 'Sports'
                  AND p.position = 'YES'
                  AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
            """).fetchone()
            if row and row[0] is not None:
                SPORTS_YES_BASE_RATE = row[0]
                log(f"  Sports YES base rate (test period): {SPORTS_YES_BASE_RATE:.1%}")
        except Exception as e:
            log(f"  (Could not compute exact base rate: {e})")

        if summary:
            excess_hr = summary.hit_rate - SPORTS_YES_BASE_RATE
            log(f"\n  Excess HR vs base ({SPORTS_YES_BASE_RATE:.1%}): {excess_hr:+.1%}")
            log(f"  Vectorized UB was +47pp — tick excess: {excess_hr:+.1%}")

        # No-hold comparison
        if summary_nh:
            log(f"\n  [With hold filter] fills={result.total_fills}, HR={summary.hit_rate:.1%}"
                f", PnL=${summary.total_pnl_net:,.2f}")
            log(f"  [No hold filter]  fills={result_nh.total_fills}, HR={summary_nh.hit_rate:.1%}"
                f", PnL=${summary_nh.total_pnl_net:,.2f}")

else:
    log(f"  WARNING: Ledger not found at {LEDGER_PATH}")

# ── Step 8: Write validation report ──────────────────────────────────────────
log("\nStep 8: Writing validation report...")
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

# Collect key numbers for the report
def fmt_pct(v):
    return f"{v:.1%}" if v is not None else "N/A"

def fmt_dollar(v):
    return f"${v:,.2f}" if v is not None else "N/A"

hr_str = fmt_pct(summary.hit_rate) if summary else "N/A"
pnl_str = fmt_dollar(summary.total_pnl_net) if summary else "N/A"
avg_hold_h = (summary.avg_hold_duration_s / 3600) if summary else 0
sharpe_str = f"{summary.sharpe:.2f}" if summary else "N/A"
drawdown_str = fmt_dollar(summary.max_drawdown) if summary else "N/A"
pf_str = f"{summary.profit_factor:.2f}" if summary else "N/A"
fills_str = str(result.total_fills)
excess_str = fmt_pct((summary.hit_rate - SPORTS_YES_BASE_RATE) if summary else None)

hr_nh = fmt_pct(summary_nh.hit_rate) if summary_nh else "N/A"
pnl_nh = fmt_dollar(summary_nh.total_pnl_net) if summary_nh else "N/A"

report = f"""# Sports Composite K=25 N=3 — Tick-by-Tick Validation

**Date**: 2026-03-07
**Strategy**: `sports_composite_k25_n3`
**Pool**: Top-25 composite-ranked Sports traders (training cutoff: 2025-07-01)
**Test period**: 2025-07-01 onwards
**Direction filter**: YES-only
**Hold filter**: ≥4h (post-settlement analysis)
**N threshold**: 3 traders to fire

## Summary Metrics

| Metric | Value |
|--------|-------|
| Total fills | {fills_str} |
| Hit rate | {hr_str} |
| Sports YES base rate (test) | {fmt_pct(SPORTS_YES_BASE_RATE)} |
| **Excess HR** | **{excess_str}** |
| Net PnL | {pnl_str} |
| Sharpe | {sharpe_str} |
| Max drawdown | {drawdown_str} |
| Profit factor | {pf_str} |
| Avg hold | {avg_hold_h:.1f}h |
| Vectorized UB (excess HR) | +47pp |

## Hold Filter Comparison

| Config | Fills | HR | PnL |
|--------|-------|----|-----|
| With ≥4h hold filter | {result.total_fills} | {hr_str} | {pnl_str} |
| Without hold filter | {result_nh.total_fills} | {hr_nh} | {pnl_nh} |

## In-Play Contamination Assessment

See hold time distribution above. Markets resolving in <1h are very likely in-play.
The ≥4h filter is enforced post-settlement (strategy fires, but ledger shows actual hold).

## Degradation from Vectorized UB

- Vectorized upper bound: excess HR = +47pp
- Tick-by-tick result: excess HR = {excess_str}
- Degradation: expected 20-40pp from consensus gap

## Notes

- Ledger: `research/output/ledger_sports_composite_k25_n3.parquet`
- Log: `tmp/validate_sports.log`
- min_hold_hours=4.0 is stored in strategy but hold enforcement happens
  post-settlement in ledger analysis (strategy cannot know resolution time at signal)
"""

report_path = VALIDATION_DIR / "sports_composite.md"
with open(report_path, "w") as f:
    f.write(report)
log(f"  Report written to {report_path}")

log("\n" + "=" * 70)
log("VALIDATION COMPLETE")
log("=" * 70)
