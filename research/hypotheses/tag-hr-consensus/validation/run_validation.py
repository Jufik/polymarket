"""Tick-by-tick validation for tag-hr-consensus — Round 4 params.

Task #11 fixes applied:
  1. price_ceil=0.40 (break-even drops from 50% to 40% HR)
  2. RealisticFillSimulator with per-universe calibrated spreads/volumes
  3. Training-window base rate for pool qualification (not test-window)
  4. Exclude traders missing from yes_entry_data (INNER JOIN, not coalesce default)
  5. Added Esports meh=20pp combo

Combos tested:
  Esports primary:   N=4, pc=0.40, meh=10pp, mpe=0.80
  Esports sensitive: N=3, pc=0.40, meh=15pp, mpe=0.70
  Esports meh20:     N=3, pc=0.40, meh=20pp, mpe=0.80
  Tennis primary:    N=3, pc=0.40, meh=20pp, mpe=0.90
  Tennis sensitive:  N=2, pc=0.40, meh=20pp, mpe=0.70

Pool qualification per fold (training window base rate):
  1. position='YES', count >= MIN_TRADES
  2. raw_hr - TRAIN_base_rate >= meh_thresh
  3. avg_entry_price <= mpe_thresh (INNER JOIN — exclude missing ep traders)
  4. raw_hr < 0.99 (bot guard)
  5. count < BOT_GUARD

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/validation/run_validation.py
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/validation/run_validation.py --tag Esports
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from research.db import db
from research.fast_replay import _df_to_ticks, load_replay_resolutions, load_replay_trades
from research.sync_replay import SyncReplayRunner, _run_coro

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.calibrate import calibrate_spreads, calibrate_volumes
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.realistic import FillModelConfig, RealisticFillSimulator
from polymarket_pipeline.strategies.ledger.analytics import compute_summary
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.types import ExecutionMode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy import ConsensusStrategy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOLDS = [
    # (train_start, train_end, test_start, test_end, test_month)
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01", 202507),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01", 202510),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01", 202601),
]

# price_ceil=0.40 (Task #11: break-even drops to 40% HR)
PRICE_CEIL = 0.40

# Each combo: (tag, label, consensus_n, price_ceil, meh_pp, mpe, window_hours)
# Task #11 params: Esports A/B/C + Tennis A/B
COMBOS = [
    ("Esports", "A",  4, PRICE_CEIL, 10, 0.80, None),  # Esports A: N=4 meh=10pp mpe=0.80
    ("Esports", "B",  3, PRICE_CEIL, 15, 0.70, None),  # Esports B: N=3 meh=15pp mpe=0.70
    ("Esports", "C",  2, PRICE_CEIL, 20, 0.80, None),  # Esports C: N=2 meh=20pp mpe=0.80 (never validated)
    ("Tennis",  "A",  3, PRICE_CEIL, 20, 0.90, None),  # Tennis A:  N=3 meh=20pp mpe=0.90
    ("Tennis",  "B",  2, PRICE_CEIL, 20, 0.70, None),  # Tennis B:  N=2 meh=20pp mpe=0.70
]

MIN_TRADES = 5
BOT_GUARD = 10_000
POSITION_SIZE_USD = 100.0
OUTPUT_DIR = Path("research/hypotheses/tag-hr-consensus/validation")
LOG_PATH = Path("tmp/consensus_validate_r3final.log")
RESULT_PREFIX = "r3_"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def _epoch(date_str: str) -> float:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def setup_tag_mkts(d, tag: str) -> None:
    d.execute("DROP TABLE IF EXISTS _val_tag_mkts")
    d.execute("""
        CREATE TEMP TABLE _val_tag_mkts AS
        SELECT DISTINCT m.condition_id
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.label = $1
    """, [tag])


def get_base_rate_window(d, start: str, end: str) -> tuple[float, int]:
    """Compute YES win rate for resolved markets in a date window."""
    r = d.fetchone(f"""
        SELECT
            sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS yw,
            count() AS tot
        FROM (
            SELECT condition_id, first(yes_won) AS yes_won
            FROM maker_positions
            WHERE condition_id IN (SELECT condition_id FROM _val_tag_mkts)
              AND CAST(resolved_at AS DATE) >= '{start}'
              AND CAST(resolved_at AS DATE) < '{end}'
            GROUP BY condition_id
        )
    """)
    yw, tot = r["yw"], r["tot"]
    return (round(yw / tot, 4) if tot > 0 else 0.5), tot


def build_qualified_pool(
    d, train_start: str, train_end: str, train_base_rate: float,
    meh_thresh: float, mpe_thresh: float,
) -> set[str]:
    """Build qualified pool with mpe filter.

    Uses INNER JOIN on yes_entry_data — excludes traders missing from it
    entirely (no coalesce default).

    Pool qualification uses TRAINING-window base rate (not test-window).
    """

    # Step 1: per-trader avg entry price from yes_entry_data (training window only)
    d.execute("DROP TABLE IF EXISTS _val_ep_tmp")
    d.execute(f"""
        CREATE TEMP TABLE _val_ep_tmp AS
        SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        WHERE y.condition_id IN (SELECT condition_id FROM _val_tag_mkts)
          AND CAST(y.first_trade AS DATE) >= '{train_start}'
          AND CAST(y.first_trade AS DATE) < '{train_end}'
        GROUP BY y.trader
    """)

    # Step 2: qualify — HR filter (vs TRAIN base rate) + mpe filter
    # INNER JOIN: traders missing from yes_entry_data are excluded
    result = d.fetchall(f"""
        SELECT p.trader
        FROM maker_positions p
        INNER JOIN _val_ep_tmp ep ON p.trader = ep.trader
        WHERE p.condition_id IN (SELECT condition_id FROM _val_tag_mkts)
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
        GROUP BY p.trader
        HAVING count() >= {MIN_TRADES}
          AND count() < {BOT_GUARD}
          AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() < 0.99
          AND sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base_rate} >= {meh_thresh}
          AND first(ep.avg_ep) <= {mpe_thresh}
    """)
    return {r["trader"] for r in result}


def get_universe(d, test_start: str, test_end: str) -> set[str]:
    result = d.fetchall(f"""
        SELECT DISTINCT condition_id
        FROM maker_positions
        WHERE condition_id IN (SELECT condition_id FROM _val_tag_mkts)
          AND CAST(resolved_at AS DATE) >= '{test_start}'
          AND CAST(resolved_at AS DATE) < '{test_end}'
    """)
    return {r["condition_id"] for r in result}


def get_yes_asset_ids(d, universe: set[str]) -> dict[str, str]:
    if not universe:
        return {}
    placeholders = ", ".join(f"'{cid}'" for cid in universe)
    result = d.fetchall(f"""
        SELECT condition_id, asset_id
        FROM token_market_map
        WHERE condition_id IN ({placeholders})
          AND outcome = 'YES'
    """)
    return {r["condition_id"]: r["asset_id"] for r in result}


def run_fold(
    d, tag: str, label: str,
    train_start: str, train_end: str,
    test_start: str, test_end: str, test_month: int,
    consensus_n: int, price_ceil: float, meh_pp: int, mpe: float,
    window_hours: float | None,
    resolutions: dict, token_map: dict,
) -> dict | None:
    meh_thresh = meh_pp / 100.0

    # Training-window base rate (for pool qualification)
    train_base_rate, n_train_markets = get_base_rate_window(d, train_start, train_end)

    # Test-window base rate (for reporting excess HR)
    test_base_rate, n_test_markets = get_base_rate_window(d, test_start, test_end)

    if n_test_markets < 5:
        log(f"    fold {test_start}: SKIP — n_test_markets={n_test_markets}")
        return None

    # Build qualified pool using TRAINING base rate
    qualified = build_qualified_pool(
        d, train_start, train_end, train_base_rate, meh_thresh, mpe
    )
    universe = get_universe(d, test_start, test_end)
    yes_asset_ids = get_yes_asset_ids(d, universe)

    log(
        f"    fold {test_start}: train_base={train_base_rate:.3f} test_base={test_base_rate:.3f} "
        f"n_train={n_train_markets} n_test={n_test_markets} "
        f"pool={len(qualified)} universe={len(universe)}"
    )

    if len(qualified) < consensus_n:
        log(f"    fold {test_start}: SKIP — pool {len(qualified)} < N={consensus_n}")
        return {
            "fold": test_start,
            "train_base_rate": train_base_rate,
            "test_base_rate": test_base_rate,
            "n_train_markets": n_train_markets,
            "n_test_markets": n_test_markets,
            "n_qualified_traders": len(qualified),
            "n_signals": 0,
            "hit_rate": None,
            "excess_hr_pp": None,
            "total_pnl_net": 0.0,
            "skip_reason": f"pool_too_small ({len(qualified)} < {consensus_n})",
        }

    if not universe:
        log(f"    fold {test_start}: SKIP — empty universe")
        return None

    # Load test ticks for calibration and replay
    ticks_df = load_replay_trades(
        universe=universe, start_month=test_month, end_month=test_month, as_ticks=False
    )

    # Calibrate spreads and volumes from test ticks
    market_spreads = calibrate_spreads(ticks_df) if not ticks_df.is_empty() else {}
    market_volumes = calibrate_volumes(ticks_df) if not ticks_df.is_empty() else {}
    log(
        f"    fold {test_start}: calibrated {len(market_spreads)} spreads, "
        f"{len(market_volumes)} volumes from {len(ticks_df):,} ticks"
    )

    # Convert to ticks for SyncReplayRunner (already sorted by published_at)
    ticks = _df_to_ticks(ticks_df) if not ticks_df.is_empty() else []

    # Build strategy
    strategy = ConsensusStrategy(
        qualified_traders=qualified,
        yes_asset_ids=yes_asset_ids,
        consensus_n=consensus_n,
        price_ceil=price_ceil,
        window_hours=window_hours,
        test_start_epoch=_epoch(test_start),
        size_usd=POSITION_SIZE_USD,
    )

    config = StrategyConfig(
        name="consensus_copy", enabled=True, mode=ExecutionMode.REPLAY,
        capital_usd=50_000, max_position_usd=POSITION_SIZE_USD,
        max_open_positions=500, cooldown_s=0,
    )

    fill_config = FillModelConfig(
        fallback_half_spread=0.005,
        impact_scale=1.0,
        default_liquidity_usd=5000.0,
    )
    executor = RealisticFillSimulator(
        config=fill_config,
        market_spreads=market_spreads,
        market_volumes=market_volumes,
        fee_pct=0.0,
    )
    gateway = ExecutionGateway(executor, strategy_budgets={"consensus_copy": 50_000})
    ctx = InMemoryContext()

    combo_key = f"{tag}_{label}_N{consensus_n}_meh{meh_pp}_mpe{mpe}_{test_start[:7]}"
    ledger_path = OUTPUT_DIR / f"ledger_{RESULT_PREFIX}{combo_key}.parquet"
    ledger = ParquetLedger(ledger_path)

    fold_resolutions = {k: v for k, v in resolutions.items() if k in universe}

    runner = SyncReplayRunner(
        strategy=strategy, ctx=ctx, gateway=gateway, config=config,
        resolutions=fold_resolutions, token_map=token_map, ledger=ledger,
    )

    t0 = time.time()
    result = runner.run(ticks)
    elapsed = time.time() - t0

    _run_coro(ledger.flush())
    records = _run_coro(ledger.read_all())

    if not records:
        log(f"    fold {test_start}: 0 signals fired ({elapsed:.1f}s)")
        return {
            "fold": test_start,
            "train_base_rate": train_base_rate,
            "test_base_rate": test_base_rate,
            "n_train_markets": n_train_markets,
            "n_test_markets": n_test_markets,
            "n_qualified_traders": len(qualified),
            "n_signals": 0,
            "hit_rate": None,
            "excess_hr_pp": None,
            "total_pnl_net": 0.0,
        }

    settled = [r for r in records if r.resolution is not None]
    n_signals = len(records)
    n_settled = len(settled)
    n_wins = sum(1 for r in settled if r.pnl_net is not None and r.pnl_net > 0)
    hr = n_wins / n_settled if n_settled > 0 else None
    excess_hr = (hr - test_base_rate) * 100 if hr is not None else None
    total_pnl = sum(r.pnl_net for r in settled if r.pnl_net is not None)
    avg_fill = sum(r.fill_price for r in records if r.fill_price) / len(records) if records else None

    summary = compute_summary(records)

    log(
        f"    fold {test_start}: sigs={n_signals} settled={n_settled} "
        f"HR={hr:.1%} excess={excess_hr:+.1f}pp pnl=${total_pnl:.2f} "
        f"avg_fill={avg_fill:.3f} ({elapsed:.1f}s)"
        if hr is not None else
        f"    fold {test_start}: sigs={n_signals} no settled ({elapsed:.1f}s)"
    )

    return {
        "fold": test_start,
        "train_base_rate": train_base_rate,
        "test_base_rate": test_base_rate,
        "n_train_markets": n_train_markets,
        "n_test_markets": n_test_markets,
        "n_qualified_traders": len(qualified),
        "n_signals": n_signals,
        "n_settled": n_settled,
        "n_wins": n_wins,
        "hit_rate": round(hr, 4) if hr is not None else None,
        "excess_hr_pp": round(excess_hr, 2) if excess_hr is not None else None,
        "total_pnl_net": round(total_pnl, 2),
        "avg_fill_price": round(avg_fill, 4) if avg_fill is not None else None,
        "avg_edge_usd": round(summary.avg_edge, 4) if summary else None,
        "sharpe": round(summary.sharpe, 3) if summary else None,
        "avg_hold_hours": round(summary.avg_hold_duration_s / 3600, 2) if summary else None,
        "elapsed_s": round(elapsed, 1),
    }


def run_combo(
    tag: str, label: str,
    consensus_n: int, price_ceil: float, meh_pp: int, mpe: float,
    window_hours: float | None,
    resolutions: dict, token_map: dict,
    tags_to_run: list[str] | None,
) -> dict:
    if tags_to_run and tag not in tags_to_run:
        return {}

    log(f"\n--- {tag} {label}: N={consensus_n} pc={price_ceil} meh={meh_pp}pp mpe={mpe} W={window_hours}h ---")

    d = db()
    setup_tag_mkts(d, tag)

    fold_results = []
    for ts, te, xs, xe, xm in FOLDS:
        fold_res = run_fold(
            d, tag, label, ts, te, xs, xe, xm,
            consensus_n, price_ceil, meh_pp, mpe, window_hours,
            resolutions, token_map,
        )
        if fold_res:
            fold_results.append(fold_res)

    valid_folds = [f for f in fold_results if f.get("hit_rate") is not None]
    if valid_folds:
        n_folds = len(valid_folds)
        agg_hr = sum(f["hit_rate"] for f in valid_folds) / n_folds
        agg_test_base = sum(f["test_base_rate"] for f in valid_folds) / n_folds
        agg_train_base = sum(f["train_base_rate"] for f in valid_folds) / n_folds
        agg_excess = sum(f["excess_hr_pp"] for f in valid_folds) / n_folds
        total_signals = sum(f["n_signals"] for f in fold_results)
        total_pnl = sum(f["total_pnl_net"] for f in fold_results)
        total_settled = sum(f.get("n_settled", 0) for f in fold_results)

        aggregate = {
            "n_folds": n_folds,
            "avg_hit_rate": round(agg_hr, 4),
            "avg_test_base_rate": round(agg_test_base, 4),
            "avg_train_base_rate": round(agg_train_base, 4),
            "avg_excess_hr_pp": round(agg_excess, 2),
            "total_signals": total_signals,
            "total_settled": total_settled,
            "signals_per_fold": round(total_signals / len(fold_results), 1),
            "total_pnl_net": round(total_pnl, 2),
        }
        log(
            f"  AGGREGATE ({tag} {label}): HR={agg_hr:.1%} excess={agg_excess:+.1f}pp "
            f"sigs={total_signals} pnl=${total_pnl:.2f}"
        )
    else:
        aggregate = None
        log(f"  AGGREGATE ({tag} {label}): no valid folds")

    return {
        "tag": tag,
        "label": label,
        "params": {
            "consensus_n": consensus_n,
            "price_ceil": price_ceil,
            "min_excess_hr_pp": meh_pp,
            "max_pool_entry_price": mpe,
            "window_hours": window_hours,
        },
        "folds": fold_results,
        "aggregate": aggregate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", nargs="+", help="Tags to run (Esports Tennis)")
    args = parser.parse_args()
    tags_to_run = args.tag

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=== tag-hr-consensus tick-by-tick validation (R3-final: price_ceil=0.40 + RealisticFills + train_base_rate + INNER JOIN) ===")

    t0 = time.time()

    log("Loading resolutions...")
    resolutions, token_map = load_replay_resolutions()
    log(f"Loaded {len(resolutions):,} resolutions, {len(token_map):,} token_map entries")

    results = {
        "hypothesis": "tag-hr-consensus",
        "phase": "validation-r3-final",
        "timestamp": datetime.now().isoformat(),
        "note": (
            "Tick-by-tick. BUY-only. price_ceil=0.40. RealisticFillSimulator (calibrated spreads, fee_pct=0). "
            "Pool uses TRAINING-window base rate. INNER JOIN on yes_entry_data (no coalesce default). "
            "Task #11 mandatory fixes all applied."
        ),
        "combos": [],
    }

    for tag, label, n, pc, meh, mpe, w in COMBOS:
        combo_result = run_combo(
            tag, label, n, pc, meh, mpe, w,
            resolutions, token_map, tags_to_run,
        )
        if combo_result:
            results["combos"].append(combo_result)

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")

    out_path = OUTPUT_DIR / f"results_{RESULT_PREFIX}final.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"Written: {out_path}")


if __name__ == "__main__":
    main()
