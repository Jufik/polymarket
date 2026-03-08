"""Full tick-by-tick validation for Crypto Elite strategy.

Config: K=50, N=2, HR-only ranked, YES-only direction filter.
Capital: $10,000, max_position $500, max_open 20.

Key concern: vectorized showed 98.5% HR with 3h median hold —
suspicious (may be post-resolution noise or in-play artifacts).

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/validate_crypto.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

from research.fast_replay import load_replay_resolutions, load_replay_trades
from research.harness import run_fast_backtest, print_summary

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/validate_crypto.log")
LOG_PATH.parent.mkdir(exist_ok=True)

def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

with open(LOG_PATH, "w") as f:
    pass

log("=" * 70)
log("Crypto Elite Tick-by-Tick Validation (K=50, N=2, YES-only)")
log("=" * 70)

# ── Step 1: Load token_map ─────────────────────────────────────────────────
log("\nStep 1: Load token_map from Parquet snapshot...")
t0 = time.time()
resolutions, token_map = load_replay_resolutions()
log(f"  token_map loaded: {len(token_map)} markets, {len(resolutions)} resolutions ({time.time()-t0:.1f}s)")

# ── Step 2: Build Crypto pool ──────────────────────────────────────────────
log("\nStep 2: Build Crypto HR-only pool (K=50)...")
t0 = time.time()

import importlib.util as _ilu
_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)
_bp_spec.loader.exec_module(_bp_mod)

pool, tag_markets, gambling_markets = _bp_mod.build_crypto_hr_pool(k=50)
log(f"  Pool: {len(pool)} traders | Tag markets (test period): {len(tag_markets)} | Gambling excluded: {len(gambling_markets)}")
log(f"  Pool build time: {time.time()-t0:.1f}s")

# ── Step 3: Strategy + Config ──────────────────────────────────────────────
log("\nStep 3: Instantiate strategy...")

from research.strategies.consensus_v2 import TokenMapStrategy

strategy = TokenMapStrategy(
    name="crypto_hr_k50_n2",
    pool=pool,
    tag_markets=tag_markets,
    gambling_markets=gambling_markets,
    n_threshold=2,
    token_map=token_map,
    direction_filter="YES",   # YES-only — NO signals are structural bias
    size_usd=500.0,           # $500 per signal (capital_usd=10000, max_open=20)
    max_price=None,
)

config = StrategyConfig(
    name=strategy.name,
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=10000.0,
    max_position_usd=500.0,
    max_open_positions=20,
    cooldown_s=0,
)

log(f"  Strategy: {strategy.name}")
log(f"  N_threshold: 2 | direction: YES-only | size_usd: $500")
log(f"  Capital: $10,000 | max_pos: $500 | max_open: 20")

# ── Step 4: Run tick-by-tick backtest ─────────────────────────────────────
log(f"\nStep 4: Run tick-by-tick backtest over {len(tag_markets)} Crypto markets...")
t0 = time.time()

output_dir = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
result, summary = run_fast_backtest(
    strategy,
    config,
    universe=tag_markets,
    output_dir=output_dir,
)
elapsed = time.time() - t0

log(f"  Backtest elapsed: {elapsed:.1f}s")
log(f"  Total trades seen: {result.total_trades:,}")
log(f"  Total intents fired: {result.total_intents}")
log(f"  Total fills: {result.total_fills}")
log(f"  Rejected intents: {len(result.rejected_intents)}")

if summary:
    log(f"\n--- Basic Metrics ---")
    log(f"  Hit Rate:        {summary.hit_rate:.1%}")
    log(f"  Net PnL:         ${summary.total_pnl_net:,.2f}")
    log(f"  Total Fees:      ${summary.total_fees:,.2f}")
    log(f"  Avg Edge:        ${summary.avg_edge:,.4f}")
    log(f"  Sharpe:          {summary.sharpe:.2f}")
    log(f"  Max Drawdown:    ${summary.max_drawdown:,.2f}")
    log(f"  Profit Factor:   {summary.profit_factor:.2f}")
    log(f"  Avg Hold:        {summary.avg_hold_duration_s / 3600:.1f}h")
    log(f"  Wins/Losses:     {summary.win_count}/{summary.loss_count} (pending: {summary.pending_count})")
    print_summary(summary, "Crypto Elite K=50 N=2")
else:
    log("  No settled positions — summary=None")

# ── Step 5: Ledger deep analysis ──────────────────────────────────────────
log("\nStep 5: Deep ledger analysis...")
ledger_path = output_dir / "ledger_crypto_hr_k50_n2.parquet"

if ledger_path.exists():
    df = pl.read_parquet(ledger_path)
    log(f"  Ledger rows: {len(df)}")
    log(f"  Columns: {df.columns}")

    # Filter to settled records
    settled = df.filter(pl.col("resolved_at").is_not_null())
    log(f"  Settled records: {len(settled)}")

    if len(settled) > 0:
        # Compute Crypto YES base rate from DuckDB
        log("\n--- Excess HR vs Crypto YES base rate ---")
        from research.db import db as get_db
        con = get_db().con
        base_rate_row = con.execute("""
            SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
            FROM maker_positions p
            JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
            WHERE mt.primary_tag = 'Crypto'
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
        """).fetchone()
        crypto_yes_base_rate = base_rate_row[0] if base_rate_row else None
        log(f"  Crypto YES base rate (test period): {crypto_yes_base_rate:.1%}" if crypto_yes_base_rate else "  Base rate: N/A")
        if summary and crypto_yes_base_rate:
            excess_hr = summary.hit_rate - crypto_yes_base_rate
            log(f"  Tick HR:     {summary.hit_rate:.1%}")
            log(f"  Excess HR:   +{excess_hr:.1%}pp")
            log(f"  Vectorized was +72pp excess. Degradation: {72 - excess_hr*100:.1f}pp")

        # Hold time distribution
        log("\n--- Hold Time Distribution ---")
        if "signal_time" in df.columns and "resolved_at" in df.columns:
            hold_s = settled.with_columns(
                ((pl.col("resolved_at") - pl.col("signal_time")) / 3600).alias("hold_h")
            )
            hold_vals = hold_s["hold_h"].drop_nulls()
            log(f"  Min hold:    {hold_vals.min():.1f}h")
            log(f"  p25 hold:    {hold_vals.quantile(0.25):.1f}h")
            log(f"  Median hold: {hold_vals.median():.1f}h")
            log(f"  p75 hold:    {hold_vals.quantile(0.75):.1f}h")
            log(f"  Max hold:    {hold_vals.max():.1f}h")

            # CRITICAL: How many resolve in <1h?
            under_1h = (hold_vals < 1).sum()
            under_6h = (hold_vals < 6).sum()
            under_24h = (hold_vals < 24).sum()
            total_settled = len(hold_vals)
            log(f"\n  CRITICAL: Fill timing vs resolution")
            log(f"  <1h hold:  {under_1h}/{total_settled} = {100*under_1h/total_settled:.1f}%")
            log(f"  <6h hold:  {under_6h}/{total_settled} = {100*under_6h/total_settled:.1f}%")
            log(f"  <24h hold: {under_24h}/{total_settled} = {100*under_24h/total_settled:.1f}%")

            if under_1h / total_settled > 0.5:
                log("  WARNING: >50% of fills resolve in <1h — LIKELY POST-RESOLUTION NOISE")
            elif under_6h / total_settled > 0.7:
                log("  WARNING: >70% of fills resolve in <6h — SHORT-LIVED SIGNAL, VERIFY CAUSALITY")
            else:
                log("  Hold distribution looks reasonable for copyable signal")
        else:
            log(f"  Columns available: {df.columns}")
            # Try to compute from what we have
            if "fill_time" in df.columns and "resolved_at" in df.columns:
                hold_s = settled.with_columns(
                    ((pl.col("resolved_at") - pl.col("fill_time")) / 3600).alias("hold_h")
                )
                hold_vals = hold_s["hold_h"].drop_nulls()
                log(f"  Median hold (fill→resolution): {hold_vals.median():.1f}h")

        # Signal timing analysis
        log("\n--- Fill / Signal Timing ---")
        if "signal_time" in df.columns:
            # Look at distribution of signal times across months
            has_signal = df.filter(pl.col("signal_time").is_not_null())
            log(f"  Records with signal_time: {len(has_signal)}")

        # PnL distribution
        log("\n--- PnL Distribution (settled) ---")
        pnl_col = None
        for c in ["pnl_net", "net_pnl", "realized_pnl", "pnl"]:
            if c in settled.columns:
                pnl_col = c
                break
        if pnl_col:
            pnl_vals = settled[pnl_col].drop_nulls()
            log(f"  Median PnL: ${pnl_vals.median():,.2f}")
            log(f"  Mean PnL:   ${pnl_vals.mean():,.2f}")
            log(f"  Total PnL:  ${pnl_vals.sum():,.2f}")
            log(f"  Positive:   {(pnl_vals > 0).sum()}/{len(pnl_vals)}")
        else:
            log(f"  PnL column not found. Available: {settled.columns}")

        # Capital utilization
        log("\n--- Capital Utilization ---")
        if "fill_time" in df.columns:
            filled = df.filter(pl.col("fill_time").is_not_null())
            log(f"  Total fills with fill_time: {len(filled)}")

    # Show sample rows
    log("\n--- Sample Ledger Rows (first 5 settled) ---")
    if len(settled) > 0:
        sample = settled.head(5)
        for col in sample.columns[:10]:  # first 10 columns
            log(f"  {col}: {sample[col].to_list()}")
else:
    log(f"  Ledger not found at {ledger_path}")

# ── Step 6: Summary verdict ────────────────────────────────────────────────
log("\n" + "=" * 70)
log("VALIDATION VERDICT")
log("=" * 70)

if result.total_fills == 0:
    log("  RESULT: ZERO FILLS — consensus signal not achievable tick-by-tick")
    log("  The vectorized result was fictitious (trades happen after resolution)")
elif summary is None:
    log(f"  RESULT: {result.total_fills} fills but no settled positions")
    log("  Markets may not have resolved yet in the test data")
else:
    log(f"  Fills:      {result.total_fills}")
    log(f"  HR:         {summary.hit_rate:.1%}")
    log(f"  Net PnL:    ${summary.total_pnl_net:,.2f}")
    log(f"  Sharpe:     {summary.sharpe:.2f}")
    log(f"  Avg Hold:   {summary.avg_hold_duration_s / 3600:.1f}h")
    if summary.hit_rate > 0.70:
        log("  VERDICT: High HR — investigate hold time for post-resolution noise")
    elif summary.total_pnl_net > 0:
        log("  VERDICT: Profitable signal — proceed to deeper analysis")
    else:
        log("  VERDICT: Unprofitable — signal degrades significantly tick-by-tick")

log(f"\nLog: {LOG_PATH}")
log("Done.")
