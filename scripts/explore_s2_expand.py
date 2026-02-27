"""Explore signal expansion for S2 Will-NO strategy.

The current niche config (706 signals, 12.1% ROI, 1.4d median lockup) uses
only ~$59 of capital. This script explores relaxing filters to increase
throughput, even if per-bet ROI drops, as long as compounding improves.

Key metric: monthly_pnl = signals/month × avg_bet × roi_per_wager
Capital ROI:  monthly_pnl / (sigs/month × avg_bet × median_lockup / 30)

Usage:
    uv run python scripts/explore_s2_expand.py
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

from polymarket_pipeline.strategies_impl.will_no.config import (
    DEFAULT_PRICE_BANDS,
    WillNoConfig,
)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
N_MONTHS = 18.0  # data spans ~18 months


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
            raise RuntimeError(body)
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


# ── Helpers ────────────────────────────────────────────────────────────


def add_pnl(df: pl.DataFrame, fee: float = 0.02) -> pl.DataFrame:
    """Add won/pnl columns for resolved markets. Uses band-adjusted sizing."""
    resolved = df.filter(pl.col("resolution_value") == 1)
    if resolved.is_empty():
        return pl.DataFrame()
    resolved = resolved.with_columns(
        (pl.col("winner_outcome") == "No").alias("won"),
    )
    resolved = resolved.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
            - pl.col("size_usd") * fee
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )
    return resolved


def add_band_sizing(df: pl.DataFrame, base_bet: float = 50.0) -> pl.DataFrame:
    """Add size_usd column based on price bands."""
    cfg = WillNoConfig()
    n = len(cfg.price_bands)
    size_expr = pl.lit(base_bet)  # fallback: flat sizing for out-of-band
    for i, (lo, hi, mult) in enumerate(cfg.price_bands):
        if i < n - 1:
            c = (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
        else:
            c = (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
        size_expr = pl.when(c).then(pl.lit(base_bet * mult)).otherwise(size_expr)
    return df.with_columns(size_expr.alias("size_usd"))


def eval_config(
    base: pl.DataFrame,
    *,
    label: str,
    prefer_kw: frozenset[str] | None = None,
    avoid_kw: frozenset[str] = frozenset({"reach", "hit"}),
    max_vol: float = 0,
    price_lo: float = 0.05,
    price_hi: float = 0.50,
    max_lockup: float = 0,
    fee: float = 0.02,
) -> dict | None:
    """Evaluate a filter configuration and return capital-adjusted metrics."""
    q_lower = pl.col("question").str.to_lowercase()
    df = base.filter(
        (pl.col("first_price") >= price_lo) & (pl.col("first_price") <= price_hi)
    )

    if prefer_kw:
        cond = pl.lit(False)
        for kw in prefer_kw:
            cond = cond | q_lower.str.contains(kw.lower())
        df = df.filter(cond)

    for kw in avoid_kw:
        df = df.filter(~q_lower.str.contains(kw.lower()))

    if max_vol > 0:
        df = df.filter(pl.col("total_volume") <= max_vol)

    if max_lockup > 0:
        df = df.filter(
            pl.col("lockup_days").is_not_null() & (pl.col("lockup_days") <= max_lockup)
        )

    df = add_band_sizing(df)
    df = add_pnl(df, fee=fee)
    if df.is_empty():
        return None

    n = len(df)
    if n < 20:
        return None

    wins = df.filter(pl.col("won")).height
    hr = wins / n
    total_pnl = df["pnl"].sum()
    total_bet = df["size_usd"].sum()
    roi = total_pnl / total_bet if total_bet > 0 else 0
    avg_bet = total_bet / n

    # Lockup
    ld = df.filter(pl.col("lockup_days").is_not_null())["lockup_days"]
    med_lockup = ld.median() if len(ld) > 0 else None
    p75_lockup = ld.quantile(0.75) if len(ld) > 0 else None

    # Capital-adjusted
    sigs_per_month = n / N_MONTHS
    monthly_wagered = sigs_per_month * avg_bet
    monthly_pnl = monthly_wagered * roi

    if med_lockup and med_lockup > 0:
        concurrent = sigs_per_month * med_lockup / 30.0
        capital = concurrent * avg_bet
        mo_roi_capital = monthly_pnl / capital if capital > 0 else 0
    else:
        capital = None
        mo_roi_capital = None

    return {
        "label": label,
        "n": n,
        "sigs_mo": round(sigs_per_month, 1),
        "hr": hr,
        "roi": roi,
        "avg_bet": avg_bet,
        "total_pnl": total_pnl,
        "monthly_pnl": monthly_pnl,
        "med_lockup": med_lockup,
        "p75_lockup": p75_lockup,
        "capital": capital,
        "mo_roi_capital": mo_roi_capital,
    }


def print_header() -> None:
    print(
        f"  {'Config':<42} {'Sigs':>5} {'S/mo':>5} {'HR':>6} "
        f"{'ROI':>7} {'$/mo':>7} {'Med LD':>6} {'Cap$':>6} {'MoROI%':>7}"
    )
    print(f"  {'-' * 42} {'-' * 5} {'-' * 5} {'-' * 6} "
          f"{'-' * 7} {'-' * 7} {'-' * 6} {'-' * 6} {'-' * 7}")


def print_result(r: dict) -> None:
    cap_str = f"${r['capital']:>5,.0f}" if r["capital"] else "    —"
    morc_str = f"{r['mo_roi_capital']:>6.0%}" if r["mo_roi_capital"] else "     —"
    ld_str = f"{r['med_lockup']:>5.1f}d" if r["med_lockup"] else "    —"
    marker = "+" if r["roi"] > 0 else "-"
    print(
        f" {marker} {r['label']:<42} {r['n']:>5,} {r['sigs_mo']:>5.0f} "
        f"{r['hr']:>5.1%} {r['roi']:>+6.1%} "
        f"${r['monthly_pnl']:>6,.0f} {ld_str} {cap_str} {morc_str}"
    )


# ── Main ──────────────────────────────────────────────────────────────


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    print("=" * 80)
    print("  S2 Will-NO: Signal Expansion Exploration")
    print("=" * 80)

    # ── Fetch data ────────────────────────────────────────────────────
    print("\nFetching data …")

    markets = await query_ch(
        client,
        """SELECT condition_id, toString(event_id) AS event_id, question
           FROM markets WHERE question LIKE 'Will %%'""",
        label="markets",
    )
    first_trades = await query_ch(
        client,
        """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               argMin(CAST(price AS Float64), timestamp) AS first_price,
               toUnixTimestamp64Milli(min(timestamp)) / 1000.0 AS first_ts
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id
        """,
        label="first-trades",
    )
    for c in ["first_price", "first_ts"]:
        first_trades = first_trades.with_columns(pl.col(c).cast(pl.Float64))

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

    events = await query_ch(
        client,
        """SELECT toString(id) AS event_id,
                  dateDiff('second', toDateTime64('1970-01-01', 3), end_date) AS end_ts
           FROM events WHERE end_date IS NOT NULL""",
        label="events",
    )
    events = events.with_columns(pl.col("end_ts").cast(pl.Float64))

    await client.aclose()

    # ── Build base table ──────────────────────────────────────────────
    print("\nBuilding base table …")

    base = markets.join(first_trades, on="condition_id", how="inner")
    base = base.join(total_volume, on="condition_id", how="left")

    # Join event end_date for lockup
    base = base.join(
        events.rename({"event_id": "event_id_ev"}).with_columns(
            pl.col("event_id_ev").alias("event_id")
        ).select("event_id", "end_ts"),
        on="event_id", how="left",
    )
    base = base.with_columns(
        ((pl.col("end_ts") - pl.col("first_ts")) / 86400.0).alias("lockup_days"),
    )

    # Resolution
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    base = base.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left",
    )

    n_resolved = base.filter(pl.col("resolution_value") == 1).height
    print(f"  Will markets with trades: {len(base):,}")
    print(f"  Resolved: {n_resolved:,}")

    # ══════════════════════════════════════════════════════════════════
    # 1. BASELINE: Current production config
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  1. BASELINE (current production config)")
    print(f"{'=' * 110}")
    print_header()

    prod_kw = frozenset({"between", "mlb", "prix", "grand", "league", "park", "traded", "fed"})
    baseline = eval_config(base, label="PRODUCTION (kw + vol<1K + 15-40%)",
                           prefer_kw=prod_kw, max_vol=1000, price_lo=0.15, price_hi=0.40)
    if baseline:
        print_result(baseline)

    # ══════════════════════════════════════════════════════════════════
    # 2. RELAX VOLUME CAP
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  2. RELAX VOLUME CAP (keep keywords + price range)")
    print(f"{'=' * 110}")
    print_header()

    for vol in [1000, 2000, 5000, 10000, 50000, 0]:
        vol_label = f"vol<${vol:,.0f}" if vol > 0 else "no vol filter"
        r = eval_config(base, label=f"prod_kw + {vol_label} + 15-40%",
                        prefer_kw=prod_kw, max_vol=vol, price_lo=0.15, price_hi=0.40)
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 3. RELAX KEYWORDS (keep vol<1K)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  3. RELAX KEYWORDS (keep vol<$1K + 15-40%)")
    print(f"{'=' * 110}")
    print_header()

    kw_sets = [
        ("prod_kw (8)", prod_kw),
        ("between only", frozenset({"between"})),
        ("sports (between+mlb+prix+league+grand)", frozenset({"between", "mlb", "prix", "league", "grand"})),
        ("no keyword filter", None),
    ]
    for kw_label, kw_set in kw_sets:
        r = eval_config(base, label=f"{kw_label} + vol<1K + 15-40%",
                        prefer_kw=kw_set, max_vol=1000, price_lo=0.15, price_hi=0.40)
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 4. RELAX PRICE RANGE
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  4. RELAX PRICE RANGE (keep prod_kw + vol<$1K)")
    print(f"{'=' * 110}")
    print_header()

    price_ranges = [
        ("15-40%", 0.15, 0.40),
        ("10-50%", 0.10, 0.50),
        ("10-45%", 0.10, 0.45),
        ("05-50%", 0.05, 0.50),
        ("15-50%", 0.15, 0.50),
        ("10-40%", 0.10, 0.40),
    ]
    for pr_label, lo, hi in price_ranges:
        r = eval_config(base, label=f"prod_kw + vol<1K + {pr_label}",
                        prefer_kw=prod_kw, max_vol=1000, price_lo=lo, price_hi=hi)
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 5. RELAX MULTIPLE FILTERS (combinatorial)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  5. RELAX MULTIPLE FILTERS (combos)")
    print(f"{'=' * 110}")
    print_header()

    combos = [
        # (label, prefer_kw, max_vol, price_lo, price_hi)
        ("prod_kw + vol<2K + 10-50%", prod_kw, 2000, 0.10, 0.50),
        ("prod_kw + vol<5K + 10-50%", prod_kw, 5000, 0.10, 0.50),
        ("sports5 + vol<2K + 10-50%",
         frozenset({"between", "mlb", "prix", "league", "grand"}), 2000, 0.10, 0.50),
        ("no kw + vol<1K + 15-40%", None, 1000, 0.15, 0.40),
        ("no kw + vol<1K + 10-50%", None, 1000, 0.10, 0.50),
        ("no kw + vol<2K + 10-50%", None, 2000, 0.10, 0.50),
        ("no kw + vol<500 + 10-50%", None, 500, 0.10, 0.50),
        ("no kw + vol<500 + 15-40%", None, 500, 0.15, 0.40),
        ("no kw + no vol + 15-40%", None, 0, 0.15, 0.40),
    ]
    for lbl, kw, vol, lo, hi in combos:
        r = eval_config(base, label=lbl, prefer_kw=kw, max_vol=vol,
                        price_lo=lo, price_hi=hi)
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 6. ADD LOCKUP CAP (fast-resolving only)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  6. ADD LOCKUP CAP (fast-resolving markets only)")
    print(f"{'=' * 110}")
    print_header()

    lockup_configs = [
        # (label, prefer_kw, max_vol, price_lo, price_hi, max_lockup)
        ("prod + lockup<3d", prod_kw, 1000, 0.15, 0.40, 3),
        ("prod + lockup<7d", prod_kw, 1000, 0.15, 0.40, 7),
        ("prod + lockup<14d", prod_kw, 1000, 0.15, 0.40, 14),
        ("no kw + vol<1K + lockup<3d + 15-40%", None, 1000, 0.15, 0.40, 3),
        ("no kw + vol<1K + lockup<7d + 15-40%", None, 1000, 0.15, 0.40, 7),
        ("no kw + vol<1K + lockup<7d + 10-50%", None, 1000, 0.10, 0.50, 7),
        ("no kw + vol<2K + lockup<7d + 10-50%", None, 2000, 0.10, 0.50, 7),
        ("no kw + vol<5K + lockup<3d + 15-40%", None, 5000, 0.15, 0.40, 3),
        ("no kw + vol<5K + lockup<7d + 15-40%", None, 5000, 0.15, 0.40, 7),
    ]
    for lbl, kw, vol, lo, hi, max_ld in lockup_configs:
        r = eval_config(base, label=lbl, prefer_kw=kw, max_vol=vol,
                        price_lo=lo, price_hi=hi, max_lockup=max_ld)
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 7. KEYWORD MINING: find new profitable keywords beyond current set
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  7. KEYWORD MINING (individual keywords, vol<$1K, 15-40%)")
    print(f"{'=' * 110}")

    # Extract all words from questions in the vol<1K + 15-40% subset
    vol_filtered = base.filter(
        (pl.col("total_volume") <= 1000)
        & (pl.col("first_price") >= 0.15)
        & (pl.col("first_price") <= 0.40)
    )
    vol_filtered = add_band_sizing(vol_filtered)
    vol_filtered = add_pnl(vol_filtered)

    if not vol_filtered.is_empty():
        # Count keyword frequency
        from collections import Counter
        import re

        all_words: Counter[str] = Counter()
        for q in vol_filtered["question"].to_list():
            words = set(re.findall(r"[a-z]+", q.lower()))
            words -= {"will", "the", "a", "an", "of", "in", "on", "to", "and", "or",
                       "be", "is", "it", "for", "at", "by", "as", "if", "no", "yes",
                       "not", "this", "that", "with", "from", "was", "are", "has",
                       "have", "its", "their", "which", "than", "s", "t"}
            all_words.update(words)

        # Test top-100 most frequent words
        top_words = [w for w, _ in all_words.most_common(150)]
        print(f"\n  Testing {len(top_words)} frequent keywords (min 30 signals)…")

        results = []
        for word in top_words:
            r = eval_config(base, label=word,
                            prefer_kw=frozenset({word}),
                            max_vol=1000, price_lo=0.15, price_hi=0.40)
            if r and r["n"] >= 30 and r["roi"] > 0:
                results.append(r)

        results.sort(key=lambda x: x["monthly_pnl"], reverse=True)
        print(f"\n  Profitable keywords (sorted by $/month):")
        print_header()
        for r in results[:30]:
            print_result(r)

        # Also test keywords NOT in current prod set that are profitable
        new_kw = [r for r in results if r["label"] not in prod_kw]
        if new_kw:
            print(f"\n  NEW keywords not in production set (sorted by $/month):")
            print_header()
            for r in new_kw[:20]:
                print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 8. EXPANDED KEYWORD SET: test combining new profitable keywords
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  8. EXPANDED KEYWORD SETS")
    print(f"{'=' * 110}")
    print_header()

    # Build expanded sets from mining results
    if results:
        new_profitable = frozenset(r["label"] for r in results[:20] if r["label"] not in prod_kw)
        expanded = prod_kw | new_profitable

        r = eval_config(base, label=f"expanded ({len(expanded)} kw) + vol<1K + 15-40%",
                        prefer_kw=expanded, max_vol=1000, price_lo=0.15, price_hi=0.40)
        if r:
            print_result(r)

        r = eval_config(base, label=f"expanded ({len(expanded)} kw) + vol<2K + 15-40%",
                        prefer_kw=expanded, max_vol=2000, price_lo=0.15, price_hi=0.40)
        if r:
            print_result(r)

        r = eval_config(base, label=f"expanded ({len(expanded)} kw) + vol<1K + 10-50%",
                        prefer_kw=expanded, max_vol=1000, price_lo=0.10, price_hi=0.50)
        if r:
            print_result(r)

        r = eval_config(base, label=f"expanded ({len(expanded)} kw) + vol<2K + 10-50%",
                        prefer_kw=expanded, max_vol=2000, price_lo=0.10, price_hi=0.50)
        if r:
            print_result(r)

        # Print the expanded keyword set for reference
        print(f"\n  Expanded keyword set: {sorted(expanded)}")

    # ══════════════════════════════════════════════════════════════════
    # 9. SUMMARY: rank all configs by monthly_pnl
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 110}")
    print("  9. RANKING: Top configs by monthly PnL (ROI > 0)")
    print(f"{'=' * 110}")

    # Re-run all configs and collect
    all_configs = [
        # (label, prefer_kw, max_vol, price_lo, price_hi, max_lockup)
        ("PRODUCTION", prod_kw, 1000, 0.15, 0.40, 0),
        ("prod_kw + vol<2K", prod_kw, 2000, 0.15, 0.40, 0),
        ("prod_kw + vol<5K", prod_kw, 5000, 0.15, 0.40, 0),
        ("prod_kw + vol<2K + 10-50%", prod_kw, 2000, 0.10, 0.50, 0),
        ("no kw + vol<1K + 15-40%", None, 1000, 0.15, 0.40, 0),
        ("no kw + vol<1K + 10-50%", None, 1000, 0.10, 0.50, 0),
        ("no kw + vol<500 + 15-40%", None, 500, 0.15, 0.40, 0),
        ("no kw + vol<500 + 10-50%", None, 500, 0.10, 0.50, 0),
        ("no kw + vol<1K + lockup<7d + 15-40%", None, 1000, 0.15, 0.40, 7),
        ("no kw + vol<2K + lockup<7d + 10-50%", None, 2000, 0.10, 0.50, 7),
        ("no kw + vol<5K + lockup<7d + 15-40%", None, 5000, 0.15, 0.40, 7),
        ("prod + lockup<7d", prod_kw, 1000, 0.15, 0.40, 7),
        ("prod + lockup<14d", prod_kw, 1000, 0.15, 0.40, 14),
    ]
    # Add expanded configs if available
    if results:
        all_configs.extend([
            (f"expanded + vol<1K + 15-40%", expanded, 1000, 0.15, 0.40, 0),
            (f"expanded + vol<2K + 10-50%", expanded, 2000, 0.10, 0.50, 0),
            (f"expanded + vol<1K + lockup<7d", expanded, 1000, 0.15, 0.40, 7),
        ])

    all_results = []
    for cfg_tuple in all_configs:
        lbl, kw, vol, lo, hi, max_ld = cfg_tuple
        r = eval_config(base, label=lbl, prefer_kw=kw, max_vol=vol,
                        price_lo=lo, price_hi=hi,
                        max_lockup=max_ld if max_ld > 0 else 0)
        if r and r["roi"] > 0:
            all_results.append(r)

    all_results.sort(key=lambda x: x["monthly_pnl"], reverse=True)
    print_header()
    for r in all_results:
        print_result(r)

    print(f"\n  Exploration complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
