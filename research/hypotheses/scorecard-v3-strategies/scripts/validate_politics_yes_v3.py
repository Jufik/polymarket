"""Tick-by-tick validation for Politics YES v3 — combined consensus (YES+NO pool).

The Politics YES signal uses combined YES+NO consensus:
  - Pool: BEH-gated composite-ranked Politics traders (YES direction, K=100)
  - Consensus: N total traders (YES or NO BUYs) must enter the market
  - Direction filter: vol-weighted direction must be YES to fire
  - max_price=0.80 (from v2 recommendation)

Tested for N=3, N=4, N=5 to compare vs v2 baseline.

v2 Baseline (K=100, N=5, max_price=0.80):
  - +51.6pp raw excess, +41pp with max_price=0.80, 262 fills (125 filtered)
  - Politics YES base rate ~19%

Test period: 2025-07-01 – 2026-03-01

Usage:
    PYTHONPATH=/mnt/nvme/git/polymarket/polymarket uv run python \\
        research/hypotheses/scorecard-v3-strategies/scripts/validate_politics_yes_v3.py
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

LOG_PATH = Path("/mnt/nvme/git/polymarket/polymarket/tmp/validate_politics_yes_v3.log")
OUTPUT_DIR = Path("/mnt/nvme/git/polymarket/polymarket/research/output")
VALIDATION_DIR = Path(
    "/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/validation"
)

TEST_START = "2025-07-01"
TEST_END = "2026-03-01"

# v2 baseline reference
V2_BASELINE = {
    "k": 100,
    "n": 5,
    "max_price": 0.80,
    "excess_hr": 0.41,  # +41pp with max_price gate
    "fills": 262,
    "fills_filtered": 125,  # filtered by max_price
}


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
    total_pnl = None
    median_pnl = None
    if "pnl_net" in settled.columns:
        total_pnl = float(settled["pnl_net"].sum())
        median_pnl = float(settled["pnl_net"].median())
        log(f"  PnL: total=${total_pnl:,.2f}, median per fill=${median_pnl:,.3f}")

    # Fill price distribution (if available)
    if "fill_price" in settled.columns:
        price_med = settled["fill_price"].median()
        price_mean = settled["fill_price"].mean()
        log(f"  Fill price: median={price_med:.3f}, mean={price_mean:.3f}")
        n_above_080 = settled.filter(pl.col("fill_price") > 0.80).height
        log(f"  Fill price > 0.80 (would be filtered): {n_above_080} / {len(settled)} = {n_above_080/len(settled):.1%}")

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
        "total_pnl": round(total_pnl, 2) if total_pnl is not None else None,
        "median_pnl_per_fill": round(median_pnl, 4) if median_pnl is not None else None,
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


def run_n_threshold(
    n: int,
    pool: set[str],
    tag_markets: set[str],
    gambling_markets: set[str],
    token_map: dict,
    base_rate: float,
    max_price: float = 0.80,
) -> dict:
    """Run tick-by-tick validation for a single N threshold."""
    name = f"politics_yes_v3_k100_n{n}"
    log(f"\n  -- Running N={n} --")

    universe = tag_markets - gambling_markets
    log(f"  Universe: {len(universe)} markets")

    strategy = TokenMapStrategy(
        name=name,
        pool=pool,
        tag_markets=tag_markets,
        gambling_markets=gambling_markets,
        n_threshold=n,
        token_map=token_map,
        direction_filter="YES",  # combined consensus: only fire when vol-weighted direction is YES
        size_usd=100.0,
        max_price=max_price,
    )

    config = make_config(name)

    t0 = time.time()
    result, summary = run_fast_backtest(
        strategy, config,
        universe=universe,
        output_dir=OUTPUT_DIR,
    )
    elapsed = time.time() - t0
    log(f"  Elapsed: {elapsed:.1f}s | trades seen: {result.total_trades:,} | "
        f"fills: {result.total_fills} | rejected: {len(result.rejected_intents)}")

    if summary:
        print_summary(summary, f"Politics_YES_v3_K100_N{n}")
    else:
        log(f"  [WARN] No summary for N={n} (no settled positions?)")

    # Analyze ledger
    ledger_path = OUTPUT_DIR / f"ledger_{name}.parquet"
    analysis = analyze_ledger(ledger_path, base_rate, f"Politics YES v3 K=100 N={n}")

    analysis.update({
        "name": name,
        "n_threshold": n,
        "max_price": max_price,
        "total_fills": result.total_fills,
        "rejected_intents": len(result.rejected_intents),
        "pool_size": len(pool),
        "direction": "YES",
        "tag": "Politics",
        "elapsed_s": round(elapsed, 1),
    })

    if summary:
        analysis.update({
            "tick_hr": round(summary.hit_rate, 4),
            "tick_pnl_net": round(summary.total_pnl_net, 2),
            "tick_sharpe": round(summary.sharpe, 4),
            "tick_max_drawdown": round(summary.max_drawdown, 2),
            "tick_profit_factor": round(summary.profit_factor, 4),
            "tick_avg_hold_h": round(summary.avg_hold_duration_s / 3600, 2),
            "tick_excess_hr": round(summary.hit_rate - base_rate, 4),
            "win_count": summary.win_count,
            "loss_count": summary.loss_count,
        })

    return analysis


def main() -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "w") as f:
        f.write("")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("Politics YES v3 — Tick-by-Tick Validation (combined consensus)")
    log(f"Test period: {TEST_START} to {TEST_END}")
    log("Pool: BEH-gated composite K=100, direction_filter=YES, max_price=0.80")
    log("Testing N=3, N=4, N=5")
    log("=" * 70)

    # ── Load token_map ────────────────────────────────────────────────────────
    log("\nLoading token_map from replay resolutions...")
    t0 = time.time()
    _, token_map_replay = load_replay_resolutions()
    log(f"  token_map (from replay): {len(token_map_replay)} markets ({time.time()-t0:.1f}s)")

    from research.db import db as get_db
    con = get_db().con

    log("  Supplementing token_map from DuckDB token_market_map...")
    token_map_db = build_token_map_from_db(con)
    # Merge: replay takes priority
    token_map = {**token_map_db, **token_map_replay}
    log(f"  Combined token_map: {len(token_map)} markets")

    # ── Import pool builder ────────────────────────────────────────────────────
    import importlib.util as _ilu
    _bp_spec = _ilu.spec_from_file_location(
        "build_pools_v3",
        Path("/mnt/nvme/git/polymarket/polymarket/research/hypotheses/scorecard-v3-strategies/scripts/build_pools_v3.py"),
    )
    _bp_mod = _ilu.module_from_spec(_bp_spec)
    _bp_spec.loader.exec_module(_bp_mod)

    # ── Build pool (single pool, reused across N sweeps) ──────────────────────
    log("\nBuilding Politics YES pool (K=100, BEH gate >= 0.02)...")
    t0 = time.time()
    pool, tag_markets, gambling_markets = _bp_mod.build_politics_yes_pool_v3(k=100)
    log(f"  Pool: {len(pool)} traders, {len(tag_markets)} tag markets ({time.time()-t0:.1f}s)")
    log(f"  Gambling markets excluded: {len(gambling_markets)}")
    log(f"  Universe (tag - gambling): {len(tag_markets - gambling_markets)}")

    # ── Compute base rate ─────────────────────────────────────────────────────
    _bp_mod._ensure_shared_tables(con)
    base_rate = compute_base_rate(con, "Politics", "YES", TEST_START, TEST_END)
    log(f"  Politics YES base rate (test period {TEST_START}–{TEST_END}): {base_rate:.1%}")

    # ── Run N=3, N=4, N=5 ────────────────────────────────────────────────────
    all_results = {}

    for n in [3, 4, 5]:
        log(f"\n{'=' * 70}")
        log(f"N={n} — Politics YES v3 K=100 N={n} combined consensus")
        log("=" * 70)

        res = run_n_threshold(
            n=n,
            pool=pool,
            tag_markets=tag_markets,
            gambling_markets=gambling_markets,
            token_map=token_map,
            base_rate=base_rate,
            max_price=0.80,
        )
        all_results[f"n{n}"] = res

    # ── Print comparison ──────────────────────────────────────────────────────
    log("\n" + "=" * 70)
    log("N SWEEP COMPARISON — Politics YES v3 K=100, max_price=0.80")
    log("=" * 70)
    log(f"  v2 baseline: N=5, excess=+41.0pp, fills=262 (125 filtered)")
    log(f"  Base rate: {base_rate:.1%}")
    log("")
    log(f"  {'N':>3}  {'Fills':>6}  {'Settled':>7}  {'HR':>6}  {'Excess HR':>10}  {'PnL':>10}  {'Sharpe':>7}  {'Max DD':>8}")
    log(f"  {'---':>3}  {'------':>6}  {'-------':>7}  {'------':>6}  {'----------':>10}  {'----------':>10}  {'-------':>7}  {'--------':>8}")
    for n in [3, 4, 5]:
        r = all_results[f"n{n}"]
        fills = r.get("total_fills", 0)
        settled = r.get("settled", 0)
        hr = f"{r.get('tick_hr', 0):.1%}" if r.get("tick_hr") is not None else "N/A"
        exc = f"{r.get('tick_excess_hr', 0):+.1%}" if r.get("tick_excess_hr") is not None else "N/A"
        pnl = f"${r.get('tick_pnl_net', 0):,.2f}" if r.get("tick_pnl_net") is not None else "N/A"
        sharpe = f"{r.get('tick_sharpe', 0):.2f}" if r.get("tick_sharpe") is not None else "N/A"
        dd = f"${r.get('tick_max_drawdown', 0):,.2f}" if r.get("tick_max_drawdown") is not None else "N/A"
        log(f"  {n:>3}  {fills:>6}  {settled:>7}  {hr:>6}  {exc:>10}  {pnl:>10}  {sharpe:>7}  {dd:>8}")

    # ── Save JSON ─────────────────────────────────────────────────────────────
    output = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "test_start": TEST_START,
            "test_end": TEST_END,
            "pool_config": "K=100, BEH gate >= 0.02, direction_filter=YES, max_price=0.80",
            "v2_baseline": V2_BASELINE,
            "note": "Combined YES+NO consensus: N total traders in market, vol-weighted direction must be YES. Tick-validated via SyncReplayRunner.",
        },
        "base_rate": base_rate,
        "pool_size": len(pool),
        "results": all_results,
    }
    json_path = VALIDATION_DIR / "politics_yes_v3_results.json"
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log(f"\nSaved JSON: {json_path}")

    # ── Write markdown ────────────────────────────────────────────────────────
    write_markdown(all_results, output["meta"], base_rate, len(pool))

    log("\n" + "=" * 70)
    log("VALIDATION COMPLETE")
    log("=" * 70)


def write_markdown(results: dict, meta: dict, base_rate: float, pool_size: int) -> None:
    def pct(v):
        return f"{v:.1%}" if v is not None else "N/A"

    def ppfmt(v):
        return f"{v:+.1%}" if v is not None else "N/A"

    def dollar(v):
        return f"${v:,.2f}" if v is not None else "N/A"

    def num(v, fmt=".2f"):
        return f"{v:{fmt}}" if v is not None else "N/A"

    def viability(exc):
        if exc is None:
            return "?"
        if exc > 0.20:
            return "GO — strong"
        if exc > 0.10:
            return "GO — viable"
        if exc > 0.05:
            return "MARGINAL — monitor"
        if exc > 0.0:
            return "WEAK — risky"
        return "NO-GO — negative edge"

    lines = [
        "# Politics YES v3 — Tick-by-Tick Validation",
        "",
        "> **TICK-VALIDATED** via SyncReplayRunner (not vectorized upper bounds).",
        "",
        f"Generated: {meta['generated_at']}",
        f"Test period: {meta['test_start']} – {meta['test_end']} (8 months)",
        f"Pool: BEH-gated composite, K=100, direction\\_filter=YES, max\\_price=0.80",
        f"Base rate (Politics YES, test period): {base_rate:.1%}",
        f"Pool size: {pool_size} traders",
        "",
        "**Model**: Combined YES+NO consensus — N total pool traders in market,",
        "vol-weighted direction must be YES to fire.",
        "",
        "**v2 baseline**: K=100, N=5, max\\_price=0.80 → +41pp excess, 262 fills, 125 filtered",
        "",
        "## N Sweep Summary",
        "",
        "| N | Fills | Settled | HR | **Excess HR** | PnL | Sharpe | Max DD | Viable? |",
        "|---|-------|---------|-----|--------------|-----|--------|--------|---------|",
    ]

    for n in [3, 4, 5]:
        r = results.get(f"n{n}", {})
        fills = r.get("total_fills", 0)
        settled = r.get("settled", 0)
        hr = pct(r.get("tick_hr"))
        exc = ppfmt(r.get("tick_excess_hr"))
        pnl = dollar(r.get("tick_pnl_net"))
        sharpe = num(r.get("tick_sharpe"))
        dd = dollar(r.get("tick_max_drawdown"))
        viable = viability(r.get("tick_excess_hr"))
        lines.append(f"| N={n} | {fills} | {settled} | {hr} | **{exc}** | {pnl} | {sharpe} | {dd} | {viable} |")

    lines += [
        "",
        "---",
        "",
        "## v2 vs v3 Comparison",
        "",
        "| Model | Pool | N | max\\_price | Fills | Excess HR | Verdict |",
        "|-------|------|---|-----------|-------|-----------|---------|",
        f"| v2 (baseline) | K=100 YES-only | 5 | 0.80 | 262 (125 filtered) | +41.0pp | Reference |",
    ]

    for n in [3, 4, 5]:
        r = results.get(f"n{n}", {})
        exc = ppfmt(r.get("tick_excess_hr"))
        fills = r.get("total_fills", 0)
        viable = viability(r.get("tick_excess_hr"))
        lines.append(f"| v3 BEH-gated | K={pool_size} YES | {n} | 0.80 | {fills} | {exc} | {viable} |")

    lines += [
        "",
        "---",
        "",
    ]

    # Detail per N
    for n in [3, 4, 5]:
        r = results.get(f"n{n}", {})
        lines += [
            f"## N={n} Detail",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total fills | {r.get('total_fills', 'N/A')} |",
            f"| Settled | {r.get('settled', 'N/A')} |",
            f"| Win / Loss | {r.get('win_count', 'N/A')} / {r.get('loss_count', 'N/A')} |",
            f"| Hit rate | {pct(r.get('tick_hr'))} |",
            f"| Base rate (test) | {pct(base_rate)} |",
            f"| **Excess HR** | **{ppfmt(r.get('tick_excess_hr'))}** |",
            f"| Net PnL | {dollar(r.get('tick_pnl_net'))} |",
            f"| Sharpe | {num(r.get('tick_sharpe'))} |",
            f"| Max drawdown | {dollar(r.get('tick_max_drawdown'))} |",
            f"| Profit factor | {num(r.get('tick_profit_factor'))} |",
            f"| Avg hold | {num(r.get('tick_avg_hold_h'))}h |",
            f"| Median hold | {num(r.get('median_hold_h'))}h |",
            f"| % fills < 1h | {pct(r.get('pct_under_1h'))} |",
            f"| % fills < 4h | {pct(r.get('pct_under_4h'))} |",
            f"| % fills < 24h | {pct(r.get('pct_under_24h'))} |",
            "",
        ]

    lines += [
        "---",
        "",
        "## Artifacts",
        "",
        "- Script: `research/hypotheses/scorecard-v3-strategies/scripts/validate_politics_yes_v3.py`",
        "- JSON: `research/hypotheses/scorecard-v3-strategies/validation/politics_yes_v3_results.json`",
        "- Ledgers: `research/output/ledger_politics_yes_v3_k100_n{3,4,5}.parquet`",
        "- Log: `tmp/validate_politics_yes_v3.log`",
        "",
    ]

    md_path = VALIDATION_DIR / "politics_yes_v3_results.md"
    with open(md_path, "w") as f:
        f.write("\n".join(lines))
    log(f"  Report written to {md_path}")


if __name__ == "__main__":
    main()
