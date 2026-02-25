"""Assess S2 (Will-NO) profitability under incremental config improvements.

Compares old baseline vs research-optimized configs:
  1. Baseline        — flat 15-40 %, avoid {reach, hit}, flat $50
  2. Narrow 15-30 %  — simple band tightening
  3. Price bands     — band-adjusted sizing (research PnL/day multipliers)
  4. + avoid by/before
  5. + prefer keywords (above, below, today, tonight)
  6. + volume ≤ $10K  (med bucket proxy)

Each scenario is applied to the same set of Will markets with known resolution.

Usage:
    uv run python scripts/assess_s2_profitability.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

import httpx
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.will_no.config import DEFAULT_PRICE_BANDS

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"

# ── Scenario definitions ────────────────────────────────────────────────


def _band_mult(price: float, bands: tuple[tuple[float, float, float], ...]) -> float:
    n = len(bands)
    for i, (lo, hi, m) in enumerate(bands):
        if i < n - 1:
            if lo <= price < hi:
                return m
        else:
            if lo <= price <= hi:
                return m
    return 0.0


SCENARIOS: dict[str, dict] = {
    "1. Baseline (old)": {
        "yes_min": 0.15, "yes_max": 0.40,
        "avoid": {"reach", "hit"},
        "prefer": set(),
        "bands": None,
        "max_vol": 0,
    },
    "2. Narrow 15-30%": {
        "yes_min": 0.15, "yes_max": 0.30,
        "avoid": {"reach", "hit"},
        "prefer": set(),
        "bands": None,
        "max_vol": 0,
    },
    "3. Price bands": {
        "avoid": {"reach", "hit"},
        "prefer": set(),
        "bands": DEFAULT_PRICE_BANDS,
        "max_vol": 0,
    },
    "4. +avoid by/before": {
        "avoid": {"reach", "hit", "by", "before"},
        "prefer": set(),
        "bands": DEFAULT_PRICE_BANDS,
        "max_vol": 0,
    },
    "5. +prefer kw": {
        "avoid": {"reach", "hit", "by", "before"},
        "prefer": {"above", "below", "today", "tonight"},
        "bands": DEFAULT_PRICE_BANDS,
        "max_vol": 0,
    },
    "6. +vol≤10K": {
        "avoid": {"reach", "hit", "by", "before"},
        "prefer": {"above", "below", "today", "tonight"},
        "bands": DEFAULT_PRICE_BANDS,
        "max_vol": 10_000,
    },
}

# ── ClickHouse helpers ──────────────────────────────────────────────────


def _fmt(t0: float) -> str:
    e = time.time() - t0
    return f"{e:.1f}s" if e < 60 else f"{e / 60:.1f}min"


async def query_ch(
    client: httpx.AsyncClient, query: str, *, label: str = "query"
) -> pl.DataFrame:
    t0 = time.time()
    print(f"  [{label}] …", end="", flush=True)
    async with client.stream(
        "POST", "/", content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB}, headers={"Content-Type": "text/plain"},
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            print(f"\n  CH Error: {body}")
            resp.raise_for_status()
        chunks: list[bytes] = []
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data.strip():
        print(f" empty ({_fmt(t0)})")
        return pl.DataFrame()
    df = pl.read_csv(io.BytesIO(data), has_header=True, skip_rows_after_header=1)
    print(f" {len(df):,} rows ({_fmt(t0)})")
    return df


# ── Signal generation & PnL ────────────────────────────────────────────


def apply_scenario(
    base: pl.DataFrame, scenario: dict,
) -> pl.DataFrame:
    """Filter and size signals according to a scenario config."""
    df = base.clone()

    # Price filter
    bands = scenario.get("bands")
    if bands:
        # Keep only prices that fall in some band
        mask = pl.lit(False)
        n = len(bands)
        for i, (lo, hi, _m) in enumerate(bands):
            if i < n - 1:
                mask = mask | ((pl.col("first_price") >= lo) & (pl.col("first_price") < hi))
            else:
                mask = mask | ((pl.col("first_price") >= lo) & (pl.col("first_price") <= hi))
        df = df.filter(mask)
    else:
        df = df.filter(
            (pl.col("first_price") >= scenario["yes_min"])
            & (pl.col("first_price") <= scenario["yes_max"])
        )

    # Avoid keywords
    for kw in scenario["avoid"]:
        df = df.filter(
            ~pl.col("question").fill_null("").str.to_lowercase().str.contains(kw.lower())
        )

    # Prefer keywords
    if scenario["prefer"]:
        cond = pl.lit(False)
        q_lower = pl.col("question").fill_null("").str.to_lowercase()
        for kw in scenario["prefer"]:
            cond = cond | q_lower.str.contains(kw.lower())
        df = df.filter(cond)

    # Volume filter
    max_vol = scenario.get("max_vol", 0)
    if max_vol > 0 and "total_volume" in df.columns:
        df = df.filter(pl.col("total_volume") <= max_vol)

    # Compute size_usd
    if bands:
        size_expr = pl.lit(0.0)
        n = len(bands)
        for i, (lo, hi, m) in enumerate(bands):
            if i < n - 1:
                c = (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
            else:
                c = (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
            size_expr = pl.when(c).then(pl.lit(50.0 * m)).otherwise(size_expr)
        df = df.with_columns(size_expr.alias("size_usd"))
    else:
        df = df.with_columns(pl.lit(50.0).alias("size_usd"))

    return df


def compute_pnl(signals: pl.DataFrame, resolution: pl.DataFrame) -> pl.DataFrame:
    """Join with resolution and compute per-signal PnL."""
    joined = signals.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left",
    )
    resolved = joined.filter(pl.col("resolution_value") == 1)
    if resolved.is_empty():
        return pl.DataFrame()

    resolved = resolved.with_columns(
        (pl.col("winner_outcome") == "No").alias("won"),
    )
    resolved = resolved.with_columns(
        pl.when(pl.col("won"))
        .then(pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price")))
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )
    return resolved


# ── Main ────────────────────────────────────────────────────────────────


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    # ── Step 1: Fetch data ──────────────────────────────────────────────
    print("=" * 72)
    print("  S2 Will-NO profitability assessment")
    print("=" * 72)

    print("\nFetching data …")

    markets = await query_ch(
        client,
        "SELECT condition_id, toString(event_id) AS event_id, question "
        "FROM markets WHERE question LIKE 'Will %%'",
        label="markets",
    )

    first_prices = await query_ch(
        client,
        """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id
        """,
        label="first-prices",
    )
    first_prices = first_prices.with_columns(pl.col("first_price").cast(pl.Float64))

    total_volume = await query_ch(
        client,
        """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               sum(CAST(price AS Float64) * CAST(size AS Float64)) AS total_volume
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id
        """,
        label="volume",
    )
    total_volume = total_volume.with_columns(pl.col("total_volume").cast(pl.Float64))

    # Lockup proxy: event end_date - first trade timestamp
    lockup = await query_ch(
        client,
        """
        WITH
            wm AS (SELECT condition_id, toString(event_id) AS event_id FROM markets WHERE question LIKE 'Will %%'),
            ft AS (
                SELECT condition_id, min(toUnixTimestamp(timestamp)) AS first_ts
                FROM (SELECT * FROM polymarket.trades_raw FINAL) t
                WHERE condition_id IN (SELECT condition_id FROM wm)
                GROUP BY condition_id
            )
        SELECT w.condition_id AS condition_id,
               (CAST(toUnixTimestamp(e.end_date) AS Float64) - ft.first_ts) / 86400.0 AS lockup_days
        FROM wm w
        JOIN ft ON ft.condition_id = w.condition_id
        JOIN events e ON toString(e.id) = w.event_id
        WHERE e.end_date IS NOT NULL
        """,
        label="lockup",
    )
    lockup = lockup.with_columns(pl.col("lockup_days").cast(pl.Float64))

    await client.aclose()

    # Build base table
    base = markets.join(first_prices, on="condition_id", how="inner")
    base = base.join(total_volume, on="condition_id", how="left")
    base = base.join(lockup, on="condition_id", how="left")

    print(f"\n  Will markets with trades: {len(base):,}")

    # Resolution data
    res_path = Path("data/metadata/markets.parquet")
    if not res_path.exists():
        print(f"  ERROR: {res_path} not found")
        return
    resolution = pl.read_parquet(str(res_path))

    # ── Step 2: Run scenarios ───────────────────────────────────────────
    print(f"\n{'=' * 72}")
    print(f"  {'Scenario':<25} {'Signals':>8} {'Resolved':>8} "
          f"{'HR':>7} {'PnL':>12} {'ROI':>7} "
          f"{'$/bet':>8} {'Med lockup':>10}")
    print(f"  {'-' * 25} {'-' * 8} {'-' * 8} "
          f"{'-' * 7} {'-' * 12} {'-' * 7} "
          f"{'-' * 8} {'-' * 10}")

    results = []
    for name, scenario in SCENARIOS.items():
        signals = apply_scenario(base, scenario)
        n_signals = len(signals)

        pnl_df = compute_pnl(signals, resolution)
        n_resolved = len(pnl_df)
        if n_resolved == 0:
            print(f"  {name:<25} {n_signals:>8,} {0:>8} — no resolved signals")
            continue

        wins = pnl_df.filter(pl.col("won")).shape[0]
        hr = wins / n_resolved
        total_pnl = pnl_df["pnl"].sum()
        total_bet = pnl_df["size_usd"].sum()
        roi = total_pnl / total_bet * 100 if total_bet > 0 else 0
        avg_pnl = total_pnl / n_resolved

        # Median lockup for this subset
        pnl_with_lockup = pnl_df.join(
            lockup.select("condition_id", "lockup_days"),
            on="condition_id", how="left",
        )
        med_lockup = pnl_with_lockup["lockup_days"].drop_nulls().median()
        lockup_str = f"{med_lockup:.1f}d" if med_lockup is not None else "—"

        print(
            f"  {name:<25} {n_signals:>8,} {n_resolved:>8,} "
            f"{hr:>6.1%} ${total_pnl:>+11,.0f} {roi:>+6.1f}% "
            f"${avg_pnl:>+7.2f} {lockup_str:>10}"
        )

        results.append({
            "name": name, "n_signals": n_signals, "n_resolved": n_resolved,
            "wins": wins, "hr": hr, "pnl": total_pnl, "roi": roi,
            "avg_pnl": avg_pnl, "med_lockup": med_lockup,
        })

    # ── Step 3: Detailed breakdowns for production config (scenario 5) ──
    print(f"\n{'=' * 72}")
    print("  Detailed breakdown: scenario 5 (production config)")
    print(f"{'=' * 72}")

    prod_scenario = SCENARIOS["5. +prefer kw"]
    prod_signals = apply_scenario(base, prod_scenario)
    prod_pnl = compute_pnl(prod_signals, resolution)

    if not prod_pnl.is_empty():
        # By price band
        print(f"\n  By YES price band:")
        for lo, hi, mult in DEFAULT_PRICE_BANDS:
            if lo == DEFAULT_PRICE_BANDS[-1][0]:
                subset = prod_pnl.filter(
                    (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
                )
            else:
                subset = prod_pnl.filter(
                    (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
                )
            n = len(subset)
            if n == 0:
                continue
            w = subset.filter(pl.col("won")).shape[0]
            p = subset["pnl"].sum()
            b = subset["size_usd"].sum()
            r = p / b * 100 if b > 0 else 0
            print(
                f"    {lo:.0%}-{hi:.0%} (mult={mult:.2f}): "
                f"{n:>5,} signals  {w / n:>5.1%} HR  "
                f"PnL ${p:>+9,.0f}  ROI {r:>+5.1f}%  "
                f"size ${50 * mult:.0f}"
            )

        # By prefer keyword
        print(f"\n  By prefer keyword match:")
        for kw in ["above", "below", "today", "tonight"]:
            subset = prod_pnl.filter(
                pl.col("question").fill_null("").str.to_lowercase().str.contains(kw)
            )
            n = len(subset)
            if n == 0:
                continue
            w = subset.filter(pl.col("won")).shape[0]
            p = subset["pnl"].sum()
            b = subset["size_usd"].sum()
            r = p / b * 100 if b > 0 else 0
            print(
                f"    \"{kw}\": {n:>5,} signals  {w / n:>5.1%} HR  "
                f"PnL ${p:>+9,.0f}  ROI {r:>+5.1f}%"
            )

        # Lockup distribution
        prod_with_lockup = prod_pnl.join(
            lockup.select("condition_id", "lockup_days"),
            on="condition_id", how="left",
        ).drop_nulls(subset=["lockup_days"])
        if len(prod_with_lockup) > 0:
            ld = prod_with_lockup["lockup_days"]
            print(f"\n  Lockup distribution (days):")
            print(f"    p25={ld.quantile(0.25):.1f}  "
                  f"median={ld.median():.1f}  "
                  f"p75={ld.quantile(0.75):.1f}  "
                  f"max={ld.max():.1f}")

    # ── Step 4: Capital simulation ──────────────────────────────────────
    print(f"\n{'=' * 72}")
    print("  $300 capital simulation (6 slots × $50, 14 months)")
    print(f"{'=' * 72}")

    if len(results) >= 2:
        for r in results:
            med_lock = r.get("med_lockup")
            if med_lock and med_lock > 0:
                rotations_per_slot_month = 30.0 / med_lock
                bets_per_month = min(6 * rotations_per_slot_month, r["n_resolved"] / 14.0)
                monthly_pnl = bets_per_month * r["avg_pnl"]
                monthly_pnl_50 = monthly_pnl * 0.5  # 50% haircut for execution
                print(
                    f"  {r['name']:<25} "
                    f"{rotations_per_slot_month:>5.1f} rot/slot/mo  "
                    f"{bets_per_month:>5.0f} bets/mo  "
                    f"PnL ${monthly_pnl:>+7.0f}/mo  "
                    f"(50% haircut: ${monthly_pnl_50:>+7.0f}/mo)"
                )

    # ── Step 5: Improvement summary ─────────────────────────────────────
    if len(results) >= 2:
        baseline = results[0]
        print(f"\n{'=' * 72}")
        print("  Improvement vs baseline")
        print(f"{'=' * 72}")
        for r in results[1:]:
            delta_roi = r["roi"] - baseline["roi"]
            delta_pnl = r["pnl"] - baseline["pnl"]
            signal_ratio = r["n_resolved"] / baseline["n_resolved"] * 100 if baseline["n_resolved"] else 0
            print(
                f"  {r['name']:<25} "
                f"ROI {delta_roi:>+5.1f}pp  "
                f"PnL ${delta_pnl:>+10,.0f}  "
                f"signals {signal_ratio:>5.1f}% of baseline"
            )

    print(f"\n  Done in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
