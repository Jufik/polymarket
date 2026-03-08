"""Full tick-by-tick validation for Politics Composite K=100, N=5, YES+NO.

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/validate_politics.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/validate_politics.log")
LOG_PATH.parent.mkdir(exist_ok=True)

# Clear log
with open(LOG_PATH, "w") as f:
    pass


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


log("=" * 70)
log("Politics Composite K=100 N=5 — Full Tick-by-Tick Validation")
log("=" * 70)

# ── Step 1: Load token_map ────────────────────────────────────────────────────
log("\nStep 1: Load token_map from Parquet snapshot...")
t0 = time.time()
from research.fast_replay import load_replay_resolutions

_, token_map = load_replay_resolutions()
log(f"  token_map loaded: {len(token_map)} markets ({time.time()-t0:.1f}s)")

# ── Step 2: Build pool ────────────────────────────────────────────────────────
log("\nStep 2: Build Politics composite pool (DuckDB)...")
t0 = time.time()

import importlib.util as _ilu
_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)
_bp_spec.loader.exec_module(_bp_mod)

build_politics_composite_pool = _bp_mod.build_politics_composite_pool

log("  Building Politics composite K=100...")
pool, tag_markets, gambling_markets = build_politics_composite_pool(k=100)
log(f"  Pool: {len(pool)} traders, {len(tag_markets)} tag markets, "
    f"{len(gambling_markets)} gambling excluded ({time.time()-t0:.1f}s)")

# ── Step 3: Create strategy ───────────────────────────────────────────────────
log("\nStep 3: Instantiate TokenMapStrategy...")
from research.strategies.consensus_v2 import TokenMapStrategy

strategy = TokenMapStrategy(
    name="politics_composite_k100_n5",
    pool=pool,
    tag_markets=tag_markets,
    gambling_markets=gambling_markets,
    n_threshold=5,
    token_map=token_map,
    direction_filter=None,   # YES + NO both viable for Politics
    size_usd=100.0,
)
log(f"  Strategy ready: {strategy.name}")

# ── Step 4: Config ────────────────────────────────────────────────────────────
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

config = StrategyConfig(
    name="politics_composite_k100_n5",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50000.0,
    max_position_usd=500.0,
    max_open_positions=100,
    cooldown_s=0,
)

# ── Step 5: Run backtest ──────────────────────────────────────────────────────
log(f"\nStep 4: Running tick-by-tick backtest on {len(tag_markets)} markets...")
from research.harness import run_fast_backtest, print_summary

t0 = time.time()
result, summary = run_fast_backtest(
    strategy,
    config,
    universe=tag_markets,
    output_dir=Path("/mnt/nvme/git/polymarket/polymarket/research/output"),
)
elapsed = time.time() - t0

log(f"\n--- Backtest complete ({elapsed:.1f}s) ---")
log(f"  Total trades seen: {result.total_trades:,}")
log(f"  Total intents:     {result.total_intents}")
log(f"  Total fills:       {result.total_fills}")
log(f"  Settled fills:     {result.settled_fills}")
log(f"  Rejected:          {len(result.rejected_intents)}")

if summary:
    log(f"\n--- Summary ---")
    log(f"  Hit rate:        {summary.hit_rate:.1%}")
    log(f"  Net PnL:         ${summary.total_pnl_net:,.2f}")
    log(f"  Sharpe:          {summary.sharpe_ratio:.3f}")
    log(f"  Max drawdown:    ${summary.max_drawdown:,.2f}")
    log(f"  Profit factor:   {summary.profit_factor:.3f}")
    log(f"  Avg hold:        {summary.avg_hold_duration_s / 3600:.1f}h")
    print_summary(summary, "Politics Composite K=100 N=5")
else:
    log("  No settled positions (summary=None)")

# ── Step 6: Direction decomposition via ledger ─────────────────────────────────
log("\nStep 5: Direction decomposition via ledger parquet...")
import polars as pl

LEDGER_PATH = Path("/mnt/nvme/git/polymarket/polymarket/research/output/ledger_politics_composite_k100_n5.parquet")

if not LEDGER_PATH.exists():
    log(f"  WARNING: Ledger not found at {LEDGER_PATH}")
    log("  Checking research/output/ for ledger files...")
    for f in Path("/mnt/nvme/git/polymarket/polymarket/research/output").glob("*.parquet"):
        log(f"    {f}")
else:
    df = pl.read_parquet(LEDGER_PATH)
    log(f"  Ledger records: {len(df)}")
    log(f"  Columns: {df.columns}")

    # Filter to settled records only (has resolution)
    settled = df.filter(pl.col("resolved_at").is_not_null()) if "resolved_at" in df.columns else df
    log(f"  Settled records: {len(settled)}")

    if len(settled) > 0:
        # Show all columns for first few rows
        log(f"\n  Schema sample:")
        for col in settled.columns[:15]:
            sample_vals = settled[col].head(3).to_list()
            log(f"    {col}: {sample_vals}")

        # Outcome field
        outcome_col = None
        for candidate in ["outcome", "direction", "side", "signal_direction"]:
            if candidate in settled.columns:
                outcome_col = candidate
                break

        if outcome_col:
            log(f"\n  Direction column: '{outcome_col}'")
            log(f"  Value counts: {settled[outcome_col].value_counts().to_dicts()}")

        # Correct field
        correct_col = None
        for candidate in ["correct", "won", "pnl_net", "result"]:
            if candidate in settled.columns:
                correct_col = candidate
                break
        log(f"  Correct column candidate: '{correct_col}'")

        # Try YES/NO direction decomposition
        if outcome_col and correct_col:
            for direction in ["YES", "NO"]:
                subset = settled.filter(pl.col(outcome_col) == direction)
                if len(subset) > 0:
                    if correct_col == "pnl_net":
                        hr = (subset[correct_col] > 0).mean()
                    else:
                        hr = subset[correct_col].cast(pl.Float64).mean()
                    log(f"\n  {direction} direction:")
                    log(f"    n={len(subset)}, HR={hr:.1%}")
                else:
                    log(f"\n  {direction}: no records")

        # Hold time distribution
        hold_col = None
        for candidate in ["hold_duration_s", "hold_s", "hold_seconds"]:
            if candidate in settled.columns:
                hold_col = candidate
                break
        if hold_col:
            hold_hours = settled[hold_col] / 3600
            log(f"\n  Hold time distribution (hours):")
            log(f"    p25={hold_hours.quantile(0.25):.1f}h")
            log(f"    p50={hold_hours.quantile(0.50):.1f}h")
            log(f"    p75={hold_hours.quantile(0.75):.1f}h")
            log(f"    <4h: {(hold_hours < 4).sum()} ({(hold_hours < 4).mean():.1%})")
            log(f"    <24h: {(hold_hours < 24).sum()} ({(hold_hours < 24).mean():.1%})")

# ── Step 7: Compute base rate and excess HR ────────────────────────────────────
log("\nStep 6: Compute Politics base rate (test period)...")
from research.db import db as get_db

con = get_db().con
TEST_START = "2025-07-01"

# Ensure shared tables exist
con.execute("""
CREATE OR REPLACE MACRO is_gambling_market(slug) AS (
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
    OR (
        (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (
            lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
            OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
            OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%sol%'
            OR lower(slug) LIKE '%-close-%'
            OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%nvda%'
            OR lower(slug) LIKE '%aapl%' OR lower(slug) LIKE '%amzn%'
        )
    )
);
""")

con.execute("""
CREATE OR REPLACE TABLE _bp_market_tags AS
SELECT
    m.condition_id,
    m.slug,
    first(et.label ORDER BY et.tag_id) AS primary_tag,
    first(et.tag_id ORDER BY et.tag_id) AS tag_id
FROM markets m
JOIN event_tags et ON m.event_id = et.event_id
WHERE NOT is_gambling_market(m.slug)
GROUP BY m.condition_id, m.slug;
""")

# Politics test-period base rate
base_rows = con.execute(f"""
SELECT
    avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
    count(DISTINCT p.condition_id) AS n_markets_test
FROM maker_positions_resolved_corrected p
JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
WHERE mt.primary_tag = 'Politics'
  AND p.position = 'YES'
  AND CAST(p.resolved_at AS DATE) >= '{TEST_START}'
""").fetchone()

yes_base_rate = base_rows[0]
n_markets_test = base_rows[1]
log(f"  Politics test-period YES base rate: {yes_base_rate:.3f} ({n_markets_test} markets)")

# NO base rate (= 1 - YES base rate for binary markets)
no_base_rate = 1 - yes_base_rate
log(f"  Politics test-period NO base rate:  {no_base_rate:.3f}")

if summary:
    excess_hr = summary.hit_rate - yes_base_rate
    log(f"\n  Strategy HR:     {summary.hit_rate:.3f}")
    log(f"  YES base rate:   {yes_base_rate:.3f}")
    log(f"  Excess HR:       {excess_hr:+.3f} ({excess_hr:+.1%})")

    # Compounding score
    avg_hold_days = summary.avg_hold_duration_s / 86400
    if avg_hold_days > 0 and excess_hr > 0:
        # Estimate avg edge per fill
        if result.total_fills > 0:
            avg_edge_usd = summary.total_pnl_net / result.total_fills
        else:
            avg_edge_usd = 0.0
        cs = excess_hr * abs(avg_edge_usd) / avg_hold_days
        log(f"\n  Compounding score: {cs:.4f}")
        log(f"    excess_hr={excess_hr:.3f}, avg_edge_usd={avg_edge_usd:.2f}, avg_hold_days={avg_hold_days:.2f}")

log("\n" + "=" * 70)
log("VALIDATION COMPLETE")
log("=" * 70)
