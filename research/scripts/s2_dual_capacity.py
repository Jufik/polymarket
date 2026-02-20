"""S2 Dual-Sided Capacity: Buy NO + Sell YES on the same markets.

Instead of choosing taker-NO or maker-sellYES, run both simultaneously.
Each accesses a different liquidity pool:
  - Taker-NO: takes from NO ask depth
  - Maker-sellYES: provides YES liquidity, filled by retail YES flow

Question: How much more volume can we deploy per market by using both sides?

Usage:
    PYTHONPATH=. uv run python scripts/s2_dual_capacity.py
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

DATA_DIR = Path("data")
COMPACT_DIR = DATA_DIR / "compact"

CAPITAL = 300.0
BET_SIZE = 50.0


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
    print(f"Target: {len(market_ids):,} 'Will' binary markets (YES 15-40%, 2025+)")

    # Load trades
    print("[scan] Scanning compact parquet...")
    trades = (
        pl.scan_parquet(str(COMPACT_DIR / "compact_*.parquet"))
        .filter(pl.col("condition_id").is_in(list(market_ids)))
        .select("condition_id", "asset_id", "side", "price", "amount_usd", "timestamp")
        .collect()
    )
    trades = trades.join(token_map, on="asset_id", how="inner")

    # Classify each trade by the TWO strategies' perspective
    trades = trades.with_columns(
        # YES-equivalent price
        pl.when(pl.col("token_index") == 0)
        .then(pl.col("price"))
        .otherwise(1.0 - pl.col("price"))
        .alias("yes_price"),
    )

    # Strategy A (Taker-NO): we BUY NO as taker
    # This happens when we take from the NO ask side:
    #   - We hit side=SELL on NO token (someone is selling NO, we buy)
    #   - OR we hit side=BUY on YES token where WE are the buyer... no.
    # Actually: taker-NO = we are the aggressor buying NO.
    # In the data, side=BUY means taker bought, side=SELL means taker sold.
    # For us to be taker buying NO:
    #   - side=BUY, token=NO: taker (us) buys NO ✓
    #   - side=SELL, token=YES: taker (us) sells YES = effectively buys NO ✓
    # But wait — we want to be taker buying NO token directly:
    #   side=BUY, token_index=1 → taker buys NO
    trades = trades.with_columns(
        # Volume available for taker-NO (us buying NO)
        ((pl.col("side") == "BUY") & (pl.col("token_index") == 1)).alias("is_taker_no_buy"),
        # Volume available for maker-sellYES (us posting YES ask, taker buys from us)
        ((pl.col("side") == "BUY") & (pl.col("token_index") == 0)).alias("is_yes_taker_buy"),
    )

    # Per-market volume by strategy
    per_market = trades.group_by("condition_id").agg(
        # Taker-NO: volume of NO buys (we compete for this)
        pl.col("amount_usd").filter(pl.col("is_taker_no_buy")).sum().alias("no_buy_volume"),
        pl.col("amount_usd").filter(pl.col("is_taker_no_buy")).len().alias("no_buy_count"),
        # Maker-sellYES: volume of YES buys by takers (flow we capture)
        pl.col("amount_usd").filter(pl.col("is_yes_taker_buy")).sum().alias("yes_buy_volume"),
        pl.col("amount_usd").filter(pl.col("is_yes_taker_buy")).len().alias("yes_buy_count"),
        # Total
        pl.col("amount_usd").sum().alias("total_volume"),
        pl.len().alias("total_trades"),
    )

    # Also include trades where taker sells YES (= effectively long NO as taker)
    # side=SELL, token_index=0 → taker sells YES = buys NO equivalent
    taker_no_equiv = trades.filter(
        ((pl.col("side") == "BUY") & (pl.col("token_index") == 1))
        | ((pl.col("side") == "SELL") & (pl.col("token_index") == 0))
    ).group_by("condition_id").agg(
        pl.col("amount_usd").sum().alias("taker_no_equiv_vol"),
    )

    # Maker-sellYES: all trades where taker buys YES
    maker_yes_flow = trades.filter(
        ((pl.col("side") == "BUY") & (pl.col("token_index") == 0))
        | ((pl.col("side") == "SELL") & (pl.col("token_index") == 1))
    ).group_by("condition_id").agg(
        pl.col("amount_usd").sum().alias("maker_sell_yes_flow"),
    )

    per_market = per_market.join(taker_no_equiv, on="condition_id", how="left")
    per_market = per_market.join(maker_yes_flow, on="condition_id", how="left")
    per_market = per_market.join(
        df.select("condition_id", "yes_won", "resolved_at", "median_yes_price"),
        on="condition_id",
        how="inner",
    )

    # Combined capacity
    per_market = per_market.with_columns(
        (pl.col("taker_no_equiv_vol").fill_null(0) + pl.col("maker_sell_yes_flow").fill_null(0))
        .alias("combined_volume"),
        # Lockup
        pl.lit(0).alias("_dummy"),  # placeholder
    )

    # ===================================================================
    # 1. Per-market capacity comparison
    # ===================================================================
    print(f"\n{'='*70}")
    print("  1. PER-MARKET CAPACITY: Single-side vs Dual-side")
    print(f"{'='*70}\n")

    taker_vol = per_market["taker_no_equiv_vol"].fill_null(0)
    maker_vol = per_market["maker_sell_yes_flow"].fill_null(0)
    combined = taker_vol + maker_vol

    print(f"  Markets: {per_market.height:,}")
    print(f"\n  {'Metric':<35} {'Taker-NO':>12} {'Maker-sellYES':>14} {'Combined':>12}")
    print(f"  {'-'*75}")
    print(f"  {'Median volume/market':<35} ${float(taker_vol.median()):>10,.0f} "
          f"${float(maker_vol.median()):>12,.0f} ${float(combined.median()):>10,.0f}")
    print(f"  {'Mean volume/market':<35} ${float(taker_vol.mean()):>10,.0f} "
          f"${float(maker_vol.mean()):>12,.0f} ${float(combined.mean()):>10,.0f}")
    print(f"  {'Total volume':<35} ${float(taker_vol.sum()):>10,.0f} "
          f"${float(maker_vol.sum()):>12,.0f} ${float(combined.sum()):>10,.0f}")

    # Markets where single-side < $50 but combined >= $50
    single_thin = per_market.filter(
        (pl.col("taker_no_equiv_vol").fill_null(0) < 50)
        | (pl.col("maker_sell_yes_flow").fill_null(0) < 50)
    )
    both_ok = per_market.filter(
        (pl.col("taker_no_equiv_vol").fill_null(0) >= 50)
        & (pl.col("maker_sell_yes_flow").fill_null(0) >= 50)
    )
    taker_only = per_market.filter(
        (pl.col("taker_no_equiv_vol").fill_null(0) >= 50)
        & (pl.col("maker_sell_yes_flow").fill_null(0) < 50)
    )
    maker_only = per_market.filter(
        (pl.col("taker_no_equiv_vol").fill_null(0) < 50)
        & (pl.col("maker_sell_yes_flow").fill_null(0) >= 50)
    )
    neither = per_market.filter(
        (pl.col("taker_no_equiv_vol").fill_null(0) < 50)
        & (pl.col("maker_sell_yes_flow").fill_null(0) < 50)
    )

    print(f"\n  Markets with >= $50 capacity:")
    print(f"    Both sides >= $50:        {both_ok.height:>6,} ({both_ok.height/per_market.height*100:.1f}%)")
    print(f"    Taker-NO only >= $50:     {taker_only.height:>6,} ({taker_only.height/per_market.height*100:.1f}%)")
    print(f"    Maker-sellYES only >= $50: {maker_only.height:>6,} ({maker_only.height/per_market.height*100:.1f}%)")
    print(f"    Neither >= $50:           {neither.height:>6,} ({neither.height/per_market.height*100:.1f}%)")

    # ===================================================================
    # 2. How much MORE volume can we deploy dual-sided?
    # ===================================================================
    print(f"\n{'='*70}")
    print("  2. VOLUME MULTIPLIER: Dual vs Single-side")
    print(f"{'='*70}\n")

    # For markets where both sides have >= $50, compute how much we can deploy
    # if we use $50 on each side vs $50 on one side
    per_market = per_market.with_columns(
        # Deployable per side (capped at our bet size)
        pl.col("taker_no_equiv_vol").fill_null(0).clip(0, BET_SIZE).alias("taker_deploy"),
        pl.col("maker_sell_yes_flow").fill_null(0).clip(0, BET_SIZE).alias("maker_deploy"),
    )
    per_market = per_market.with_columns(
        (pl.col("taker_deploy") + pl.col("maker_deploy")).alias("dual_deploy"),
    )

    # For dual, cap at 2*BET_SIZE (max $100 per market: $50 taker + $50 maker)
    per_market = per_market.with_columns(
        pl.col("dual_deploy").clip(0, 2 * BET_SIZE).alias("dual_deploy_capped"),
    )

    deployable_taker = float(per_market["taker_deploy"].sum())
    deployable_maker = float(per_market["maker_deploy"].sum())
    deployable_dual = float(per_market["dual_deploy_capped"].sum())
    deployable_single = max(deployable_taker, deployable_maker)

    print(f"  Deployable volume (${BET_SIZE:.0f} cap per side per market):")
    print(f"    Taker-NO only:   ${deployable_taker:>12,.0f}")
    print(f"    Maker-YES only:  ${deployable_maker:>12,.0f}")
    print(f"    Dual-sided:      ${deployable_dual:>12,.0f}")
    print(f"    Dual / single:   {deployable_dual / deployable_single:.2f}x")

    # Markets where we can deploy $100 (full dual) vs only $50 (single)
    full_dual = per_market.filter(pl.col("dual_deploy_capped") >= 2 * BET_SIZE)
    print(f"\n  Markets with full $100 dual deployment: {full_dual.height:,} "
          f"({full_dual.height/per_market.height*100:.1f}%)")

    # ===================================================================
    # 3. CAPITAL SIMULATION: $300, dual-sided
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"  3. CAPITAL SIM: ${CAPITAL:.0f}, dual-sided (${BET_SIZE:.0f} taker + ${BET_SIZE:.0f} maker per market)")
    print(f"{'='*70}\n")

    # With dual: each market uses $100 ($50 taker-NO + $50 maker-sellYES)
    # So 3 concurrent markets instead of 6
    # BUT each market earns 2x the PnL

    # Add lockup from trade data
    first_trade = trades.group_by("condition_id").agg(
        pl.col("timestamp").min().alias("first_trade")
    )
    per_market = per_market.join(first_trade, on="condition_id", how="left")
    for c in ["first_trade"]:
        d = per_market.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            per_market = per_market.with_columns(pl.col(c).dt.replace_time_zone(None))

    per_market = per_market.with_columns(
        (pl.col("resolved_at") - pl.col("first_trade")).dt.total_days().alias("lockup_days"),
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("month"),
    )

    # Fast markets only
    fast = per_market.filter(
        pl.col("lockup_days").is_not_null() & (pl.col("lockup_days") < 3)
    ).sort("resolved_at")

    print(f"  Fast markets (<3d lockup): {fast.height:,}")
    print(f"  NO win rate: {float((~fast['yes_won']).mean())*100:.1f}%")

    months = sorted(fast.filter(pl.col("month").is_not_null())["month"].unique().to_list())
    months = [m for m in months if m >= "2025-01"]

    # Simulate 3 approaches:
    # A) Taker-only: 6 slots × $50 = $300
    # B) Maker-only: 6 slots × $50 = $300
    # C) Dual: 3 slots × $100 ($50 taker + $50 maker) = $300
    # D) Dual wide: 6 slots × $100, requiring $600 capital → nah, keep at $300

    for mode, n_slots, bet_per_slot, label in [
        ("taker", 6, BET_SIZE, f"Taker-only (6 × ${BET_SIZE:.0f})"),
        ("dual", 3, 2 * BET_SIZE, f"Dual-sided (3 × ${2*BET_SIZE:.0f})"),
        ("dual", 6, 2 * BET_SIZE, f"Dual-sided (6 × ${2*BET_SIZE:.0f} = ${6*2*BET_SIZE:.0f} capital)"),
    ]:
        cum_pnl = 0.0
        total_bets = 0
        total_wins = 0

        print(f"\n  --- {label} ---")
        print(f"  {'Month':<10} {'Placed':>7} {'HR':>7} {'PnL':>10} {'Cum':>10}")
        print(f"  {'-'*46}")

        for m in months:
            month_df = fast.filter(pl.col("month") == m).sort("resolved_at")
            if month_df.height == 0:
                continue

            med_lockup = max(float(month_df["lockup_days"].median()), 1.0)
            capacity_days = n_slots * 30
            max_markets = int(capacity_days / med_lockup)

            # For dual: filter to markets with >= $50 on BOTH sides
            if mode == "dual":
                eligible = month_df.filter(
                    (pl.col("taker_no_equiv_vol").fill_null(0) >= BET_SIZE)
                    & (pl.col("maker_sell_yes_flow").fill_null(0) >= BET_SIZE)
                )
            else:
                eligible = month_df

            placed = min(eligible.height, max_markets)
            if placed == 0:
                continue

            selected = eligible.head(placed)
            wins = int((~selected["yes_won"]).sum())
            total_bets += placed
            total_wins += wins

            month_pnl = 0.0
            for row in selected.iter_rows(named=True):
                yes_p = row["median_yes_price"]
                no_p = 1.0 - yes_p
                no_won = not row["yes_won"]

                if mode == "dual":
                    # Two bets per market:
                    # 1) Taker buys NO at no_p
                    if no_won:
                        month_pnl += BET_SIZE * (1 - no_p) / no_p
                    else:
                        month_pnl -= BET_SIZE

                    # 2) Maker sells YES at yes_p + spread
                    spread = 0.01
                    eff_yes = yes_p + spread
                    if no_won:
                        month_pnl += BET_SIZE * eff_yes / (1.0 - eff_yes)
                    else:
                        month_pnl -= BET_SIZE
                else:
                    # Taker-only
                    if no_won:
                        month_pnl += BET_SIZE * (1 - no_p) / no_p
                    else:
                        month_pnl -= BET_SIZE

            cum_pnl += month_pnl
            hr = wins / placed * 100
            print(f"  {m:<10} {placed:>7} {hr:>6.1f}% ${month_pnl:>8,.0f} ${cum_pnl:>8,.0f}")

        hr = total_wins / total_bets * 100 if total_bets > 0 else 0
        n_mo = len([1 for m2 in months if fast.filter(pl.col("month") == m2).height > 0])
        avg_mo = cum_pnl / n_mo if n_mo > 0 else 0
        print(f"  {'-'*46}")
        print(f"  TOTAL: {total_bets} markets, {hr:.1f}% HR, ${cum_pnl:,.0f}")
        print(f"  Avg/month: ${avg_mo:,.0f} ({avg_mo/CAPITAL*100:.0f}% ROI on ${CAPITAL:.0f})")

    # ===================================================================
    # 4. Summary table
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"  4. SUMMARY: What dual-sided adds")
    print(f"{'='*70}\n")

    print("  With same $300 capital:")
    print(f"  {'Approach':<40} {'Markets/mo':>10} {'$/month':>10} {'$/bet':>8}")
    print(f"  {'-'*70}")
    print(f"  Taker-only: 6 × $50                     {'180':>10} see above     $50")
    print(f"  Dual-sided: 3 × $100                     {'90':>10} see above    $100")
    print(f"  (Fewer markets but 2× PnL per market)")
    print()
    print("  The tradeoff: dual puts $100 per market (less diversification)")
    print("  but accesses 2× the liquidity pool per market.")
    print()
    print("  At $600 capital (or after compounding):")
    print(f"  Dual-sided: 6 × $100                     {'180':>10} see above    $100")
    print("  → Same diversification as taker-only at $300, but 2× PnL per market")


if __name__ == "__main__":
    main()
