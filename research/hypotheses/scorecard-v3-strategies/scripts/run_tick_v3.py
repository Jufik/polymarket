"""Tick-by-tick validation for scorecard-v3 strategies.

Three legs validated via SyncReplayRunner:
  1. Sports YES K=25 N=2 (v3 BEH-gated) — vs v2 baseline +39.8pp excess
  2. Politics NO K=100 N=2 (first NO-direction tick validation)
  3. Sports In-Play YES K=25 N=1 (dedicated RT track, hold<4h expected)

Test period: 2025-07-01 – 2026-03-01 (8 months, same as vectorized sweep).
Train cutoff: 2025-07-01 (all pool training uses data before this date).

ALL RESULTS ARE TICK-VALIDATED (not upper bounds).
Per live_infrastructure.md: do NOT apply in-play latency penalties.
Sub-second WS delivery means simulation fill model is the only degradation source.

Usage:
    PYTHONPATH=/mnt/nvme/git/polymarket/polymarket uv run python \
        research/hypotheses/scorecard-v3-strategies/scripts/run_tick_v3.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/nvme/git/polymarket/polymarket")

import polars as pl

from polymarket_pipeline.strategies.config import StrategyConfig
from polymarket_pipeline.strategies.types import ExecutionMode

from research.harness import run_fast_backtest, print_summary
from research.fast_replay import load_replay_resolutions
from research.strategies.consensus_v2 import TokenMapStrategy

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/tick_v3.log")
OUTPUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
VALIDATION_DIR = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/validation"
)

TEST_START = "2025-07-01"
TEST_END = "2026-03-01"


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def build_token_map_from_db(con) -> dict[str, dict[str, str]]:
    """Build token_map: {condition_id: {'YES': asset_id, 'NO': asset_id}}."""
    rows = con.execute(
        "SELECT condition_id, asset_id, outcome FROM token_market_map"
    ).fetchall()
    token_map: dict[str, dict[str, str]] = {}
    for cid, aid, outcome in rows:
        token_map.setdefault(cid, {})[outcome] = aid
    return token_map


def compute_base_rate(con, tag: str, direction: str, test_start: str, test_end: str) -> float:
    """Compute test-period base rate for a tag+direction."""
    if direction == "YES":
        pos_col = "position = 'YES'"
        correct_expr = "avg(CAST(p.yes_won AS DOUBLE))"
    else:
        pos_col = "position = 'NO'"
        correct_expr = "avg(p.correct::DOUBLE)"

    row = con.execute(f"""
        SELECT {correct_expr} AS base_rate
        FROM maker_positions p
        JOIN _v3_market_tags mt ON p.condition_id = mt.condition_id
        WHERE mt.primary_tag = '{tag}'
          AND p.{pos_col}
          AND CAST(p.resolved_at AS DATE) >= '{test_start}'
          AND CAST(p.resolved_at AS DATE) < '{test_end}'
          AND p.volume > 0
    """).fetchone()
    return round(row[0], 4) if row and row[0] is not None else 0.0


def analyze_ledger(ledger_path: Path, base_rate: float, label: str) -> dict:
    """Analyze ledger parquet — hold time distribution, HR by bucket, monthly breakdown."""
    if not ledger_path.exists():
        log(f"  [WARN] Ledger not found: {ledger_path}")
        return {}

    df = pl.read_parquet(ledger_path)
    log(f"  Ledger rows: {len(df)}, columns: {df.columns}")

    settled = df.filter(pl.col("resolution").is_not_null())
    log(f"  Settled: {len(settled)}")

    if len(settled) == 0:
        return {"settled": 0}

    # Hold time in hours
    hold_h = settled.with_columns(
        (pl.col("hold_duration_s") / 3600).alias("hold_h")
    )

    pct_under_1h  = hold_h.filter(pl.col("hold_h") < 1).height / len(hold_h)
    pct_under_4h  = hold_h.filter(pl.col("hold_h") < 4).height / len(hold_h)
    pct_under_24h = hold_h.filter(pl.col("hold_h") < 24).height / len(hold_h)
    median_hold_h = hold_h["hold_h"].median()
    log(f"  Hold: median={median_hold_h:.1f}h | <1h={pct_under_1h:.1%} | <4h={pct_under_4h:.1%} | <24h={pct_under_24h:.1%}")

    # HR by hold bucket
    hb = hold_h.with_columns(
        pl.when(pl.col("hold_h") < 1).then(pl.lit("1_lt1h"))
          .when(pl.col("hold_h") < 4).then(pl.lit("2_1-4h"))
          .when(pl.col("hold_h") < 24).then(pl.lit("3_4-24h"))
          .when(pl.col("hold_h") < 72).then(pl.lit("4_24-72h"))
          .otherwise(pl.lit("5_gt72h"))
          .alias("hold_bucket")
    )
    hr_by_bucket = (
        hb.group_by("hold_bucket")
          .agg([
              pl.len().alias("n"),
              (pl.col("resolution") == "WON").mean().alias("hr"),
          ])
          .sort("hold_bucket")
    )
    log(f"  HR by hold bucket:\n{hr_by_bucket}")

    # Monthly fill count + HR
    monthly_stats = None
    if "signal_time" in settled.columns:
        monthly = settled.with_columns(
            (pl.col("signal_time") * 1e6).cast(pl.Datetime("us"))
              .dt.truncate("1mo").alias("month")
        )
        monthly_stats = (
            monthly.group_by("month")
              .agg([
                  pl.len().alias("n_fills"),
                  (pl.col("resolution") == "WON").mean().alias("hr"),
                  pl.col("pnl_net").sum().alias("pnl_net"),
              ])
              .sort("month")
        )
        log(f"  Monthly breakdown:\n{monthly_stats}")

    # PnL
    if "pnl_net" in settled.columns:
        total_pnl = settled["pnl_net"].sum()
        median_pnl = settled["pnl_net"].median()
        log(f"  PnL: total=${total_pnl:,.2f}, median per fill=${median_pnl:,.3f}")

    hr_overall = (settled["resolution"] == "WON").mean()
    excess_hr = hr_overall - base_rate
    log(f"  HR: {hr_overall:.1%}, base_rate: {base_rate:.1%}, excess: {excess_hr:+.1%}")

    return {
        "settled": len(settled),
        "hr": round(hr_overall, 4),
        "excess_hr": round(excess_hr, 4),
        "base_rate": base_rate,
        "pct_under_1h": round(pct_under_1h, 3),
        "pct_under_4h": round(pct_under_4h, 3),
        "pct_under_24h": round(pct_under_24h, 3),
        "median_hold_h": round(median_hold_h, 1) if median_hold_h is not None else None,
        "total_pnl": round(float(settled["pnl_net"].sum()), 2) if "pnl_net" in settled.columns else None,
        "monthly_stats": monthly_stats.to_dicts() if monthly_stats is not None else None,
    }


def make_config(name: str, capital: float = 5000.0) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=capital,
        max_position_usd=100.0,
        max_open_positions=50,
        cooldown_s=0,
    )


def main() -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "w") as f:
        f.write("")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("Scorecard V3 — Tick-by-Tick Validation (3 legs)")
    log(f"Test period: {TEST_START} to {TEST_END}")
    log("=" * 70)

    # ── Load token_map and DuckDB connection ──────────────────────────────────
    log("\nLoading token_map from replay resolutions...")
    t0 = time.time()
    _, token_map_replay = load_replay_resolutions()
    log(f"  token_map (from replay): {len(token_map_replay)} markets ({time.time()-t0:.1f}s)")

    # Also get DuckDB connection (shared across all pool builders)
    from research.db import db as get_db
    con = get_db().con

    # Build token_map from DuckDB for completeness (replay may not have all)
    log("  Supplementing token_map from DuckDB token_market_map...")
    token_map_db = build_token_map_from_db(con)
    # Merge: replay takes priority (has verified winners)
    token_map = {**token_map_db, **token_map_replay}
    log(f"  Combined token_map: {len(token_map)} markets")

    # ── Import pool builders ──────────────────────────────────────────────────
    import importlib.util as _ilu
    _bp_spec = _ilu.spec_from_file_location(
        "build_pools_v3",
        Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py"),
    )
    _bp_mod = _ilu.module_from_spec(_bp_spec)
    _bp_spec.loader.exec_module(_bp_mod)

    all_results = {}

    # ═══════════════════════════════════════════════════════════════════════
    # LEG 1: Sports YES K=25 N=2 (v3 BEH-gated)
    # Vectorized UB: 86.7% HR, +53.4pp excess
    # v2 baseline: +39.8pp tick excess, 612 fills, Sharpe=11.94
    # ═══════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 70)
    log("LEG 1: Sports YES K=25 N=2 (v3 BEH-gated)")
    log("=" * 70)

    t0 = time.time()
    pool_sports, markets_sports, gambling = _bp_mod.build_sports_yes_pool_v3(k=25)
    log(f"  Pool: {len(pool_sports)} traders, {len(markets_sports)} tag markets ({time.time()-t0:.1f}s)")

    universe_sports = markets_sports - gambling
    log(f"  Universe (excl gambling): {len(universe_sports)} markets")

    strategy_sports = TokenMapStrategy(
        name="sports_yes_v3_k25_n2",
        pool=pool_sports,
        tag_markets=markets_sports,
        gambling_markets=gambling,
        n_threshold=2,
        token_map=token_map,
        direction_filter="YES",
        size_usd=100.0,
    )

    config_sports = make_config("sports_yes_v3_k25_n2")

    log("  Running tick replay...")
    t0 = time.time()
    result_sports, summary_sports = run_fast_backtest(
        strategy_sports, config_sports,
        universe=universe_sports,
        output_dir=OUTPUT_DIR,
    )
    elapsed_sports = time.time() - t0
    log(f"  Elapsed: {elapsed_sports:.1f}s | trades seen: {result_sports.total_trades:,} | "
        f"fills: {result_sports.total_fills} | rejected: {len(result_sports.rejected_intents)}")

    if summary_sports:
        print_summary(summary_sports, "Sports_YES_v3_K25_N2")

    # Compute base rate for Sports YES in test period
    _bp_mod._ensure_shared_tables(con)
    sports_base_rate = compute_base_rate(con, "Sports", "YES", TEST_START, TEST_END)
    log(f"  Sports YES base rate (test period): {sports_base_rate:.1%}")

    leg1_analysis = analyze_ledger(
        OUTPUT_DIR / "ledger_sports_yes_v3_k25_n2.parquet",
        sports_base_rate,
        "Sports YES v3 K=25 N=2",
    )
    leg1_analysis.update({
        "name": "sports_yes_v3_k25_n2",
        "vectorized_ub_hr": 0.867,
        "vectorized_ub_excess": 0.534,
        "v2_tick_excess": 0.398,
        "total_fills": result_sports.total_fills,
        "pool_size": len(pool_sports),
        "n_threshold": 2,
        "direction": "YES",
        "tag": "Sports",
    })
    if summary_sports:
        leg1_analysis.update({
            "tick_hr": round(summary_sports.hit_rate, 4),
            "tick_pnl_net": round(summary_sports.total_pnl_net, 2),
            "tick_sharpe": round(summary_sports.sharpe, 4),
            "tick_max_drawdown": round(summary_sports.max_drawdown, 2),
            "tick_profit_factor": round(summary_sports.profit_factor, 4),
            "tick_avg_hold_h": round(summary_sports.avg_hold_duration_s / 3600, 2),
            "tick_excess_hr": round(summary_sports.hit_rate - sports_base_rate, 4),
        })
    all_results["leg1_sports_yes"] = leg1_analysis

    # ═══════════════════════════════════════════════════════════════════════
    # LEG 2: Politics NO K=100 N=2 (FIRST NO-direction tick validation)
    # Vectorized UB: 81.8% HR, +8.8pp excess, 66 signals/8mo
    # NOTE: Politics NO base rate is ~73-77%. Small excess expected.
    # ═══════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 70)
    log("LEG 2: Politics NO K=100 N=2 (first NO-direction tick)")
    log("=" * 70)

    t0 = time.time()
    pool_pol_no, markets_pol, gambling2 = _bp_mod.build_politics_no_pool(k=100)
    log(f"  Pool: {len(pool_pol_no)} traders, {len(markets_pol)} tag markets ({time.time()-t0:.1f}s)")

    universe_pol = markets_pol - gambling2

    strategy_pol_no = TokenMapStrategy(
        name="politics_no_v3_k100_n2",
        pool=pool_pol_no,
        tag_markets=markets_pol,
        gambling_markets=gambling2,
        n_threshold=2,
        token_map=token_map,
        direction_filter="NO",
        size_usd=100.0,
    )

    config_pol_no = make_config("politics_no_v3_k100_n2")

    log("  Running tick replay...")
    t0 = time.time()
    result_pol_no, summary_pol_no = run_fast_backtest(
        strategy_pol_no, config_pol_no,
        universe=universe_pol,
        output_dir=OUTPUT_DIR,
    )
    elapsed_pol = time.time() - t0
    log(f"  Elapsed: {elapsed_pol:.1f}s | trades seen: {result_pol_no.total_trades:,} | "
        f"fills: {result_pol_no.total_fills} | rejected: {len(result_pol_no.rejected_intents)}")

    if summary_pol_no:
        print_summary(summary_pol_no, "Politics_NO_v3_K100_N2")

    pol_no_base_rate = compute_base_rate(con, "Politics", "NO", TEST_START, TEST_END)
    log(f"  Politics NO base rate (test period): {pol_no_base_rate:.1%}")

    leg2_analysis = analyze_ledger(
        OUTPUT_DIR / "ledger_politics_no_v3_k100_n2.parquet",
        pol_no_base_rate,
        "Politics NO v3 K=100 N=2",
    )
    leg2_analysis.update({
        "name": "politics_no_v3_k100_n2",
        "vectorized_ub_hr": 0.818,
        "vectorized_ub_excess": 0.088,
        "v2_tick_excess": None,  # first NO-direction validation
        "total_fills": result_pol_no.total_fills,
        "pool_size": len(pool_pol_no),
        "n_threshold": 2,
        "direction": "NO",
        "tag": "Politics",
    })
    if summary_pol_no:
        leg2_analysis.update({
            "tick_hr": round(summary_pol_no.hit_rate, 4),
            "tick_pnl_net": round(summary_pol_no.total_pnl_net, 2),
            "tick_sharpe": round(summary_pol_no.sharpe, 4),
            "tick_max_drawdown": round(summary_pol_no.max_drawdown, 2),
            "tick_profit_factor": round(summary_pol_no.profit_factor, 4),
            "tick_avg_hold_h": round(summary_pol_no.avg_hold_duration_s / 3600, 2),
            "tick_excess_hr": round(summary_pol_no.hit_rate - pol_no_base_rate, 4),
        })
    all_results["leg2_politics_no"] = leg2_analysis

    # ═══════════════════════════════════════════════════════════════════════
    # LEG 3: Sports In-Play YES K=25 N=1 (dedicated RT track)
    # Vectorized UB: 67.5% HR, +34.2pp excess, 320 signals/8mo, hold<4h
    # Per live_infrastructure.md: no latency penalty. Fill model is the gap.
    # ═══════════════════════════════════════════════════════════════════════
    log("\n" + "=" * 70)
    log("LEG 3: Sports In-Play YES K=25 N=1 (RT track)")
    log("=" * 70)

    # Reuse Sports pool (same K=25 BEH-gated v3 pool, but N=1)
    strategy_inplay = TokenMapStrategy(
        name="sports_inplay_v3_k25_n1",
        pool=pool_sports,  # same pool as Leg 1
        tag_markets=markets_sports,
        gambling_markets=gambling,
        n_threshold=1,
        token_map=token_map,
        direction_filter="YES",
        size_usd=100.0,
    )

    config_inplay = make_config("sports_inplay_v3_k25_n1")

    log("  Running tick replay...")
    t0 = time.time()
    result_inplay, summary_inplay = run_fast_backtest(
        strategy_inplay, config_inplay,
        universe=universe_sports,
        output_dir=OUTPUT_DIR,
    )
    elapsed_inplay = time.time() - t0
    log(f"  Elapsed: {elapsed_inplay:.1f}s | trades seen: {result_inplay.total_trades:,} | "
        f"fills: {result_inplay.total_fills} | rejected: {len(result_inplay.rejected_intents)}")

    if summary_inplay:
        print_summary(summary_inplay, "Sports_InPlay_v3_K25_N1")

    leg3_analysis = analyze_ledger(
        OUTPUT_DIR / "ledger_sports_inplay_v3_k25_n1.parquet",
        sports_base_rate,
        "Sports InPlay v3 K=25 N=1",
    )
    leg3_analysis.update({
        "name": "sports_inplay_v3_k25_n1",
        "vectorized_ub_hr": 0.675,
        "vectorized_ub_excess": 0.342,
        "v2_tick_excess": None,
        "total_fills": result_inplay.total_fills,
        "pool_size": len(pool_sports),
        "n_threshold": 1,
        "direction": "YES",
        "tag": "Sports",
        "note": "N=1 fires on first elite trader entry. Fill model is degradation source. No latency penalty per live_infrastructure.md.",
    })
    if summary_inplay:
        leg3_analysis.update({
            "tick_hr": round(summary_inplay.hit_rate, 4),
            "tick_pnl_net": round(summary_inplay.total_pnl_net, 2),
            "tick_sharpe": round(summary_inplay.sharpe, 4),
            "tick_max_drawdown": round(summary_inplay.max_drawdown, 2),
            "tick_profit_factor": round(summary_inplay.profit_factor, 4),
            "tick_avg_hold_h": round(summary_inplay.avg_hold_duration_s / 3600, 2),
            "tick_excess_hr": round(summary_inplay.hit_rate - sports_base_rate, 4),
        })
    all_results["leg3_sports_inplay"] = leg3_analysis

    # ── Save JSON ─────────────────────────────────────────────────────────────
    output = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "test_start": TEST_START,
            "test_end": TEST_END,
            "note": "Tick-by-tick validated via SyncReplayRunner. NOT upper bounds.",
        },
        "results": all_results,
    }
    json_path = VALIDATION_DIR / "tick_results_v3.json"
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log(f"\nSaved JSON: {json_path}")

    # ── Write markdown report ─────────────────────────────────────────────────
    write_markdown(all_results, output["meta"])

    log("\n" + "=" * 70)
    log("VALIDATION COMPLETE")
    log("=" * 70)


def write_markdown(results: dict, meta: dict) -> None:
    r1 = results.get("leg1_sports_yes", {})
    r2 = results.get("leg2_politics_no", {})
    r3 = results.get("leg3_sports_inplay", {})

    def pct(v):
        return f"{v:.1%}" if v is not None else "N/A"

    def ppfmt(v):
        return f"{v:+.1%}" if v is not None else "N/A"

    def dollar(v):
        return f"${v:,.2f}" if v is not None else "N/A"

    def num(v, fmt=".2f"):
        return f"{v:{fmt}}" if v is not None else "N/A"

    lines = [
        "# Scorecard V3 — Tick-by-Tick Validation Results",
        "",
        "> **TICK-VALIDATED** (SyncReplayRunner, not vectorized upper bounds).",
        "> Per `knowledge/execution/live_infrastructure.md`: no additional latency penalty applied.",
        "> Sub-second WS delivery in production — simulation fill model is the only degradation source.",
        "",
        f"Generated: {meta['generated_at']}",
        f"Test period: {meta['test_start']} – {meta['test_end']} (8 months)",
        "",
        "## Summary Table",
        "",
        "| Leg | Config | Fills | Tick HR | Base Rate | **Excess HR** | PnL | Sharpe | Vectorized UB | v2 Baseline |",
        "|-----|--------|-------|---------|-----------|--------------|-----|--------|--------------|-------------|",
    ]

    def row(name, r, vub, v2base):
        fills = r.get("total_fills", 0)
        hr = pct(r.get("tick_hr"))
        base = pct(r.get("base_rate"))
        exc = ppfmt(r.get("tick_excess_hr"))
        pnl = dollar(r.get("tick_pnl_net"))
        sh = num(r.get("tick_sharpe"))
        vub_str = ppfmt(vub) if vub else "N/A"
        v2_str = ppfmt(v2base) if v2base else "first"
        return f"| {name} | K={r.get('pool_size','?')} N={r.get('n_threshold','?')} | {fills} | {hr} | {base} | **{exc}** | {pnl} | {sh} | {vub_str} | {v2_str} |"

    lines.append(row("Sports YES v3", r1, r1.get("vectorized_ub_excess"), r1.get("v2_tick_excess")))
    lines.append(row("Politics NO v3", r2, r2.get("vectorized_ub_excess"), r2.get("v2_tick_excess")))
    lines.append(row("Sports InPlay v3", r3, r3.get("vectorized_ub_excess"), r3.get("v2_tick_excess")))

    lines += [
        "",
        "---",
        "",
        "## Leg 1: Sports YES K=25 N=2 (v3 BEH-gated)",
        "",
        "**Vectorized UB**: 86.7% HR, +53.4pp excess | **v2 baseline**: +39.8pp tick, 612 fills, Sharpe=11.94",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total fills | {r1.get('total_fills', 'N/A')} |",
        f"| Hit rate | {pct(r1.get('tick_hr'))} |",
        f"| Sports YES base rate (test) | {pct(r1.get('base_rate'))} |",
        f"| **Excess HR** | **{ppfmt(r1.get('tick_excess_hr'))}** |",
        f"| Net PnL | {dollar(r1.get('tick_pnl_net'))} |",
        f"| Sharpe | {num(r1.get('tick_sharpe'))} |",
        f"| Max drawdown | {dollar(r1.get('tick_max_drawdown'))} |",
        f"| Profit factor | {num(r1.get('tick_profit_factor'))} |",
        f"| Avg hold | {num(r1.get('tick_avg_hold_h'))}h |",
        f"| % fills < 1h (in-play proxy) | {pct(r1.get('pct_under_1h'))} |",
        f"| % fills < 4h | {pct(r1.get('pct_under_4h'))} |",
        f"| Vectorized UB excess | +53.4pp |",
        f"| v2 tick excess (K=25 N=3) | +39.8pp |",
        f"| Degradation from vectorized | {ppfmt((r1.get('tick_excess_hr') or 0) - 0.534)} |",
        "",
        "**Key changes vs v2**: BEH gate (bucket_excess_hr >= 0.02) removes near-certainty traders. N=2 vs N=3 lowers consensus bar.",
        "",
        "---",
        "",
        "## Leg 2: Politics NO K=100 N=2 (first NO-direction tick validation)",
        "",
        "**Vectorized UB**: 81.8% HR, +8.8pp excess | **First NO-direction strategy ever tick-validated**",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total fills | {r2.get('total_fills', 'N/A')} |",
        f"| Hit rate | {pct(r2.get('tick_hr'))} |",
        f"| Politics NO base rate (test) | {pct(r2.get('base_rate'))} |",
        f"| **Excess HR** | **{ppfmt(r2.get('tick_excess_hr'))}** |",
        f"| Net PnL | {dollar(r2.get('tick_pnl_net'))} |",
        f"| Sharpe | {num(r2.get('tick_sharpe'))} |",
        f"| Max drawdown | {dollar(r2.get('tick_max_drawdown'))} |",
        f"| Profit factor | {num(r2.get('tick_profit_factor'))} |",
        f"| Avg hold | {num(r2.get('tick_avg_hold_h'))}h |",
        f"| % fills < 1h | {pct(r2.get('pct_under_1h'))} |",
        f"| % fills < 24h | {pct(r2.get('pct_under_24h'))} |",
        f"| Vectorized UB excess | +8.8pp |",
        f"| Degradation from vectorized | {ppfmt((r2.get('tick_excess_hr') or 0) - 0.088)} |",
        "",
        "**Note**: Small vectorized excess (+8.8pp) — even a modest degradation may wipe the edge.",
        "Positive tick excess required for this leg to be viable.",
        "",
        "---",
        "",
        "## Leg 3: Sports In-Play YES K=25 N=1 (RT track)",
        "",
        "**Vectorized UB**: 67.5% HR, +34.2pp excess, hold<4h | **RT infra**: sub-second WS, no latency penalty",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total fills | {r3.get('total_fills', 'N/A')} |",
        f"| Hit rate | {pct(r3.get('tick_hr'))} |",
        f"| Sports YES base rate (test) | {pct(r3.get('base_rate'))} |",
        f"| **Excess HR** | **{ppfmt(r3.get('tick_excess_hr'))}** |",
        f"| Net PnL | {dollar(r3.get('tick_pnl_net'))} |",
        f"| Sharpe | {num(r3.get('tick_sharpe'))} |",
        f"| Max drawdown | {dollar(r3.get('tick_max_drawdown'))} |",
        f"| Avg hold | {num(r3.get('tick_avg_hold_h'))}h |",
        f"| % fills < 1h | {pct(r3.get('pct_under_1h'))} |",
        f"| % fills < 4h | {pct(r3.get('pct_under_4h'))} |",
        f"| Vectorized UB excess | +34.2pp |",
        f"| Degradation from vectorized | {ppfmt((r3.get('tick_excess_hr') or 0) - 0.342)} |",
        "",
        "**RT infrastructure note**: Degradation comes from fill model only.",
        "Production WS latency (~50ms) << elite trader lead time (58 min median).",
        "",
        "---",
        "",
        "## Vectorized vs Tick Comparison",
        "",
        "| Leg | Vectorized UB | Tick | Degradation | Viable? |",
        "|-----|--------------|------|-------------|---------|",
    ]

    def viability(exc):
        if exc is None:
            return "?"
        if exc > 0.15:
            return "YES — strong"
        if exc > 0.05:
            return "MARGINAL — monitor"
        if exc > 0.0:
            return "WEAK — risky"
        return "NO — negative edge"

    lines += [
        f"| Sports YES K=25 N=2 | +53.4pp | {ppfmt(r1.get('tick_excess_hr'))} | {ppfmt((r1.get('tick_excess_hr') or 0) - 0.534)} | {viability(r1.get('tick_excess_hr'))} |",
        f"| Politics NO K=100 N=2 | +8.8pp | {ppfmt(r2.get('tick_excess_hr'))} | {ppfmt((r2.get('tick_excess_hr') or 0) - 0.088)} | {viability(r2.get('tick_excess_hr'))} |",
        f"| Sports InPlay K=25 N=1 | +34.2pp | {ppfmt(r3.get('tick_excess_hr'))} | {ppfmt((r3.get('tick_excess_hr') or 0) - 0.342)} | {viability(r3.get('tick_excess_hr'))} |",
        "",
        "---",
        "",
        "## Artifacts",
        "",
        "- Script: `research/hypotheses/scorecard-v3-strategies/scripts/run_tick_v3.py`",
        "- JSON: `research/hypotheses/scorecard-v3-strategies/validation/tick_results_v3.json`",
        "- Ledgers: `research/output/ledger_v3_*.parquet`",
        "- Log: `tmp/tick_v3.log`",
        "",
    ]

    md_path = VALIDATION_DIR / "tick_results_v3.md"
    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    log(f"  Report written to {md_path}")


if __name__ == "__main__":
    main()
