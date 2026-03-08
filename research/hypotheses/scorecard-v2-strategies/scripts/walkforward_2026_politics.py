"""Walk-forward fold: train < 2026-01-01, test = January 2026. Politics YES Composite K=100 N=5."""
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/walkforward_2026_politics.log")
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
log("Walk-Forward Fold: train < 2026-01-01, test = January 2026")
log("Politics YES Composite K=100 N=5, max_price=0.80")
log("=" * 70)

# ── Step 1: Setup DB and build pool ──────────────────────────────────────────
log("\nStep 1: Build Politics composite pool (train < 2026-01-01)...")
t0 = time.time()

import importlib.util as _ilu

_bp_spec = _ilu.spec_from_file_location(
    "build_pools",
    Path(__file__).parent / "build_pools.py",
)
_bp_mod = _ilu.module_from_spec(_bp_spec)
_bp_spec.loader.exec_module(_bp_mod)

_ensure_shared_tables = _bp_mod._ensure_shared_tables
_build_composite_pool = _bp_mod._build_composite_pool
get_gambling_markets = _bp_mod.get_gambling_markets

from research.db import db as get_db

con = get_db().con
_ensure_shared_tables(con)

pool = _build_composite_pool(con, "Politics", k=100, train_cutoff="2026-01-01")
log(f"  Pool size: {len(pool)} traders ({time.time()-t0:.1f}s)")

# ── Step 2: Get January 2026 Politics markets ─────────────────────────────────
log("\nStep 2: Identify January 2026 Politics markets...")
rows = con.execute("""
    SELECT DISTINCT p.condition_id
    FROM maker_positions p
    JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Politics'
      AND CAST(p.resolved_at AS DATE) >= '2026-01-01'
      AND CAST(p.resolved_at AS DATE) < '2026-02-01'
""").fetchall()
jan_markets = {r[0] for r in rows}
log(f"  January 2026 Politics markets: {len(jan_markets)}")

gambling_markets = get_gambling_markets(con=con)
log(f"  Gambling markets excluded: {len(gambling_markets)}")

# ── Step 3: Compute test-period base rate ─────────────────────────────────────
log("\nStep 3: Compute January 2026 Politics YES base rate...")
base_rows = con.execute("""
    SELECT
        avg(CAST(p.yes_won AS DOUBLE)) AS yes_base_rate,
        count(DISTINCT p.condition_id) AS n_markets
    FROM maker_positions p
    JOIN _bp_market_tags mt ON p.condition_id = mt.condition_id
    WHERE mt.primary_tag = 'Politics'
      AND p.position = 'YES'
      AND p.volume > 0
      AND CAST(p.resolved_at AS DATE) >= '2026-01-01'
      AND CAST(p.resolved_at AS DATE) < '2026-02-01'
""").fetchone()
yes_base_rate = base_rows[0]
n_markets_test = base_rows[1]
log(f"  January 2026 YES base rate: {yes_base_rate:.3f} ({n_markets_test} markets with positions)")

# ── Step 4: Load token map ────────────────────────────────────────────────────
log("\nStep 4: Load token_map from Parquet snapshot...")
t0 = time.time()
from research.fast_replay import load_replay_resolutions

_, token_map_raw = load_replay_resolutions()
log(f"  token_map_raw loaded: {len(token_map_raw)} markets ({time.time()-t0:.1f}s)")

# Check sample keys to see format
sample_cid = next(iter(token_map_raw))
sample_outcomes = token_map_raw[sample_cid]
log(f"  Sample token_map key sample: {sample_cid} -> {list(sample_outcomes.keys())[:5]}")

# Also load from harness load_token_map (returns outcome-keyed dict)
from research.harness import load_token_map

token_map_hm = load_token_map()
log(f"  load_token_map returned {len(token_map_hm)} markets")

# Check key format of load_token_map
if token_map_hm:
    sample2 = next(iter(token_map_hm))
    log(f"  load_token_map sample: {sample2} -> {list(token_map_hm[sample2].keys())}")

# Fix case: load_token_map returns mixed "Yes"/"No" — need uppercase "YES"/"NO"
token_map: dict[str, dict[str, str]] = {}
for cid, outcomes in token_map_hm.items():
    token_map[cid] = {k.upper(): v for k, v in outcomes.items()}

log(f"  Fixed token_map: {len(token_map)} markets")

# Verify fixed keys
if token_map:
    sample3 = next(iter(token_map))
    log(f"  Fixed sample: {sample3} -> {list(token_map[sample3].keys())}")

# ── Step 5: Instantiate strategy ─────────────────────────────────────────────
log("\nStep 5: Instantiate TokenMapStrategy (direction_filter='YES', max_price=0.80)...")
from research.strategies.consensus_v2 import TokenMapStrategy

strategy = TokenMapStrategy(
    name="politics_wf_2026jan",
    pool=pool,
    tag_markets=jan_markets,
    gambling_markets=gambling_markets,
    n_threshold=5,
    token_map=token_map,
    direction_filter="YES",
    max_price=0.80,
    size_usd=100.0,
)
log(f"  Strategy ready: {strategy.name}")
log(f"  Pool traders: {len(strategy._pool)}")
log(f"  Tag markets: {len(strategy._tag_markets)}")

# ── Step 6: Config ────────────────────────────────────────────────────────────
from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

config = StrategyConfig(
    name="politics_wf_2026jan",
    enabled=True,
    mode=ExecutionMode.REPLAY,
    capital_usd=5000.0,
    max_position_usd=100.0,
    max_open_positions=50,
    cooldown_s=0,
)

# ── Step 7: Run tick-by-tick backtest ─────────────────────────────────────────
log(f"\nStep 6: Running tick-by-tick backtest on {len(jan_markets)} January 2026 markets...")
from research.harness import run_fast_backtest, print_summary

t0 = time.time()
result, summary = run_fast_backtest(
    strategy,
    config,
    universe=jan_markets,
    output_dir=Path("/mnt/nvme/git/polymarket/polymarket/research/output"),
)
elapsed = time.time() - t0

log(f"\n--- Backtest complete ({elapsed:.1f}s) ---")
log(f"  Total trades seen:  {result.total_trades:,}")
log(f"  Total intents:      {result.total_intents}")
log(f"  Total fills:        {result.total_fills}")
log(f"  Rejected intents:   {len(result.rejected_intents)}")

# ── Step 8: Report results ────────────────────────────────────────────────────
log("\n" + "=" * 70)
log("RESULTS: Walk-forward January 2026 Politics YES Composite")
log("=" * 70)

if summary:
    print_summary(summary, "politics_wf_2026jan")

    hr = summary.hit_rate
    excess = hr - yes_base_rate
    avg_hold_days = summary.avg_hold_duration_s / 86400
    net_pnl = summary.total_pnl_net
    n_fills = result.total_fills

    log(f"\n  January 2026 YES base rate:  {yes_base_rate:.1%}")
    log(f"  Strategy hit rate:           {hr:.1%}")
    log(f"  Excess HR:                   {excess:+.1%}")
    log(f"  Net PnL:                     ${net_pnl:,.2f}")
    log(f"  Fills:                       {n_fills}")
    log(f"  Avg hold:                    {avg_hold_days:.2f} days")
    log(f"  Sharpe:                      {summary.sharpe:.3f}")

    # Compounding score
    if avg_hold_days > 0 and excess > 0 and n_fills > 0:
        avg_edge_usd = net_pnl / n_fills
        cs = excess * abs(avg_edge_usd) / avg_hold_days
        log(f"\n  Compounding score:           {cs:.4f}")
        log(f"    excess_hr={excess:.3f}, avg_edge_usd=${avg_edge_usd:.2f}, avg_hold_days={avg_hold_days:.2f}")

    # Comparison to full-period result
    log(f"\n--- Comparison to full-period (2025-07 onwards) ---")
    log(f"  Full-period fills:           64")
    log(f"  Full-period HR:              71.9%")
    log(f"  January 2026 fold fills:     {result.total_fills}")
    log(f"  January 2026 fold HR:        {hr:.1%}")
else:
    log("  No settled positions (summary=None) — 0 fills")
    log("  Possible causes:")
    log("    - Pool traders not active in January 2026 markets")
    log("    - max_price=0.80 filtering all signals")
    log("    - N=5 threshold too high for this period")
    log(f"\n  Debug: total_intents={result.total_intents}, total_fills={result.total_fills}")
    log(f"         rejected={len(result.rejected_intents)}")
    if result.rejected_intents:
        for ri in result.rejected_intents[:5]:
            log(f"           rejected: {ri}")

log("\n" + "=" * 70)
log("DONE")
log("=" * 70)
