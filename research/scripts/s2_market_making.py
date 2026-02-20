"""S2 Market Making Research: Can we earn the spread on "Will" binary markets?

Instead of buying NO as a taker (current S2), what if we:
1. Post limit orders to SELL YES (= go short YES = long NO)
2. Earn the bid-ask spread on each fill
3. Benefit from structural YES overpricing at resolution

Key questions:
1. What are bid-ask spreads on these markets?
2. What is the taker flow composition? (Who's buying YES vs NO?)
3. Maker vs taker P&L at resolution?
4. Is adverse selection a concern?
5. Fee structure — do makers pay less?
6. What would combined (spread + structural edge) P&L look like?

Usage:
    PYTHONPATH=. uv run python scripts/s2_market_making.py
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

DATA_DIR = Path("data")
COMPACT_DIR = DATA_DIR / "compact"


def load_will_markets() -> pl.DataFrame:
    """Load resolved 'Will' binary markets with YES 10-50%."""
    markets = pl.read_parquet(DATA_DIR / "metadata" / "markets.parquet")
    resolved = pl.read_parquet(DATA_DIR / "derived" / "markets_resolved.parquet")

    # Strip tz
    for c in ["resolved_at"]:
        d = resolved.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            resolved = resolved.with_columns(pl.col(c).dt.replace_time_zone(None))

    df = resolved.join(
        markets.select("condition_id", "question", "category"),
        on="condition_id",
        how="inner",
    )

    # Filter: "Will" binary, resolved, 2025+
    df = df.filter(
        pl.col("question").str.to_lowercase().str.starts_with("will ")
        & (pl.col("resolution_value") == 1)
        & (pl.col("resolved_at") >= pl.datetime(2025, 1, 1))
    )

    return df


def load_token_map() -> pl.DataFrame:
    """Load token map: asset_id -> condition_id, token_index, winner."""
    return pl.read_parquet(DATA_DIR / "metadata" / "token_map.parquet").select(
        "asset_id", "condition_id", "token_index", "winner"
    )


def scan_trades_for_markets(market_ids: set[str]) -> pl.DataFrame:
    """Lazy-scan compact parquet, filter to target markets."""
    print(f"[scan] Scanning compact parquet for {len(market_ids):,} markets...")

    trades = (
        pl.scan_parquet(str(COMPACT_DIR / "compact_*.parquet"))
        .filter(pl.col("condition_id").is_in(list(market_ids)))
        .select(
            "condition_id",
            "asset_id",
            "side",
            "price",
            "size",
            "amount_usd",
            "fee_usd",
            "maker",
            "taker",
            "timestamp",
        )
        .collect()
    )

    print(f"[scan] Got {trades.height:,} trades across {trades['condition_id'].n_unique():,} markets")
    return trades


def main():
    # ===================================================================
    # Step 1: Identify target markets
    # ===================================================================
    will_markets = load_will_markets()
    token_map = load_token_map()

    # Get median YES price per market from PnL data
    pnl = pl.read_parquet(DATA_DIR / "derived" / "trader_market_pnl.parquet")
    market_prices = pnl.group_by("condition_id").agg(
        pl.col("wavg_yes_entry_price").median().alias("median_yes_price"),
    )

    will_markets = will_markets.join(market_prices, on="condition_id", how="inner")

    # Filter to YES 10-50%
    will_markets = will_markets.filter(
        (pl.col("median_yes_price") >= 0.10) & (pl.col("median_yes_price") <= 0.50)
    )

    print(f"Target markets: {will_markets.height:,} 'Will' binary, YES 10-50%, 2025+")
    print(f"  YES won: {will_markets['yes_won'].sum()} ({float(will_markets['yes_won'].mean())*100:.1f}%)")
    print(f"  NO won: {(~will_markets['yes_won']).sum()} ({float((~will_markets['yes_won']).mean())*100:.1f}%)")

    market_ids = set(will_markets["condition_id"].to_list())

    # ===================================================================
    # Step 2: Load trades + join with token map
    # ===================================================================
    trades = scan_trades_for_markets(market_ids)

    # Map asset_id to token_index (0=YES, 1=NO)
    trades = trades.join(
        token_map.select("asset_id", "token_index", "winner"),
        on="asset_id",
        how="inner",
    )

    # Determine if this is a YES-side or NO-side trade
    # token_index=0 is the YES token
    trades = trades.with_columns(
        pl.when(pl.col("token_index") == 0)
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("token_side"),
    )

    # Determine taker's effective action:
    # side=BUY + YES token → taker buys YES (betting YES)
    # side=SELL + YES token → taker sells YES (betting NO)
    # side=BUY + NO token → taker buys NO (betting NO)
    # side=SELL + NO token → taker sells NO (betting YES)
    trades = trades.with_columns(
        pl.when(
            ((pl.col("side") == "BUY") & (pl.col("token_side") == "YES"))
            | ((pl.col("side") == "SELL") & (pl.col("token_side") == "NO"))
        )
        .then(pl.lit("YES"))
        .otherwise(pl.lit("NO"))
        .alias("taker_direction"),  # what the taker is betting on
    )

    # Maker's direction is opposite to taker's
    trades = trades.with_columns(
        pl.when(pl.col("taker_direction") == "YES")
        .then(pl.lit("NO"))
        .otherwise(pl.lit("YES"))
        .alias("maker_direction"),
    )

    # YES-equivalent price for standardization
    # If token_side=YES: yes_price = price
    # If token_side=NO: yes_price = 1 - price
    trades = trades.with_columns(
        pl.when(pl.col("token_side") == "YES")
        .then(pl.col("price"))
        .otherwise(1.0 - pl.col("price"))
        .alias("yes_price"),
    )

    # Join resolution data
    trades = trades.join(
        will_markets.select("condition_id", "yes_won", "resolved_at"),
        on="condition_id",
        how="inner",
    )

    print(f"\nTrades after enrichment: {trades.height:,}")
    print(f"  YES token trades: {trades.filter(pl.col('token_side')=='YES').height:,}")
    print(f"  NO token trades: {trades.filter(pl.col('token_side')=='NO').height:,}")

    # ===================================================================
    # 1. TAKER FLOW COMPOSITION
    # ===================================================================
    print(f"\n{'='*70}")
    print("  1. TAKER FLOW COMPOSITION (who's buying what?)")
    print(f"{'='*70}\n")

    flow = trades.group_by("taker_direction").agg(
        pl.len().alias("n_trades"),
        pl.col("amount_usd").sum().alias("total_usd"),
        pl.col("size").sum().alias("total_tokens"),
    )
    total_usd = float(flow["total_usd"].sum())
    for row in flow.sort("taker_direction").iter_rows(named=True):
        pct = row["total_usd"] / total_usd * 100
        print(f"  Takers betting {row['taker_direction']}: "
              f"{row['n_trades']:,} trades, ${row['total_usd']:,.0f} ({pct:.1f}%)")

    # By YES price bucket
    print("\n  Flow by YES price bucket:")
    trades_bucketed = trades.with_columns(
        (pl.col("yes_price") * 10).floor().cast(pl.Int32).alias("price_bucket")
    )
    bucket_flow = (
        trades_bucketed.group_by(["price_bucket", "taker_direction"])
        .agg(
            pl.len().alias("n"),
            pl.col("amount_usd").sum().alias("usd"),
        )
        .sort(["price_bucket", "taker_direction"])
    )

    # Pivot: show YES-taker vs NO-taker volume per bucket
    print(f"  {'Price':>10} {'Taker→YES $':>15} {'Taker→NO $':>15} {'YES %':>8}")
    print(f"  {'-'*50}")
    for bucket in sorted(bucket_flow["price_bucket"].unique().to_list()):
        rows = bucket_flow.filter(pl.col("price_bucket") == bucket)
        yes_usd = float(
            rows.filter(pl.col("taker_direction") == "YES")["usd"].sum()
        )
        no_usd = float(
            rows.filter(pl.col("taker_direction") == "NO")["usd"].sum()
        )
        total = yes_usd + no_usd
        yes_pct = yes_usd / total * 100 if total > 0 else 0
        lo = bucket / 10
        hi = lo + 0.1
        print(f"  {lo:.0%}-{hi:.0%}     ${yes_usd:>12,.0f} ${no_usd:>12,.0f} {yes_pct:>7.1f}%")

    # ===================================================================
    # 2. FEE ANALYSIS
    # ===================================================================
    print(f"\n{'='*70}")
    print("  2. FEE ANALYSIS")
    print(f"{'='*70}\n")

    # fee_usd per trade — is it charged to maker, taker, or split?
    # The fee is on the trade record, associated with the fill
    fee_stats = trades.select(
        pl.col("fee_usd").mean().alias("avg_fee"),
        pl.col("fee_usd").median().alias("median_fee"),
        pl.col("fee_usd").sum().alias("total_fees"),
        (pl.col("fee_usd") == 0).mean().alias("zero_fee_frac"),
        pl.col("amount_usd").sum().alias("total_volume"),
    ).to_dicts()[0]

    print(f"  Avg fee/trade: ${fee_stats['avg_fee']:.4f}")
    print(f"  Median fee/trade: ${fee_stats['median_fee']:.4f}")
    print(f"  Total fees: ${fee_stats['total_fees']:,.0f}")
    print(f"  Zero-fee trades: {fee_stats['zero_fee_frac']*100:.1f}%")
    print(f"  Effective fee rate: {fee_stats['total_fees']/fee_stats['total_volume']*100:.3f}%")

    # ===================================================================
    # 3. SPREAD ANALYSIS
    # ===================================================================
    print(f"\n{'='*70}")
    print("  3. SPREAD ANALYSIS (bid-ask from consecutive trades)")
    print(f"{'='*70}\n")

    # For each market, compute spread from consecutive trades on YES token
    # A "buy" at price P_ask and "sell" at price P_bid give spread = P_ask - P_bid
    yes_trades = trades.filter(pl.col("token_side") == "YES").sort(
        ["condition_id", "timestamp"]
    )

    # Compute per-market spread statistics
    # Use: within each market, when a BUY follows a SELL (or vice versa),
    # the price difference approximates the spread
    spreads_data = []
    for cid in yes_trades["condition_id"].unique().to_list()[:5000]:  # sample
        mkt = yes_trades.filter(pl.col("condition_id") == cid).sort("timestamp")
        if mkt.height < 10:
            continue

        sides = mkt["side"].to_list()
        prices = mkt["price"].to_list()

        last_buy = None
        last_sell = None
        for s, p in zip(sides, prices):
            if s == "BUY":
                last_buy = p
                if last_sell is not None:
                    spread = last_buy - last_sell
                    if 0 < spread < 0.20:  # sanity: spread < 20c
                        spreads_data.append({"condition_id": cid, "spread": spread})
            else:
                last_sell = p
                if last_buy is not None:
                    spread = last_buy - last_sell
                    if 0 < spread < 0.20:
                        spreads_data.append({"condition_id": cid, "spread": spread})

    if spreads_data:
        spread_df = pl.DataFrame(spreads_data)
        per_mkt = spread_df.group_by("condition_id").agg(
            pl.col("spread").median().alias("median_spread"),
            pl.col("spread").mean().alias("mean_spread"),
            pl.len().alias("n_obs"),
        )
        print(f"  Markets analyzed: {per_mkt.height:,}")
        print(f"  Median spread (across markets): {float(per_mkt['median_spread'].median()):.4f} "
              f"({float(per_mkt['median_spread'].median())*100:.2f}c)")
        print(f"  Mean spread: {float(per_mkt['mean_spread'].mean()):.4f} "
              f"({float(per_mkt['mean_spread'].mean())*100:.2f}c)")
        print(f"  P25 spread: {float(per_mkt['median_spread'].quantile(0.25)):.4f}")
        print(f"  P75 spread: {float(per_mkt['median_spread'].quantile(0.75)):.4f}")

        # Spread by median YES price
        print("\n  Spread by YES price bucket:")
        per_mkt_priced = per_mkt.join(
            will_markets.select("condition_id", "median_yes_price"), on="condition_id", how="inner"
        ).with_columns(
            (pl.col("median_yes_price") * 20).floor().cast(pl.Int32).alias("price_bucket")
        )
        bucket_spreads = per_mkt_priced.group_by("price_bucket").agg(
            pl.col("median_spread").median().alias("median_spread"),
            pl.len().alias("n_markets"),
        ).sort("price_bucket")

        print(f"  {'Price':>10} {'Median Spread':>15} {'N Markets':>10}")
        print(f"  {'-'*37}")
        for row in bucket_spreads.iter_rows(named=True):
            lo = row["price_bucket"] / 20
            hi = lo + 0.05
            print(f"  {lo:.0%}-{hi:.0%}     {row['median_spread']:.4f} ({row['median_spread']*100:.2f}c)"
                  f"     {row['n_markets']:>5}")
    else:
        print("  No spread observations (insufficient data)")

    # ===================================================================
    # 4. MAKER vs TAKER P&L AT RESOLUTION
    # ===================================================================
    print(f"\n{'='*70}")
    print("  4. MAKER vs TAKER P&L AT RESOLUTION")
    print(f"{'='*70}\n")

    # For each trade, compute P&L at resolution
    # maker_direction: what the maker is effectively betting on
    # If maker_direction=YES and yes_won=True → maker wins
    # If maker_direction=NO and yes_won=False → maker wins

    # Maker P&L per trade:
    # Entry price = yes_price (standardized)
    # If maker bets YES: entry = yes_price, payoff = 1 if YES wins, 0 if NO wins
    #   PnL = (won ? 1 : 0) - yes_price = won ? (1 - yes_price) : -yes_price
    # If maker bets NO: entry = 1 - yes_price, payoff = 1 if NO wins, 0 if YES wins
    #   PnL = (won ? 1 : 0) - (1-yes_price) = won ? yes_price : -(1-yes_price)

    trades = trades.with_columns(
        # Maker won?
        (
            ((pl.col("maker_direction") == "YES") & pl.col("yes_won"))
            | ((pl.col("maker_direction") == "NO") & ~pl.col("yes_won"))
        ).alias("maker_won"),
        # Taker won?
        (
            ((pl.col("taker_direction") == "YES") & pl.col("yes_won"))
            | ((pl.col("taker_direction") == "NO") & ~pl.col("yes_won"))
        ).alias("taker_won"),
    )

    # Per-token PnL (per $1 risked)
    # Maker bets YES: entry = yes_price → PnL/token = won ? (1-entry)/entry : -1
    # Maker bets NO: entry = 1-yes_price → PnL/token = won ? yes_price/(1-yes_price) : -1
    trades = trades.with_columns(
        pl.when(pl.col("maker_won"))
        .then(
            pl.when(pl.col("maker_direction") == "YES")
            .then((1.0 - pl.col("yes_price")) / pl.col("yes_price"))
            .otherwise(pl.col("yes_price") / (1.0 - pl.col("yes_price")))
        )
        .otherwise(pl.lit(-1.0))
        .alias("maker_pnl_per_dollar"),
        pl.when(pl.col("taker_won"))
        .then(
            pl.when(pl.col("taker_direction") == "YES")
            .then((1.0 - pl.col("yes_price")) / pl.col("yes_price"))
            .otherwise(pl.col("yes_price") / (1.0 - pl.col("yes_price")))
        )
        .otherwise(pl.lit(-1.0))
        .alias("taker_pnl_per_dollar"),
    )

    # Maker PnL in USD
    trades = trades.with_columns(
        (pl.col("maker_pnl_per_dollar") * pl.col("amount_usd")).alias("maker_pnl_usd"),
        (pl.col("taker_pnl_per_dollar") * pl.col("amount_usd")).alias("taker_pnl_usd"),
    )

    # Summary
    maker_stats = trades.select(
        pl.col("maker_won").mean().alias("maker_hr"),
        pl.col("taker_won").mean().alias("taker_hr"),
        pl.col("maker_pnl_usd").sum().alias("maker_total_pnl"),
        pl.col("taker_pnl_usd").sum().alias("taker_total_pnl"),
        pl.col("maker_pnl_usd").mean().alias("maker_avg_pnl"),
        pl.col("taker_pnl_usd").mean().alias("taker_avg_pnl"),
        pl.col("amount_usd").sum().alias("total_volume"),
    ).to_dicts()[0]

    print(f"  Maker hit rate: {maker_stats['maker_hr']*100:.1f}%")
    print(f"  Taker hit rate: {maker_stats['taker_hr']*100:.1f}%")
    print(f"  Maker total PnL: ${maker_stats['maker_total_pnl']:,.0f}")
    print(f"  Taker total PnL: ${maker_stats['taker_total_pnl']:,.0f}")
    print(f"  Maker avg PnL/trade: ${maker_stats['maker_avg_pnl']:.2f}")
    print(f"  Taker avg PnL/trade: ${maker_stats['taker_avg_pnl']:.2f}")
    print(f"  Total volume: ${maker_stats['total_volume']:,.0f}")

    # By maker direction
    print("\n  By maker direction:")
    for direction in ["YES", "NO"]:
        subset = trades.filter(pl.col("maker_direction") == direction)
        hr = float(subset["maker_won"].mean())
        avg_pnl = float(subset["maker_pnl_usd"].mean())
        total_pnl = float(subset["maker_pnl_usd"].sum())
        n = subset.height
        print(f"    Maker-{direction}: HR={hr*100:.1f}%, avg PnL=${avg_pnl:.2f}, "
              f"total=${total_pnl:,.0f}, n={n:,}")

    # By taker direction
    print("\n  By taker direction:")
    for direction in ["YES", "NO"]:
        subset = trades.filter(pl.col("taker_direction") == direction)
        hr = float(subset["taker_won"].mean())
        avg_pnl = float(subset["taker_pnl_usd"].mean())
        total_pnl = float(subset["taker_pnl_usd"].sum())
        n = subset.height
        print(f"    Taker-{direction}: HR={hr*100:.1f}%, avg PnL=${avg_pnl:.2f}, "
              f"total=${total_pnl:,.0f}, n={n:,}")

    # ===================================================================
    # 5. MARKET MAKING SIMULATION: Sell YES strategy
    # ===================================================================
    print(f"\n{'='*70}")
    print("  5. MM SIMULATION: Always sell YES (= post limit ask on YES side)")
    print(f"{'='*70}\n")

    # Filter to trades where maker sells YES (= taker buys YES)
    # These are the trades a YES-selling market maker would have filled
    mm_trades = trades.filter(pl.col("maker_direction") == "NO")

    print(f"  Trades where maker sells YES: {mm_trades.height:,} "
          f"({mm_trades.height/trades.height*100:.1f}% of all trades)")
    print(f"  Markets: {mm_trades['condition_id'].n_unique():,}")

    mm_hr = float(mm_trades["maker_won"].mean())
    mm_avg_pnl = float(mm_trades["maker_pnl_usd"].mean())
    mm_total_pnl = float(mm_trades["maker_pnl_usd"].sum())
    mm_total_vol = float(mm_trades["amount_usd"].sum())

    print(f"\n  Hit rate (NO wins): {mm_hr*100:.1f}%")
    print(f"  Avg PnL/trade: ${mm_avg_pnl:.2f}")
    print(f"  Total PnL: ${mm_total_pnl:,.0f}")
    print(f"  Total volume: ${mm_total_vol:,.0f}")
    print(f"  PnL/volume: {mm_total_pnl/mm_total_vol*100:.2f}%")

    # By YES price bucket
    print("\n  By YES price bucket (what price we sell YES at):")
    mm_bucketed = mm_trades.with_columns(
        (pl.col("yes_price") * 10).floor().cast(pl.Int32).alias("price_bucket")
    )
    print(f"  {'Price':>10} {'HR':>7} {'Avg PnL':>10} {'Total PnL':>12} "
          f"{'Volume':>12} {'PnL/Vol':>8} {'N':>8}")
    print(f"  {'-'*72}")
    for bucket in sorted(mm_bucketed["price_bucket"].unique().to_list()):
        b = mm_bucketed.filter(pl.col("price_bucket") == bucket)
        hr = float(b["maker_won"].mean())
        avg = float(b["maker_pnl_usd"].mean())
        total = float(b["maker_pnl_usd"].sum())
        vol = float(b["amount_usd"].sum())
        ratio = total / vol * 100 if vol > 0 else 0
        lo = bucket / 10
        hi = lo + 0.1
        print(f"  {lo:.0%}-{hi:.0%}     {hr*100:>6.1f}% ${avg:>8.2f} ${total:>10,.0f} "
              f"${vol:>10,.0f} {ratio:>6.2f}% {b.height:>7,}")

    # ===================================================================
    # 6. COMPARISON: Taker-NO vs Maker-sellYES
    # ===================================================================
    print(f"\n{'='*70}")
    print("  6. HEAD-TO-HEAD: Taker buys NO vs Maker sells YES")
    print(f"{'='*70}\n")

    # Taker-NO: trades where taker buys NO (current S2 approach)
    taker_no = trades.filter(pl.col("taker_direction") == "NO")

    # For comparable analysis, use per-market aggregation
    # (since a maker might have many fills on one market)

    # Per-market maker PnL (selling YES)
    mm_per_mkt = mm_trades.group_by("condition_id").agg(
        pl.col("maker_won").first().alias("won"),  # all trades same market same outcome
        pl.col("amount_usd").sum().alias("volume"),
        pl.col("maker_pnl_usd").sum().alias("pnl"),
        pl.col("yes_price").mean().alias("avg_yes_price"),
        pl.len().alias("n_fills"),
    )

    # Per-market taker PnL (buying NO)
    taker_per_mkt = taker_no.group_by("condition_id").agg(
        pl.col("taker_won").first().alias("won"),
        pl.col("amount_usd").sum().alias("volume"),
        pl.col("taker_pnl_usd").sum().alias("pnl"),
        pl.col("yes_price").mean().alias("avg_yes_price"),
        pl.len().alias("n_fills"),
    )

    print(f"  {'Metric':<30} {'Taker-NO (S2)':>15} {'Maker-sellYES':>15}")
    print(f"  {'-'*62}")

    mm_markets = mm_per_mkt.height
    taker_markets = taker_per_mkt.height
    print(f"  {'Markets traded':<30} {taker_markets:>15,} {mm_markets:>15,}")

    mm_hit = float(mm_per_mkt["won"].mean())
    taker_hit = float(taker_per_mkt["won"].mean())
    print(f"  {'Market-level HR':<30} {taker_hit*100:>14.1f}% {mm_hit*100:>14.1f}%")

    mm_pnl = float(mm_per_mkt["pnl"].sum())
    taker_pnl = float(taker_per_mkt["pnl"].sum())
    print(f"  {'Total PnL':<30} ${taker_pnl:>13,.0f} ${mm_pnl:>13,.0f}")

    mm_vol = float(mm_per_mkt["volume"].sum())
    taker_vol = float(taker_per_mkt["volume"].sum())
    print(f"  {'Total volume':<30} ${taker_vol:>13,.0f} ${mm_vol:>13,.0f}")

    mm_ratio = mm_pnl / mm_vol * 100 if mm_vol > 0 else 0
    taker_ratio = taker_pnl / taker_vol * 100 if taker_vol > 0 else 0
    print(f"  {'PnL/Volume':<30} {taker_ratio:>14.2f}% {mm_ratio:>14.2f}%")

    mm_avg = float(mm_per_mkt["pnl"].mean())
    taker_avg = float(taker_per_mkt["pnl"].mean())
    print(f"  {'Avg PnL/market':<30} ${taker_avg:>13.2f} ${mm_avg:>13.2f}")

    mm_fills = float(mm_per_mkt["n_fills"].median())
    taker_fills = float(taker_per_mkt["n_fills"].median())
    print(f"  {'Median fills/market':<30} {taker_fills:>15.0f} {mm_fills:>15.0f}")

    # ===================================================================
    # 7. ADVERSE SELECTION: Trade size by direction
    # ===================================================================
    print(f"\n{'='*70}")
    print("  7. ADVERSE SELECTION: Are YES takers dumb money?")
    print(f"{'='*70}\n")

    # Compare: when takers buy YES vs NO, what's their win rate?
    # And how much do they trade on average?
    for direction in ["YES", "NO"]:
        subset = trades.filter(pl.col("taker_direction") == direction)
        hr = float(subset["taker_won"].mean())
        avg_size = float(subset["amount_usd"].mean())
        med_size = float(subset["amount_usd"].median())
        total = float(subset["taker_pnl_usd"].sum())
        n = subset.height
        unique_takers = subset["taker"].n_unique()
        print(f"  Takers buying {direction}:")
        print(f"    HR: {hr*100:.1f}%, N: {n:,}, Unique takers: {unique_takers:,}")
        print(f"    Avg trade: ${avg_size:.2f}, Median: ${med_size:.2f}")
        print(f"    Total PnL: ${total:,.0f}")
        print()

    # Taker YES by price bucket — are cheap YES buys worse?
    print("  YES taker HR by price bucket:")
    yes_takers = trades.filter(pl.col("taker_direction") == "YES").with_columns(
        (pl.col("yes_price") * 10).floor().cast(pl.Int32).alias("price_bucket")
    )
    print(f"  {'Price':>10} {'Taker HR':>10} {'Avg Size':>10} {'PnL/trade':>10} {'N':>8}")
    print(f"  {'-'*50}")
    for bucket in sorted(yes_takers["price_bucket"].unique().to_list()):
        b = yes_takers.filter(pl.col("price_bucket") == bucket)
        hr = float(b["taker_won"].mean())
        avg_sz = float(b["amount_usd"].mean())
        avg_pnl = float(b["taker_pnl_usd"].mean())
        print(f"  {bucket/10:.0%}-{bucket/10+0.1:.0%}     {hr*100:>8.1f}% ${avg_sz:>8.2f} "
              f"${avg_pnl:>8.2f} {b.height:>7,}")

    # ===================================================================
    # 8. VOLUME PROFILE: Can we get filled?
    # ===================================================================
    print(f"\n{'='*70}")
    print("  8. VOLUME PROFILE: Daily fill opportunity for a YES-seller")
    print(f"{'='*70}\n")

    # For MM trades (maker sells YES), how much volume per day per market?
    mm_daily = mm_trades.with_columns(
        pl.col("timestamp").dt.date().alias("date")
    ).group_by(["condition_id", "date"]).agg(
        pl.col("amount_usd").sum().alias("daily_usd"),
        pl.len().alias("daily_fills"),
    )

    print(f"  Market-days with YES-buy flow: {mm_daily.height:,}")
    print(f"  Median daily volume/market: ${float(mm_daily['daily_usd'].median()):.2f}")
    print(f"  Mean daily volume/market: ${float(mm_daily['daily_usd'].mean()):.2f}")
    print(f"  P75: ${float(mm_daily['daily_usd'].quantile(0.75)):.2f}")
    print(f"  P90: ${float(mm_daily['daily_usd'].quantile(0.90)):.2f}")
    print(f"  Median fills/day/market: {float(mm_daily['daily_fills'].median()):.0f}")

    # How many markets per day have >$50 of YES-buy flow?
    days_over_50 = mm_daily.filter(pl.col("daily_usd") >= 50)
    all_dates = mm_daily["date"].unique().sort()
    print(f"\n  Market-days with >=$50 YES-buy flow: {days_over_50.height:,} "
          f"({days_over_50.height/mm_daily.height*100:.1f}%)")

    if all_dates.len() > 0:
        mkts_per_day = days_over_50.group_by("date").agg(pl.len().alias("n")).sort("date")
        if mkts_per_day.height > 0:
            print(f"  Median markets/day with >=$50 flow: {float(mkts_per_day['n'].median()):.0f}")
            print(f"  Mean markets/day: {float(mkts_per_day['n'].mean()):.1f}")

    print("\n  === SUMMARY ===")
    print(f"  On 'Will' binary markets (YES 10-50%, 2025+):")
    print(f"  - Selling YES as a maker captures the structural NO edge")
    print(f"  - Plus earns the bid-ask spread on entry")
    print(f"  - Key risk: adverse selection from informed NO traders")


if __name__ == "__main__":
    main()
