"""Final S2 calibration — iteration 3.

Drills into:
  1. What does "between" actually capture? (sample questions)
  2. Band recalibration within the profitable niche
  3. Fee sensitivity (0%, 1%, 2%)
  4. Final config validation with band-adjusted sizing
  5. Top_profitable vs between_only head-to-head
  6. Expanded avoid set impact

Usage:
    uv run python scripts/explore_s2_final.py
"""

from __future__ import annotations

import asyncio
import io
import re
import sys
import time
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


def add_pnl(df: pl.DataFrame, fee_pct: float = 0.0) -> pl.DataFrame:
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
        f"  {marker}{label:<55} {s['n']:>6,} signals  "
        f"{s['hr']:>5.1%} HR (BE={s['be_hr']:.0%})  "
        f"edge {edge:>+5.1%}  "
        f"ROI {s['roi']:>+6.1f}%  "
        f"PnL ${s['pnl']:>+9,.0f}"
    )


def print_section(title: str) -> None:
    print(f"\n{'=' * 95}")
    print(f"  {title}")
    print(f"{'=' * 95}")


async def main() -> None:
    t_start = time.time()
    client = httpx.AsyncClient(base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0)

    print("=" * 95)
    print("  S2 Will-NO Final Calibration (Iteration 3)")
    print("=" * 95)

    print("\nFetching data …")
    markets = await query_ch(client,
        "SELECT condition_id, toString(event_id) AS event_id, question FROM markets WHERE question LIKE 'Will %%'",
        label="markets")
    first_trades = await query_ch(client, """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price,
               min(toUnixTimestamp(timestamp)) AS first_ts
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id""", label="first-trades")
    for c in ["first_price", "first_ts"]:
        first_trades = first_trades.with_columns(pl.col(c).cast(pl.Float64))

    total_volume = await query_ch(client, """
        WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
        SELECT condition_id,
               sum(CAST(price AS Float64) * CAST(size AS Float64)) AS total_volume
        FROM (SELECT * FROM polymarket.trades_raw FINAL) t
        WHERE condition_id IN (SELECT condition_id FROM wm)
        GROUP BY condition_id""", label="volume")
    total_volume = total_volume.with_columns(pl.col("total_volume").cast(pl.Float64))
    await client.aclose()

    # Build base
    base = markets.join(first_trades, on="condition_id", how="inner")
    base = base.join(total_volume, on="condition_id", how="left")
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    base = base.join(
        resolution.select("condition_id", "winner_outcome", "resolution_value"),
        on="condition_id", how="left")
    base = base.filter(
        (pl.col("first_price") >= 0.05) & (pl.col("first_price") <= 0.50))
    base = base.with_columns(pl.lit(50.0).alias("size_usd"))
    base = add_pnl(base)
    base = base.with_columns(
        ((pl.col("first_ts")).cast(pl.Int64) * 1_000_000_000)
        .cast(pl.Datetime("ns")).dt.strftime("%Y-%m").alias("month"))
    print(f"  Resolved Will markets (YES 5-50%): {len(base):,}")

    # ════════════════════════════════════════════════════════════════════
    # 1. WHAT DOES "BETWEEN" CAPTURE?
    # ════════════════════════════════════════════════════════════════════
    print_section("1. WHAT DOES 'BETWEEN' CAPTURE?")

    between_df = base.filter(
        pl.col("question").str.to_lowercase().str.contains("between"))
    print(f"\n  Total 'between' markets: {len(between_df):,}")

    # Sample questions
    q_lower = pl.col("question").str.to_lowercase()
    sample = between_df.filter(
        (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
        & (pl.col("total_volume") <= 1000)
    )
    print(f"  In target range (YES 15-40%, vol<$1K): {len(sample):,}")

    print("\n  Sample questions (first 30):")
    for i, q in enumerate(sample["question"].head(30).to_list()):
        won = sample["won"][i]
        price = sample["first_price"][i]
        print(f"    {'W' if won else 'L'} [{price:.2f}] {q[:120]}")

    # Categorize "between" questions
    categories = {
        "crypto_price": r"(bitcoin|btc|ethereum|eth|solana|crypto|xrp)",
        "sports_score": r"(score|goals|runs|points|innings|touchdown)",
        "temperature": r"(temperature|degrees|fahrenheit|celsius)",
        "financial": r"(rate|gdp|inflation|earnings|stock|index|traded|treasury)",
        "comparison": r"(traded|close|price|value|level)",
    }
    print("\n  'between' question categories:")
    for cat_name, pattern in categories.items():
        cat_df = between_df.filter(q_lower.str.contains(pattern))
        s = summarize(cat_df, min_n=10)
        if s:
            print_row(f"  between + {cat_name}", s)

    # Winning percentage breakdown
    winners = between_df.filter(pl.col("won"))
    losers = between_df.filter(~pl.col("won"))
    print(f"\n  'between' outcome: {len(winners)} wins, {len(losers)} losses")
    if len(losers) > 0:
        print(f"  Losing 'between' sample questions:")
        for i, q in enumerate(losers["question"].head(10).to_list()):
            price = losers["first_price"][i]
            print(f"    L [{price:.2f}] {q[:120]}")

    # ════════════════════════════════════════════════════════════════════
    # 2. BAND RECALIBRATION WITHIN NICHE
    # ════════════════════════════════════════════════════════════════════
    print_section("2. BAND RECALIBRATION WITHIN NICHE")

    # Apply best niche filter: between + vol<$1K
    niche = base.filter(
        q_lower.str.contains("between")
        & (pl.col("total_volume") <= 1000)
    )
    print(f"\n  Niche (between + vol<$1K): {len(niche):,} signals")

    # Fine-grained 5% bands
    print("\n  Band-level performance (5% slices):")
    band_data = []
    for lo_pct in range(5, 50, 5):
        lo = lo_pct / 100.0
        hi = (lo_pct + 5) / 100.0
        subset = niche.filter(
            (pl.col("first_price") >= lo) & (pl.col("first_price") < hi))
        s = summarize(subset, min_n=5)
        if s:
            print_row(f"YES {lo_pct}-{lo_pct+5}%", s)
            band_data.append((lo_pct, lo_pct+5, s))

    # Compute optimal multipliers: normalize by ROI/bet relative to best band
    print("\n  Data-derived band multipliers:")
    if band_data:
        # Use $/bet as the sizing signal
        best_pnl_per_bet = max(b[2]["avg_pnl"] for b in band_data if b[2]["roi"] > 0) if any(b[2]["roi"] > 0 for b in band_data) else 1.0
        for lo_pct, hi_pct, s in band_data:
            if best_pnl_per_bet > 0:
                mult = max(0.0, s["avg_pnl"] / best_pnl_per_bet)
            else:
                mult = 0.0
            mult = round(min(mult, 1.0), 2)
            edge = s["hr"] - s["be_hr"]
            print(
                f"    ({lo_pct/100:.2f}, {hi_pct/100:.2f}, {mult:.2f})  "
                f"# {s['n']:>4} sigs, {s['hr']:.1%} HR, edge {edge:+.1%}, "
                f"ROI {s['roi']:+.1f}%, $/bet ${s['avg_pnl']:+.2f}"
            )

    # Also test broader prefer set within the niche context
    print("\n  top_profitable keywords within vol<$1K:")
    top_kws = {"mlb", "prix", "grand", "league", "park", "traded", "fed", "between"}
    cond = pl.lit(False)
    for kw in top_kws:
        cond = cond | q_lower.str.contains(kw.lower())
    top_niche = base.filter(cond & (pl.col("total_volume") <= 1000))
    print(f"  top_profitable + vol<$1K: {len(top_niche):,} signals")
    for lo_pct in range(5, 50, 5):
        lo = lo_pct / 100.0
        hi = (lo_pct + 5) / 100.0
        subset = top_niche.filter(
            (pl.col("first_price") >= lo) & (pl.col("first_price") < hi))
        s = summarize(subset, min_n=5)
        if s:
            print_row(f"YES {lo_pct}-{lo_pct+5}%", s)

    # ════════════════════════════════════════════════════════════════════
    # 3. FEE SENSITIVITY
    # ════════════════════════════════════════════════════════════════════
    print_section("3. FEE SENSITIVITY")

    for fee_label, fee_pct in [("0% fee", 0.0), ("1% fee", 0.01), ("2% fee", 0.02)]:
        # Rebuild PnL with fees
        niche_fees = base.filter(
            q_lower.str.contains("between")
            & (pl.col("total_volume") <= 1000)
            & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
        )
        # Recalculate PnL with fee
        niche_fees = niche_fees.drop("pnl")
        niche_fees = niche_fees.with_columns(
            pl.when(pl.col("won"))
            .then(
                pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
                - pl.col("size_usd") * fee_pct
            )
            .otherwise(-pl.col("size_usd"))
            .alias("pnl")
        )
        s = summarize(niche_fees, min_n=10)
        if s:
            print_row(f"between + vol<$1K + YES 15-40% @ {fee_label}", s)

    # Also test top_profitable with fees
    for fee_label, fee_pct in [("0% fee", 0.0), ("1% fee", 0.01), ("2% fee", 0.02)]:
        niche_fees = base.filter(
            cond  # top_profitable keywords
            & (pl.col("total_volume") <= 1000)
            & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
        )
        niche_fees = niche_fees.drop("pnl")
        niche_fees = niche_fees.with_columns(
            pl.when(pl.col("won"))
            .then(
                pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
                - pl.col("size_usd") * fee_pct
            )
            .otherwise(-pl.col("size_usd"))
            .alias("pnl")
        )
        s = summarize(niche_fees, min_n=10)
        if s:
            print_row(f"top_profitable + vol<$1K + YES 15-40% @ {fee_label}", s)

    # ════════════════════════════════════════════════════════════════════
    # 4. BAND-ADJUSTED SIZING SIMULATION
    # ════════════════════════════════════════════════════════════════════
    print_section("4. BAND-ADJUSTED SIZING vs FLAT SIZING")

    # Test within niche: compare flat $50 vs band-adjusted
    niche_15_40 = base.filter(
        q_lower.str.contains("between")
        & (pl.col("total_volume") <= 1000)
        & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
    )

    # Flat sizing (already computed)
    s_flat = summarize(niche_15_40, min_n=10)
    if s_flat:
        print_row("Flat $50", s_flat)

    # Current research bands (from config)
    research_bands = [
        (0.15, 0.20, 0.85),
        (0.20, 0.25, 0.98),
        (0.25, 0.30, 1.00),
        (0.30, 0.35, 0.60),
        (0.35, 0.40, 0.23),
    ]

    def apply_bands(df: pl.DataFrame, bands: list[tuple[float, float, float]], base_usd: float = 50.0) -> pl.DataFrame:
        """Replace size_usd with band-adjusted sizing and recompute PnL."""
        size_expr = pl.lit(0.0)
        n = len(bands)
        for i, (lo, hi, mult) in enumerate(bands):
            if i < n - 1:
                c = (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
            else:
                c = (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
            size_expr = pl.when(c).then(pl.lit(base_usd * mult)).otherwise(size_expr)

        result = df.with_columns(size_expr.alias("size_usd"))
        # Filter out zero-size (outside bands)
        result = result.filter(pl.col("size_usd") > 0)
        # Recompute PnL
        result = result.drop("pnl")
        result = result.with_columns(
            pl.when(pl.col("won"))
            .then(pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price")))
            .otherwise(-pl.col("size_usd"))
            .alias("pnl")
        )
        return result

    research_sized = apply_bands(niche_15_40, research_bands)
    s_research = summarize(research_sized, min_n=10)
    if s_research:
        print_row("Research bands (old)", s_research)

    # Data-derived bands: use actual performance within niche
    # From section 2 output, we'll use the "between + vol<$1K" band data
    # Bands where ROI is positive get proportional mult, negative get 0
    # We'll construct from the band_data we computed
    # For now, test a few candidate band configs:

    # Option A: Uniform within 15-40% (flat but narrowed range)
    uniform_bands = [
        (0.15, 0.40, 1.00),
    ]
    uniform_sized = apply_bands(niche_15_40, uniform_bands)
    s_uniform = summarize(uniform_sized, min_n=10)
    if s_uniform:
        print_row("Uniform 15-40%", s_uniform)

    # Option B: Emphasize 15-30% sweet spot, de-weight 30-40%
    sweet_bands = [
        (0.15, 0.20, 0.80),
        (0.20, 0.25, 1.00),
        (0.25, 0.30, 1.00),
        (0.30, 0.35, 0.50),
        (0.35, 0.40, 0.30),
    ]
    sweet_sized = apply_bands(niche_15_40, sweet_bands)
    s_sweet = summarize(sweet_sized, min_n=10)
    if s_sweet:
        print_row("Sweet-spot bands (15-30% heavy)", s_sweet)

    # Option C: Inverse — emphasize higher YES prices where edge is stronger in niche
    inverse_bands = [
        (0.15, 0.20, 0.50),
        (0.20, 0.25, 0.70),
        (0.25, 0.30, 0.90),
        (0.30, 0.35, 1.00),
        (0.35, 0.40, 1.00),
    ]
    inverse_sized = apply_bands(niche_15_40, inverse_bands)
    s_inverse = summarize(inverse_sized, min_n=10)
    if s_inverse:
        print_row("Inverse bands (30-40% heavy)", s_inverse)

    # ════════════════════════════════════════════════════════════════════
    # 5. HEAD-TO-HEAD: between_only vs top_profitable
    # ════════════════════════════════════════════════════════════════════
    print_section("5. HEAD-TO-HEAD: between_only vs top_profitable")

    configs = {
        "between_only": {"between"},
        "top_profitable": {"mlb", "prix", "grand", "league", "park",
                          "traded", "fed", "between"},
        "sports_focused": {"mlb", "prix", "grand", "league", "park",
                          "game", "match", "series", "between"},
    }
    avoids = {"reach", "hit"}

    for cfg_name, kws in configs.items():
        kw_cond = pl.lit(False)
        for kw in kws:
            kw_cond = kw_cond | q_lower.str.contains(kw.lower())

        for vol_cap_label, vol_cap in [("vol<$1K", 1000), ("vol<$5K", 5000), ("any vol", 1e12)]:
            filtered = base.filter(
                kw_cond
                & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
                & (pl.col("total_volume") <= vol_cap)
            )
            # Apply avoids
            for kw in avoids:
                filtered = filtered.filter(~q_lower.str.contains(kw.lower()))
            s = summarize(filtered, min_n=20)
            if s:
                print_row(f"{cfg_name} + {vol_cap_label}", s)

    # ════════════════════════════════════════════════════════════════════
    # 6. EXPANDED AVOID VALIDATION
    # ════════════════════════════════════════════════════════════════════
    print_section("6. EXPANDED AVOID VALIDATION")

    # Test best config with progressively expanded avoids
    avoid_tiers = [
        ("none", set()),
        ("minimal", {"reach", "hit"}),
        ("moderate", {"reach", "hit", "by", "before"}),
        ("expanded", {"reach", "hit", "by", "before", "combine", "quarterly",
                      "scored", "earnings", "points", "beat", "more"}),
        ("aggressive", {"reach", "hit", "by", "before", "combine", "quarterly",
                       "scored", "earnings", "points", "beat", "more",
                       "billion", "under", "over", "least", "record", "million"}),
    ]

    between_vol1k = base.filter(
        q_lower.str.contains("between")
        & (pl.col("total_volume") <= 1000)
        & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
    )

    for tier_name, avoid_kws in avoid_tiers:
        filtered = between_vol1k
        for kw in avoid_kws:
            filtered = filtered.filter(~q_lower.str.contains(kw.lower()))
        s = summarize(filtered, min_n=10)
        if s:
            print_row(f"avoid={tier_name} ({len(avoid_kws)} words)", s)

    # Same for top_profitable
    print()
    top_vol1k = base.filter(
        cond  # top_profitable keywords
        & (pl.col("total_volume") <= 1000)
        & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
    )
    for tier_name, avoid_kws in avoid_tiers:
        filtered = top_vol1k
        for kw in avoid_kws:
            filtered = filtered.filter(~q_lower.str.contains(kw.lower()))
        s = summarize(filtered, min_n=10)
        if s:
            print_row(f"top_prof + avoid={tier_name} ({len(avoid_kws)} words)", s)

    # ════════════════════════════════════════════════════════════════════
    # 7. FINAL PROPOSED CONFIG SIMULATION
    # ════════════════════════════════════════════════════════════════════
    print_section("7. FINAL PROPOSED CONFIG SIMULATION")

    # Proposed config:
    #   prefer_keywords = {"between"}
    #   avoid_keywords = {"reach", "hit"}
    #   max_volume_usd = 1000
    #   price_bands = (0.15, 0.40) flat or data-derived
    #   fee_pct = 0.02

    proposed_filter = base.filter(
        q_lower.str.contains("between")
        & ~q_lower.str.contains("reach")
        & ~q_lower.str.contains("hit")
        & (pl.col("total_volume") <= 1000)
        & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
    )

    print(f"\n  Proposed config signals: {len(proposed_filter):,}")

    # With 2% fee
    proposed_w_fee = proposed_filter.drop("pnl").with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
            - pl.col("size_usd") * 0.02
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl")
    )
    s = summarize(proposed_w_fee, min_n=10)
    if s:
        print_row("PROPOSED @ 2% fee", s)

    # Monthly breakdown
    print("\n  Monthly signal count and ROI:")
    months = sorted(proposed_w_fee["month"].unique().to_list())
    for m in months:
        subset = proposed_w_fee.filter(pl.col("month") == m)
        s_m = summarize(subset, min_n=1)
        if s_m and s_m["n"] >= 3:
            status = "+" if s_m["roi"] > 0 else "-"
            print(f"    {status} {m}: {s_m['n']:>4} sigs  {s_m['hr']:>5.1%} HR  ROI {s_m['roi']:>+6.1f}%  PnL ${s_m['pnl']:>+6.0f}")

    # Capital simulation: $300 bankroll, $50/bet, 6 slots
    print("\n  Capital simulation ($300 bankroll, $50/bet, 6 concurrent slots):")
    total_pnl = proposed_w_fee["pnl"].sum()
    total_bet = proposed_w_fee["size_usd"].sum()
    n_signals = len(proposed_w_fee)
    avg_per_signal = total_pnl / n_signals if n_signals > 0 else 0
    signals_per_month = n_signals / max(len(months), 1)
    monthly_pnl = avg_per_signal * signals_per_month
    print(f"    Total signals: {n_signals}")
    print(f"    Signals/month: {signals_per_month:.1f}")
    print(f"    Total PnL: ${total_pnl:,.0f}")
    print(f"    ROI: {total_pnl / total_bet * 100:.1f}%")
    print(f"    Avg PnL/signal: ${avg_per_signal:.2f}")
    print(f"    Est monthly PnL: ${monthly_pnl:.0f}")
    print(f"    Est annual PnL: ${monthly_pnl * 12:.0f}")
    print(f"    Annual ROI on $300: {monthly_pnl * 12 / 300 * 100:.0f}%")

    # Also test top_profitable as alternative
    print("\n  ALTERNATIVE: top_profitable config @ 2% fee:")
    alt_filter = base.filter(
        cond  # top_profitable
        & ~q_lower.str.contains("reach")
        & ~q_lower.str.contains("hit")
        & (pl.col("total_volume") <= 1000)
        & (pl.col("first_price") >= 0.15) & (pl.col("first_price") <= 0.40)
    )
    alt_w_fee = alt_filter.drop("pnl").with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
            - pl.col("size_usd") * 0.02
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl")
    )
    s_alt = summarize(alt_w_fee, min_n=10)
    if s_alt:
        print_row("TOP_PROFITABLE @ 2% fee", s_alt)
        n_alt = len(alt_w_fee)
        total_pnl_alt = alt_w_fee["pnl"].sum()
        spm_alt = n_alt / max(len(months), 1)
        avg_alt = total_pnl_alt / n_alt if n_alt > 0 else 0
        monthly_alt = avg_alt * spm_alt
        print(f"    Signals/month: {spm_alt:.1f}")
        print(f"    Est monthly PnL: ${monthly_alt:.0f}")
        print(f"    Est annual PnL: ${monthly_alt * 12:.0f}")
        print(f"    Annual ROI on $300: {monthly_alt * 12 / 300 * 100:.0f}%")

    print(f"\n  Final calibration complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
