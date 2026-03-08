"""Tick-by-tick validation — Task #13: Sharp pool (top-K) + price_ceil=0.40.

Key changes from R3-final:
  - Top-K pool: rank traders by excess_hr DESC, take K best (not threshold-based)
  - price_ceil=0.40 (as specified)
  - fee_pct=0.0 (confirmed zero fees for Esports/Tennis)
  - SimulatedExecutor (zero fees confirmed)
  - Training-window base rate for ranking
  - INNER JOIN on yes_entry_data (exclude missing)

Combos:
  Esports K=20, N=3, pc=0.40
  Esports K=30, N=3, pc=0.40
  Esports K=50, N=3, pc=0.40
  Tennis  K=30, N=3, pc=0.40

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/validation/run_validation_r4.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from research.db import db
from research.fast_replay import load_replay_resolutions, load_replay_trades, _df_to_ticks
from research.sync_replay import SyncReplayRunner, _run_coro

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.execution.gateway import ExecutionGateway
from polymarket_pipeline.strategies.execution.simulated import SimulatedExecutor
from polymarket_pipeline.strategies.ledger.analytics import compute_summary
from polymarket_pipeline.strategies.ledger.parquet import ParquetLedger
from polymarket_pipeline.strategies.types import ExecutionMode

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy import ConsensusStrategy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOLDS = [
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01", 202507),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01", 202510),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01", 202601),
]

PRICE_CEIL = 0.40  # as specified in Task #13
MIN_TRADES = 5
BOT_GUARD = 10_000
POSITION_SIZE_USD = 100.0
OUTPUT_DIR = Path("research/hypotheses/tag-hr-consensus/validation")
LOG_PATH = Path("tmp/consensus_validate_r4_sharppool.log")

# Each combo: (tag, label, top_k, consensus_n, price_ceil)
COMBOS = [
    ("Esports", "K20_N3", 20, 3, PRICE_CEIL),
    ("Esports", "K30_N3", 30, 3, PRICE_CEIL),
    ("Esports", "K50_N3", 50, 3, PRICE_CEIL),
    ("Tennis",  "K30_N3", 30, 3, PRICE_CEIL),
]


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
    d.execute("DROP TABLE IF EXISTS _r4_tag_mkts")
    d.execute("""
        CREATE TEMP TABLE _r4_tag_mkts AS
        SELECT DISTINCT m.condition_id
        FROM markets m
        JOIN event_tags et ON m.event_id = et.event_id
        WHERE et.label = $1
    """, [tag])


def get_base_rate(d, start: str, end: str) -> tuple[float, int]:
    r = d.fetchone(f"""
        SELECT
            sum(CASE WHEN yes_won THEN 1 ELSE 0 END)::INT AS yw,
            count() AS tot
        FROM (
            SELECT condition_id, first(yes_won) AS yes_won
            FROM maker_positions
            WHERE condition_id IN (SELECT condition_id FROM _r4_tag_mkts)
              AND CAST(resolved_at AS DATE) >= '{start}'
              AND CAST(resolved_at AS DATE) < '{end}'
            GROUP BY condition_id
        )
    """)
    yw, tot = r["yw"], r["tot"]
    return (round(yw / tot, 4) if tot > 0 else 0.5), tot


def build_top_k_pool(
    d, train_start: str, train_end: str, train_base: float, top_k: int,
) -> set[str]:
    """Build pool of top-K traders ranked by excess_hr (training window).

    - INNER JOIN on yes_entry_data (exclude missing ep traders)
    - Minimum floor: excess_hr > 0, mpe <= 0.80, hr < 0.99, min_trades=5
    - Rank by excess_hr DESC, take top K
    """
    # Pre-compute avg_ep (training window, INNER JOIN later)
    d.execute("DROP TABLE IF EXISTS _r4_ep_tmp")
    d.execute(f"""
        CREATE TEMP TABLE _r4_ep_tmp AS
        SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        WHERE y.condition_id IN (SELECT condition_id FROM _r4_tag_mkts)
          AND CAST(y.first_trade AS DATE) >= '{train_start}'
          AND CAST(y.first_trade AS DATE) < '{train_end}'
        GROUP BY y.trader
    """)

    # Rank all eligible traders by excess_hr DESC, take top K
    result = d.fetchall(f"""
        WITH ranked AS (
            SELECT
                p.trader,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS raw_hr,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} AS excess_hr,
                count() AS n_mkts,
                first(ep.avg_ep) AS avg_ep,
                ROW_NUMBER() OVER (ORDER BY
                    sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} DESC,
                    count() DESC
                ) AS rank_hr
            FROM maker_positions p
            INNER JOIN _r4_ep_tmp ep ON p.trader = ep.trader
            WHERE p.condition_id IN (SELECT condition_id FROM _r4_tag_mkts)
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '{train_start}'
              AND CAST(p.resolved_at AS DATE) < '{train_end}'
            GROUP BY p.trader
            HAVING n_mkts >= {MIN_TRADES}
              AND n_mkts < {BOT_GUARD}
              AND raw_hr < 0.99
              AND excess_hr > 0
              AND avg_ep <= 0.80
        )
        SELECT trader FROM ranked WHERE rank_hr <= {top_k}
    """)
    return {r["trader"] for r in result}


def get_universe(d, test_start: str, test_end: str) -> set[str]:
    result = d.fetchall(f"""
        SELECT DISTINCT condition_id
        FROM maker_positions
        WHERE condition_id IN (SELECT condition_id FROM _r4_tag_mkts)
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
    top_k: int, consensus_n: int, price_ceil: float,
    resolutions: dict, token_map: dict,
) -> dict | None:
    # Training base rate (for ranking pool)
    train_base, n_train = get_base_rate(d, train_start, train_end)
    # Test base rate (for reporting)
    test_base, n_test = get_base_rate(d, test_start, test_end)

    if n_test < 5:
        log(f"    fold {test_start}: SKIP — n_test={n_test}")
        return None

    qualified = build_top_k_pool(d, train_start, train_end, train_base, top_k)
    universe = get_universe(d, test_start, test_end)
    yes_asset_ids = get_yes_asset_ids(d, universe)

    log(
        f"    fold {test_start}: train_base={train_base:.3f} test_base={test_base:.3f} "
        f"pool(top-{top_k})={len(qualified)} universe={len(universe)}"
    )

    if len(qualified) < consensus_n:
        log(f"    fold {test_start}: SKIP — pool {len(qualified)} < N={consensus_n}")
        return {
            "fold": test_start, "train_base_rate": train_base, "test_base_rate": test_base,
            "n_qualified_traders": len(qualified), "n_signals": 0,
            "hit_rate": None, "excess_hr_pp": None, "total_pnl_net": 0.0,
            "skip_reason": f"pool_too_small ({len(qualified)} < {consensus_n})",
        }

    strategy = ConsensusStrategy(
        qualified_traders=qualified,
        yes_asset_ids=yes_asset_ids,
        consensus_n=consensus_n,
        price_ceil=price_ceil,
        window_hours=None,
        test_start_epoch=_epoch(test_start),
        size_usd=POSITION_SIZE_USD,
    )

    config = StrategyConfig(
        name="consensus_copy", enabled=True, mode=ExecutionMode.REPLAY,
        capital_usd=50_000, max_position_usd=POSITION_SIZE_USD,
        max_open_positions=500, cooldown_s=0,
    )

    executor = SimulatedExecutor(fee_pct=0.0)
    gateway = ExecutionGateway(executor, strategy_budgets={"consensus_copy": 50_000})
    ctx = InMemoryContext()

    combo_key = f"{tag}_{label}_pc{price_ceil}_{test_start[:7]}"
    ledger_path = OUTPUT_DIR / f"ledger_r4_{combo_key}.parquet"
    ledger = ParquetLedger(ledger_path)

    fold_resolutions = {k: v for k, v in resolutions.items() if k in universe}

    runner = SyncReplayRunner(
        strategy=strategy, ctx=ctx, gateway=gateway, config=config,
        resolutions=fold_resolutions, token_map=token_map, ledger=ledger,
    )

    ticks_df = load_replay_trades(universe=universe, start_month=test_month, end_month=test_month, as_ticks=False)
    ticks = _df_to_ticks(ticks_df) if not ticks_df.is_empty() else []

    t0 = time.time()
    runner.run(ticks)
    elapsed = time.time() - t0

    _run_coro(ledger.flush())
    records = _run_coro(ledger.read_all())

    if not records:
        log(f"    fold {test_start}: 0 signals fired ({elapsed:.1f}s)")
        return {
            "fold": test_start, "train_base_rate": train_base, "test_base_rate": test_base,
            "n_qualified_traders": len(qualified), "n_signals": 0,
            "hit_rate": None, "excess_hr_pp": None, "total_pnl_net": 0.0,
        }

    settled = [r for r in records if r.resolution is not None]
    n_signals = len(records)
    n_settled = len(settled)
    n_wins = sum(1 for r in settled if r.pnl_net is not None and r.pnl_net > 0)
    hr = n_wins / n_settled if n_settled > 0 else None
    excess_hr = (hr - test_base) * 100 if hr is not None else None
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
        "train_base_rate": train_base,
        "test_base_rate": test_base,
        "n_train_markets": n_train,
        "n_test_markets": n_test,
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
    tag: str, label: str, top_k: int, consensus_n: int, price_ceil: float,
    resolutions: dict, token_map: dict,
) -> dict:
    log(f"\n--- {tag} {label}: top-K={top_k} N={consensus_n} pc={price_ceil} ---")

    d = db()
    setup_tag_mkts(d, tag)

    fold_results = []
    for ts, te, xs, xe, xm in FOLDS:
        fold_res = run_fold(
            d, tag, label, ts, te, xs, xe, xm,
            top_k, consensus_n, price_ceil,
            resolutions, token_map,
        )
        if fold_res:
            fold_results.append(fold_res)

    valid_folds = [f for f in fold_results if f.get("hit_rate") is not None]
    if valid_folds:
        n_folds = len(valid_folds)
        agg_hr = sum(f["hit_rate"] for f in valid_folds) / n_folds
        agg_test_base = sum(f["test_base_rate"] for f in valid_folds) / n_folds
        agg_excess = sum(f["excess_hr_pp"] for f in valid_folds) / n_folds
        total_signals = sum(f["n_signals"] for f in fold_results)
        total_pnl = sum(f["total_pnl_net"] for f in fold_results)
        total_settled = sum(f.get("n_settled", 0) for f in fold_results)
        aggregate = {
            "n_folds": n_folds,
            "avg_hit_rate": round(agg_hr, 4),
            "avg_test_base_rate": round(agg_test_base, 4),
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
            "top_k": top_k,
            "consensus_n": consensus_n,
            "price_ceil": price_ceil,
            "fee_pct": 0.0,
            "pool_method": "top-K by excess_hr (INNER JOIN yes_entry_data, mpe<=0.80, hr<0.99, excess>0)",
        },
        "folds": fold_results,
        "aggregate": aggregate,
    }


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=== Task #13: Sharp pool (top-K) tick validation — price_ceil=0.40, fee_pct=0.0 ===")
    t0 = time.time()

    log("Loading resolutions...")
    resolutions, token_map = load_replay_resolutions()
    log(f"Loaded {len(resolutions):,} resolutions, {len(token_map):,} token_map entries")

    results = {
        "hypothesis": "tag-hr-consensus",
        "phase": "validation-r4-sharppool",
        "timestamp": datetime.now().isoformat(),
        "note": (
            "Task #13. Tick-by-tick. BUY-only. Top-K pool (ranked by excess_hr DESC). "
            "price_ceil=0.40. SimulatedExecutor fee_pct=0.0 (zero fees confirmed). "
            "Training-window base rate for ranking. INNER JOIN yes_entry_data."
        ),
        "combos": [],
    }

    for tag, label, k, n, pc in COMBOS:
        combo_result = run_combo(tag, label, k, n, pc, resolutions, token_map)
        if combo_result:
            results["combos"].append(combo_result)

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")

    out_path = OUTPUT_DIR / "results_r4_sharppool.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"Written: {out_path}")


if __name__ == "__main__":
    main()
