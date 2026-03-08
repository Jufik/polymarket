"""Walk-forward fold: train < 2026-01-01, test = January 2026.

Crypto YES HR-only K=50 N=2, trigger_price_cap=0.65.

Key design decisions:
- Pool trained exclusively on data before 2026-01-01 (no data leakage)
- January 2026 Crypto markets only (resolved 2026-01-01 to 2026-02-01)
- Uses PriceGatedStrategy with trigger_price_cap=0.65 to filter near-resolved in-play artifacts
- token_map loaded from load_replay_resolutions() — returns uppercase "YES"/"NO" keys
  (NOT load_token_map() which returns "Yes"/"No" mixed-case, causing 0 fills)
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

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/walkforward_2026_crypto.log")
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

    Skips signal generation if the Nth pool trader's triggering price
    exceeds trigger_price_cap. This filters out near-resolved in-play
    markets where pool traders buy YES at 0.99 (already known outcome).
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
        if triggering_price > self._trigger_price_cap:
            return None
        return super()._maybe_fire(
            condition_id, triggering_asset_id, triggering_price, signal_time
        )


log("=" * 70)
log("Walk-forward fold: train<2026-01-01, test=Jan 2026")
log("Crypto YES HR-only K=50, N=2, trigger_price_cap=0.65")
log("=" * 70)

# ── Step 1: Load token_map via load_replay_resolutions ────────────────────
# CRITICAL: load_replay_resolutions returns uppercase "YES"/"NO" keys.
# load_token_map() returns "Yes"/"No" which breaks TokenMapStrategy direction cache.
log("\nStep 1: Load token_map via load_replay_resolutions...")
t0 = time.time()
resolutions, token_map = load_replay_resolutions()
log(f"  token_map: {len(token_map)} markets, {len(resolutions)} resolutions ({time.time()-t0:.1f}s)")

# ── Step 2: Build pool with train_cutoff=2026-01-01 ───────────────────────
log("\nStep 2: Build Crypto HR-only pool (K=50, train<2026-01-01)...")
t0 = time.time()

import importlib.util as _ilu

_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)
_bp_spec.loader.exec_module(_bp_mod)

con = _bp_mod._get_db()
_bp_mod._ensure_shared_tables(con)

pool = _bp_mod._build_hr_only_pool(con, "Crypto", k=50, train_cutoff="2026-01-01")
log(f"  Pool size: {len(pool)} traders ({time.time()-t0:.1f}s)")

# Sanity check pool: show top-3 trader hr stats
rows = con.execute("""
    SELECT trader, hit_rate, n_markets, excess_hr
    FROM _bp_hronly_crypto
    WHERE rank <= 3
    ORDER BY rank
""").fetchall()
log(f"  Top-3 pool traders (train HR):")
for r in rows:
    log(f"    trader={r[0][:10]}... hr={r[1]:.3f} n_markets={r[2]} excess={r[3]:.3f}")

# ── Step 3: Get January 2026 Crypto markets ───────────────────────────────
log("\nStep 3: Fetch January 2026 Crypto markets...")
t0 = time.time()

jan_rows = con.execute("""
    SELECT DISTINCT p.condition_id
    FROM maker_positions p
    JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Crypto'
      AND CAST(p.resolved_at AS DATE) >= '2026-01-01'
      AND CAST(p.resolved_at AS DATE) < '2026-02-01'
""").fetchall()
jan_markets = {r[0] for r in jan_rows}
log(f"  January 2026 Crypto markets: {len(jan_markets)} ({time.time()-t0:.1f}s)")

gambling_markets = _bp_mod.get_gambling_markets(con=con)
log(f"  Gambling markets excluded: {len(gambling_markets)}")

# Compute January 2026 Crypto YES base rate (test period reference)
base_row = con.execute("""
    SELECT avg(CAST(p.yes_won AS DOUBLE)) AS base_rate,
           count(DISTINCT p.condition_id) AS n_markets,
           count(*) AS n_positions
    FROM maker_positions p
    JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Crypto'
      AND p.position = 'YES'
      AND CAST(p.resolved_at AS DATE) >= '2026-01-01'
      AND CAST(p.resolved_at AS DATE) < '2026-02-01'
""").fetchone()
jan_base_rate = base_row[0] if base_row else None
jan_n_mkt = base_row[1] if base_row else None
jan_n_pos = base_row[2] if base_row else None
log(f"  Jan 2026 Crypto YES base rate: {jan_base_rate:.3f} ({jan_n_mkt} markets, {jan_n_pos} positions)")

# ── Step 4: Instantiate strategy ──────────────────────────────────────────
log("\nStep 4: Instantiate PriceGatedStrategy (trigger_price_cap=0.65)...")

strategy = PriceGatedStrategy(
    name="crypto_wf_2026jan",
    pool=pool,
    tag_markets=jan_markets,
    gambling_markets=gambling_markets,
    n_threshold=2,
    token_map=token_map,
    direction_filter="YES",
    size_usd=100.0,
    max_price=None,           # standard: triggering_price + 0.02 per signal
    trigger_price_cap=0.65,
)

config = StrategyConfig(
    name="crypto_wf_2026jan",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=5000.0,
    max_position_usd=100.0,
    max_open_positions=50,
    cooldown_s=0,
)

log(f"  Strategy: {strategy.name}")
log(f"  N_threshold=2 | direction=YES | trigger_price_cap=0.65 | size_usd=$100")
log(f"  Capital=$5,000 | max_pos=$100 | max_open=50")
log(f"  Asset cache pre-populated: {len(strategy._asset_direction_cache)} entries")

# ── Step 5: Run tick-by-tick backtest ─────────────────────────────────────
log(f"\nStep 5: Run tick-by-tick replay over {len(jan_markets)} Jan 2026 Crypto markets...")
t0 = time.time()

output_dir = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
result, summary = run_fast_backtest(
    strategy,
    config,
    universe=jan_markets,
    output_dir=output_dir,
)
elapsed = time.time() - t0

log(f"  Replay elapsed: {elapsed:.1f}s")
log(f"  Total trades seen: {result.total_trades:,}")
log(f"  Total intents fired: {result.total_intents}")
log(f"  Total fills: {result.total_fills}")
log(f"  Rejected intents: {len(result.rejected_intents)}")

# ── Step 6: Results ────────────────────────────────────────────────────────
if summary:
    log(f"\n--- Validation Results ---")
    log(f"  Hit Rate:     {summary.hit_rate:.1%}")
    log(f"  Net PnL:      ${summary.total_pnl_net:,.2f}")
    log(f"  Total Fees:   ${summary.total_fees:,.2f}")
    log(f"  Avg Edge:     ${summary.avg_edge:,.4f}")
    log(f"  Sharpe:       {summary.sharpe:.2f}")
    log(f"  Max Drawdown: ${summary.max_drawdown:,.2f}")
    log(f"  Profit Factor:{summary.profit_factor:.2f}")
    log(f"  Avg Hold:     {summary.avg_hold_duration_s / 3600:.1f}h")
    log(f"  Wins/Losses:  {summary.win_count}/{summary.loss_count} (pending: {summary.pending_count})")
    print_summary(summary, "Crypto WF 2026-Jan K=50 N=2")

    if jan_base_rate:
        excess = summary.hit_rate - jan_base_rate
        log(f"\n  Jan 2026 base rate: {jan_base_rate:.1%}")
        log(f"  Excess HR:          +{excess:.1%}pp")
else:
    log("  No summary — 0 fills or no settled positions")

# ── Step 7: Ledger deep analysis ──────────────────────────────────────────
log("\nStep 6: Ledger analysis...")
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

        # Hold time
        log("\n--- Hold Time ---")
        if "signal_time" in settled.columns:
            holds = ((settled["resolved_at"] - settled["signal_time"]) / 3600).drop_nulls()
            log(f"  Min:    {holds.min():.1f}h")
            log(f"  p25:    {holds.quantile(0.25):.1f}h")
            log(f"  Median: {holds.median():.1f}h")
            log(f"  p75:    {holds.quantile(0.75):.1f}h")
            log(f"  Max:    {holds.max():.1f}h")
            total = len(holds)
            log(f"  <1h:    {(holds < 1).sum()}/{total} = {100*(holds < 1).mean():.1f}%")
            log(f"  1-6h:   {((holds >= 1) & (holds < 6)).sum()}/{total}")
            log(f"  6-24h:  {((holds >= 6) & (holds < 24)).sum()}/{total}")
            log(f"  >24h:   {(holds >= 24).sum()}/{total}")

        # PnL distribution
        pnl_col = next((c for c in ["pnl_net", "net_pnl", "pnl"] if c in settled.columns), None)
        if pnl_col:
            log("\n--- PnL Distribution ---")
            pnl = settled[pnl_col].drop_nulls()
            log(f"  Median: ${pnl.median():,.2f}")
            log(f"  Mean:   ${pnl.mean():,.2f}")
            log(f"  Total:  ${pnl.sum():,.2f}")
            log(f"  Wins:   {(pnl > 0).sum()}, Losses: {(pnl < 0).sum()}")
else:
    log(f"  Ledger not found at {ledger_path}")

# ── Step 8: Comparison summary ────────────────────────────────────────────
log("\n" + "=" * 70)
log("WALK-FORWARD VERDICT")
log("=" * 70)
log("  Comparison vs full-period January result (train<2025-07-01, test=all):")
log("")
log("  Metric            | Full-period Jan | Walk-forward Jan (train<2026-01-01)")
log("  ------------------|-----------------|--------------------------------------")
log(f"  Train cutoff      | 2025-07-01      | 2026-01-01")
log(f"  Test period       | 2025-07+        | 2026-01 only")
log(f"  Fills             | 7               | {result.total_fills}")
if summary:
    log(f"  HR                | 85.7%           | {summary.hit_rate:.1%}")
    log(f"  Base rate         | N/A             | {jan_base_rate:.1%}" if jan_base_rate else "  Base rate         | N/A             | N/A")
    if jan_base_rate:
        excess = summary.hit_rate - jan_base_rate
        log(f"  Excess HR         | N/A             | +{excess:.1%}pp")
    log(f"  Net PnL ($100/pos)| N/A             | ${summary.total_pnl_net:,.0f}")
else:
    log(f"  HR                | 85.7%           | N/A (no settled positions)")

log(f"\nLog: {LOG_PATH}")
log("Done.")
