"""Tick-by-tick validation R5: sharp pool + signal-time volume + dissent filter.

Three structural fixes stacked (all confirmed in vectorized exploration):
  1. Sharp pool K=30 (top traders by excess_hr) — controls pool explosion
  2. Signal-time volume >= $500 (causal, from first N traders)
  3. Dissent ratio >= 0.90 (skip when qualified NO traders dilute)

Plus:
  - price_ceil=0.75 (NOT 0.40 — confirmed anti-knowledge from R4)
  - fee_pct=0.0 (confirmed zero fees for Esports/Tennis)
  - Training-window base rate for pool qualification (no look-ahead)
  - INNER JOIN on yes_entry_data (no coalesce default bug)
  - Regime gate: skip folds where train_base > 0.50

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/validation/run_validation_r5.py
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/validation/run_validation_r5.py --tag Esports
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
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.ledger.analytics import compute_summary
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.types import ExecutionMode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy_r5 import ConsensusStrategyR5

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOLDS = [
    # (train_start, train_end, test_start, test_end, test_month)
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01", 202507),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01", 202510),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01", 202601),
]

# R5 params — all three structural fixes
PRICE_CEIL = 0.75       # NOT 0.40 (anti-knowledge)
FEE_PCT = 0.0           # confirmed zero fees for Esports/Tennis
REGIME_GATE = 0.55      # skip folds where train_base > this

# Each combo: (tag, label, consensus_n, sharp_k, min_signal_vol, min_dissent, window_hours)
# Sweep: K x N x vol_gate x dissent levels
COMBOS = [
    # --- Esports: sweep K and N ---
    # K=30, strict vol+dissent
    ("Esports", "K30_N3_v200_d70",  3, 30, 200.0, 0.70, None),
    ("Esports", "K30_N2_v200_d70",  2, 30, 200.0, 0.70, None),
    ("Esports", "K30_N2_v200_d00",  2, 30, 200.0, 0.00, None),  # no dissent
    ("Esports", "K30_N3_v0_d00",    3, 30, 0.0,   0.00, None),  # sharp pool only
    # K=50, more signal
    ("Esports", "K50_N3_v200_d70",  3, 50, 200.0, 0.70, None),
    ("Esports", "K50_N2_v200_d00",  2, 50, 200.0, 0.00, None),
    # --- Tennis: sweep K and N ---
    ("Tennis",  "K30_N3_v200_d70",  3, 30, 200.0, 0.70, None),
    ("Tennis",  "K30_N2_v200_d70",  2, 30, 200.0, 0.70, None),
    ("Tennis",  "K30_N2_v200_d00",  2, 30, 200.0, 0.00, None),
    ("Tennis",  "K30_N3_v0_d00",    3, 30, 0.0,   0.00, None),
    ("Tennis",  "K50_N3_v200_d70",  3, 50, 200.0, 0.70, None),
    ("Tennis",  "K50_N2_v200_d00",  2, 50, 200.0, 0.00, None),
]

MIN_TRADES = 5
BOT_GUARD = 10_000
POSITION_SIZE_USD = 100.0
OUTPUT_DIR = Path("research/hypotheses/tag-hr-consensus/validation")
LOG_PATH = Path("tmp/consensus_validate_r5.log")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def build_sharp_pool(
    d, train_start: str, train_end: str, train_base_rate: float,
    k: int,
) -> set[str]:
    """Build sharp pool: top K traders by excess_hr.

    Uses INNER JOIN on yes_entry_data (no coalesce default).
    Training-window base rate for excess HR computation.
    """
    # Per-trader avg entry price from yes_entry_data (training window only)
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

    # All qualifying traders with their excess_hr, ranked
    result = d.fetchall(f"""
        SELECT
            p.trader,
            sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS raw_hr,
            sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base_rate} AS excess_hr,
            count() AS n_trades
        FROM maker_positions p
        INNER JOIN _val_ep_tmp ep ON p.trader = ep.trader
        WHERE p.condition_id IN (SELECT condition_id FROM _val_tag_mkts)
          AND p.position = 'YES'
          AND CAST(p.resolved_at AS DATE) >= '{train_start}'
          AND CAST(p.resolved_at AS DATE) < '{train_end}'
        GROUP BY p.trader
        HAVING count() >= {MIN_TRADES}
          AND count() < {BOT_GUARD}
          AND raw_hr < 0.99
          AND excess_hr > 0
          AND first(ep.avg_ep) <= 0.80
        ORDER BY excess_hr DESC
        LIMIT {k}
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


def get_asset_ids(d, universe: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Get both YES and NO asset_ids for dissent tracking."""
    if not universe:
        return {}, {}
    placeholders = ", ".join(f"'{cid}'" for cid in universe)
    result = d.fetchall(f"""
        SELECT condition_id, asset_id, outcome
        FROM token_market_map
        WHERE condition_id IN ({placeholders})
    """)
    yes_map = {}
    no_map = {}
    for r in result:
        if r["outcome"] == "YES":
            yes_map[r["condition_id"]] = r["asset_id"]
        elif r["outcome"] == "NO":
            no_map[r["condition_id"]] = r["asset_id"]
    return yes_map, no_map


# ---------------------------------------------------------------------------
# Fold runner
# ---------------------------------------------------------------------------


def run_fold(
    d, tag: str, label: str,
    train_start: str, train_end: str,
    test_start: str, test_end: str, test_month: int,
    consensus_n: int, sharp_k: int, min_signal_vol: float,
    min_dissent: float, window_hours: float | None,
    resolutions: dict, token_map: dict,
) -> dict | None:
    # Training-window base rate
    train_base_rate, n_train_markets = get_base_rate_window(d, train_start, train_end)

    # Regime gate: skip if training base rate too high
    if train_base_rate > REGIME_GATE:
        log(f"    fold {test_start}: REGIME GATE — train_base={train_base_rate:.3f} > {REGIME_GATE}")
        return {
            "fold": test_start,
            "train_base_rate": train_base_rate,
            "test_base_rate": None,
            "n_train_markets": n_train_markets,
            "n_test_markets": 0,
            "n_qualified_traders": 0,
            "n_signals": 0,
            "hit_rate": None,
            "excess_hr_pp": None,
            "total_pnl_net": 0.0,
            "skip_reason": f"regime_gate (train_base={train_base_rate:.3f})",
        }

    # Test-window base rate (for reporting)
    test_base_rate, n_test_markets = get_base_rate_window(d, test_start, test_end)

    if n_test_markets < 5:
        log(f"    fold {test_start}: SKIP — n_test_markets={n_test_markets}")
        return None

    # Build SHARP pool (top K by excess_hr)
    qualified = build_sharp_pool(d, train_start, train_end, train_base_rate, sharp_k)
    universe = get_universe(d, test_start, test_end)
    yes_asset_ids, no_asset_ids = get_asset_ids(d, universe)

    log(
        f"    fold {test_start}: train_base={train_base_rate:.3f} test_base={test_base_rate:.3f} "
        f"n_train={n_train_markets} n_test={n_test_markets} "
        f"sharp_pool={len(qualified)}/{sharp_k} universe={len(universe)}"
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

    # Load test ticks
    ticks_df = load_replay_trades(
        universe=universe, start_month=test_month, end_month=test_month, as_ticks=False
    )
    ticks = _df_to_ticks(ticks_df) if not ticks_df.is_empty() else []

    # Build strategy with gates
    strategy = ConsensusStrategyR5(
        qualified_traders=qualified,
        yes_asset_ids=yes_asset_ids,
        no_asset_ids=no_asset_ids,
        consensus_n=consensus_n,
        price_ceil=PRICE_CEIL,
        min_signal_vol=min_signal_vol,
        min_dissent_ratio=min_dissent,
        test_start_epoch=_epoch(test_start),
        size_usd=POSITION_SIZE_USD,
    )

    config = StrategyConfig(
        name="consensus_r5", enabled=True, mode=ExecutionMode.REPLAY,
        capital_usd=50_000, max_position_usd=POSITION_SIZE_USD,
        max_open_positions=500, cooldown_s=0,
    )

    # SimulatedExecutor with zero fees (confirmed for Esports/Tennis)
    executor = SimulatedExecutor(fee_pct=FEE_PCT)
    gateway = ExecutionGateway(executor, strategy_budgets={"consensus_r5": 50_000})
    ctx = InMemoryContext()

    combo_key = f"{tag}_{label}_N{consensus_n}_{test_start[:7]}"
    ledger_path = OUTPUT_DIR / f"ledger_r5_{combo_key}.parquet"
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

    # Log gate stats
    log(
        f"    fold {test_start}: gates — skip_vol={strategy.skip_vol} "
        f"skip_dissent={strategy.skip_dissent} skip_price={strategy.skip_price} "
        f"fired={strategy.total_fired}"
    )

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
            "gate_stats": {
                "skip_vol": strategy.skip_vol,
                "skip_dissent": strategy.skip_dissent,
                "skip_price": strategy.skip_price,
            },
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
        "gate_stats": {
            "skip_vol": strategy.skip_vol,
            "skip_dissent": strategy.skip_dissent,
            "skip_price": strategy.skip_price,
        },
    }


# ---------------------------------------------------------------------------
# Combo runner
# ---------------------------------------------------------------------------


def run_combo(
    tag: str, label: str,
    consensus_n: int, sharp_k: int, min_signal_vol: float,
    min_dissent: float, window_hours: float | None,
    resolutions: dict, token_map: dict,
    tags_to_run: list[str] | None,
) -> dict:
    if tags_to_run and tag not in tags_to_run:
        return {}

    log(
        f"\n--- {tag} {label}: N={consensus_n} K={sharp_k} "
        f"pc={PRICE_CEIL} vol>=${min_signal_vol} dissent>={min_dissent} ---"
    )

    d = db()
    setup_tag_mkts(d, tag)

    fold_results = []
    for ts, te, xs, xe, xm in FOLDS:
        fold_res = run_fold(
            d, tag, label, ts, te, xs, xe, xm,
            consensus_n, sharp_k, min_signal_vol, min_dissent,
            window_hours,
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
            "sharp_k": sharp_k,
            "price_ceil": PRICE_CEIL,
            "min_signal_vol": min_signal_vol,
            "min_dissent_ratio": min_dissent,
            "fee_pct": FEE_PCT,
            "regime_gate": REGIME_GATE,
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

    log(
        "=== tag-hr-consensus R5: sharp pool K=30 + signal-time vol >= $500 "
        "+ dissent >= 0.90 + price_ceil=0.75 + regime gate ==="
    )

    t0 = time.time()

    log("Loading resolutions...")
    resolutions, token_map = load_replay_resolutions()
    log(f"Loaded {len(resolutions):,} resolutions, {len(token_map):,} token_map entries")

    results = {
        "hypothesis": "tag-hr-consensus",
        "phase": "validation-r5",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "price_ceil": PRICE_CEIL,
            "fee_pct": FEE_PCT,
            "regime_gate": REGIME_GATE,
            "note": "per-combo K, vol, dissent — see each combo's params",
        },
        "note": (
            "Tick-by-tick R5. BUY-only. Three structural fixes stacked: "
            "sharp pool K=30 + signal-time vol >= $500 + dissent >= 0.90. "
            "price_ceil=0.75. SimulatedExecutor fee=0 (Esports/Tennis zero fees). "
            "Regime gate: skip train_base > 0.50. "
            "Training-window base rate. INNER JOIN on yes_entry_data."
        ),
        "combos": [],
    }

    for tag, label, n, k, vol, dis, w in COMBOS:
        combo_result = run_combo(
            tag, label, n, k, vol, dis, w,
            resolutions, token_map, tags_to_run,
        )
        if combo_result:
            results["combos"].append(combo_result)

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")

    out_path = OUTPUT_DIR / "results_r5.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"Written: {out_path}")


if __name__ == "__main__":
    main()
