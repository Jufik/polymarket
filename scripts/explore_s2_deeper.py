"""Deeper edge exploration for S2 — beyond current production filters.

Starting from the winning config (prod_kw + vol<$2K + 10-50%, +15.1% ROI),
this script explores dimensions NOT yet tested:

  1. Beyond "Will" — do other question patterns carry NO bias?
  2. Avoid keyword mining — systematically find more toxic keywords
  3. Category / tag-based filtering
  4. neg_risk markets — different edge profile?
  5. Price above 50% — contrarian NO on "likely YES" markets
  6. Event-level signals — events.volume vs market volume
  7. Market age at first trade — freshness effect?
  8. Multi-market events vs single-market events
  9. Rolling stability of the expanded config
 10. Optimal band multiplier calibration

Usage:
    uv run python scripts/explore_s2_deeper.py
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
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.will_no.config import (
    DEFAULT_PRICE_BANDS,
    WillNoConfig,
)

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
N_MONTHS = 18.0


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
    """Add won/pnl columns for resolved markets with band-adjusted sizing."""
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


def add_band_sizing(
    df: pl.DataFrame,
    base_bet: float = 50.0,
    bands: tuple[tuple[float, float, float], ...] | None = None,
) -> pl.DataFrame:
    """Add size_usd column based on price bands."""
    if bands is None:
        bands = DEFAULT_PRICE_BANDS
    n = len(bands)
    size_expr = pl.lit(base_bet)  # fallback
    for i, (lo, hi, mult) in enumerate(bands):
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
    price_lo: float = 0.10,
    price_hi: float = 0.50,
    max_lockup: float = 0,
    fee: float = 0.02,
    question_pattern: str | None = None,
    bands: tuple[tuple[float, float, float], ...] | None = None,
    min_signals: int = 20,
) -> dict | None:
    """Evaluate a filter configuration and return capital-adjusted metrics."""
    q_lower = pl.col("question").str.to_lowercase()
    df = base.filter(
        (pl.col("first_price") >= price_lo) & (pl.col("first_price") <= price_hi)
    )

    if question_pattern is not None:
        df = df.filter(pl.col("question").str.contains(question_pattern))

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

    df = add_band_sizing(df, bands=bands)
    df = add_pnl(df, fee=fee)
    if df.is_empty():
        return None

    n = len(df)
    if n < min_signals:
        return None

    wins = df.filter(pl.col("won")).height
    hr = wins / n
    total_pnl = df["pnl"].sum()
    total_bet = df["size_usd"].sum()
    roi = total_pnl / total_bet if total_bet > 0 else 0
    avg_bet = total_bet / n

    ld = df.filter(pl.col("lockup_days").is_not_null())["lockup_days"]
    med_lockup = ld.median() if len(ld) > 0 else None

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
        "capital": capital,
        "mo_roi_capital": mo_roi_capital,
    }


def print_header() -> None:
    print(
        f"  {'Config':<48} {'Sigs':>5} {'S/mo':>5} {'HR':>6} "
        f"{'ROI':>7} {'$/mo':>7} {'Med LD':>6} {'Cap$':>6} {'MoROI%':>7}"
    )
    print(
        f"  {'-' * 48} {'-' * 5} {'-' * 5} {'-' * 6} "
        f"{'-' * 7} {'-' * 7} {'-' * 6} {'-' * 6} {'-' * 7}"
    )


def print_result(r: dict) -> None:
    cap_str = f"${r['capital']:>5,.0f}" if r["capital"] else "    —"
    morc_str = f"{r['mo_roi_capital']:>6.0%}" if r["mo_roi_capital"] else "     —"
    ld_str = f"{r['med_lockup']:>5.1f}d" if r["med_lockup"] else "    —"
    marker = "+" if r["roi"] > 0 else "-"
    print(
        f" {marker} {r['label']:<48} {r['n']:>5,} {r['sigs_mo']:>5.0f} "
        f"{r['hr']:>5.1%} {r['roi']:>+6.1%} "
        f"${r['monthly_pnl']:>6,.0f} {ld_str} {cap_str} {morc_str}"
    )


def section(num: int, title: str) -> None:
    print(f"\n{'=' * 120}")
    print(f"  {num}. {title}")
    print(f"{'=' * 120}")


# ── Main ──────────────────────────────────────────────────────────────


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(
        base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0
    )

    print("=" * 120)
    print("  S2 Will-NO: DEEPER Edge Exploration")
    print("=" * 120)

    # ── Fetch data (ALL markets, not just "Will") ─────────────────────
    print("\nFetching data (all markets) …")

    all_markets = await query_ch(
        client,
        """SELECT condition_id, toString(event_id) AS event_id, question,
                  category, neg_risk,
                  toUnixTimestamp(created_at) AS created_ts
           FROM markets""",
        label="all-markets",
    )

    first_trades = await query_ch(
        client,
        """SELECT condition_id,
                  argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price,
                  min(toUnixTimestamp(timestamp)) AS first_ts
           FROM (SELECT * FROM polymarket.trades_raw FINAL) t
           GROUP BY condition_id""",
        label="first-trades",
    )
    for c in ["first_price", "first_ts"]:
        first_trades = first_trades.with_columns(pl.col(c).cast(pl.Float64))

    total_volume = await query_ch(
        client,
        """SELECT condition_id,
                  sum(CAST(price AS Float64) * CAST(size AS Float64)) AS total_volume
           FROM (SELECT * FROM polymarket.trades_raw FINAL) t
           GROUP BY condition_id""",
        label="volume",
    )
    total_volume = total_volume.with_columns(
        pl.col("total_volume").cast(pl.Float64)
    )

    trade_counts = await query_ch(
        client,
        """SELECT condition_id, count() AS n_trades
           FROM (SELECT * FROM polymarket.trades_raw FINAL) t
           GROUP BY condition_id""",
        label="trade-counts",
    )

    events = await query_ch(
        client,
        """SELECT toString(id) AS event_id,
                  dateDiff('second', toDateTime64('1970-01-01', 3), end_date) AS end_ts,
                  volume AS event_volume,
                  liquidity AS event_liquidity
           FROM events WHERE end_date IS NOT NULL""",
        label="events",
    )
    for c in ["end_ts", "event_volume", "event_liquidity"]:
        events = events.with_columns(pl.col(c).cast(pl.Float64))

    # Markets-per-event count
    mkt_per_event = await query_ch(
        client,
        """SELECT toString(event_id) AS event_id, count() AS markets_in_event
           FROM markets WHERE event_id IS NOT NULL
           GROUP BY event_id""",
        label="mkts-per-event",
    )

    # Tags
    event_tags = await query_ch(
        client,
        """SELECT toString(et.event_id) AS event_id, t.label AS tag
           FROM event_tags et JOIN tags t ON et.tag_id = t.id""",
        label="tags",
    )

    await client.aclose()

    # ── Build base table ──────────────────────────────────────────────
    print("\nBuilding base table …")

    base = all_markets.join(first_trades, on="condition_id", how="inner")
    base = base.join(total_volume, on="condition_id", how="left")
    base = base.join(trade_counts, on="condition_id", how="left")
    base = base.join(
        events.select("event_id", "end_ts", "event_volume", "event_liquidity"),
        on="event_id", how="left",
    )
    base = base.join(mkt_per_event, on="event_id", how="left")

    # Lockup + market age
    base = base.with_columns([
        ((pl.col("end_ts") - pl.col("first_ts")) / 86400.0).alias("lockup_days"),
        ((pl.col("first_ts") - pl.col("created_ts").cast(pl.Float64)) / 3600.0)
        .alias("market_age_hours"),
    ])

    # Resolution
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    base = base.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left",
    )

    # Classify question pattern
    base = base.with_columns(
        pl.when(pl.col("question").str.contains(r"(?i)^Will\b"))
        .then(pl.lit("Will"))
        .when(pl.col("question").str.to_lowercase().str.contains("up or down"))
        .then(pl.lit("UpOrDown"))
        .when(pl.col("question").str.to_lowercase().str.contains(r"o/u "))
        .then(pl.lit("O/U"))
        .when(pl.col("question").str.to_lowercase().str.contains("over/under"))
        .then(pl.lit("OverUnder"))
        .when(pl.col("question").str.to_lowercase().str.contains("spread"))
        .then(pl.lit("Spread"))
        .when(pl.col("question").str.to_lowercase().str.contains("handicap"))
        .then(pl.lit("Handicap"))
        .when(pl.col("question").str.to_lowercase().str.contains(r"to win"))
        .then(pl.lit("ToWin"))
        .when(pl.col("question").str.to_lowercase().str.contains(r" vs\.? "))
        .then(pl.lit("VS"))
        .otherwise(pl.lit("Other"))
        .alias("q_pattern"),
    )

    will_base = base.filter(pl.col("q_pattern") == "Will")
    n_total = len(base)
    n_will = len(will_base)
    n_resolved = base.filter(pl.col("resolution_value") == 1).height
    print(f"  All markets with trades: {n_total:,}")
    print(f"  Will markets: {n_will:,}")
    print(f"  Resolved (all): {n_resolved:,}")

    prod_kw = frozenset({
        "between", "mlb", "prix", "grand", "league", "park", "traded", "fed",
    })

    # ══════════════════════════════════════════════════════════════════
    # 0. BASELINE: Current production expanded config
    # ══════════════════════════════════════════════════════════════════
    section(0, "BASELINE (current production expanded config)")
    print_header()
    baseline = eval_config(
        will_base, label="prod_kw + vol<2K + 10-50% (CURRENT)",
        prefer_kw=prod_kw, max_vol=2000, price_lo=0.10, price_hi=0.50,
    )
    if baseline:
        print_result(baseline)

    # ══════════════════════════════════════════════════════════════════
    # 1. BEYOND "WILL" — NO base rate by question pattern
    # ══════════════════════════════════════════════════════════════════
    section(1, "BEYOND 'WILL' — NO base rate by question pattern")

    resolved_all = base.filter(pl.col("resolution_value") == 1)
    print(f"\n  NO base rate by question pattern (resolved markets):\n")
    print(f"  {'Pattern':<15} {'Total':>8} {'NO wins':>8} {'NO %':>6} {'YES %':>6}")
    print(f"  {'-'*15} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")

    for pattern in ["Will", "UpOrDown", "O/U", "OverUnder", "Spread",
                     "Handicap", "ToWin", "VS", "Other"]:
        subset = resolved_all.filter(pl.col("q_pattern") == pattern)
        if len(subset) < 50:
            continue
        no_wins = subset.filter(pl.col("winner_outcome") == "No").height
        total = len(subset)
        no_pct = no_wins / total * 100
        print(
            f"  {pattern:<15} {total:>8,} {no_wins:>8,} "
            f"{no_pct:>5.1f}% {100-no_pct:>5.1f}%"
        )

    # Test NO strategy on non-Will patterns
    print(f"\n  NO strategy performance by question pattern (vol<$2K, 10-50%):")
    print_header()
    for pattern in ["Will", "UpOrDown", "O/U", "Spread", "Handicap",
                     "ToWin", "VS", "Other"]:
        subset = base.filter(pl.col("q_pattern") == pattern)
        if len(subset) < 50:
            continue
        r = eval_config(
            subset, label=f"{pattern} (no kw filter, vol<2K)",
            max_vol=2000, price_lo=0.10, price_hi=0.50,
            avoid_kw=frozenset(), min_signals=30,
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 2. AVOID KEYWORD MINING — find more toxic keywords to filter out
    # ══════════════════════════════════════════════════════════════════
    section(2, "AVOID KEYWORD MINING — find toxic keywords to exclude")

    # Get current production universe (Will + prod_kw + vol<2K + 10-50%)
    prod_universe = will_base.filter(
        (pl.col("first_price") >= 0.10) & (pl.col("first_price") <= 0.50)
        & (pl.col("total_volume") <= 2000)
    )
    q_lower_col = pl.col("question").str.to_lowercase()
    kw_cond = pl.lit(False)
    for kw in prod_kw:
        kw_cond = kw_cond | q_lower_col.str.contains(kw.lower())
    prod_universe = prod_universe.filter(kw_cond)
    prod_universe = add_band_sizing(prod_universe)
    prod_universe = add_pnl(prod_universe)

    if not prod_universe.is_empty():
        # Extract all words
        all_words: Counter[str] = Counter()
        stop = {
            "will", "the", "a", "an", "of", "in", "on", "to", "and", "or",
            "be", "is", "it", "for", "at", "by", "as", "if", "no", "yes",
            "not", "this", "that", "with", "from", "was", "are", "has",
            "have", "its", "their", "which", "than", "s", "t", "end",
            "between", "match", "draw", "game",
        }
        for q in prod_universe["question"].to_list():
            words = set(re.findall(r"[a-z]+", q.lower())) - stop
            all_words.update(words)

        print(f"\n  Testing {min(len(all_words), 200)} frequent words within production universe …")
        print(f"  Looking for keywords where ROI < 0 (should be avoided):\n")
        print(f"  {'Keyword':<20} {'Sigs':>5} {'HR':>6} {'ROI':>7} {'$/mo':>7}")
        print(f"  {'-'*20} {'-'*5} {'-'*6} {'-'*7} {'-'*7}")

        toxic_results = []
        for word, _count in all_words.most_common(200):
            if _count < 10:
                continue
            # Test: prod config with this specific keyword
            subset = prod_universe.filter(q_lower_col.str.contains(word))
            if len(subset) < 10:
                continue
            resolved_sub = subset.filter(pl.col("resolution_value") == 1)
            if len(resolved_sub) < 10:
                continue
            wins = resolved_sub.filter(pl.col("won")).height
            total = len(resolved_sub)
            hr = wins / total
            total_pnl = resolved_sub["pnl"].sum()
            total_bet = resolved_sub["size_usd"].sum()
            roi = total_pnl / total_bet if total_bet > 0 else 0
            sigs_mo = total / N_MONTHS
            monthly_pnl = sigs_mo * (total_bet / total) * roi

            if roi < -0.05:  # Negative edge keywords
                toxic_results.append({
                    "word": word, "n": total, "hr": hr, "roi": roi,
                    "monthly_pnl": monthly_pnl,
                })

        toxic_results.sort(key=lambda x: x["roi"])
        for r in toxic_results[:25]:
            print(
                f"  {r['word']:<20} {r['n']:>5} {r['hr']:>5.1%} "
                f"{r['roi']:>+6.1%} ${r['monthly_pnl']:>6,.0f}"
            )

        # Test impact of adding top toxic keywords to avoid list
        if toxic_results:
            print(f"\n  Impact of expanded avoid list:")
            print_header()

            # Current avoid
            r = eval_config(
                will_base, label="current avoid (reach,hit)",
                prefer_kw=prod_kw, max_vol=2000,
                avoid_kw=frozenset({"reach", "hit"}),
            )
            if r:
                print_result(r)

            # Expanded avoids
            for n_avoids in [3, 5, 8, 12]:
                new_avoids = frozenset(
                    {x["word"] for x in toxic_results[:n_avoids]}
                ) | {"reach", "hit"}
                r = eval_config(
                    will_base,
                    label=f"avoid top-{n_avoids} toxic + reach,hit ({len(new_avoids)} total)",
                    prefer_kw=prod_kw, max_vol=2000,
                    avoid_kw=new_avoids,
                )
                if r:
                    print_result(r)

            # Print the recommended expanded avoid list
            top_toxic = [x["word"] for x in toxic_results[:8]]
            print(f"\n  Recommended expanded avoid keywords: {top_toxic}")

    # ══════════════════════════════════════════════════════════════════
    # 3. TAG-BASED FILTERING (using event_tags)
    # ══════════════════════════════════════════════════════════════════
    section(3, "TAG-BASED FILTERING — edge by event tag")

    will_tagged = will_base.join(event_tags, on="event_id", how="inner")
    will_tagged = add_band_sizing(will_tagged)
    will_tagged_pnl = add_pnl(will_tagged)

    if not will_tagged_pnl.is_empty():
        tag_stats = (
            will_tagged_pnl
            .group_by("tag")
            .agg([
                pl.count().alias("n"),
                pl.col("won").sum().alias("wins"),
                pl.col("pnl").sum().alias("total_pnl"),
                pl.col("size_usd").sum().alias("total_bet"),
            ])
            .filter(pl.col("n") >= 20)
            .with_columns(
                (pl.col("wins") / pl.col("n")).alias("hr"),
                (pl.col("total_pnl") / pl.col("total_bet")).alias("roi"),
                (pl.col("total_pnl") / N_MONTHS).alias("monthly_pnl"),
            )
            .sort("monthly_pnl", descending=True)
        )

        print(f"\n  Top tags by monthly PnL (NO strategy, all Will, min 20 sigs):\n")
        print(f"  {'Tag':<40} {'Sigs':>5} {'HR':>6} {'ROI':>7} {'$/mo':>7}")
        print(f"  {'-'*40} {'-'*5} {'-'*6} {'-'*7} {'-'*7}")

        for row in tag_stats.head(30).iter_rows(named=True):
            marker = "+" if row["roi"] > 0 else "-"
            print(
                f" {marker} {row['tag']:<40} {row['n']:>5} "
                f"{row['hr']:>5.1%} {row['roi']:>+6.1%} "
                f"${row['monthly_pnl']:>6,.0f}"
            )

        # Bottom tags (negative edge)
        print(f"\n  Bottom tags (negative edge, avoid):\n")
        print(f"  {'Tag':<40} {'Sigs':>5} {'HR':>6} {'ROI':>7} {'$/mo':>7}")
        print(f"  {'-'*40} {'-'*5} {'-'*6} {'-'*7} {'-'*7}")
        for row in tag_stats.sort("roi").head(20).iter_rows(named=True):
            if row["roi"] >= 0:
                break
            print(
                f" - {row['tag']:<40} {row['n']:>5} "
                f"{row['hr']:>5.1%} {row['roi']:>+6.1%} "
                f"${row['monthly_pnl']:>6,.0f}"
            )

        # Test tag-based filter (include only profitable tags)
        print(f"\n  Tag-based filtering vs keyword-based:")
        print_header()

        profit_tags = set(
            tag_stats.filter(pl.col("roi") > 0.05)["tag"].to_list()
        )
        loss_tags = set(
            tag_stats.filter(pl.col("roi") < -0.05)["tag"].to_list()
        )
        if profit_tags:
            # Filter Will markets to only those with profitable tags
            profit_event_ids = set(
                event_tags
                .filter(pl.col("tag").is_in(list(profit_tags)))["event_id"]
                .to_list()
            )
            will_profitable_tags = will_base.filter(
                pl.col("event_id").is_in(list(profit_event_ids))
            )
            r = eval_config(
                will_profitable_tags,
                label=f"profit tags ({len(profit_tags)}) + vol<2K + 10-50%",
                max_vol=2000,
                avoid_kw=frozenset({"reach", "hit"}),
            )
            if r:
                print_result(r)

        if loss_tags:
            # Filter out events with loss tags
            loss_event_ids = set(
                event_tags
                .filter(pl.col("tag").is_in(list(loss_tags)))["event_id"]
                .to_list()
            )
            will_no_loss_tags = will_base.filter(
                ~pl.col("event_id").is_in(list(loss_event_ids))
            )
            r = eval_config(
                will_no_loss_tags,
                label=f"exclude loss tags ({len(loss_tags)}) + prod_kw + vol<2K",
                prefer_kw=prod_kw, max_vol=2000,
                avoid_kw=frozenset({"reach", "hit"}),
            )
            if r:
                print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 4. NEG_RISK MARKETS — different edge profile?
    # ══════════════════════════════════════════════════════════════════
    section(4, "NEG_RISK MARKETS — split by neg_risk flag")
    print_header()

    for neg_risk_val in [True, False]:
        label_nr = "neg_risk=TRUE" if neg_risk_val else "neg_risk=FALSE"
        subset = will_base.filter(pl.col("neg_risk") == neg_risk_val)
        r = eval_config(
            subset, label=f"{label_nr} + prod_kw + vol<2K + 10-50%",
            prefer_kw=prod_kw, max_vol=2000,
        )
        if r:
            print_result(r)

    for neg_risk_val in [True, False]:
        label_nr = "neg_risk=TRUE" if neg_risk_val else "neg_risk=FALSE"
        subset = will_base.filter(pl.col("neg_risk") == neg_risk_val)
        r = eval_config(
            subset, label=f"{label_nr} + no kw + vol<2K + 10-50%",
            max_vol=2000, avoid_kw=frozenset({"reach", "hit"}),
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 5. PRICE ABOVE 50% — contrarian NO on "likely YES" markets
    # ══════════════════════════════════════════════════════════════════
    section(5, "PRICE ABOVE 50% — contrarian NO on likely YES")
    print_header()

    high_price_ranges = [
        ("50-55%", 0.50, 0.55),
        ("50-60%", 0.50, 0.60),
        ("55-65%", 0.55, 0.65),
        ("50-70%", 0.50, 0.70),
        ("60-75%", 0.60, 0.75),
        ("50-90%", 0.50, 0.90),
    ]
    for pr_label, lo, hi in high_price_ranges:
        # With keywords
        r = eval_config(
            will_base, label=f"prod_kw + vol<2K + {pr_label}",
            prefer_kw=prod_kw, max_vol=2000, price_lo=lo, price_hi=hi,
        )
        if r:
            print_result(r)

    print()
    for pr_label, lo, hi in high_price_ranges:
        # Without keywords
        r = eval_config(
            will_base, label=f"no kw + vol<2K + {pr_label}",
            max_vol=2000, price_lo=lo, price_hi=hi,
            avoid_kw=frozenset({"reach", "hit"}),
        )
        if r:
            print_result(r)

    # Combined: full range 10-60%, 10-70%
    print(f"\n  Extended price ranges (10-X%):")
    print_header()
    for hi in [0.55, 0.60, 0.65, 0.70]:
        r = eval_config(
            will_base, label=f"prod_kw + vol<2K + 10-{int(hi*100)}%",
            prefer_kw=prod_kw, max_vol=2000, price_lo=0.10, price_hi=hi,
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 6. EVENT VOLUME vs MARKET VOLUME — which filter works better?
    # ══════════════════════════════════════════════════════════════════
    section(6, "EVENT VOLUME (Gamma) vs MARKET VOLUME (trades) as filter")

    # Test event_volume as filter instead of market volume
    print(f"\n  Event volume filter (Gamma API volume):")
    print_header()

    for ev_cap in [1000, 2000, 5000, 10000, 50000]:
        subset = will_base.filter(
            pl.col("event_volume").is_not_null()
            & (pl.col("event_volume") <= ev_cap)
        )
        r = eval_config(
            subset, label=f"prod_kw + event_vol<${ev_cap:,} + 10-50%",
            prefer_kw=prod_kw,
        )
        if r:
            print_result(r)

    # Event liquidity
    print(f"\n  Event liquidity filter:")
    print_header()
    for liq_cap in [500, 1000, 5000, 10000]:
        subset = will_base.filter(
            pl.col("event_liquidity").is_not_null()
            & (pl.col("event_liquidity") <= liq_cap)
        )
        r = eval_config(
            subset, label=f"prod_kw + event_liq<${liq_cap:,} + 10-50%",
            prefer_kw=prod_kw,
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 7. MARKET AGE — does freshness matter?
    # ══════════════════════════════════════════════════════════════════
    section(7, "MARKET AGE AT FIRST TRADE — freshness effect")
    print_header()

    age_brackets = [
        ("age<1h", 0, 1),
        ("age<6h", 0, 6),
        ("age<24h", 0, 24),
        ("age<48h", 0, 48),
        ("age 1-24h", 1, 24),
        ("age 24-168h (1-7d)", 24, 168),
        ("age>24h", 24, 999999),
        ("age>168h (>7d)", 168, 999999),
    ]
    for age_label, lo_h, hi_h in age_brackets:
        subset = will_base.filter(
            pl.col("market_age_hours").is_not_null()
            & (pl.col("market_age_hours") >= lo_h)
            & (pl.col("market_age_hours") < hi_h)
        )
        r = eval_config(
            subset, label=f"prod_kw + vol<2K + 10-50% + {age_label}",
            prefer_kw=prod_kw, max_vol=2000,
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 8. MULTI-MARKET EVENTS — single vs multi-market events
    # ══════════════════════════════════════════════════════════════════
    section(8, "MULTI-MARKET EVENTS — event structure effect")
    print_header()

    mkt_count_brackets = [
        ("1 mkt/event", 1, 1),
        ("2-5 mkts/event", 2, 5),
        ("6-20 mkts/event", 6, 20),
        ("21-50 mkts/event", 21, 50),
        ("51+ mkts/event", 51, 99999),
    ]
    for mc_label, lo_mc, hi_mc in mkt_count_brackets:
        subset = will_base.filter(
            pl.col("markets_in_event").is_not_null()
            & (pl.col("markets_in_event") >= lo_mc)
            & (pl.col("markets_in_event") <= hi_mc)
        )
        r = eval_config(
            subset, label=f"prod_kw + vol<2K + 10-50% + {mc_label}",
            prefer_kw=prod_kw, max_vol=2000,
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 9. ROLLING STABILITY of expanded config
    # ══════════════════════════════════════════════════════════════════
    section(9, "ROLLING STABILITY — expanded config rolling 3-month windows")

    # Build expanded config dataset with timestamps
    expanded_ds = will_base.filter(
        (pl.col("first_price") >= 0.10) & (pl.col("first_price") <= 0.50)
        & (pl.col("total_volume") <= 2000)
    )
    kw_cond2 = pl.lit(False)
    for kw in prod_kw:
        kw_cond2 = kw_cond2 | pl.col("question").str.to_lowercase().str.contains(kw)
    expanded_ds = expanded_ds.filter(kw_cond2)
    for kw in ["reach", "hit"]:
        expanded_ds = expanded_ds.filter(
            ~pl.col("question").str.to_lowercase().str.contains(kw)
        )
    expanded_ds = add_band_sizing(expanded_ds)
    expanded_ds = add_pnl(expanded_ds)

    if not expanded_ds.is_empty():
        expanded_ds = expanded_ds.with_columns(
            pl.from_epoch(pl.col("first_ts").cast(pl.Int64), time_unit="s")
            .alias("trade_date")
        )
        expanded_ds = expanded_ds.with_columns(
            pl.col("trade_date").dt.strftime("%Y-%m").alias("month"),
        )

        months = sorted(expanded_ds["month"].unique().to_list())
        print(f"\n  Rolling 3-month windows (expanded config, {len(months)} months):\n")
        print(f"  {'Window':<20} {'Sigs':>5} {'HR':>6} {'ROI':>7} {'PnL':>8}")
        print(f"  {'-'*20} {'-'*5} {'-'*6} {'-'*7} {'-'*8}")

        win_count = 0
        loss_count = 0
        for i in range(len(months) - 2):
            window_months = months[i : i + 3]
            window = expanded_ds.filter(pl.col("month").is_in(window_months))
            if len(window) < 5:
                continue
            wins_w = window.filter(pl.col("won")).height
            total_w = len(window)
            hr_w = wins_w / total_w
            pnl_w = window["pnl"].sum()
            bet_w = window["size_usd"].sum()
            roi_w = pnl_w / bet_w if bet_w > 0 else 0
            marker = "+" if roi_w > 0 else "-"
            if roi_w > 0:
                win_count += 1
            else:
                loss_count += 1
            print(
                f" {marker} {window_months[0]}..{window_months[2]:<8} "
                f"{total_w:>5} {hr_w:>5.1%} {roi_w:>+6.1%} "
                f"${pnl_w:>+7,.0f}"
            )

        total_windows = win_count + loss_count
        print(
            f"\n  {win_count}/{total_windows} windows profitable "
            f"({win_count/total_windows*100:.0f}%)"
        )

        # Monthly breakdown
        print(f"\n  Monthly PnL breakdown (expanded config):\n")
        print(f"  {'Month':<10} {'Sigs':>5} {'HR':>6} {'ROI':>7} {'PnL':>8}")
        print(f"  {'-'*10} {'-'*5} {'-'*6} {'-'*7} {'-'*8}")

        for m in months:
            month_data = expanded_ds.filter(pl.col("month") == m)
            if len(month_data) < 1:
                continue
            wins_m = month_data.filter(pl.col("won")).height
            total_m = len(month_data)
            hr_m = wins_m / total_m
            pnl_m = month_data["pnl"].sum()
            bet_m = month_data["size_usd"].sum()
            roi_m = pnl_m / bet_m if bet_m > 0 else 0
            marker = "+" if roi_m > 0 else "-"
            print(
                f" {marker} {m:<10} {total_m:>5} {hr_m:>5.1%} "
                f"{roi_m:>+6.1%} ${pnl_m:>+7,.0f}"
            )

    # ══════════════════════════════════════════════════════════════════
    # 10. KEYWORD MINING: broader search for NEW prefer keywords
    # ══════════════════════════════════════════════════════════════════
    section(10, "KEYWORD MINING — broader search in vol<$2K 10-50% universe")

    broad_universe = will_base.filter(
        (pl.col("first_price") >= 0.10) & (pl.col("first_price") <= 0.50)
        & (pl.col("total_volume") <= 2000)
    )
    for kw in ["reach", "hit"]:
        broad_universe = broad_universe.filter(
            ~pl.col("question").str.to_lowercase().str.contains(kw)
        )
    broad_universe = add_band_sizing(broad_universe)
    broad_universe = add_pnl(broad_universe)

    if not broad_universe.is_empty():
        word_counter: Counter[str] = Counter()
        broader_stop = {
            "will", "the", "a", "an", "of", "in", "on", "to", "and", "or",
            "be", "is", "it", "for", "at", "by", "as", "if", "no", "yes",
            "not", "this", "that", "with", "from", "was", "are", "has",
            "have", "its", "their", "which", "than", "s", "t",
        }
        for q in broad_universe["question"].to_list():
            words = set(re.findall(r"[a-z]+", q.lower())) - broader_stop
            word_counter.update(words)

        print(f"\n  Testing {min(len(word_counter), 300)} frequent words …")
        print(f"  Looking for keywords with positive ROI and high monthly PnL:\n")

        kw_results = []
        for word, _count in word_counter.most_common(300):
            if _count < 15:
                continue
            subset = broad_universe.filter(
                pl.col("question").str.to_lowercase().str.contains(word)
            )
            resolved_sub = subset.filter(pl.col("resolution_value") == 1)
            if len(resolved_sub) < 15:
                continue
            wins = resolved_sub.filter(pl.col("won")).height
            total = len(resolved_sub)
            hr = wins / total
            total_pnl = resolved_sub["pnl"].sum()
            total_bet = resolved_sub["size_usd"].sum()
            roi = total_pnl / total_bet if total_bet > 0 else 0
            avg_bet = total_bet / total
            sigs_mo = total / N_MONTHS
            monthly_pnl = sigs_mo * avg_bet * roi

            kw_results.append({
                "word": word, "n": total, "hr": hr, "roi": roi,
                "monthly_pnl": monthly_pnl, "sigs_mo": sigs_mo,
            })

        kw_results.sort(key=lambda x: x["monthly_pnl"], reverse=True)

        print(f"  {'Keyword':<20} {'Sigs':>5} {'S/mo':>5} {'HR':>6} {'ROI':>7} {'$/mo':>7}")
        print(f"  {'-'*20} {'-'*5} {'-'*5} {'-'*6} {'-'*7} {'-'*7}")
        for r in kw_results[:40]:
            marker = "+" if r["roi"] > 0 else "-"
            print(
                f" {marker} {r['word']:<20} {r['n']:>5} {r['sigs_mo']:>5.1f} "
                f"{r['hr']:>5.1%} {r['roi']:>+6.1%} "
                f"${r['monthly_pnl']:>6,.0f}"
            )

        # Test expanded keyword sets
        new_profitable = [
            r["word"] for r in kw_results
            if r["roi"] > 0.05 and r["word"] not in prod_kw
               and r["monthly_pnl"] > 5
        ]
        if new_profitable:
            print(f"\n  NEW profitable keywords not in prod set: {new_profitable[:20]}")

            # Build expanded sets incrementally
            print(f"\n  Impact of adding new keywords to production set:")
            print_header()

            r = eval_config(
                will_base, label="current prod_kw (8)",
                prefer_kw=prod_kw, max_vol=2000,
            )
            if r:
                print_result(r)

            for add_count in [3, 5, 8, 12, 20]:
                additions = frozenset(new_profitable[:add_count])
                expanded = prod_kw | additions
                r = eval_config(
                    will_base,
                    label=f"prod + {add_count} new kw ({len(expanded)} total)",
                    prefer_kw=expanded, max_vol=2000,
                )
                if r:
                    print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 11. TRADE COUNT FILTER — minimum trades per market
    # ══════════════════════════════════════════════════════════════════
    section(11, "TRADE COUNT — minimum trades per market")
    print_header()

    for min_trades in [1, 2, 5, 10, 20, 50]:
        subset = will_base.filter(
            pl.col("n_trades").is_not_null() & (pl.col("n_trades") >= min_trades)
        )
        r = eval_config(
            subset, label=f"prod_kw + vol<2K + n_trades>={min_trades}",
            prefer_kw=prod_kw, max_vol=2000,
        )
        if r:
            print_result(r)

    # ══════════════════════════════════════════════════════════════════
    # 12. COMBINED BEST — stack all promising filters
    # ══════════════════════════════════════════════════════════════════
    section(12, "COMBINED BEST — stacking promising filters")
    print_header()

    # The baseline for comparison
    r = eval_config(
        will_base, label="BASELINE: prod_kw + vol<2K + 10-50%",
        prefer_kw=prod_kw, max_vol=2000,
    )
    if r:
        print_result(r)

    # Combo: expanded avoid list
    if toxic_results:
        expanded_avoid = frozenset(
            {x["word"] for x in toxic_results[:5]}
        ) | {"reach", "hit"}
        r = eval_config(
            will_base, label=f"+ expanded avoid ({len(expanded_avoid)} kw)",
            prefer_kw=prod_kw, max_vol=2000, avoid_kw=expanded_avoid,
        )
        if r:
            print_result(r)

    # Combo: lockup cap
    r = eval_config(
        will_base, label="+ lockup<14d",
        prefer_kw=prod_kw, max_vol=2000, max_lockup=14,
    )
    if r:
        print_result(r)

    # Combo: wider price
    r = eval_config(
        will_base, label="+ wider price (10-60%)",
        prefer_kw=prod_kw, max_vol=2000, price_lo=0.10, price_hi=0.60,
    )
    if r:
        print_result(r)

    r = eval_config(
        will_base, label="+ wider price (10-70%)",
        prefer_kw=prod_kw, max_vol=2000, price_lo=0.10, price_hi=0.70,
    )
    if r:
        print_result(r)

    # Combo: expanded kw + avoid + lockup
    if toxic_results and new_profitable:
        expanded_kw = prod_kw | frozenset(new_profitable[:5])
        r = eval_config(
            will_base,
            label=f"expanded kw({len(expanded_kw)}) + avoid({len(expanded_avoid)}) + vol<2K",
            prefer_kw=expanded_kw, max_vol=2000, avoid_kw=expanded_avoid,
        )
        if r:
            print_result(r)

        r = eval_config(
            will_base,
            label=f"expanded kw({len(expanded_kw)}) + avoid({len(expanded_avoid)}) + vol<3K",
            prefer_kw=expanded_kw, max_vol=3000, avoid_kw=expanded_avoid,
        )
        if r:
            print_result(r)

        r = eval_config(
            will_base,
            label=f"exp kw + exp avoid + vol<2K + lockup<14d",
            prefer_kw=expanded_kw, max_vol=2000,
            avoid_kw=expanded_avoid, max_lockup=14,
        )
        if r:
            print_result(r)

    # Combo: higher volume cap with lockup cap (fast resolution)
    r = eval_config(
        will_base, label="prod_kw + vol<5K + 10-50% + lockup<7d",
        prefer_kw=prod_kw, max_vol=5000, max_lockup=7,
    )
    if r:
        print_result(r)

    r = eval_config(
        will_base, label="prod_kw + vol<10K + 10-50% + lockup<3d",
        prefer_kw=prod_kw, max_vol=10000, max_lockup=3,
    )
    if r:
        print_result(r)

    # No keyword + vol<500 (ultra-thin)
    r = eval_config(
        will_base, label="NO KW + vol<500 + 10-50%",
        max_vol=500, avoid_kw=frozenset({"reach", "hit"}),
    )
    if r:
        print_result(r)

    print(f"\n  Exploration complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
