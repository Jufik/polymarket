"""Analyze the NO-side edge in Polymarket.

Overall base rate: 38.1% YES-won, 61.9% NO-won.
This script investigates WHERE and WHY NO is systematically underpriced,
and where overpricing of NO creates opportunities on the YES side.
"""

from __future__ import annotations

import polars as pl
import json
from pathlib import Path

DATA_DIR = Path("data")
OUT_DIR = Path("insights/overpriceNo")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_data() -> dict[str, pl.DataFrame]:
    """Load all key tables."""
    mr = pl.read_parquet(DATA_DIR / "derived/markets_resolved.parquet")
    mkt = pl.read_parquet(DATA_DIR / "metadata/markets.parquet")
    pnl = pl.scan_parquet(DATA_DIR / "derived/trader_market_pnl.parquet")
    mvf = pl.read_parquet(DATA_DIR / "derived/maker_volume_fractions.parquet")
    token_map = pl.read_parquet(DATA_DIR / "metadata/token_map.parquet")
    prices = pl.scan_parquet(DATA_DIR / "derived/market_prices.parquet")

    # Join resolved with market metadata
    resolved = mr.join(mkt, on="condition_id", how="left", suffix="_meta")

    return {
        "resolved": resolved,
        "pnl": pnl,
        "mvf": mvf,
        "token_map": token_map,
        "prices": prices,
        "markets_raw": mkt,
    }


def section_base_rates(resolved: pl.DataFrame) -> str:
    """Section 1: Overall base rates and decomposition."""
    lines = ["# 1. Base Rate Decomposition\n"]

    total = resolved.height
    yes_won = resolved.filter(pl.col("yes_won")).height
    no_won = total - yes_won

    lines.append(f"Total resolved markets: {total:,}")
    lines.append(f"YES won: {yes_won:,} ({yes_won/total*100:.1f}%)")
    lines.append(f"NO won:  {no_won:,} ({no_won/total*100:.1f}%)")
    lines.append("")

    # By neg_risk
    lines.append("## By neg_risk (multi-outcome vs binary)")
    by_neg = (
        resolved.group_by("neg_risk")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").sum().alias("yes_count"),
            pl.col("yes_won").mean().alias("yes_rate"),
        )
        .sort("neg_risk")
    )
    lines.append(f"{'neg_risk':<10} {'Count':>8} {'YES won':>10} {'YES rate':>10} {'NO rate':>10}")
    lines.append("-" * 55)
    for row in by_neg.iter_rows(named=True):
        nr = row["neg_risk"]
        n = row["count"]
        yr = row["yes_rate"]
        lines.append(
            f"{str(nr):<10} {n:>8,} {row['yes_count']:>10,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}%"
        )
    lines.append("")

    # By year
    lines.append("## By resolution year")
    resolved_with_year = resolved.with_columns(
        pl.col("resolved_at").dt.year().alias("year")
    )
    by_year = (
        resolved_with_year.filter(pl.col("year").is_not_null())
        .group_by("year")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("yes_rate"),
        )
        .sort("year")
    )
    lines.append(f"{'Year':<6} {'Count':>8} {'YES rate':>10} {'NO rate':>10}")
    lines.append("-" * 40)
    for row in by_year.iter_rows(named=True):
        yr = row["yes_rate"]
        lines.append(
            f"{row['year']:<6} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}%"
        )
    lines.append("")

    # By category (top 25)
    lines.append("## By category (top 25 by count, where category is non-empty)")
    resolved_cat = resolved.filter(
        (pl.col("category").is_not_null()) & (pl.col("category") != "")
    )
    by_cat = (
        resolved_cat.group_by("category")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("yes_rate"),
        )
        .sort("count", descending=True)
        .head(25)
    )
    lines.append(
        f"{'Category':<25} {'Count':>8} {'YES rate':>10} {'NO rate':>10} {'NO skew':>10}"
    )
    lines.append("-" * 70)
    for row in by_cat.iter_rows(named=True):
        yr = row["yes_rate"]
        skew = (1 - yr) - 0.619  # vs overall base rate
        lines.append(
            f"{str(row['category'])[:24]:<25} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}% {skew*100:>+9.1f}pp"
        )
    lines.append("")

    return "\n".join(lines)


def section_tags_analysis(resolved: pl.DataFrame) -> str:
    """Section 2: NO rate by tags."""
    lines = ["# 2. NO Win Rate by Tags\n"]

    # Explode tags
    with_tags = resolved.filter(
        (pl.col("tags").is_not_null()) & (pl.col("tags") != "")
    ).with_columns(
        pl.col("tags").str.split(",").alias("tag_list")
    ).explode("tag_list").with_columns(
        pl.col("tag_list").str.strip_chars().alias("tag")
    ).filter(pl.col("tag") != "")

    by_tag = (
        with_tags.group_by("tag")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("yes_rate"),
        )
        .filter(pl.col("count") >= 100)
        .sort("yes_rate")
    )

    # Tags with highest NO win rate (lowest YES rate)
    lines.append("## Tags with HIGHEST NO win rate (strongest NO edge)")
    top_no = by_tag.head(30)
    lines.append(
        f"{'Tag':<35} {'Count':>8} {'YES rate':>10} {'NO rate':>10}"
    )
    lines.append("-" * 70)
    for row in top_no.iter_rows(named=True):
        yr = row["yes_rate"]
        lines.append(
            f"{str(row['tag'])[:34]:<35} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}%"
        )
    lines.append("")

    # Tags with highest YES win rate (where NO is overpriced)
    lines.append("## Tags with HIGHEST YES win rate (NO is OVERPRICED here)")
    top_yes = by_tag.sort("yes_rate", descending=True).head(30)
    lines.append(
        f"{'Tag':<35} {'Count':>8} {'YES rate':>10} {'NO rate':>10}"
    )
    lines.append("-" * 70)
    for row in top_yes.iter_rows(named=True):
        yr = row["yes_rate"]
        lines.append(
            f"{str(row['tag'])[:34]:<35} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}%"
        )
    lines.append("")

    return "\n".join(lines)


def section_question_patterns(resolved: pl.DataFrame) -> str:
    """Section 3: Question patterns and YES/NO rates."""
    lines = ["# 3. Question Pattern Analysis\n"]

    lines.append("## Will X happen? questions (expecting NO)")
    # Analyze question structure
    will_q = resolved.filter(pl.col("question").str.starts_with("Will"))
    lines.append(f"'Will ...' questions: {will_q.height:,}")
    lines.append(
        f"  YES won: {will_q.filter(pl.col('yes_won')).height:,} "
        f"({will_q['yes_won'].mean()*100:.1f}%)"
    )
    lines.append("")

    # Various question patterns
    patterns = {
        "Will": "Will",
        "Who will": "Who will",
        "What will": "What will",
        "When will": "When will",
        "How": "How",
        "above": "above",
        "below": "below",
        "over": "over",
        "under": "under",
        "yes/no binary": None,  # special
        "before": "before",
        "by": "by",
        "win": "win",
        "price": "price",
    }

    lines.append("## YES/NO rates by question pattern")
    lines.append(
        f"{'Pattern':<25} {'Count':>8} {'YES rate':>10} {'NO rate':>10} {'vs base':>10}"
    )
    lines.append("-" * 70)

    for label, pat in patterns.items():
        if pat is None:
            continue
        subset = resolved.filter(
            pl.col("question").str.to_lowercase().str.contains(pat.lower())
        )
        if subset.height >= 50:
            yr = subset["yes_won"].mean()
            lines.append(
                f"{label:<25} {subset.height:>8,} "
                f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}% {(yr-0.381)*100:>+9.1f}pp"
            )
    lines.append("")

    # "Will ... before/by" patterns (deadlines)
    lines.append("## Deadline questions ('before' or 'by' with date)")
    deadline_q = resolved.filter(
        pl.col("question").str.to_lowercase().str.contains("before|by 20")
    )
    if deadline_q.height > 0:
        yr = deadline_q["yes_won"].mean()
        lines.append(
            f"Deadline questions: {deadline_q.height:,} markets, "
            f"YES rate: {yr*100:.1f}%, NO rate: {(1-yr)*100:.1f}%"
        )
    lines.append("")

    # Price/threshold questions
    lines.append("## Price/threshold questions")
    for pat, label in [
        ("above", "Price above X"),
        ("below", "Price below X"),
        ("over", "Over X"),
        ("under", "Under X"),
        ("higher than", "Higher than"),
        ("lower than", "Lower than"),
        ("reach", "Will reach"),
        ("hit", "Will hit"),
        ("exceed", "Will exceed"),
        ("drop", "Will drop"),
    ]:
        subset = resolved.filter(
            pl.col("question").str.to_lowercase().str.contains(pat)
        )
        if subset.height >= 30:
            yr = subset["yes_won"].mean()
            lines.append(
                f"  {label:<25} {subset.height:>6,} markets  "
                f"YES: {yr*100:.1f}%  NO: {(1-yr)*100:.1f}%  vs base: {(yr-0.381)*100:>+.1f}pp"
            )
    lines.append("")

    return "\n".join(lines)


def section_entry_price_analysis(data: dict) -> str:
    """Section 4: Entry price analysis — where is NO overpriced?"""
    lines = ["# 4. Entry Price vs Outcome Analysis\n"]
    lines.append(
        "Key insight: if market prices YES at p, actual P(YES) > p means YES is underpriced."
    )
    lines.append(
        "Conversely, if P(YES) < p, YES is overpriced and NO is UNDERPRICED.\n"
    )

    resolved = data["resolved"]
    pnl = data["pnl"]

    # Get weighted avg YES entry price per market (across all traders)
    mkt_avg_price = (
        pnl.group_by("condition_id")
        .agg(
            pl.col("wavg_yes_entry_price").mean().alias("avg_yes_price"),
            pl.col("market_volume").sum().alias("total_volume"),
            pl.col("trader").n_unique().alias("n_traders"),
        )
        .collect()
    )

    # Join with resolution data
    price_outcome = mkt_avg_price.join(
        resolved.select("condition_id", "yes_won"),
        on="condition_id",
        how="inner",
    ).filter(pl.col("avg_yes_price").is_not_null())

    # Bucket by average YES price
    price_outcome = price_outcome.with_columns(
        (pl.col("avg_yes_price") * 10).floor().cast(pl.Int64).clip(0, 9).alias("price_decile")
    )

    by_decile = (
        price_outcome.group_by("price_decile")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("actual_yes_rate"),
            pl.col("avg_yes_price").mean().alias("avg_implied_yes"),
            pl.col("total_volume").sum().alias("total_vol"),
            pl.col("total_volume").mean().alias("avg_vol"),
        )
        .sort("price_decile")
    )

    lines.append("## YES outcome rate by implied price decile")
    lines.append(
        "Price bucket = avg YES entry price across all traders in that market."
    )
    lines.append(
        "If actual > implied: YES is underpriced (buy YES). If actual < implied: NO is underpriced (buy NO).\n"
    )
    lines.append(
        f"{'Decile':<8} {'Price':>8} {'Count':>8} {'Actual YES':>12} {'Implied':>10} {'Edge':>10} {'Avg Vol':>12}"
    )
    lines.append("-" * 78)

    for row in by_decile.iter_rows(named=True):
        d = row["price_decile"]
        lo = d / 10
        hi = (d + 1) / 10
        actual = row["actual_yes_rate"]
        implied = row["avg_implied_yes"]
        edge = actual - implied
        lines.append(
            f"{lo:.1f}-{hi:.1f}  {implied:>7.1%} {row['count']:>8,} "
            f"{actual:>11.1%} {implied:>9.1%} {edge:>+9.1%} ${row['avg_vol']:>10,.0f}"
        )

    lines.append("")
    lines.append("**Reading**: Positive edge = YES underpriced, buy YES.")
    lines.append("             Negative edge = YES overpriced, buy NO.")
    lines.append("")

    # Focus on where NO edge is biggest
    lines.append("## Where is the NO edge biggest?")
    lines.append("Markets where actual YES rate << implied YES price → NO is underpriced\n")

    # Look at high YES-price markets where NO actually won
    high_yes_price = price_outcome.filter(pl.col("avg_yes_price") >= 0.6)
    yr = high_yes_price["yes_won"].mean()
    lines.append(
        f"Markets priced YES >= 60%: {high_yes_price.height:,} markets, "
        f"actual YES rate: {yr*100:.1f}% (expected ~60%+)"
    )
    lines.append(f"  → Edge for NO: {(0.6 - yr)*100:+.1f}pp when buying NO at 40%")
    lines.append("")

    mid_yes_price = price_outcome.filter(
        (pl.col("avg_yes_price") >= 0.4) & (pl.col("avg_yes_price") < 0.6)
    )
    yr = mid_yes_price["yes_won"].mean()
    lines.append(
        f"Markets priced YES 40-60%: {mid_yes_price.height:,} markets, "
        f"actual YES rate: {yr*100:.1f}% (expected ~50%)"
    )
    lines.append(f"  → Edge for NO: {(0.5 - yr)*100:+.1f}pp when NO is priced at ~50%")
    lines.append("")

    low_yes_price = price_outcome.filter(pl.col("avg_yes_price") < 0.4)
    yr = low_yes_price["yes_won"].mean()
    lines.append(
        f"Markets priced YES < 40%: {low_yes_price.height:,} markets, "
        f"actual YES rate: {yr*100:.1f}% (expected ~40%-)"
    )
    lines.append(f"  → Edge for NO: {(0.4 - yr)*100:+.1f}pp (already pricing in low YES)")
    lines.append("")

    return "\n".join(lines)


def section_volume_weighted(data: dict) -> str:
    """Section 5: Volume-weighted NO edge analysis."""
    lines = ["# 5. Volume-Weighted NO Edge\n"]

    resolved = data["resolved"]
    pnl = data["pnl"]

    # Compute market-level volume
    mkt_vol = (
        pnl.group_by("condition_id")
        .agg(pl.col("market_volume").sum().alias("total_volume"))
        .collect()
    )

    vol_resolved = mkt_vol.join(
        resolved.select("condition_id", "yes_won", "neg_risk"),
        on="condition_id",
        how="inner",
    )

    # Volume tiers
    vol_resolved = vol_resolved.with_columns(
        pl.when(pl.col("total_volume") < 1000)
        .then(pl.lit("micro (<$1K)"))
        .when(pl.col("total_volume") < 10_000)
        .then(pl.lit("small ($1K-10K)"))
        .when(pl.col("total_volume") < 100_000)
        .then(pl.lit("medium ($10K-100K)"))
        .when(pl.col("total_volume") < 1_000_000)
        .then(pl.lit("large ($100K-1M)"))
        .otherwise(pl.lit("mega (>$1M)"))
        .alias("vol_tier")
    )

    by_vol = (
        vol_resolved.group_by("vol_tier")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("yes_rate"),
            pl.col("total_volume").sum().alias("total_vol"),
        )
        .sort("yes_rate")
    )

    lines.append(
        f"{'Volume Tier':<20} {'Count':>8} {'YES rate':>10} {'NO rate':>10} {'vs base':>10} {'Total Vol':>15}"
    )
    lines.append("-" * 80)
    for row in by_vol.iter_rows(named=True):
        yr = row["yes_rate"]
        lines.append(
            f"{row['vol_tier']:<20} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}% {(yr-0.381)*100:>+9.1f}pp ${row['total_vol']:>13,.0f}"
        )
    lines.append("")

    # By neg_risk AND volume
    lines.append("## neg_risk x Volume Tier")
    by_neg_vol = (
        vol_resolved.group_by("neg_risk", "vol_tier")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("yes_rate"),
        )
        .sort("neg_risk", "yes_rate")
    )

    lines.append(
        f"{'neg_risk':<8} {'Volume Tier':<20} {'Count':>8} {'YES rate':>10} {'NO rate':>10}"
    )
    lines.append("-" * 62)
    for row in by_neg_vol.iter_rows(named=True):
        yr = row["yes_rate"]
        lines.append(
            f"{str(row['neg_risk']):<8} {row['vol_tier']:<20} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}%"
        )
    lines.append("")

    return "\n".join(lines)


def section_no_trader_pnl(data: dict) -> str:
    """Section 6: PnL analysis for NO-side traders."""
    lines = ["# 6. NO-Side Trader PnL Analysis\n"]

    pnl = data["pnl"]
    resolved = data["resolved"]
    mvf = data["mvf"]

    # Join PnL with resolution data
    pnl_res = (
        pnl.join(
            pl.LazyFrame(resolved.select("condition_id", "yes_won")),
            on="condition_id",
            how="inner",
        )
        .with_columns(
            (pl.col("net_yes_tokens") <= 0).alias("bet_no")  # negative or zero net_yes = bet NO
        )
        .collect()
    )

    # Overall: NO bettors vs YES bettors
    no_bettors = pnl_res.filter(pl.col("bet_no"))
    yes_bettors = pnl_res.filter(~pl.col("bet_no"))

    lines.append("## Overall NO vs YES bettor performance")
    lines.append(f"{'Side':<8} {'Positions':>12} {'Avg PnL':>12} {'Med PnL':>12} {'Win Rate':>10}")
    lines.append("-" * 58)

    for label, df in [("NO", no_bettors), ("YES", yes_bettors)]:
        won = df.filter(pl.col("market_pnl") > 0).height
        wr = won / df.height if df.height > 0 else 0
        lines.append(
            f"{label:<8} {df.height:>12,} "
            f"${df['market_pnl'].mean():>10,.2f} "
            f"${df['market_pnl'].median():>10,.2f} "
            f"{wr*100:>9.1f}%"
        )
    lines.append("")

    # NO bettors by yes_won outcome
    lines.append("## NO bettor PnL by actual outcome")
    for outcome, label in [(False, "NO won (correct)"), (True, "YES won (wrong)")]:
        subset = no_bettors.filter(pl.col("yes_won") == outcome)
        if subset.height > 0:
            lines.append(
                f"  {label}: {subset.height:,} positions, "
                f"avg PnL: ${subset['market_pnl'].mean():,.2f}, "
                f"med PnL: ${subset['market_pnl'].median():,.2f}"
            )
    lines.append("")

    # NO bettors by MVF bucket
    lines.append("## NO bettor PnL by MVF bucket")
    no_with_mvf = no_bettors.join(mvf.select("trader", "mvf"), on="trader", how="left")
    no_with_mvf = no_with_mvf.with_columns(
        pl.when(pl.col("mvf") < 0.1)
        .then(pl.lit("pure_taker"))
        .when(pl.col("mvf") < 0.3)
        .then(pl.lit("taker_leaning"))
        .when(pl.col("mvf") < 0.7)
        .then(pl.lit("mixed"))
        .when(pl.col("mvf") < 0.9)
        .then(pl.lit("maker_leaning"))
        .otherwise(pl.lit("pure_maker"))
        .alias("mvf_bucket")
    )

    by_mvf = (
        no_with_mvf.group_by("mvf_bucket")
        .agg(
            pl.len().alias("count"),
            pl.col("market_pnl").mean().alias("avg_pnl"),
            pl.col("market_pnl").median().alias("med_pnl"),
            (pl.col("market_pnl") > 0).mean().alias("win_rate"),
            pl.col("market_pnl").sum().alias("total_pnl"),
        )
        .sort("avg_pnl", descending=True)
    )

    lines.append(
        f"{'MVF Bucket':<15} {'Positions':>10} {'Avg PnL':>12} {'Med PnL':>12} {'Win Rate':>10} {'Total PnL':>15}"
    )
    lines.append("-" * 80)
    for row in by_mvf.iter_rows(named=True):
        lines.append(
            f"{row['mvf_bucket']:<15} {row['count']:>10,} "
            f"${row['avg_pnl']:>10,.2f} "
            f"${row['med_pnl']:>10,.2f} "
            f"{row['win_rate']*100:>9.1f}% "
            f"${row['total_pnl']:>13,.0f}"
        )
    lines.append("")

    return "\n".join(lines)


def section_temporal_no_edge(resolved: pl.DataFrame) -> str:
    """Section 7: Temporal evolution of the NO edge."""
    lines = ["# 7. Temporal Evolution of the NO Edge\n"]

    resolved_with_qtr = resolved.filter(
        pl.col("resolved_at").is_not_null()
    ).with_columns(
        pl.col("resolved_at").dt.year().alias("year"),
        (
            pl.col("resolved_at").dt.year().cast(pl.Utf8)
            + "Q"
            + ((pl.col("resolved_at").dt.month() - 1) // 3 + 1).cast(pl.Utf8)
        ).alias("quarter"),
    )

    by_qtr = (
        resolved_with_qtr.filter(pl.col("quarter").is_not_null())
        .group_by("quarter")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("yes_rate"),
        )
        .sort("quarter")
    )

    lines.append(f"{'Quarter':<10} {'Count':>8} {'YES rate':>10} {'NO rate':>10} {'NO skew':>10}")
    lines.append("-" * 55)
    for row in by_qtr.iter_rows(named=True):
        yr = row["yes_rate"]
        lines.append(
            f"{row['quarter']:<10} {row['count']:>8,} "
            f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}% {(1-yr-0.619)*100:>+9.1f}pp"
        )
    lines.append("")

    # Is the NO edge growing or shrinking?
    lines.append("## Trend: Is the NO edge stable?")
    recent = by_qtr.filter(pl.col("quarter") >= "2025Q1")
    early = by_qtr.filter(pl.col("quarter") < "2025Q1")
    if recent.height > 0 and early.height > 0:
        # Volume-weighted average
        recent_rate = (
            (recent["yes_rate"] * recent["count"]).sum() / recent["count"].sum()
        )
        early_rate = (
            (early["yes_rate"] * early["count"]).sum() / early["count"].sum()
        )
        lines.append(
            f"Pre-2025: YES rate = {early_rate*100:.1f}%, NO rate = {(1-early_rate)*100:.1f}%"
        )
        lines.append(
            f"2025+:    YES rate = {recent_rate*100:.1f}%, NO rate = {(1-recent_rate)*100:.1f}%"
        )
        lines.append(
            f"Trend:    NO rate shift = {((1-recent_rate)-(1-early_rate))*100:+.1f}pp"
        )
    lines.append("")

    return "\n".join(lines)


def section_neg_risk_deep_dive(resolved: pl.DataFrame) -> str:
    """Section 8: neg_risk deep dive — multi-outcome markets."""
    lines = ["# 8. neg_risk Markets: Multi-Outcome NO Edge\n"]
    lines.append(
        "neg_risk markets are multi-outcome (e.g., 'Who will win?'). "
        "In these markets, each outcome token's YES side competes against "
        "all other outcomes. Only ONE token resolves YES; all others resolve NO."
    )
    lines.append(
        "This creates a *structural* NO bias: if there are N outcomes, "
        "the base rate for any single token's YES is ~1/N, and NO is ~(N-1)/N.\n"
    )

    # neg_risk YES rate
    neg_risk = resolved.filter(pl.col("neg_risk") == True)
    standard = resolved.filter(pl.col("neg_risk") == False)

    if neg_risk.height > 0 and standard.height > 0:
        nr_yes = neg_risk["yes_won"].mean()
        std_yes = standard["yes_won"].mean()
        lines.append(f"neg_risk markets:  {neg_risk.height:,} markets, YES rate: {nr_yes*100:.1f}%, NO rate: {(1-nr_yes)*100:.1f}%")
        lines.append(f"Standard markets:  {standard.height:,} markets, YES rate: {std_yes*100:.1f}%, NO rate: {(1-std_yes)*100:.1f}%")
        lines.append(f"Difference:        {(nr_yes - std_yes)*100:+.1f}pp YES rate")
        lines.append("")

        # How many outcomes per event in neg_risk?
        nr_with_event = neg_risk.filter(pl.col("event_id").is_not_null())
        if nr_with_event.height > 0:
            outcomes_per_event = (
                nr_with_event.group_by("event_id")
                .agg(pl.len().alias("n_outcomes"))
                .sort("n_outcomes", descending=True)
            )
            lines.append("## Outcomes per event in neg_risk markets")
            lines.append(
                f"  Mean: {outcomes_per_event['n_outcomes'].mean():.1f}, "
                f"Median: {outcomes_per_event['n_outcomes'].median():.1f}, "
                f"Max: {outcomes_per_event['n_outcomes'].max()}"
            )

            # YES rate by number of outcomes
            lines.append("\n## YES rate by number of outcomes")
            nr_with_n = nr_with_event.join(
                outcomes_per_event, on="event_id", how="left"
            )
            by_n = (
                nr_with_n.group_by("n_outcomes")
                .agg(
                    pl.len().alias("count"),
                    pl.col("yes_won").mean().alias("yes_rate"),
                )
                .filter(pl.col("count") >= 20)
                .sort("n_outcomes")
            )
            lines.append(
                f"{'N outcomes':<12} {'Count':>8} {'YES rate':>10} {'NO rate':>10} {'1/N':>8} {'NO edge':>10}"
            )
            lines.append("-" * 65)
            for row in by_n.iter_rows(named=True):
                yr = row["yes_rate"]
                expected = 1 / row["n_outcomes"]
                no_edge = (1 - yr) - (1 - expected)  # actual NO - expected NO
                lines.append(
                    f"{row['n_outcomes']:<12} {row['count']:>8,} "
                    f"{yr*100:>9.1f}% {(1-yr)*100:>9.1f}% "
                    f"{expected*100:>7.1f}% {no_edge*100:>+9.1f}pp"
                )
    lines.append("")

    return "\n".join(lines)


def section_profitable_no_strategies(data: dict) -> str:
    """Section 9: Synthesize profitable NO strategies."""
    lines = ["# 9. Profitable NO Strategies Synthesis\n"]
    lines.append("Based on all the evidence above, here are the actionable NO edges:\n")

    lines.append("## Strategy 1: Structural NO on Multi-Outcome Markets")
    lines.append("- **What**: Buy NO on all tokens in neg_risk events with 5+ outcomes")
    lines.append("- **Why**: Structural 1/N bias means each YES is overpriced vs actual outcome rate")
    lines.append("- **Size**: Proportional to (1 - 1/N - NO_price). If NO_price < (N-1)/N, there's edge")
    lines.append("- **Risk**: Correlated outcomes (e.g., if 2 candidates are similar)")
    lines.append("")

    lines.append("## Strategy 2: Consensus Copy NO-only (from backtester)")
    lines.append("- **What**: Copy skilled pure-taker NO bets when 5-7+ traders agree")
    lines.append("- **Why**: Backtester shows NO-only + pure_taker has 50.6% positive Sharpe rate")
    lines.append("- **Config**: NO-only, MVF<0.1, 60s delay, wide price band")
    lines.append("- **Edge**: Price selection effect — delayed entry improves NO entry prices")
    lines.append("")

    lines.append("## Strategy 3: Deadline Contrarian")
    lines.append("- **What**: Buy NO on 'Will X happen by DATE?' markets")
    lines.append("- **Why**: Deadline markets have the strongest NO bias — most events don't happen on schedule")
    lines.append("- **Filter**: Volume >$10K, entry before 50% of market lifetime")
    lines.append("- **Risk**: Black swan events (sudden YES resolution)")
    lines.append("")

    lines.append("## Strategy 4: Category-Specific NO")
    lines.append("- **What**: Focus NO bets on categories with highest NO win rates")
    lines.append("- **Why**: Some categories structurally resolve NO more often")
    lines.append("- **Filter**: Volume-weighted NO rate > 70% in category, >$10K volume")
    lines.append("")

    lines.append("## Risk Factors Across All Strategies")
    lines.append("1. **The NO edge may be priced in**: If market makers know about the 62% base rate, NO tokens may already be priced at a premium")
    lines.append("2. **Fees**: Even 0-2% fees can erode thin edges on high-probability NO bets")
    lines.append("3. **Capital lockup**: NO bets at p=0.70 tie up $70 to win $30 — low capital efficiency")
    lines.append("4. **Tail risk**: When NO is 'obvious', the loss from being wrong is large (asymmetric payoff)")
    lines.append("5. **Temporal instability**: The NO edge varies by quarter; check if it persists in 2026")
    lines.append("")

    return "\n".join(lines)


def main():
    print("Loading data...")
    data = load_data()
    resolved = data["resolved"]
    print(f"Loaded {resolved.height:,} resolved markets")

    sections = []
    print("Section 1: Base rates...")
    sections.append(section_base_rates(resolved))
    print("Section 2: Tags...")
    sections.append(section_tags_analysis(resolved))
    print("Section 3: Question patterns...")
    sections.append(section_question_patterns(resolved))
    print("Section 4: Entry price analysis...")
    sections.append(section_entry_price_analysis(data))
    print("Section 5: Volume weighted...")
    sections.append(section_volume_weighted(data))
    print("Section 6: NO trader PnL...")
    sections.append(section_no_trader_pnl(data))
    print("Section 7: Temporal...")
    sections.append(section_temporal_no_edge(resolved))
    print("Section 8: neg_risk deep dive...")
    sections.append(section_neg_risk_deep_dive(resolved))
    print("Section 9: Strategy synthesis...")
    sections.append(section_profitable_no_strategies(data))

    # Write output
    header = """# Overpriced NO: Edge Research in Polymarket's NO-Skewed Market

**Date**: 2026-02-18
**Data**: 390K resolved markets, 70.9M trader-market PnL rows, Nov 2022 - Jan 2026

**Core question**: The Polymarket ecosystem has a 62:38 NO:YES resolution skew.
Where is the NO side overpriced (creating YES opportunities)?
Where is the NO side underpriced (creating NO opportunities)?
And what structural edges exist within this skew?

---

"""
    full_text = header + "\n---\n\n".join(sections)
    out_path = OUT_DIR / "01_no_edge_analysis.md"
    out_path.write_text(full_text)
    print(f"\nWrote analysis to {out_path}")

    # Also save key numbers as JSON for downstream use
    stats = {
        "total_resolved": resolved.height,
        "yes_won": int(resolved.filter(pl.col("yes_won")).height),
        "no_won": int(resolved.filter(~pl.col("yes_won")).height),
        "yes_rate": float(resolved["yes_won"].mean()),
        "no_rate": 1 - float(resolved["yes_won"].mean()),
    }
    json_path = OUT_DIR / "01_stats.json"
    json_path.write_text(json.dumps(stats, indent=2))
    print(f"Wrote stats to {json_path}")


if __name__ == "__main__":
    main()
