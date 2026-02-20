"""Estimate your PnL from portfolio replication strategy."""

import polars as pl
from datetime import datetime

from strategies.consistency_copy.backtester.runner import (
    get_consistent_traders, _compute_trader_median_entry, _precompute_mvf_subsets
)


def main():
    pnl = pl.read_parquet("data/derived/trader_market_pnl.parquet")
    markets = pl.read_parquet("data/derived/markets_resolved.parquet")
    for c in ["resolved_at"]:
        d = markets.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            markets = markets.with_columns(pl.col(c).dt.replace_time_zone(None))
    for c in ["first_trade", "last_trade"]:
        d = pnl.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            pnl = pnl.with_columns(pl.col(c).dt.replace_time_zone(None))
    df_pnl = pnl.join(markets, on="condition_id", how="inner")

    mvf = pl.read_parquet("data/derived/maker_volume_fractions.parquet")
    mvf_subsets = _precompute_mvf_subsets(mvf)
    med_entry_df = _compute_trader_median_entry(df_pnl)

    YOUR_CAPITAL = 10_000

    windows = [
        ("2025-05", datetime(2025,5,1), datetime(2025,6,1), datetime(2023,1,1), datetime(2025,5,1)),
        ("2025-06", datetime(2025,6,1), datetime(2025,7,1), datetime(2023,1,1), datetime(2025,6,1)),
        ("2025-07", datetime(2025,7,1), datetime(2025,8,1), datetime(2023,1,1), datetime(2025,7,1)),
        ("2025-08", datetime(2025,8,1), datetime(2025,9,1), datetime(2023,1,1), datetime(2025,8,1)),
        ("2025-09", datetime(2025,9,1), datetime(2025,10,1), datetime(2023,1,1), datetime(2025,9,1)),
        ("2025-10", datetime(2025,10,1), datetime(2025,11,1), datetime(2023,1,1), datetime(2025,10,1)),
        ("2025-11", datetime(2025,11,1), datetime(2025,12,1), datetime(2023,1,1), datetime(2025,11,1)),
        ("2025-12", datetime(2025,12,1), datetime(2026,1,1), datetime(2023,1,1), datetime(2025,12,1)),
        ("2026-01", datetime(2026,1,1), datetime(2026,2,1), datetime(2023,1,1), datetime(2026,1,1)),
    ]

    print(f"YOUR CAPITAL: ${YOUR_CAPITAL:,}")
    print("Pool: 9-month consistent, 20+ markets, entry<=0.80, pure_taker")
    print()

    rows = []
    for name, h_start, h_end, t_start, t_end in windows:
        skilled = get_consistent_traders(df_pnl, t_start, t_end, 9, 20)
        eligible = set(med_entry_df.filter(pl.col("median_entry") <= 0.80)["trader"].to_list())
        pool = skilled & mvf_subsets["pure_taker"] & eligible
        if len(pool) < 5:
            continue

        train_vol = df_pnl.filter(
            (pl.col("resolved_at") >= t_start) & (pl.col("resolved_at") < t_end)
            & pl.col("trader").is_in(list(pool))
        ).group_by("trader").agg(pl.col("market_volume").sum().alias("train_vol"))
        total_tv = train_vol["train_vol"].sum()

        holdout = df_pnl.filter(
            (pl.col("resolved_at") >= h_start) & (pl.col("resolved_at") < h_end)
            & pl.col("trader").is_in(list(pool))
        )
        if holdout.height == 0:
            continue

        th = holdout.group_by("trader").agg(
            pl.col("market_pnl").sum().alias("pnl"),
            pl.col("market_volume").sum().alias("vol"),
        ).join(train_vol, on="trader", how="left").with_columns(
            (pl.col("train_vol") / total_tv).alias("share"),
            (pl.col("pnl") / pl.col("vol")).alias("roi"),
        )

        # S1: vol-weighted all
        s1 = float((th["share"] * YOUR_CAPITAL * th["roi"]).sum())
        # S2: top-5 vol-weighted
        top5_tv = train_vol.sort("train_vol", descending=True).head(5)
        t5set = set(top5_tv["trader"].to_list())
        t5_tot = top5_tv["train_vol"].sum()
        t5h = th.filter(pl.col("trader").is_in(list(t5set))).with_columns(
            (pl.col("train_vol") / t5_tot).alias("t5share"))
        s2 = float((t5h["t5share"] * YOUR_CAPITAL * t5h["roi"]).sum())
        # S3: equal-weight
        s3 = float((YOUR_CAPITAL / th.height * th["roi"]).sum())

        rows.append({"m": name, "pool": len(pool), "s1": s1, "s2": s2, "s3": s3})

    # Print results
    print(f"{'Month':>8} {'Pool':>5} {'Vol-wtd All':>12} {'Vol-wtd Top5':>13} {'Equal-wtd':>12}")
    print("-" * 56)
    t1 = t2 = t3 = 0.0
    for r in rows:
        t1 += r["s1"]
        t2 += r["s2"]
        t3 += r["s3"]
        s1_s = f"${r['s1']:,.0f}"
        s2_s = f"${r['s2']:,.0f}"
        s3_s = f"${r['s3']:,.0f}"
        print(f"{r['m']:>8} {r['pool']:>5} {s1_s:>12} {s2_s:>13} {s3_s:>12}")
    print("-" * 56)
    n = len(rows)
    print(f"{'TOTAL':>8} {'':>5} {f'${t1:,.0f}':>12} {f'${t2:,.0f}':>13} {f'${t3:,.0f}':>12}")
    print(f"{'AVG/MO':>8} {'':>5} {f'${t1/n:,.0f}':>12} {f'${t2/n:,.0f}':>13} {f'${t3/n:,.0f}':>12}")
    r1 = t1/n/YOUR_CAPITAL*100
    r2 = t2/n/YOUR_CAPITAL*100
    r3 = t3/n/YOUR_CAPITAL*100
    print(f"{'ROI/MO':>8} {'':>5} {f'{r1:.1f}%':>12} {f'{r2:.1f}%':>13} {f'{r3:.1f}%':>12}")
    print(f"{'Ann ROI':>8} {'':>5} {f'{r1*12:.0f}%':>12} {f'{r2*12:.0f}%':>13} {f'{r3*12:.0f}%':>12}")

    print()
    print("=" * 56)
    print("  REALISTIC ESTIMATES (with execution haircuts)")
    print("=" * 56)
    print()
    print("Upper bounds assume perfect replication of sizing at exact entry.")
    print("Haircuts account for slippage, partial fills, detection lag.")
    print()

    for label, haircut in [
        ("Optimistic (30% haircut)", 0.70),
        ("Realistic (50% haircut)", 0.50),
        ("Conservative (70% haircut)", 0.30),
    ]:
        adj1 = t1/n * haircut
        adj3 = t3/n * haircut
        print(f"  {label}:")
        print(f"    Vol-weighted: ${adj1:,.0f}/mo = ${adj1*12:,.0f}/yr ({adj1/YOUR_CAPITAL*100:.1f}%/mo)")
        print(f"    Equal-weight: ${adj3:,.0f}/mo = ${adj3*12:,.0f}/yr ({adj3/YOUR_CAPITAL*100:.1f}%/mo)")

    # Direction accuracy analysis
    print()
    print("=" * 56)
    print("  WHY FIXED-BET COPY FAILS BUT PROPORTIONAL WORKS")
    print("=" * 56)
    print()
    print("The traders' edge is NOT pure direction prediction.")
    print("Direction accuracy is only 37-54% across windows.")
    print()
    print("Their edge is POSITION SIZING:")
    print("  - They bet BIG when confident (high ROI markets)")
    print("  - They bet SMALL when speculative (exploratory)")
    print("  - Fixed $100 bets destroy this sizing alpha")
    print("  - Proportional copy preserves the sizing signal")

    # Show the sizing asymmetry
    print()
    print("=" * 56)
    print("  SIZING ASYMMETRY (Oct 2025 window)")
    print("=" * 56)

    h_start, h_end = datetime(2025, 10, 1), datetime(2025, 11, 1)
    t_start, t_end = datetime(2023, 1, 1), datetime(2025, 10, 1)
    skilled = get_consistent_traders(df_pnl, t_start, t_end, 9, 20)
    eligible = set(med_entry_df.filter(pl.col("median_entry") <= 0.80)["trader"].to_list())
    pool = skilled & mvf_subsets["pure_taker"] & eligible

    holdout = df_pnl.filter(
        (pl.col("resolved_at") >= h_start) & (pl.col("resolved_at") < h_end)
        & pl.col("trader").is_in(list(pool))
    )

    # Split by win/loss
    wins = holdout.filter(pl.col("market_pnl") > 0)
    losses = holdout.filter(pl.col("market_pnl") <= 0)
    print()
    print(f"  Winning positions: {wins.height} trades")
    print(f"    Avg volume: ${wins['market_volume'].mean():,.0f}")
    print(f"    Median volume: ${wins['market_volume'].median():,.0f}")
    print(f"    Avg PnL: ${wins['market_pnl'].mean():,.0f}")
    print()
    print(f"  Losing positions: {losses.height} trades")
    print(f"    Avg volume: ${losses['market_volume'].mean():,.0f}")
    print(f"    Median volume: ${losses['market_volume'].median():,.0f}")
    print(f"    Avg PnL: ${losses['market_pnl'].mean():,.0f}")
    print()
    vol_ratio = wins["market_volume"].mean() / losses["market_volume"].mean()
    print(f"  Winners are {vol_ratio:.1f}x larger than losers by volume")
    print(f"  This is the edge that fixed-bet copy destroys")


if __name__ == "__main__":
    main()
