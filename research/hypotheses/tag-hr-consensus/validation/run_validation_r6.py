"""Tick-by-tick validation R6: sharp pool + sustainability tiers + vol gate.

Stacked fixes:
  1. Sharp pool K=50 (top traders by excess_hr)
  2. Sustainability tiers: split training into 3x2-month sub-windows, grade by consistency
  3. Signal-time volume >= $200 (causal)
  4. price_ceil=0.75
  5. fee_pct=0.0 (confirmed zero fees for Esports/Tennis)

Tier-weighted sizing:
  - Esports: hard gate avg_tier <= 2.5 + tier-weighted size
  - Tennis: no tier gate (inverted signal), tier-weighted sizing only

Tier weights: {1: 2.0, 2: 1.5, 3: 1.0}
Position size = base_size * mean(tier_weight[t] for t in consensus_traders)

Usage:
    PYTHONPATH=. uv run python research/hypotheses/tag-hr-consensus/validation/run_validation_r6.py
"""

from __future__ import annotations

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
from polymarket_pipeline.strategies.types import ExecutionMode, TradeIntent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FOLDS = [
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-08-01", 202507),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2025-11-01", 202510),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-02-01", 202601),
]

SHARP_K = 50
PRICE_CEIL = 0.75
MIN_SIGNAL_VOL = 200.0
FEE_PCT = 0.0
BASE_SIZE = 100.0

TIER_WEIGHTS = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.5, 5: 0.25}

# Per-tag config: (consensus_n, use_tier_gate, max_avg_tier)
TAG_CONFIG = {
    "Esports": {"consensus_n": 3, "tier_gate": False, "max_avg_tier": 99.0},
    "Tennis":  {"consensus_n": 3, "tier_gate": False, "max_avg_tier": 99.0},
}

MIN_TRADES = 5
MIN_TRADES_PER_WINDOW = 2
BOT_GUARD = 10_000
MPE_THRESH = 0.80

OUTPUT_DIR = Path("research/hypotheses/tag-hr-consensus/validation")
LOG_PATH = Path("tmp/consensus_validate_r6.log")


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def _epoch(date_str: str) -> float:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def months_offset(date_str: str, months: int) -> str:
    year, month, day = map(int, date_str.split("-"))
    month += months
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def setup_tag_mkts(d, tag: str) -> None:
    d.execute("DROP TABLE IF EXISTS _r6_tag_mkts")
    d.execute("""
        CREATE TEMP TABLE _r6_tag_mkts AS
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
            WHERE condition_id IN (SELECT condition_id FROM _r6_tag_mkts)
              AND CAST(resolved_at AS DATE) >= '{start}'
              AND CAST(resolved_at AS DATE) < '{end}'
            GROUP BY condition_id
        )
    """)
    yw, tot = r["yw"], r["tot"]
    return (round(yw / tot, 4) if tot > 0 else 0.5), tot


def build_tiered_pool(d, train_start: str, train_end: str, train_base: float) -> dict[str, int]:
    """Build sharp pool K=50 with sustainability tiers.

    Returns: {trader_address: tier} for top-K traders.
    """
    # Entry price filter
    d.execute("DROP TABLE IF EXISTS _r6_ep")
    d.execute(f"""
        CREATE TEMP TABLE _r6_ep AS
        SELECT y.trader, sum(y.price_x_vol) / sum(y.volume) AS avg_ep
        FROM yes_entry_data y
        WHERE y.condition_id IN (SELECT condition_id FROM _r6_tag_mkts)
          AND CAST(y.first_trade AS DATE) >= '{train_start}'
          AND CAST(y.first_trade AS DATE) < '{train_end}'
        GROUP BY y.trader
    """)

    # Top-K by aggregate excess_hr
    agg_pool = d.fetchall(f"""
        WITH ranked AS (
            SELECT
                p.trader,
                count() AS n_total,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() AS raw_hr,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base} AS excess_hr,
                ROW_NUMBER() OVER (
                    ORDER BY (sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {train_base}) DESC,
                             count() DESC
                ) AS rank_hr
            FROM maker_positions p
            INNER JOIN _r6_ep ep ON p.trader = ep.trader
            WHERE p.condition_id IN (SELECT condition_id FROM _r6_tag_mkts)
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '{train_start}'
              AND CAST(p.resolved_at AS DATE) < '{train_end}'
            GROUP BY p.trader
            HAVING n_total >= {MIN_TRADES}
              AND n_total < {BOT_GUARD}
              AND raw_hr < 0.99
              AND excess_hr > 0
              AND first(ep.avg_ep) <= {MPE_THRESH}
        )
        SELECT trader, excess_hr FROM ranked WHERE rank_hr <= {SHARP_K}
    """)

    if not agg_pool:
        return {}

    trader_set = {r["trader"] for r in agg_pool}
    traders_joined = ", ".join(f"'{t}'" for t in trader_set)

    # Sub-windows
    sw_boundaries = [
        (train_start, months_offset(train_start, 2)),
        (months_offset(train_start, 2), months_offset(train_start, 4)),
        (months_offset(train_start, 4), train_end),
    ]

    # Per-trader, per-sub-window excess_hr
    sw_positive = {t: 0 for t in trader_set}
    sw_measured = {t: 0 for t in trader_set}

    for sw_start, sw_end in sw_boundaries:
        sw_base, _ = get_base_rate(d, sw_start, sw_end)
        rows = d.fetchall(f"""
            SELECT
                p.trader,
                count() AS n_sw,
                sum(CASE WHEN p.correct = 1 THEN 1 ELSE 0 END)::DOUBLE / count() - {sw_base} AS sw_excess
            FROM maker_positions p
            WHERE p.trader IN ({traders_joined})
              AND p.condition_id IN (SELECT condition_id FROM _r6_tag_mkts)
              AND p.position = 'YES'
              AND CAST(p.resolved_at AS DATE) >= '{sw_start}'
              AND CAST(p.resolved_at AS DATE) < '{sw_end}'
            GROUP BY p.trader
            HAVING n_sw >= {MIN_TRADES_PER_WINDOW}
        """)
        for r in rows:
            t = r["trader"]
            if t in sw_positive:
                sw_measured[t] += 1
                if r["sw_excess"] > 0:
                    sw_positive[t] += 1

    # Assign tiers
    trader_tiers = {}
    for t in trader_set:
        n_pos = sw_positive[t]
        n_meas = sw_measured[t]
        if n_meas == 0:
            tier = 5
        elif n_pos >= 3:
            tier = 1
        elif n_pos == 2:
            tier = 2
        elif n_pos == 1:
            tier = 3
        else:
            tier = 4
        trader_tiers[t] = tier

    return trader_tiers


def get_universe(d, test_start: str, test_end: str) -> set[str]:
    result = d.fetchall(f"""
        SELECT DISTINCT condition_id
        FROM maker_positions
        WHERE condition_id IN (SELECT condition_id FROM _r6_tag_mkts)
          AND CAST(resolved_at AS DATE) >= '{test_start}'
          AND CAST(resolved_at AS DATE) < '{test_end}'
    """)
    return {r["condition_id"] for r in result}


def get_asset_ids(d, universe: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    if not universe:
        return {}, {}
    placeholders = ", ".join(f"'{cid}'" for cid in universe)
    result = d.fetchall(f"""
        SELECT condition_id, asset_id, outcome
        FROM token_market_map
        WHERE condition_id IN ({placeholders})
    """)
    yes_map, no_map = {}, {}
    for r in result:
        if r["outcome"] == "YES":
            yes_map[r["condition_id"]] = r["asset_id"]
        elif r["outcome"] == "NO":
            no_map[r["condition_id"]] = r["asset_id"]
    return yes_map, no_map


# ---------------------------------------------------------------------------
# Strategy: consensus with tier-weighted sizing
# ---------------------------------------------------------------------------

class ConsensusTieredStrategy:
    name: str = "consensus_r6"

    def __init__(
        self,
        trader_tiers: dict[str, int],
        yes_asset_ids: dict[str, str],
        no_asset_ids: dict[str, str],
        consensus_n: int,
        price_ceil: float,
        min_signal_vol: float,
        tier_gate: bool,
        max_avg_tier: float,
        base_size: float,
        test_start_epoch: float,
    ) -> None:
        self._tiers = trader_tiers
        self._qualified = set(trader_tiers.keys())
        self._yes_ids = yes_asset_ids
        self._no_ids = no_asset_ids
        self._n = consensus_n
        self._price_ceil = price_ceil
        self._min_vol = min_signal_vol
        self._tier_gate = tier_gate
        self._max_avg_tier = max_avg_tier
        self._base_size = base_size
        self._test_start = test_start_epoch

        self._yes_traders: dict[str, set[str]] = {}
        self._no_traders: dict[str, set[str]] = {}
        self._yes_vol: dict[str, dict[str, float]] = {}
        self._fired: set[str] = set()

        # Stats
        self.skip_vol = 0
        self.skip_tier = 0
        self.skip_price = 0
        self.total_fired = 0
        self.sizes_used: list[float] = []
        self.avg_tiers_used: list[float] = []

    def reset(self) -> None:
        self._yes_traders.clear()
        self._no_traders.clear()
        self._yes_vol.clear()
        self._fired.clear()
        self.skip_vol = 0
        self.skip_tier = 0
        self.skip_price = 0
        self.total_fired = 0
        self.sizes_used.clear()
        self.avg_tiers_used.clear()

    async def on_trade(self, trade, ctx) -> list[TradeIntent] | None:
        if trade.side != "BUY":
            return None

        cid = trade.condition_id
        maker = trade.maker
        ts = trade.published_at
        asset_id = trade.asset_id

        if ts < self._test_start:
            return None
        if not maker or maker not in self._qualified:
            return None
        if cid in self._fired:
            return None

        expected_yes = self._yes_ids.get(cid)
        expected_no = self._no_ids.get(cid)

        is_yes = expected_yes and asset_id == expected_yes
        is_no = expected_no and asset_id == expected_no

        if not is_yes and not is_no:
            return None

        # Track NO for dissent-aware analysis
        if is_no:
            if cid not in self._no_traders:
                self._no_traders[cid] = set()
            self._no_traders[cid].add(maker)
            return None

        # YES trade
        if cid not in self._yes_traders:
            self._yes_traders[cid] = set()
            self._yes_vol[cid] = {}

        traders = self._yes_traders[cid]
        vol_map = self._yes_vol[cid]

        trade_usd = getattr(trade, "amount_usd", 0.0) or (trade.price * getattr(trade, "size", 0.0))
        vol_map[maker] = vol_map.get(maker, 0.0) + trade_usd

        if maker not in traders:
            traders.add(maker)

        if len(traders) < self._n:
            return None

        # Gate 1: signal-time volume
        signal_vol = sum(vol_map.values())
        if signal_vol < self._min_vol:
            self.skip_vol += 1
            return None

        # Gate 2: tier composition
        consensus_tiers = [self._tiers.get(t, 5) for t in traders]
        avg_tier = sum(consensus_tiers) / len(consensus_tiers)

        if self._tier_gate and avg_tier > self._max_avg_tier:
            self.skip_tier += 1
            return None

        # Gate 3: price ceiling
        signal_price = trade.price
        if signal_price > self._price_ceil:
            self.skip_price += 1
            return None

        # Compute tier-weighted size
        tier_weights = [TIER_WEIGHTS.get(t, 0.25) for t in consensus_tiers]
        quality = sum(tier_weights) / len(tier_weights)
        size = self._base_size * quality

        self._fired.add(cid)
        self.total_fired += 1
        self.sizes_used.append(size)
        self.avg_tiers_used.append(avg_tier)

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome="YES",
                size_usd=size,
                urgency="patient",
                max_price=min(signal_price + 0.02, self._price_ceil),
                reason=f"n={len(traders)} avg_tier={avg_tier:.1f} quality={quality:.2f} vol=${signal_vol:.0f}",
                signal_time=ts,
                asset_id=expected_yes or "",
            )
        ]

    async def on_market_update(self, update, ctx) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, now, ctx) -> list[TradeIntent] | None:
        return None


# ---------------------------------------------------------------------------
# Fold runner
# ---------------------------------------------------------------------------

def run_fold(
    d, tag: str,
    train_start: str, train_end: str,
    test_start: str, test_end: str, test_month: int,
    tag_cfg: dict,
    resolutions: dict, token_map: dict,
) -> dict | None:
    train_base, n_train = get_base_rate(d, train_start, train_end)
    test_base, n_test = get_base_rate(d, test_start, test_end)

    if n_test < 5:
        log(f"    fold {test_start}: SKIP — n_test={n_test}")
        return None

    # Build tiered pool
    trader_tiers = build_tiered_pool(d, train_start, train_end, train_base)
    universe = get_universe(d, test_start, test_end)
    yes_ids, no_ids = get_asset_ids(d, universe)

    tier_dist = {}
    for t in trader_tiers.values():
        tier_dist[t] = tier_dist.get(t, 0) + 1

    log(
        f"    fold {test_start}: train_base={train_base:.3f} test_base={test_base:.3f} "
        f"pool={len(trader_tiers)} tiers={dict(sorted(tier_dist.items()))} "
        f"universe={len(universe)}"
    )

    consensus_n = tag_cfg["consensus_n"]
    if len(trader_tiers) < consensus_n:
        log(f"    fold {test_start}: SKIP — pool {len(trader_tiers)} < N={consensus_n}")
        return {"fold": test_start, "skip_reason": "pool_too_small", "n_signals": 0, "total_pnl_net": 0.0}

    # Load ticks
    ticks_df = load_replay_trades(universe=universe, start_month=test_month, end_month=test_month, as_ticks=False)
    ticks = _df_to_ticks(ticks_df) if not ticks_df.is_empty() else []

    strategy = ConsensusTieredStrategy(
        trader_tiers=trader_tiers,
        yes_asset_ids=yes_ids,
        no_asset_ids=no_ids,
        consensus_n=consensus_n,
        price_ceil=PRICE_CEIL,
        min_signal_vol=MIN_SIGNAL_VOL,
        tier_gate=tag_cfg["tier_gate"],
        max_avg_tier=tag_cfg["max_avg_tier"],
        base_size=BASE_SIZE,
        test_start_epoch=_epoch(test_start),
    )

    config = StrategyConfig(
        name="consensus_r6", enabled=True, mode=ExecutionMode.REPLAY,
        capital_usd=100_000, max_position_usd=500.0,
        max_open_positions=500, cooldown_s=0,
    )

    executor = SimulatedExecutor(fee_pct=FEE_PCT)
    gateway = ExecutionGateway(executor, strategy_budgets={"consensus_r6": 100_000})
    ctx = InMemoryContext()

    combo_key = f"{tag}_N{consensus_n}_{test_start[:7]}"
    ledger_path = OUTPUT_DIR / f"ledger_r6_{combo_key}.parquet"
    ledger = ParquetLedger(ledger_path)

    fold_resolutions = {k: v for k, v in resolutions.items() if k in universe}

    runner = SyncReplayRunner(
        strategy=strategy, ctx=ctx, gateway=gateway, config=config,
        resolutions=fold_resolutions, token_map=token_map, ledger=ledger,
    )

    t0 = time.time()
    runner.run(ticks)
    elapsed = time.time() - t0

    _run_coro(ledger.flush())
    records = _run_coro(ledger.read_all())

    log(
        f"    gates: skip_vol={strategy.skip_vol} skip_tier={strategy.skip_tier} "
        f"skip_price={strategy.skip_price} fired={strategy.total_fired}"
    )

    if not records:
        log(f"    fold {test_start}: 0 signals ({elapsed:.1f}s)")
        return {
            "fold": test_start, "train_base_rate": train_base, "test_base_rate": test_base,
            "n_train": n_train, "n_test": n_test,
            "pool_size": len(trader_tiers), "tier_dist": {str(k): v for k, v in sorted(tier_dist.items())},
            "n_signals": 0, "hit_rate": None, "excess_hr_pp": None, "total_pnl_net": 0.0,
            "gates": {"skip_vol": strategy.skip_vol, "skip_tier": strategy.skip_tier, "skip_price": strategy.skip_price},
        }

    settled = [r for r in records if r.resolution is not None]
    n_sig = len(records)
    n_set = len(settled)
    n_win = sum(1 for r in settled if r.pnl_net is not None and r.pnl_net > 0)
    hr = n_win / n_set if n_set > 0 else None
    excess = (hr - test_base) * 100 if hr is not None else None
    total_pnl = sum(r.pnl_net for r in settled if r.pnl_net is not None)
    avg_fill = sum(r.fill_price for r in records if r.fill_price) / n_sig if records else None
    avg_size = sum(strategy.sizes_used) / len(strategy.sizes_used) if strategy.sizes_used else BASE_SIZE
    avg_tier_used = sum(strategy.avg_tiers_used) / len(strategy.avg_tiers_used) if strategy.avg_tiers_used else 0

    summary = compute_summary(records)

    log(
        f"    fold {test_start}: sigs={n_sig} HR={hr:.1%} excess={excess:+.1f}pp "
        f"pnl=${total_pnl:.2f} avg_fill={avg_fill:.3f} avg_size=${avg_size:.0f} "
        f"avg_tier={avg_tier_used:.1f} ({elapsed:.1f}s)"
        if hr is not None else
        f"    fold {test_start}: sigs={n_sig} no settled ({elapsed:.1f}s)"
    )

    return {
        "fold": test_start, "train_base_rate": train_base, "test_base_rate": test_base,
        "n_train": n_train, "n_test": n_test,
        "pool_size": len(trader_tiers), "tier_dist": {str(k): v for k, v in sorted(tier_dist.items())},
        "n_signals": n_sig, "n_settled": n_set, "n_wins": n_win,
        "hit_rate": round(hr, 4) if hr is not None else None,
        "excess_hr_pp": round(excess, 2) if excess is not None else None,
        "total_pnl_net": round(total_pnl, 2),
        "avg_fill_price": round(avg_fill, 4) if avg_fill else None,
        "avg_position_size": round(avg_size, 2),
        "avg_tier_in_consensus": round(avg_tier_used, 2),
        "avg_edge_usd": round(summary.avg_edge, 4) if summary else None,
        "sharpe": round(summary.sharpe, 3) if summary else None,
        "avg_hold_hours": round(summary.avg_hold_duration_s / 3600, 2) if summary else None,
        "elapsed_s": round(elapsed, 1),
        "gates": {"skip_vol": strategy.skip_vol, "skip_tier": strategy.skip_tier, "skip_price": strategy.skip_price},
    }


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=== R6b: sharp pool K=50 + tier-weighted sizing (NO tier gate) ===")

    t0 = time.time()
    log("Loading resolutions...")
    resolutions, token_map = load_replay_resolutions()
    log(f"Loaded {len(resolutions):,} resolutions, {len(token_map):,} token_map entries")

    results = {
        "hypothesis": "tag-hr-consensus",
        "phase": "validation-r6b",
        "timestamp": datetime.now().isoformat(),
        "config": {
            "sharp_k": SHARP_K, "price_ceil": PRICE_CEIL, "min_signal_vol": MIN_SIGNAL_VOL,
            "fee_pct": FEE_PCT, "base_size": BASE_SIZE, "tier_weights": TIER_WEIGHTS,
        },
        "tag_configs": TAG_CONFIG,
        "combos": [],
    }

    for tag, tag_cfg in TAG_CONFIG.items():
        log(f"\n{'='*60}")
        log(f"TAG: {tag} — N={tag_cfg['consensus_n']} tier_gate={tag_cfg['tier_gate']} max_avg_tier={tag_cfg['max_avg_tier']}")

        d = db()
        setup_tag_mkts(d, tag)

        fold_results = []
        for ts, te, xs, xe, xm in FOLDS:
            fold_res = run_fold(d, tag, ts, te, xs, xe, xm, tag_cfg, resolutions, token_map)
            if fold_res:
                fold_results.append(fold_res)

        valid = [f for f in fold_results if f.get("hit_rate") is not None]
        if valid:
            agg = {
                "n_folds": len(valid),
                "avg_hr": round(sum(f["hit_rate"] for f in valid) / len(valid), 4),
                "avg_test_base": round(sum(f["test_base_rate"] for f in valid) / len(valid), 4),
                "avg_excess_pp": round(sum(f["excess_hr_pp"] for f in valid) / len(valid), 2),
                "total_signals": sum(f["n_signals"] for f in fold_results),
                "total_pnl": round(sum(f["total_pnl_net"] for f in fold_results), 2),
                "avg_position_size": round(sum(f.get("avg_position_size", BASE_SIZE) for f in valid) / len(valid), 2),
                "avg_tier_in_consensus": round(sum(f.get("avg_tier_in_consensus", 0) for f in valid) / len(valid), 2),
            }
            log(f"  AGGREGATE ({tag}): HR={agg['avg_hr']:.1%} excess={agg['avg_excess_pp']:+.1f}pp "
                f"sigs={agg['total_signals']} pnl=${agg['total_pnl']:.2f} avg_size=${agg['avg_position_size']:.0f}")
        else:
            agg = None
            log(f"  AGGREGATE ({tag}): no valid folds")

        results["combos"].append({"tag": tag, "config": tag_cfg, "folds": fold_results, "aggregate": agg})

    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.0f}s")

    out_path = OUTPUT_DIR / "results_r6b.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"Written: {out_path}")


if __name__ == "__main__":
    main()
