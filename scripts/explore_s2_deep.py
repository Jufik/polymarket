"""Deep-dive S2 Will-NO edge exploration — iteration 2.

Builds on findings from explore_s2_edge.py:
  - Edge is in sports/competition markets, NOT "above"/"below" keywords
  - Top keywords: mlb, park, league, traded, prix, grand, between, fed
  - Top tags: F1, MLB, Golf, Hockey, Basketball

This script drills into:
  1. Tag-based filtering (more stable than keywords)
  2. Keyword clusters (sports, finance, crypto groupings)
  3. Band-level profitability within winning subsets
  4. Avoid-keyword validation (are current avoids correct?)
  5. Multi-filter stacking for optimal config
  6. Stability check: rolling 3-month windows

Usage:
    uv run python scripts/explore_s2_deep.py
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


def add_pnl(df: pl.DataFrame, fee_pct: float = 0.0) -> pl.DataFrame:
    """Add won/pnl columns. Expects: first_price, size_usd, winner_outcome, resolution_value."""
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
            - pl.col("size_usd") * fee_pct
        )
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
        f"  {marker}{label:<50} {s['n']:>6,} signals  "
        f"{s['hr']:>5.1%} HR (BE={s['be_hr']:.0%})  "
        f"edge {edge:>+5.1%}  "
        f"ROI {s['roi']:>+6.1f}%  "
        f"PnL ${s['pnl']:>+9,.0f}  "
        f"$/bet ${s['avg_pnl']:>+6.2f}"
    )


def print_section(title: str) -> None:
    print(f"\n{'=' * 90}")
    print(f"  {title}")
    print(f"{'=' * 90}")


# ── Main ────────────────────────────────────────────────────────────────


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    print("=" * 90)
    print("  S2 Will-NO Deep Edge Exploration (Iteration 2)")
    print("=" * 90)

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
                  dateDiff('second', toDateTime64('1970-01-01', 3), end_date) AS end_ts
           FROM events WHERE end_date IS NOT NULL""",
        label="events",
    )
    events = events.with_columns(pl.col("end_ts").cast(pl.Float64))

    # ALL tags per event (not just first)
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

    # Resolution data
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    base = base.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left",
    )

    # Filter to YES 5-50%
    base = base.filter(
        (pl.col("first_price") >= 0.05) & (pl.col("first_price") <= 0.50)
    )

    # Add size_usd (flat $50 for exploration)
    base = base.with_columns(pl.lit(50.0).alias("size_usd"))

    # Add PnL
    base = add_pnl(base)

    # Add temporal columns
    base = base.with_columns(
        ((pl.col("first_ts")).cast(pl.Int64) * 1_000_000_000)
        .cast(pl.Datetime("ns"))
        .dt.strftime("%Y-%m")
        .alias("month"),
    )

    print(f"  Resolved Will markets (YES 5-50%): {len(base):,}")

    # Build tag membership: event_id -> set of tags
    tag_groups = tags.group_by("event_id").agg(pl.col("tag").alias("tags"))

    # Add all tags per market
    base = base.join(tag_groups, on="event_id", how="left")

    overall = summarize(base, min_n=1)
    if overall:
        print_row("BASELINE (all Will, YES 5-50%)", overall)

    # ════════════════════════════════════════════════════════════════════
    # 1. TAG-BASED FILTERING (all tags per event, not just first)
    # ════════════════════════════════════════════════════════════════════
    print_section("1. TAG-BASED FILTERING (all tags, sorted by ROI)")

    all_tag_values = tags["tag"].unique().to_list()
    tag_results = []
    for t in all_tag_values:
        # Explode tags list and filter
        subset = base.filter(
            pl.col("tags").list.contains(t)
        )
        s = summarize(subset, min_n=20)
        if s:
            tag_results.append((t, s))
    tag_results.sort(key=lambda x: x[1]["roi"], reverse=True)

    print("\n  Top 30 tags by ROI (min 20 signals):")
    for t, s in tag_results[:30]:
        print_row(t, s)

    profitable_tags = [(t, s) for t, s in tag_results if s["roi"] > 0 and s["n"] >= 20]
    print(f"\n  {len(profitable_tags)} profitable tags total")

    # Tag groups — combine related tags
    print("\n  Tag clusters (combined):")
    tag_clusters = {
        "Sports": ["MLB", "Basketball", "Hockey", "Golf", "Football", "Tennis",
                    "Soccer", "Boxing", "MMA", "UFC", "Cricket", "Rugby"],
        "Motorsport": ["Formula 1", "NASCAR", "F1 Singapore Grand Prix",
                       "F1 Japanese Grand Prix", "F1 Australian Grand Prix"],
        "Finance": ["Fed", "Interest Rates", "Economy", "Inflation",
                     "Stock Market", "Earnings"],
        "Crypto": ["Bitcoin", "Ethereum", "Crypto", "BTC", "ETH", "Solana",
                    "Cryptocurrency"],
        "Politics": ["US Politics", "Elections", "Congress", "Senate",
                     "Trump", "Biden"],
        "Weather": ["Weather", "Climate", "Temperature"],
    }
    for cluster_name, cluster_tags in tag_clusters.items():
        # Match any tag in cluster
        matched_tags = [t for t in cluster_tags if t in all_tag_values]
        if not matched_tags:
            # Try case-insensitive partial match
            matched_tags = [
                tv for tv in all_tag_values
                if any(ct.lower() in tv.lower() for ct in cluster_tags)
            ]
        if matched_tags:
            subset = base.filter(
                pl.col("tags").list.eval(
                    pl.element().is_in(matched_tags)
                ).list.any()
            )
            s = summarize(subset, min_n=20)
            if s:
                print_row(f"{cluster_name} ({len(matched_tags)} tags)", s)

    # ════════════════════════════════════════════════════════════════════
    # 2. KEYWORD CLUSTERS — semantic groups
    # ════════════════════════════════════════════════════════════════════
    print_section("2. KEYWORD CLUSTERS (semantic groups)")

    keyword_clusters = {
        "sports_terms": ["game", "match", "score", "win", "beat", "play",
                        "team", "season", "league", "championship", "tournament",
                        "series", "round", "final", "semifinal"],
        "sports_specific": ["mlb", "nba", "nfl", "nhl", "ufc", "f1",
                           "premier", "prix", "grand", "park", "stadium",
                           "innings", "runs", "goals", "points", "touchdown"],
        "finance_terms": ["fed", "rate", "rates", "inflation", "gdp",
                         "earnings", "revenue", "stock", "market", "traded",
                         "index", "dow", "nasdaq", "treasury"],
        "crypto_terms": ["bitcoin", "btc", "ethereum", "eth", "crypto",
                        "solana", "sol", "dogecoin", "xrp", "token",
                        "blockchain"],
        "time_terms": ["today", "tonight", "tomorrow", "week", "month",
                      "year", "daily", "weekly", "monthly", "quarterly"],
        "direction_terms": ["above", "below", "over", "under", "exceed",
                           "surpass", "reach", "hit", "break", "cross"],
        "politics_terms": ["president", "congress", "senate", "vote",
                          "election", "republican", "democrat", "trump",
                          "biden", "governor", "mayor"],
        "weather_terms": ["temperature", "rain", "snow", "hurricane",
                         "storm", "degrees", "celsius", "fahrenheit"],
    }

    for cluster_name, words in keyword_clusters.items():
        q_lower = pl.col("question").str.to_lowercase()
        cond = pl.lit(False)
        for w in words:
            cond = cond | q_lower.str.contains(w.lower())
        subset = base.filter(cond)
        s = summarize(subset, min_n=20)
        if s:
            print_row(cluster_name, s)

    # ════════════════════════════════════════════════════════════════════
    # 3. BAND PROFITABILITY WITHIN WINNING SUBSETS
    # ════════════════════════════════════════════════════════════════════
    print_section("3. BAND PROFITABILITY WITHIN WINNING SUBSETS")

    # For each promising keyword cluster, check which price bands work
    promising_clusters = {
        "sports_specific": ["mlb", "nba", "nfl", "nhl", "ufc", "f1",
                           "premier", "prix", "grand", "park",
                           "innings", "runs", "goals"],
        "finance_terms": ["fed", "rate", "rates", "inflation", "gdp",
                         "traded", "index", "treasury"],
    }
    bands = [
        ("5-10%", 0.05, 0.10),
        ("10-15%", 0.10, 0.15),
        ("15-20%", 0.15, 0.20),
        ("20-25%", 0.20, 0.25),
        ("25-30%", 0.25, 0.30),
        ("30-35%", 0.30, 0.35),
        ("35-40%", 0.35, 0.40),
        ("40-50%", 0.40, 0.50),
    ]

    for cluster_name, words in promising_clusters.items():
        q_lower = pl.col("question").str.to_lowercase()
        cond = pl.lit(False)
        for w in words:
            cond = cond | q_lower.str.contains(w.lower())
        cluster_df = base.filter(cond)
        s_all = summarize(cluster_df, min_n=10)
        if s_all:
            print(f"\n  {cluster_name} (all prices): {s_all['n']} signals, ROI={s_all['roi']:+.1f}%")
            for band_label, lo, hi in bands:
                subset = cluster_df.filter(
                    (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
                )
                s = summarize(subset, min_n=5)
                if s:
                    print_row(f"  {band_label}", s)

    # ════════════════════════════════════════════════════════════════════
    # 4. AVOID-KEYWORD VALIDATION
    # ════════════════════════════════════════════════════════════════════
    print_section("4. AVOID-KEYWORD VALIDATION")

    # Current avoid keywords: "reach", "hit", "by", "before"
    # Test: are these actually harmful? What other words should be avoided?
    current_avoids = ["reach", "hit", "by", "before"]
    potential_avoids = [
        "combine", "quarterly", "points", "scored", "beat", "earnings",
        "least", "most", "total", "over", "under", "half",
        "record", "high", "low", "drop", "fall", "decline",
        "million", "billion", "percent", "more", "less",
    ]

    print("\n  Current avoid keywords — impact when EXCLUDED vs INCLUDED:")
    for kw in current_avoids:
        q_lower = pl.col("question").str.to_lowercase()
        has_kw = base.filter(q_lower.str.contains(kw.lower()))
        no_kw = base.filter(~q_lower.str.contains(kw.lower()))
        s_has = summarize(has_kw, min_n=10)
        s_no = summarize(no_kw, min_n=10)
        if s_has:
            print_row(f'WITH "{kw}"', s_has)
        if s_no:
            print_row(f'WITHOUT "{kw}"', s_no)
        print()

    print("\n  Potential additional avoid keywords:")
    avoid_candidates = []
    for kw in potential_avoids:
        q_lower = pl.col("question").str.to_lowercase()
        has_kw = base.filter(q_lower.str.contains(kw.lower()))
        s_has = summarize(has_kw, min_n=20)
        if s_has and s_has["roi"] < -10:
            avoid_candidates.append((kw, s_has))
            print_row(f'"{kw}"', s_has)

    # ════════════════════════════════════════════════════════════════════
    # 5. OPTIMAL CONFIGURATION SEARCH
    # ════════════════════════════════════════════════════════════════════
    print_section("5. OPTIMAL CONFIGURATION SEARCH")

    # Build candidate prefer-keyword sets and test them
    prefer_sets = {
        "original": {"above", "below", "today", "tonight"},
        "sports_only": {"mlb", "prix", "grand", "league", "park",
                       "game", "match", "series"},
        "sports+finance": {"mlb", "prix", "grand", "league", "park",
                          "game", "match", "series", "fed", "traded",
                          "rate", "treasury"},
        "top_profitable": {"mlb", "prix", "grand", "league", "park",
                          "traded", "fed", "between"},
        "between_only": {"between"},
        "broad_sports": {"mlb", "nba", "nfl", "nhl", "prix", "grand",
                        "league", "park", "game", "match", "series",
                        "championship", "tournament", "round"},
        "no_prefer": set(),  # No keyword filter (all "Will" markets)
    }

    avoid_sets = {
        "original_research": {"reach", "hit"},
        "current_impl": {"reach", "hit", "by", "before"},
        "expanded": {"reach", "hit", "by", "before", "combine",
                    "quarterly", "scored", "earnings", "points"},
        "none": set(),
    }

    vol_caps = [0, 1_000, 5_000, 10_000, 50_000]
    price_ranges = [
        ("5-50%", 0.05, 0.50),
        ("10-35%", 0.10, 0.35),
        ("15-30%", 0.15, 0.30),
        ("15-40%", 0.15, 0.40),
        ("5-30%", 0.05, 0.30),
    ]

    configs_results = []

    for pref_name, pref_kws in prefer_sets.items():
        for avoid_name, avoid_kws in avoid_sets.items():
            for p_label, p_lo, p_hi in price_ranges:
                for vol_cap in vol_caps:
                    filtered = base.filter(
                        (pl.col("first_price") >= p_lo)
                        & (pl.col("first_price") < p_hi)
                    )

                    # Apply prefer keywords
                    if pref_kws:
                        q_lower = pl.col("question").str.to_lowercase()
                        cond = pl.lit(False)
                        for kw in pref_kws:
                            cond = cond | q_lower.str.contains(kw.lower())
                        filtered = filtered.filter(cond)

                    # Apply avoid keywords
                    if avoid_kws:
                        for kw in avoid_kws:
                            filtered = filtered.filter(
                                ~pl.col("question").str.to_lowercase().str.contains(kw.lower())
                            )

                    # Apply volume cap
                    if vol_cap > 0:
                        filtered = filtered.filter(
                            pl.col("total_volume") <= vol_cap
                        )

                    s = summarize(filtered, min_n=30)
                    if s:
                        vol_label = f"vol<${vol_cap:,}" if vol_cap > 0 else "any vol"
                        cfg_label = (
                            f"prefer={pref_name} avoid={avoid_name} "
                            f"price={p_label} {vol_label}"
                        )
                        configs_results.append((cfg_label, s, pref_name, avoid_name,
                                               p_label, vol_cap))

    # Sort by ROI
    configs_results.sort(key=lambda x: x[1]["roi"], reverse=True)

    print(f"\n  Tested {len(configs_results)} config combinations. Top 30:")
    for cfg_label, s, *_ in configs_results[:30]:
        print_row(cfg_label, s)

    # Show best config per signal count bracket
    print("\n  Best config per signal count bracket:")
    for min_signals, max_signals in [(30, 100), (100, 300), (300, 1000),
                                     (1000, 5000), (5000, 50000)]:
        bracket_results = [
            (l, s, *rest) for l, s, *rest in configs_results
            if min_signals <= s["n"] < max_signals and s["roi"] > 0
        ]
        if bracket_results:
            best = bracket_results[0]
            print_row(f"[{min_signals}-{max_signals} sigs] {best[0]}", best[1])

    # ════════════════════════════════════════════════════════════════════
    # 6. STABILITY CHECK — rolling 3-month windows
    # ════════════════════════════════════════════════════════════════════
    print_section("6. STABILITY CHECK — rolling 3-month windows")

    # Take the top 3 configs and check stability
    top_configs = configs_results[:5]
    months = sorted(base["month"].unique().to_list())

    for cfg_label, _, pref_name, avoid_name, p_label, vol_cap in top_configs:
        print(f"\n  Config: {cfg_label}")
        # Get price range
        p_lo_s, p_hi_s = p_label.replace("%", "").split("-")
        p_lo = float(p_lo_s) / 100
        p_hi = float(p_hi_s) / 100

        pref_kws = prefer_sets[pref_name]
        avoid_kws = avoid_sets[avoid_name]

        # Apply all filters to get base filtered set
        filtered = base.filter(
            (pl.col("first_price") >= p_lo) & (pl.col("first_price") < p_hi)
        )
        if pref_kws:
            q_lower = pl.col("question").str.to_lowercase()
            cond = pl.lit(False)
            for kw in pref_kws:
                cond = cond | q_lower.str.contains(kw.lower())
            filtered = filtered.filter(cond)
        if avoid_kws:
            for kw in avoid_kws:
                filtered = filtered.filter(
                    ~pl.col("question").str.to_lowercase().str.contains(kw.lower())
                )
        if vol_cap > 0:
            filtered = filtered.filter(pl.col("total_volume") <= vol_cap)

        # Rolling 3-month windows
        profitable_windows = 0
        total_windows = 0
        for i in range(len(months) - 2):
            window_months = months[i:i+3]
            window_df = filtered.filter(pl.col("month").is_in(window_months))
            s = summarize(window_df, min_n=5)
            if s:
                total_windows += 1
                status = "+" if s["roi"] > 0 else "-"
                if s["roi"] > 0:
                    profitable_windows += 1
                print(
                    f"    {status} {window_months[0]}..{window_months[-1]}: "
                    f"{s['n']:>4} sigs  {s['hr']:>5.1%} HR  ROI {s['roi']:>+6.1f}%"
                )

        if total_windows > 0:
            win_rate = profitable_windows / total_windows * 100
            print(f"    Stability: {profitable_windows}/{total_windows} windows profitable ({win_rate:.0f}%)")

    # ════════════════════════════════════════════════════════════════════
    # 7. FINAL RECOMMENDATION
    # ════════════════════════════════════════════════════════════════════
    print_section("7. FINAL RECOMMENDATION")

    if configs_results:
        # Pick best with >=100 signals and positive ROI
        viable = [
            (l, s, pn, an, pl_, vc) for l, s, pn, an, pl_, vc in configs_results
            if s["roi"] > 0 and s["n"] >= 50
        ]
        if viable:
            best = viable[0]
            print(f"\n  BEST CONFIG (ROI-optimized, >=50 signals):")
            print_row(best[0], best[1])

            print(f"\n  Parameters:")
            print(f"    prefer_keywords = {prefer_sets[best[2]]}")
            print(f"    avoid_keywords  = {avoid_sets[best[3]]}")
            print(f"    price_range     = {best[4]}")
            print(f"    max_volume      = ${best[5]:,}" if best[5] > 0 else "    max_volume      = no limit")

            # Also show best balanced config (enough signals for practical use)
            balanced = [
                (l, s, pn, an, pl_, vc) for l, s, pn, an, pl_, vc in configs_results
                if s["roi"] > 0 and s["n"] >= 200
            ]
            if balanced:
                b = balanced[0]
                print(f"\n  BEST BALANCED CONFIG (ROI-optimized, >=200 signals):")
                print_row(b[0], b[1])
                print(f"    prefer_keywords = {prefer_sets[b[2]]}")
                print(f"    avoid_keywords  = {avoid_sets[b[3]]}")
                print(f"    price_range     = {b[4]}")
                print(f"    max_volume      = ${b[5]:,}" if b[5] > 0 else "    max_volume      = no limit")

            # Signal rate
            total_months = len(months)
            for label, cfg in [("BEST", viable[0] if viable else None),
                               ("BALANCED", balanced[0] if balanced else None)]:
                if cfg:
                    signals_per_month = cfg[1]["n"] / max(total_months, 1)
                    print(f"\n  {label}: ~{signals_per_month:.1f} signals/month over {total_months} months")
        else:
            print("\n  No config with >=50 signals and positive ROI found.")
    else:
        print("\n  No config results computed.")

    print(f"\n  Deep exploration complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
