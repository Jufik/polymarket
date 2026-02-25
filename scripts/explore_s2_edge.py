"""Explore where the S2 Will-NO edge actually lives.

Slices the full ClickHouse dataset across multiple dimensions:
  - Time period (monthly cohorts)
  - YES price bands
  - Question keywords (data-driven, not just research set)
  - Market volume
  - Resolution speed (lockup)
  - Category/tags
  - Combinations of filters

Goal: find subsets with HR > break-even threshold and positive ROI.

Usage:
    uv run python scripts/explore_s2_edge.py
"""

from __future__ import annotations

import asyncio
import io
import re
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"


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


# ── PnL computation ────────────────────────────────────────────────────


def add_pnl(df: pl.DataFrame) -> pl.DataFrame:
    """Add won/pnl columns. Expects: first_price, size_usd, winner_outcome, resolution_value."""
    resolved = df.filter(pl.col("resolution_value") == 1)
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


def summarize(df: pl.DataFrame, min_n: int = 20) -> dict | None:
    """Summarize a PnL DataFrame. Returns None if too few signals."""
    n = len(df)
    if n < min_n:
        return None
    wins = df.filter(pl.col("won")).shape[0]
    hr = wins / n
    total_pnl = df["pnl"].sum()
    total_bet = df["size_usd"].sum()
    roi = total_pnl / total_bet * 100 if total_bet > 0 else 0
    avg_pnl = total_pnl / n
    avg_price = df["first_price"].mean()
    # Break-even HR for this avg price
    be_hr = 1.0 / (1.0 + avg_price / (1.0 - avg_price)) if avg_price < 1.0 else 1.0
    return {
        "n": n, "wins": wins, "hr": hr, "be_hr": be_hr,
        "pnl": total_pnl, "roi": roi, "avg_pnl": avg_pnl,
        "avg_price": avg_price,
    }


def print_row(label: str, s: dict) -> None:
    edge = s["hr"] - s["be_hr"]
    marker = "**" if s["roi"] > 0 else "  "
    print(
        f"  {marker}{label:<40} {s['n']:>6,} signals  "
        f"{s['hr']:>5.1%} HR (BE={s['be_hr']:.0%})  "
        f"edge {edge:>+5.1%}  "
        f"ROI {s['roi']:>+6.1f}%  "
        f"PnL ${s['pnl']:>+9,.0f}  "
        f"$/bet ${s['avg_pnl']:>+6.2f}"
    )


# ── Main ────────────────────────────────────────────────────────────────


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    print("=" * 80)
    print("  S2 Will-NO Edge Exploration")
    print("=" * 80)

    # ── Fetch data ──────────────────────────────────────────────────────
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
               argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price,
               min(toUnixTimestamp(timestamp)) AS first_ts
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
               sum(CAST(price AS Float64) * CAST(size AS Float64)) AS total_volume,
               count() AS trade_count,
               uniq(maker) AS trader_count
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id
        """,
        label="volume",
    )
    for c in ["total_volume", "trade_count", "trader_count"]:
        total_volume = total_volume.with_columns(pl.col(c).cast(pl.Float64))

    events = await query_ch(
        client,
        """SELECT toString(id) AS event_id,
                  toUnixTimestamp(end_date) AS end_ts
           FROM events WHERE end_date IS NOT NULL""",
        label="events",
    )
    events = events.with_columns(pl.col("end_ts").cast(pl.Float64))

    tags = await query_ch(
        client,
        """SELECT toString(et.event_id) AS event_id, t.label AS tag
           FROM event_tags et JOIN tags t ON et.tag_id = t.id""",
        label="tags",
    )

    await client.aclose()

    # ── Build base table ────────────────────────────────────────────────
    print("\nBuilding base table …")

    base = markets.join(first_trades, on="condition_id", how="inner")
    base = base.join(total_volume, on="condition_id", how="left")
    base = base.join(
        events.rename({"event_id": "event_id_ev", "end_ts": "end_ts"}).with_columns(
            pl.col("event_id_ev").alias("event_id")
        ).select("event_id", "end_ts"),
        on="event_id", how="left",
    )
    base = base.with_columns(
        ((pl.col("end_ts") - pl.col("first_ts")) / 86400.0).alias("lockup_days"),
    )

    # Add tags (first tag per event)
    first_tag = tags.sort("tag").unique(subset=["event_id"], keep="first")
    base = base.join(first_tag, on="event_id", how="left")

    # Filter to YES 5-50% (broad range for exploration)
    base = base.filter(
        (pl.col("first_price") >= 0.05) & (pl.col("first_price") <= 0.50)
    )

    # Resolution data
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    base = base.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left",
    )

    # Add size_usd (flat $50 for exploration)
    base = base.with_columns(pl.lit(50.0).alias("size_usd"))

    # Add PnL
    base = add_pnl(base)
    print(f"  Resolved Will markets (YES 5-50%): {len(base):,}")

    # Add temporal columns
    base = base.with_columns(
        (pl.col("first_ts") / 86400.0).cast(pl.Int64).alias("day_epoch"),
    )
    base = base.with_columns(
        # Year-month from epoch day
        ((pl.col("first_ts")).cast(pl.Int64) * 1_000_000_000)
        .cast(pl.Datetime("ns"))
        .dt.strftime("%Y-%m")
        .alias("month"),
    )

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 1: Time period (monthly cohorts)
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  1. BY TIME PERIOD (monthly)")
    print(f"{'=' * 80}")

    months = sorted(base["month"].unique().to_list())
    profitable_months = []
    for m in months:
        subset = base.filter(pl.col("month") == m)
        s = summarize(subset, min_n=30)
        if s:
            print_row(m, s)
            if s["roi"] > 0:
                profitable_months.append(m)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 2: YES price bands (fine-grained 5% slices)
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  2. BY YES PRICE BAND (5% slices)")
    print(f"{'=' * 80}")

    for lo_pct in range(5, 50, 5):
        lo = lo_pct / 100.0
        hi = (lo_pct + 5) / 100.0
        subset = base.filter(
            (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
        )
        s = summarize(subset)
        if s:
            print_row(f"YES {lo_pct}-{lo_pct + 5}%", s)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 3: Keywords (data-driven top-50)
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  3. BY KEYWORD (data-driven, sorted by ROI)")
    print(f"{'=' * 80}")

    # Extract interesting words from questions
    all_questions = base["question"].to_list()
    word_counter: Counter[str] = Counter()
    for q in all_questions:
        words = set(re.findall(r"[a-zA-Z]+", q.lower()))
        # Skip very common words
        skip = {"will", "the", "a", "an", "in", "on", "at", "to", "of", "and", "or", "be", "is", "it", "for", "this", "that", "with", "from", "as", "not", "its", "are", "has", "have", "do", "does", "did", "was", "were", "been"}
        words -= skip
        for w in words:
            if len(w) >= 2:
                word_counter[w] += 1

    # Only words appearing in 20+ markets
    candidate_words = [w for w, c in word_counter.most_common(500) if c >= 20]

    keyword_results = []
    for word in candidate_words:
        subset = base.filter(
            pl.col("question").str.to_lowercase().str.contains(word)
        )
        s = summarize(subset, min_n=20)
        if s:
            keyword_results.append((word, s))

    # Sort by ROI descending
    keyword_results.sort(key=lambda x: x[1]["roi"], reverse=True)

    print("\n  Top 25 profitable keywords:")
    for word, s in keyword_results[:25]:
        print_row(f'"{word}"', s)

    print("\n  Bottom 10 keywords (worst ROI):")
    for word, s in keyword_results[-10:]:
        print_row(f'"{word}"', s)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 4: Market volume
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  4. BY MARKET VOLUME")
    print(f"{'=' * 80}")

    vol_brackets = [
        ("< $100", 0, 100),
        ("$100-$500", 100, 500),
        ("$500-$1K", 500, 1_000),
        ("$1K-$5K", 1_000, 5_000),
        ("$5K-$10K", 5_000, 10_000),
        ("$10K-$50K", 10_000, 50_000),
        ("$50K-$100K", 50_000, 100_000),
        ("$100K+", 100_000, 1e12),
    ]
    for label, lo, hi in vol_brackets:
        subset = base.filter(
            (pl.col("total_volume") >= lo) & (pl.col("total_volume") < hi)
        )
        s = summarize(subset)
        if s:
            print_row(f"vol {label}", s)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 5: Resolution speed (lockup)
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  5. BY LOCKUP (resolution speed)")
    print(f"{'=' * 80}")

    lockup_brackets = [
        ("< 1 day", 0, 1),
        ("1-3 days", 1, 3),
        ("3-7 days", 3, 7),
        ("7-14 days", 7, 14),
        ("14-30 days", 14, 30),
        ("30-90 days", 30, 90),
        ("90+ days", 90, 1e6),
    ]
    for label, lo, hi in lockup_brackets:
        subset = base.filter(
            (pl.col("lockup_days") >= lo) & (pl.col("lockup_days") < hi)
        )
        s = summarize(subset)
        if s:
            print_row(f"lockup {label}", s)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 6: Tags/categories
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  6. BY TAG/CATEGORY")
    print(f"{'=' * 80}")

    tag_values = base["tag"].drop_nulls().unique().to_list()
    tag_results = []
    for t in tag_values:
        subset = base.filter(pl.col("tag") == t)
        s = summarize(subset, min_n=30)
        if s:
            tag_results.append((t, s))
    tag_results.sort(key=lambda x: x[1]["roi"], reverse=True)
    for t, s in tag_results[:20]:
        print_row(t, s)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 7: Trader count (proxy for market attention)
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  7. BY TRADER COUNT")
    print(f"{'=' * 80}")

    trader_brackets = [
        ("1-5 traders", 1, 5),
        ("5-10 traders", 5, 10),
        ("10-25 traders", 10, 25),
        ("25-50 traders", 25, 50),
        ("50-100 traders", 50, 100),
        ("100+ traders", 100, 1e6),
    ]
    for label, lo, hi in trader_brackets:
        subset = base.filter(
            (pl.col("trader_count") >= lo) & (pl.col("trader_count") < hi)
        )
        s = summarize(subset)
        if s:
            print_row(f"{label}", s)

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 8: Combined filters — search for profitable combos
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  8. COMBINED FILTERS — searching for profitable subsets")
    print(f"{'=' * 80}")

    # Use the top profitable keywords × volume × price combinations
    profitable_kws = [w for w, s in keyword_results if s["roi"] > 0 and s["n"] >= 30][:15]

    print(f"\n  Scanning {len(profitable_kws)} profitable keywords × volume × price …")

    combo_results = []

    for kw in profitable_kws:
        kw_df = base.filter(
            pl.col("question").str.to_lowercase().str.contains(kw)
        )
        # × volume brackets
        for vol_label, vol_lo, vol_hi in [
            ("vol<1K", 0, 1_000),
            ("vol<5K", 0, 5_000),
            ("vol<10K", 0, 10_000),
            ("vol<50K", 0, 50_000),
            ("any vol", 0, 1e12),
        ]:
            vol_df = kw_df.filter(
                (pl.col("total_volume") >= vol_lo) & (pl.col("total_volume") < vol_hi)
            )
            # × price bands
            for price_label, p_lo, p_hi in [
                ("YES 5-25%", 0.05, 0.25),
                ("YES 10-30%", 0.10, 0.30),
                ("YES 15-30%", 0.15, 0.30),
                ("YES 15-40%", 0.15, 0.40),
                ("YES 5-50%", 0.05, 0.50),
            ]:
                subset = vol_df.filter(
                    (pl.col("first_price") >= p_lo) & (pl.col("first_price") < p_hi)
                )
                s = summarize(subset, min_n=20)
                if s and s["roi"] > 0:
                    combo_label = f'"{kw}" + {vol_label} + {price_label}'
                    combo_results.append((combo_label, s))

    # Also try time-windowed combos (recent only)
    recent_months = [m for m in months if m >= "2025-01"]
    if recent_months:
        recent = base.filter(pl.col("month").is_in(recent_months))
        for vol_label, vol_lo, vol_hi in [
            ("vol<1K", 0, 1_000),
            ("vol<5K", 0, 5_000),
            ("vol<10K", 0, 10_000),
            ("any vol", 0, 1e12),
        ]:
            vol_df = recent.filter(
                (pl.col("total_volume") >= vol_lo) & (pl.col("total_volume") < vol_hi)
            )
            for price_label, p_lo, p_hi in [
                ("YES 5-25%", 0.05, 0.25),
                ("YES 10-30%", 0.10, 0.30),
                ("YES 15-30%", 0.15, 0.30),
                ("YES 15-40%", 0.15, 0.40),
            ]:
                subset = vol_df.filter(
                    (pl.col("first_price") >= p_lo) & (pl.col("first_price") < p_hi)
                )
                s = summarize(subset, min_n=20)
                if s and s["roi"] > 0:
                    combo_label = f"2025+ + {vol_label} + {price_label}"
                    combo_results.append((combo_label, s))

        # 2025+ × keywords
        for kw in profitable_kws[:8]:
            kw_df = recent.filter(
                pl.col("question").str.to_lowercase().str.contains(kw)
            )
            for vol_label, vol_lo, vol_hi in [
                ("vol<5K", 0, 5_000),
                ("vol<10K", 0, 10_000),
                ("any vol", 0, 1e12),
            ]:
                subset = kw_df.filter(
                    (pl.col("total_volume") >= vol_lo) & (pl.col("total_volume") < vol_hi)
                )
                s = summarize(subset, min_n=20)
                if s and s["roi"] > 0:
                    combo_label = f'2025+ + "{kw}" + {vol_label}'
                    combo_results.append((combo_label, s))

    # Sort by ROI, then by PnL
    combo_results.sort(key=lambda x: (x[1]["roi"], x[1]["pnl"]), reverse=True)

    # Deduplicate by keeping best ROI for similar combos
    seen_labels = set()
    unique_combos = []
    for label, s in combo_results:
        if label not in seen_labels:
            seen_labels.add(label)
            unique_combos.append((label, s))

    if unique_combos:
        print(f"\n  Found {len(unique_combos)} profitable combos. Top 30:")
        for label, s in unique_combos[:30]:
            print_row(label, s)
    else:
        print("\n  No profitable combinations found.")

    # ════════════════════════════════════════════════════════════════════
    # DIMENSION 9: YES price at different time windows
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  9. BY PERIOD × YES PRICE (where did the research edge live?)")
    print(f"{'=' * 80}")

    periods = [
        ("2023", "2023-01", "2023-12"),
        ("2024-H1", "2024-01", "2024-06"),
        ("2024-H2", "2024-07", "2024-12"),
        ("2025-Q1", "2025-01", "2025-03"),
        ("2025-Q2+", "2025-04", "2025-12"),
        ("2026", "2026-01", "2026-12"),
    ]
    for period_label, m_lo, m_hi in periods:
        period_df = base.filter(
            (pl.col("month") >= m_lo) & (pl.col("month") <= m_hi)
        )
        if len(period_df) < 20:
            continue
        # 15-30% band (research sweet spot)
        subset = period_df.filter(
            (pl.col("first_price") >= 0.15) & (pl.col("first_price") < 0.30)
        )
        s = summarize(subset, min_n=20)
        if s:
            print_row(f"{period_label} YES 15-30%", s)
        # 5-15% band (high HR but low payoff)
        subset2 = period_df.filter(
            (pl.col("first_price") >= 0.05) & (pl.col("first_price") < 0.15)
        )
        s2 = summarize(subset2, min_n=20)
        if s2:
            print_row(f"{period_label} YES 5-15%", s2)

    # ════════════════════════════════════════════════════════════════════
    # Summary: where is the edge?
    # ════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("  SUMMARY: Strongest profitable signals")
    print(f"{'=' * 80}")

    # Collect all profitable results across all dimensions
    all_profitable = []

    # Keywords
    for word, s in keyword_results:
        if s["roi"] > 0 and s["n"] >= 30:
            all_profitable.append((f'keyword: "{word}"', s))

    # Tags
    for t, s in tag_results:
        if s["roi"] > 0 and s["n"] >= 30:
            all_profitable.append((f"tag: {t}", s))

    # Combos
    for label, s in unique_combos:
        if s["n"] >= 30:
            all_profitable.append((f"combo: {label}", s))

    # Time periods
    for m in profitable_months:
        subset = base.filter(pl.col("month") == m)
        s = summarize(subset)
        if s and s["roi"] > 0 and s["n"] >= 30:
            all_profitable.append((f"month: {m}", s))

    # Sort by total PnL
    all_profitable.sort(key=lambda x: x[1]["pnl"], reverse=True)

    if all_profitable:
        print(f"\n  Sorted by total PnL (min 30 signals):")
        for label, s in all_profitable[:40]:
            print_row(label, s)
    else:
        print("\n  No profitable subsets with 30+ signals found.")

    print(f"\n  Exploration complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
