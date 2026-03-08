"""Smoke test: run each consensus strategy through SyncReplayRunner.

Verifies that each strategy produces fills. Used as a quick sanity check
before running full tick-by-tick validation.

Usage:
    PYTHONPATH=. uv run python research/hypotheses/scorecard-v2-strategies/scripts/run_tick_validation.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

from research.harness import run_fast_backtest, print_summary
from research.fast_replay import load_replay_resolutions

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/tick_validation_smoke.log")


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


# Clear log
LOG_PATH.parent.mkdir(exist_ok=True)
with open(LOG_PATH, "w") as f:
    pass

log("=" * 70)
log("Consensus Strategy v2 — Smoke Test")
log("=" * 70)

# ── Step 1: Load token_map (needed for direction resolution) ──────────────────
log("\nStep 1: Load token_map from Parquet snapshot...")
t0 = time.time()
_, token_map = load_replay_resolutions()
log(f"  token_map loaded: {len(token_map)} markets ({time.time()-t0:.1f}s)")

# ── Step 2: Build pools ───────────────────────────────────────────────────────
log("\nStep 2: Build trader pools (DuckDB)...")
t0 = time.time()

import importlib.util as _ilu
_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)
_bp_spec.loader.exec_module(_bp_mod)

build_politics_composite_pool = _bp_mod.build_politics_composite_pool
build_crypto_hr_pool = _bp_mod.build_crypto_hr_pool
build_sports_composite_pool = _bp_mod.build_sports_composite_pool

log("  Building Politics composite K=100...")
pool_pol, markets_pol, gambling = build_politics_composite_pool(k=100)
log(f"  Politics: {len(pool_pol)} traders, {len(markets_pol)} tag markets")

log("  Building Crypto hr_only K=50...")
pool_crypt, markets_crypt, _ = build_crypto_hr_pool(k=50)
log(f"  Crypto: {len(pool_crypt)} traders, {len(markets_crypt)} tag markets")

log("  Building Sports composite K=25...")
pool_sport, markets_sport, _ = build_sports_composite_pool(k=25)
log(f"  Sports: {len(pool_sport)} traders, {len(markets_sport)} tag markets")

log(f"  All pools built ({time.time()-t0:.1f}s total)")

# ── Step 3: Create strategies ─────────────────────────────────────────────────
log("\nStep 3: Instantiate strategies with pre-loaded token_map...")

from research.strategies.consensus_v2 import TokenMapStrategy

strategy_politics = TokenMapStrategy(
    name="politics_composite_k100_n5",
    pool=pool_pol,
    tag_markets=markets_pol,
    gambling_markets=gambling,
    n_threshold=5,
    token_map=token_map,
    direction_filter=None,   # YES + NO both viable for Politics
    size_usd=100.0,
)

strategy_crypto = TokenMapStrategy(
    name="crypto_hr_k50_n2",
    pool=pool_crypt,
    tag_markets=markets_crypt,
    gambling_markets=gambling,
    n_threshold=2,
    token_map=token_map,
    direction_filter="YES",  # YES-only — NO signals are structural bias
    size_usd=100.0,
)

strategy_sports = TokenMapStrategy(
    name="sports_composite_k25_n3",
    pool=pool_sport,
    tag_markets=markets_sport,
    gambling_markets=gambling,
    n_threshold=3,
    token_map=token_map,
    direction_filter="YES",  # YES-only — NO signals are structural bias
    min_hold_hours=4.0,      # Sports: skip markets with <4h hold time
    size_usd=100.0,
)

log("  3 strategies ready")

# ── Step 4: Config ────────────────────────────────────────────────────────────
base_config = dict(
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=50000.0,
    max_position_usd=500.0,
    max_open_positions=100,
    cooldown_s=0,
)

results = {}

# ── Step 5: Run each strategy ─────────────────────────────────────────────────
for strategy, universe, label in [
    (strategy_politics, markets_pol, "Politics"),
    (strategy_crypto,  markets_crypt, "Crypto"),
    (strategy_sports,  markets_sport, "Sports"),
]:
    log(f"\n--- Running {label} ({len(universe)} markets) ---")
    config = StrategyConfig(name=strategy.name, **base_config)

    t0 = time.time()
    result, summary = run_fast_backtest(
        strategy,
        config,
        universe=universe,
        output_dir=Path("research/output"),
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
        print_summary(summary, label)
    else:
        log("  No settled positions (summary=None)")

    results[label] = {
        "fills": result.total_fills,
        "summary": summary,
    }

# ── Step 6: Summary ───────────────────────────────────────────────────────────
log("\n" + "=" * 70)
log("SMOKE TEST SUMMARY")
log("=" * 70)
any_fills = False
for label, r in results.items():
    fills = r["fills"]
    s = r["summary"]
    status = "PASS" if fills > 0 else "FAIL (no fills)"
    hr_str = f"HR={s.hit_rate:.1%}" if s else "HR=N/A"
    log(f"  {label:<12} fills={fills:4d}  {hr_str}  [{status}]")
    if fills > 0:
        any_fills = True

log("")
if any_fills:
    log("RESULT: At least one strategy produced fills — smoke test PASSED")
else:
    log("RESULT: No fills from any strategy — smoke test FAILED")
    sys.exit(1)
