"""Crypto YES HR-only K=50 N=2 — rerun with trigger_price_cap=0.65.

Context: previous validation (validate_crypto.py) found 67.5% of fills at price=0.99
(in-play/near-resolution artifacts). The headline +67.5pp excess HR was fake.
Real excess on genuine fills (price<0.70) was +30.9pp with only 98 fills.

This rerun gates the INTENT TRIGGER at max triggering trade price = 0.65.
This prevents near-resolved markets from generating signals at all.

Design notes:
- PriceGatedStrategy overrides _maybe_fire to skip signals where trigger_price > cap
- SimulatedExecutor fills at triggering_price + 0.02 (standard), so fills up to 0.67
- CRITICAL: use load_replay_resolutions() for token_map — returns "YES"/"NO" uppercase keys
  (not load_token_map() which returns "Yes"/"No" breaking TokenMapStrategy direction cache)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode, TradeIntent
from research.fast_replay import load_replay_resolutions
from research.harness import run_fast_backtest, print_summary
from research.strategies.consensus_v2 import TokenMapStrategy

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/crypto_maxprice065.log")
LOG_PATH.parent.mkdir(exist_ok=True)


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


with open(LOG_PATH, "w") as f:
    pass


class PriceGatedStrategy(TokenMapStrategy):
    """TokenMapStrategy with a triggering price gate.

    Does NOT fire an intent if the Nth pool trader's triggering price exceeds
    trigger_price_cap. This filters out near-resolved in-play markets where
    pool traders buy YES at 0.99 (already known outcome).

    The cap is applied to the triggering trade price (not the fill price).
    max_price on the intent is set to triggering_price + 0.02 (standard behavior).
    """

    def __init__(self, *args, trigger_price_cap: float = 0.65, **kwargs):
        super().__init__(*args, **kwargs)
        self._trigger_price_cap = trigger_price_cap

    def _maybe_fire(
        self,
        condition_id: str,
        triggering_asset_id: str,
        triggering_price: float,
        signal_time: float,
    ) -> list[TradeIntent] | None:
        # Gate: skip if trigger price exceeds cap
        if triggering_price > self._trigger_price_cap:
            return None
        return super()._maybe_fire(
            condition_id, triggering_asset_id, triggering_price, signal_time
        )


log("=" * 70)
log("Crypto Elite Rerun: trigger_price_cap=0.65 (K=50, N=2, YES-only)")
log("=" * 70)

# ── Step 1: Load token_map ─────────────────────────────────────────────────
log("\nStep 1: Load token_map via load_replay_resolutions...")
t0 = time.time()
resolutions, token_map = load_replay_resolutions()
log(f"  token_map: {len(token_map)} markets, {len(resolutions)} resolutions ({time.time()-t0:.1f}s)")

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
log(
    f"  Pool: {len(pool)} traders | "
    f"Tag markets (test period): {len(tag_markets)} | "
    f"Gambling excluded: {len(gambling_markets)} "
    f"({time.time()-t0:.1f}s)"
)

# ── Step 3: Strategy with trigger_price_cap=0.65 ──────────────────────────
log("\nStep 3: Instantiate PriceGatedStrategy (trigger_price_cap=0.65)...")

strategy = PriceGatedStrategy(
    name="crypto_yes_hr_k50_n2_pricegate065",
    pool=pool,
    tag_markets=tag_markets,
    gambling_markets=gambling_markets,
    n_threshold=2,
    token_map=token_map,
    direction_filter="YES",
    max_price=None,       # standard: triggering_price + 0.02 per signal
    size_usd=100.0,
    trigger_price_cap=0.65,
)

config = StrategyConfig(
    name="crypto_yes_hr_k50_n2_pricegate065",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=5000.0,
    max_position_usd=100.0,
    max_open_positions=50,
    cooldown_s=0,
)

log(f"  Strategy: {strategy.name}")
log(f"  N_threshold: 2 | direction: YES-only | trigger_price_cap: 0.65 | size_usd: $100")
log(f"  Capital: $5,000 | max_pos: $100 | max_open: 50")
log(f"  Asset cache pre-populated: {len(strategy._asset_direction_cache)} entries")

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
    print_summary(summary, "Crypto Elite K=50 N=2 pricegate=0.65")
else:
    log("  No summary — 0 fills or no settled positions")

# ── Step 5: Ledger deep analysis ──────────────────────────────────────────
log("\nStep 5: Ledger analysis...")
ledger_path = output_dir / f"ledger_{strategy.name}.parquet"

if ledger_path.exists():
    df = pl.read_parquet(ledger_path)
    settled = df.filter(pl.col("resolved_at").is_not_null())
    log(f"  Ledger rows: {len(df)}, Settled: {len(settled)}")

    if len(settled) > 0:
        # Fill price distribution
        log("\n--- Fill Price Distribution ---")
        prices = settled["fill_price"].drop_nulls()
        log(f"  N: {len(prices)}")
        log(f"  Min:    {prices.min():.3f}")
        log(f"  p25:    {prices.quantile(0.25):.3f}")
        log(f"  Median: {prices.median():.3f}")
        log(f"  p75:    {prices.quantile(0.75):.3f}")
        log(f"  Max:    {prices.max():.3f}")
        above_065 = (prices > 0.65).sum()
        above_067 = (prices > 0.67).sum()
        log(f"  >0.65:  {above_065} (small overshoot from trigger_price+0.02 logic)")
        log(f"  >0.67:  {above_067} (should be 0)")

        # Base rate
        from research.db import db as get_db
        con = get_db().con
        row = con.execute("""
            SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate
            FROM maker_positions p
            JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
            WHERE mt.primary_tag = 'Crypto'
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '2025-07-01'
        """).fetchone()
        base = row[0] if row else None

        log("\n--- HR vs Base Rate ---")
        if base:
            log(f"  Crypto YES base rate (test, 2025-07+): {base:.1%}")
        if summary and base:
            excess = summary.hit_rate - base
            log(f"  Strategy HR:  {summary.hit_rate:.1%}")
            log(f"  Excess HR:    +{excess:.1%}")

        # Hold time
        log("\n--- Hold Time Distribution ---")
        if "signal_time" in settled.columns:
            holds = ((settled["resolved_at"] - settled["signal_time"]) / 3600).drop_nulls()
            log(f"  Min:    {holds.min():.1f}h")
            log(f"  p25:    {holds.quantile(0.25):.1f}h")
            log(f"  Median: {holds.median():.1f}h")
            log(f"  p75:    {holds.quantile(0.75):.1f}h")
            log(f"  Max:    {holds.max():.1f}h")
            total = len(holds)
            log(f"  <1h:    {(holds < 1).sum()}/{total} = {100*(holds < 1).mean():.1f}%")
            log(f"  1-6h:   {((holds >= 1) & (holds < 6)).sum()}/{total} = {100*((holds >= 1) & (holds < 6)).mean():.1f}%")
            log(f"  6-24h:  {((holds >= 6) & (holds < 24)).sum()}/{total} = {100*((holds >= 6) & (holds < 24)).mean():.1f}%")
            log(f"  >24h:   {(holds >= 24).sum()}/{total} = {100*(holds >= 24).mean():.1f}%")

        # PnL
        pnl_col = next((c for c in ["pnl_net", "net_pnl", "pnl"] if c in settled.columns), None)
        if pnl_col:
            log("\n--- PnL Distribution (settled) ---")
            pnl = settled[pnl_col].drop_nulls()
            log(f"  Median: ${pnl.median():,.2f}")
            log(f"  Mean:   ${pnl.mean():,.2f}")
            log(f"  Total:  ${pnl.sum():,.2f}")
            log(f"  Win %:  {100*(pnl > 0).mean():.1f}%")
            log(f"  Wins:   {(pnl > 0).sum()}, Losses: {(pnl < 0).sum()}")

        # Monthly breakdown
        if pnl_col:
            log("\n--- Monthly Breakdown ---")
            monthly = settled.with_columns(
                pl.from_epoch(pl.col("resolved_at").cast(pl.Int64), time_unit="s")
                .dt.truncate("1mo").alias("month")
            ).group_by("month").agg([
                pl.len().alias("n"),
                pl.col(pnl_col).sum().alias("total_pnl"),
                (pl.col(pnl_col) > 0).mean().alias("hr"),
            ]).sort("month")
            for r in monthly.iter_rows(named=True):
                log(f"  {str(r['month'])[:7]}: N={r['n']}, HR={r['hr']:.1%}, PnL=${r['total_pnl']:,.0f}")
else:
    log(f"  Ledger not found at {ledger_path}")

# ── Step 6: Comparison summary ────────────────────────────────────────────
log("\n" + "=" * 70)
log("COMPARISON vs PRIOR RUN")
log("=" * 70)
log("  Metric            | Prior (all) | Prior (genuine) | This run (gate=0.65)")
log("  ------------------|-------------|-----------------|--------------------")
log(f"  Fills             | 478         | 98              | {result.total_fills}")
if summary:
    log(f"  HR                | 82.6%       | 45.9%           | {summary.hit_rate:.1%}")
    log(f"  Excess HR         | +67.5pp     | +30.9pp         | +{(summary.hit_rate - 0.151)*100:.1f}pp")
    log(f"  Median Hold       | 3.1h        | 64.1h           | -- (see above)")
    log(f"  Net PnL ($100/pos)| $47,358     | $57,206 (true)  | ${summary.total_pnl_net:,.0f}")
    log(f"  Sharpe            | 0.61        | N/A             | {summary.sharpe:.2f}")

log(f"\nLog: {LOG_PATH}")
log("Done.")
