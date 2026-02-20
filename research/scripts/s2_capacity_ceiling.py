"""S2 Capacity Ceiling: How much capital can "Will" binary markets absorb?

At what capital level does S2 saturate?
- Per-market: how much can we deploy without moving the price?
- Per-month: how many markets are available?
- Combined: max capital → max monthly throughput → max PnL

Usage:
    PYTHONPATH=. uv run python scripts/s2_capacity_ceiling.py
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

DATA_DIR = Path("data")
COMPACT_DIR = DATA_DIR / "compact"


def main():
    # Load markets
    markets = pl.read_parquet(DATA_DIR / "metadata" / "markets.parquet")
    resolved = pl.read_parquet(DATA_DIR / "derived" / "markets_resolved.parquet")
    token_map = pl.read_parquet(DATA_DIR / "metadata" / "token_map.parquet").select(
        "asset_id", "condition_id", "token_index"
    )

    for c in ["resolved_at"]:
        d = resolved.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            resolved = resolved.with_columns(pl.col(c).dt.replace_time_zone(None))

    df = resolved.join(
        markets.select("condition_id", "question"),
        on="condition_id",
        how="inner",
    )
    df = df.filter(
        pl.col("question").str.to_lowercase().str.starts_with("will ")
        & (pl.col("resolution_value") == 1)
        & (pl.col("resolved_at") >= pl.datetime(2025, 1, 1))
    )

    pnl = pl.read_parquet(DATA_DIR / "derived" / "trader_market_pnl.parquet")
    market_prices = pnl.group_by("condition_id").agg(
        pl.col("wavg_yes_entry_price").median().alias("median_yes_price"),
    )
    df = df.join(market_prices, on="condition_id", how="inner")
    df = df.filter(
        (pl.col("median_yes_price") >= 0.15) & (pl.col("median_yes_price") <= 0.40)
    )

    market_ids = set(df["condition_id"].to_list())

    # Load trades
    print(f"[scan] Scanning trades for {len(market_ids):,} markets...")
    trades = (
        pl.scan_parquet(str(COMPACT_DIR / "compact_*.parquet"))
        .filter(pl.col("condition_id").is_in(list(market_ids)))
        .select("condition_id", "asset_id", "side", "amount_usd", "timestamp")
        .collect()
    )
    trades = trades.join(token_map, on="asset_id", how="inner")

    # Per-market volume + timing
    per_market = trades.group_by("condition_id").agg(
        pl.col("amount_usd").sum().alias("total_volume"),
        pl.col("timestamp").min().alias("first_trade"),
        pl.col("timestamp").max().alias("last_trade"),
        pl.len().alias("n_trades"),
    )
    per_market = per_market.join(
        df.select("condition_id", "yes_won", "resolved_at", "median_yes_price"),
        on="condition_id",
        how="inner",
    )

    for c in ["first_trade", "last_trade"]:
        d = per_market.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            per_market = per_market.with_columns(pl.col(c).dt.replace_time_zone(None))

    per_market = per_market.with_columns(
        (pl.col("resolved_at") - pl.col("first_trade")).dt.total_days().alias("lockup_days"),
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("month"),
        (1.0 - pl.col("median_yes_price")).alias("no_entry_price"),
    )

    # Fast markets
    fast = per_market.filter(
        pl.col("lockup_days").is_not_null() & (pl.col("lockup_days") < 3)
    )

    months = sorted(fast.filter(pl.col("month").is_not_null())["month"].unique().to_list())
    months = [m for m in months if m >= "2025-01"]

    print(f"\nUniverse: {fast.height:,} fast 'Will' binary markets (YES 15-40%, <3d lockup)")
    print(f"NO win rate: {float((~fast['yes_won']).mean())*100:.1f}%")
    print(f"Median volume/market: ${float(fast['total_volume'].median()):,.0f}")
    print(f"Median lockup: {float(fast['lockup_days'].median()):.1f} days")

    # ===================================================================
    # 1. PER-MARKET CAPACITY: How much can we load per market?
    # ===================================================================
    print(f"\n{'='*70}")
    print("  1. PER-MARKET CAPACITY (% of volume thresholds)")
    print(f"{'='*70}\n")

    # If we limit our bet to X% of total market volume, how big can each bet be?
    print(f"  {'Impact %':>10} {'Median bet':>12} {'P25 bet':>10} {'P75 bet':>10} "
          f"{'Markets≥$50':>12} {'Markets≥$100':>13}")
    print(f"  {'-'*70}")

    for pct in [1, 2, 5, 10, 20]:
        bet_col = fast["total_volume"] * pct / 100
        med = float(bet_col.median())
        p25 = float(bet_col.quantile(0.25))
        p75 = float(bet_col.quantile(0.75))
        ge50 = int((bet_col >= 50).sum())
        ge100 = int((bet_col >= 100).sum())
        print(f"  {pct:>9}% ${med:>10,.0f} ${p25:>8,.0f} ${p75:>8,.0f} "
              f"{ge50:>11,} ({ge50/fast.height*100:.0f}%) "
              f"{ge100:>5,} ({ge100/fast.height*100:.0f}%)")

    # ===================================================================
    # 2. MONTHLY MARKET SUPPLY
    # ===================================================================
    print(f"\n{'='*70}")
    print("  2. MONTHLY MARKET SUPPLY")
    print(f"{'='*70}\n")

    print(f"  {'Month':<10} {'Markets':>8} {'Med Vol':>10} {'Med Lockup':>11} "
          f"{'NO HR':>7} {'Total Vol':>12}")
    print(f"  {'-'*60}")

    monthly_stats = []
    for m in months:
        mf = fast.filter(pl.col("month") == m)
        if mf.height == 0:
            continue
        n = mf.height
        med_vol = float(mf["total_volume"].median())
        med_lock = float(mf["lockup_days"].median())
        no_hr = float((~mf["yes_won"]).mean())
        tot_vol = float(mf["total_volume"].sum())
        monthly_stats.append({
            "month": m, "n": n, "med_vol": med_vol, "med_lock": med_lock,
            "no_hr": no_hr, "tot_vol": tot_vol
        })
        print(f"  {m:<10} {n:>8} ${med_vol:>8,.0f} {med_lock:>10.1f}d "
              f"{no_hr*100:>6.1f}% ${tot_vol:>10,.0f}")

    avg_markets = sum(s["n"] for s in monthly_stats) / len(monthly_stats)
    avg_vol = sum(s["tot_vol"] for s in monthly_stats) / len(monthly_stats)
    print(f"  {'-'*60}")
    print(f"  {'Average':<10} {avg_markets:>8.0f} {'':>10} {'':>11} "
          f"{'':>7} ${avg_vol:>10,.0f}")

    # ===================================================================
    # 3. CAPACITY CEILING AT DIFFERENT CAPITAL LEVELS
    # ===================================================================
    print(f"\n{'='*70}")
    print("  3. CAPACITY CEILING: PnL at different capital levels")
    print(f"{'='*70}\n")

    # For each capital level, simulate:
    # - Bet size = min(capital / N_slots, max_bet_per_market)
    # - max_bet_per_market = market_impact_pct * median_volume
    # - Dual-sided: $bet on taker + $bet on maker = 2*bet per market, 2*bet capital per market

    IMPACT_PCT = 0.05  # max 5% of market volume
    SPREAD_EDGE = 0.01

    capital_levels = [300, 500, 1000, 2000, 5000, 10000, 20000]

    print(f"  Impact limit: {IMPACT_PCT*100:.0f}% of market volume (dual-sided)")
    print(f"  Lockup: fast markets only (<3 days)")
    print()
    print(f"  {'Capital':>10} {'Bet/side':>10} {'Slots':>6} {'Bets/mo':>8} "
          f"{'PnL/mo':>10} {'ROI/mo':>8} {'Ann ROI':>8} {'Saturated?':>11}")
    print(f"  {'-'*76}")

    for capital in capital_levels:
        # How big can each bet be? Constrained by:
        # 1) Capital per slot: capital / n_slots (n_slots = capital / (2*bet_per_side))
        # 2) Market impact: 5% of median market volume / 2 (each side)
        # Try different bet sizes to find the sweet spot

        best_pnl = 0
        best_cfg = {}

        for bet_per_side in [25, 50, 100, 200, 500, 1000]:
            capital_per_market = 2 * bet_per_side  # dual-sided
            n_slots = max(1, int(capital / capital_per_market))

            # Monthly sim: average across all months
            monthly_pnls = []
            monthly_bets = []
            for m in months:
                mf = fast.filter(pl.col("month") == m).sort("resolved_at")
                if mf.height == 0:
                    continue

                # Filter to markets with enough volume
                eligible = mf.filter(
                    pl.col("total_volume") * IMPACT_PCT >= bet_per_side
                )
                if eligible.height == 0:
                    monthly_pnls.append(0)
                    monthly_bets.append(0)
                    continue

                med_lockup = max(float(eligible["lockup_days"].median()), 1.0)
                max_bets = int(n_slots * 30 / med_lockup)
                placed = min(eligible.height, max_bets)

                selected = eligible.head(placed)
                month_pnl = 0.0
                for row in selected.iter_rows(named=True):
                    yes_p = row["median_yes_price"]
                    no_p = 1.0 - yes_p
                    no_won = not row["yes_won"]

                    # Taker side
                    if no_won:
                        month_pnl += bet_per_side * (1.0 - no_p) / no_p
                    else:
                        month_pnl -= bet_per_side

                    # Maker side (with spread edge)
                    eff_yes = yes_p + SPREAD_EDGE
                    if no_won:
                        month_pnl += bet_per_side * eff_yes / (1.0 - eff_yes)
                    else:
                        month_pnl -= bet_per_side

                monthly_pnls.append(month_pnl)
                monthly_bets.append(placed)

            avg_pnl = sum(monthly_pnls) / len(monthly_pnls) if monthly_pnls else 0
            avg_bets = sum(monthly_bets) / len(monthly_bets) if monthly_bets else 0

            if avg_pnl > best_pnl:
                best_pnl = avg_pnl
                best_cfg = {
                    "bet_per_side": bet_per_side,
                    "n_slots": n_slots,
                    "avg_bets": avg_bets,
                    "avg_pnl": avg_pnl,
                }

        if best_cfg:
            roi = best_cfg["avg_pnl"] / capital * 100
            ann_roi = roi * 12
            # Saturated = we're placing fewer bets than slots allow
            # (market supply is the bottleneck, not capital)
            saturated = best_cfg["avg_bets"] < best_cfg["n_slots"] * 20  # rough threshold
            sat_str = "YES" if saturated else "no"
            print(f"  ${capital:>8,} ${best_cfg['bet_per_side']:>8}/side "
                  f"{best_cfg['n_slots']:>5} {best_cfg['avg_bets']:>7.0f} "
                  f"${best_cfg['avg_pnl']:>8,.0f} {roi:>6.1f}% {ann_roi:>6.0f}% "
                  f"{'':>3}{sat_str}")

    # ===================================================================
    # 4. DIMINISHING RETURNS CURVE
    # ===================================================================
    print(f"\n{'='*70}")
    print("  4. DIMINISHING RETURNS: PnL vs Capital (dual, 5% impact)")
    print(f"{'='*70}\n")

    detailed_capitals = [100, 200, 300, 500, 750, 1000, 1500, 2000, 3000,
                         5000, 7500, 10000, 15000, 20000]

    print(f"  {'Capital':>10} {'PnL/mo':>10} {'ROI/mo':>8} {'Marginal ROI':>13} {'PnL/$1K':>8}")
    print(f"  {'-'*52}")

    prev_pnl = 0
    prev_cap = 0
    for capital in detailed_capitals:
        # Optimal bet sizing: try all bet sizes
        best_pnl = 0
        for bet_per_side in [25, 50, 100, 200, 500, 1000, 2000]:
            if bet_per_side * 2 > capital:
                continue
            n_slots = max(1, int(capital / (2 * bet_per_side)))
            monthly_pnls = []
            for m in months:
                mf = fast.filter(pl.col("month") == m).sort("resolved_at")
                if mf.height == 0:
                    continue
                eligible = mf.filter(
                    pl.col("total_volume") * IMPACT_PCT >= bet_per_side
                )
                if eligible.height == 0:
                    monthly_pnls.append(0)
                    continue
                med_lockup = max(float(eligible["lockup_days"].median()), 1.0)
                max_bets = int(n_slots * 30 / med_lockup)
                placed = min(eligible.height, max_bets)
                selected = eligible.head(placed)
                month_pnl = 0.0
                for row in selected.iter_rows(named=True):
                    yes_p = row["median_yes_price"]
                    no_p = 1.0 - yes_p
                    no_won = not row["yes_won"]
                    if no_won:
                        month_pnl += bet_per_side * (1.0 - no_p) / no_p
                    else:
                        month_pnl -= bet_per_side
                    eff_yes = yes_p + SPREAD_EDGE
                    if no_won:
                        month_pnl += bet_per_side * eff_yes / (1.0 - eff_yes)
                    else:
                        month_pnl -= bet_per_side
                monthly_pnls.append(month_pnl)
            avg_pnl = sum(monthly_pnls) / len(monthly_pnls) if monthly_pnls else 0
            if avg_pnl > best_pnl:
                best_pnl = avg_pnl

        roi = best_pnl / capital * 100
        marginal = (best_pnl - prev_pnl) / (capital - prev_cap) * 100 if capital > prev_cap else 0
        pnl_per_1k = best_pnl / (capital / 1000)
        print(f"  ${capital:>8,} ${best_pnl:>8,.0f} {roi:>6.1f}% {marginal:>11.1f}% ${pnl_per_1k:>6,.0f}")

        prev_pnl = best_pnl
        prev_cap = capital

    # ===================================================================
    # 5. ABSOLUTE CEILING
    # ===================================================================
    print(f"\n{'='*70}")
    print("  5. ABSOLUTE CEILING")
    print(f"{'='*70}\n")

    # If we could bet on EVERY fast market at 5% impact, what's the max monthly PnL?
    # (unlimited capital, limited by market supply)
    unlimited_monthly = []
    for m in months:
        mf = fast.filter(pl.col("month") == m).sort("resolved_at")
        if mf.height == 0:
            continue
        month_pnl = 0.0
        month_deployed = 0.0
        for row in mf.iter_rows(named=True):
            bet = row["total_volume"] * IMPACT_PCT / 2  # per side at 5% impact
            if bet < 10:  # minimum viable
                continue
            yes_p = row["median_yes_price"]
            no_p = 1.0 - yes_p
            no_won = not row["yes_won"]

            # Taker side
            if no_won:
                month_pnl += bet * (1.0 - no_p) / no_p
            else:
                month_pnl -= bet
            # Maker side
            eff_yes = yes_p + SPREAD_EDGE
            if no_won:
                month_pnl += bet * eff_yes / (1.0 - eff_yes)
            else:
                month_pnl -= bet
            month_deployed += 2 * bet

        unlimited_monthly.append({"month": m, "pnl": month_pnl, "deployed": month_deployed})

    avg_unlimited = sum(r["pnl"] for r in unlimited_monthly) / len(unlimited_monthly)
    avg_deployed = sum(r["deployed"] for r in unlimited_monthly) / len(unlimited_monthly)

    print(f"  Unlimited capital, 5% impact limit, dual-sided:")
    print(f"  Avg monthly PnL:      ${avg_unlimited:>10,.0f}")
    print(f"  Avg monthly deployed: ${avg_deployed:>10,.0f}")
    print(f"  Implied capital need:  ~${avg_deployed/30:>8,.0f} (deployed/30 for 1-day rotation)")
    print(f"  PnL/deployed:         {avg_unlimited/avg_deployed*100:.1f}%")

    # With 50% haircut
    print(f"\n  With 50% haircut:")
    print(f"  Realistic monthly PnL: ${avg_unlimited*0.5:>10,.0f}")
    print(f"  Implied capital:       ~${avg_deployed/30*0.5:>8,.0f}")


if __name__ == "__main__":
    main()
