"""S2 MM Estimation: What to expect from $300 capital as a YES-seller.

Simulates month-by-month PnL under the market making approach:
- Post limit orders to sell YES on "Will" binary markets
- Model fill rates, idle capital, and competition scenarios
- Compare head-to-head with taker-NO baseline

Usage:
    PYTHONPATH=. uv run python scripts/s2_mm_estimate.py
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

DATA_DIR = Path("data")
COMPACT_DIR = DATA_DIR / "compact"

CAPITAL = 300.0
BET_SIZE = 50.0


def load_markets_with_trades() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load 'Will' binary markets + their trade-level data."""
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
        markets.select("condition_id", "question", "category"),
        on="condition_id",
        how="inner",
    )

    # "Will" binary, resolved, 2025+
    df = df.filter(
        pl.col("question").str.to_lowercase().str.starts_with("will ")
        & (pl.col("resolution_value") == 1)
        & (pl.col("resolved_at") >= pl.datetime(2025, 1, 1))
    )

    # Get median YES price from PnL
    pnl = pl.read_parquet(DATA_DIR / "derived" / "trader_market_pnl.parquet")
    market_prices = pnl.group_by("condition_id").agg(
        pl.col("wavg_yes_entry_price").median().alias("median_yes_price"),
    )
    df = df.join(market_prices, on="condition_id", how="inner")

    # YES 15-40% (optimized range from insights #17 and #18)
    df = df.filter(
        (pl.col("median_yes_price") >= 0.15) & (pl.col("median_yes_price") <= 0.40)
    )

    market_ids = set(df["condition_id"].to_list())

    # Load trades for these markets
    print(f"[load] Scanning compact parquet for {len(market_ids):,} markets...")
    trades = (
        pl.scan_parquet(str(COMPACT_DIR / "compact_*.parquet"))
        .filter(pl.col("condition_id").is_in(list(market_ids)))
        .select("condition_id", "asset_id", "side", "price", "amount_usd", "timestamp")
        .collect()
    )

    # Map to YES/NO token
    trades = trades.join(token_map, on="asset_id", how="inner")

    # Identify YES-buy flow (what we'd sell into)
    # Taker buys YES = (side=BUY & token=YES) or (side=SELL & token=NO)
    trades = trades.with_columns(
        (
            ((pl.col("side") == "BUY") & (pl.col("token_index") == 0))
            | ((pl.col("side") == "SELL") & (pl.col("token_index") == 1))
        ).alias("is_yes_buy"),
        # YES-equivalent price
        pl.when(pl.col("token_index") == 0)
        .then(pl.col("price"))
        .otherwise(1.0 - pl.col("price"))
        .alias("yes_price"),
    )

    print(f"[load] {df.height:,} markets, {trades.height:,} trades")

    return df, trades


def compute_per_market_metrics(
    markets: pl.DataFrame, trades: pl.DataFrame
) -> pl.DataFrame:
    """Per-market: YES-buy volume, median YES price, lockup, resolution."""

    # YES-buy flow per market
    yes_buys = trades.filter(pl.col("is_yes_buy")).group_by("condition_id").agg(
        pl.col("amount_usd").sum().alias("yes_buy_volume"),
        pl.col("yes_price").median().alias("trade_yes_price"),
        pl.len().alias("yes_buy_count"),
        pl.col("timestamp").min().alias("first_yes_buy"),
        pl.col("timestamp").max().alias("last_yes_buy"),
    )

    # Total flow per market
    total_flow = trades.group_by("condition_id").agg(
        pl.col("amount_usd").sum().alias("total_volume"),
    )

    df = markets.join(yes_buys, on="condition_id", how="inner")
    df = df.join(total_flow, on="condition_id", how="inner")

    # Lockup: days from first YES-buy to resolution
    for c in ["first_yes_buy", "last_yes_buy"]:
        d = df.schema.get(c)
        if d and isinstance(d, pl.Datetime) and d.time_zone:
            df = df.with_columns(pl.col(c).dt.replace_time_zone(None))

    df = df.with_columns(
        (pl.col("resolved_at") - pl.col("first_yes_buy"))
        .dt.total_days()
        .alias("lockup_days"),
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("month"),
    )

    # NO entry price = 1 - yes_price
    df = df.with_columns(
        (1.0 - pl.col("trade_yes_price")).alias("no_entry_price"),
    )

    return df


def simulate_mm(
    mkt_df: pl.DataFrame,
    fill_capture_pct: float,
    label: str,
) -> dict:
    """Simulate month-by-month MM with capital constraint.

    fill_capture_pct: fraction of each market's YES-buy flow we capture
    (accounts for competition with other MMs).
    """
    max_slots = int(CAPITAL / BET_SIZE)

    # Sort by resolution date for FIFO processing
    mkt_df = mkt_df.sort("resolved_at")

    months = sorted(mkt_df.filter(pl.col("month").is_not_null())["month"].unique().to_list())
    months = [m for m in months if m >= "2025-01"]

    cum_pnl = 0.0
    total_bets = 0
    total_wins = 0
    monthly_results = []

    for m in months:
        month_df = mkt_df.filter(pl.col("month") == m).sort("resolved_at")
        if month_df.height == 0:
            continue

        # Capital-constrained: how many bets can we place?
        med_lockup = max(float(month_df["lockup_days"].median()), 1.0)
        capacity_days = max_slots * 30
        max_bets = int(capacity_days / med_lockup)

        # Fill adjustment: we only capture fill_capture_pct of markets
        # Filter to markets where our $50 order would be a reasonable fill
        # (YES-buy volume >= $50 * fill_capture_pct)
        fillable = month_df.filter(
            pl.col("yes_buy_volume") * fill_capture_pct >= BET_SIZE
        )

        placed = min(fillable.height, max_bets)
        if placed == 0:
            monthly_results.append({
                "month": m, "available": month_df.height, "fillable": fillable.height,
                "placed": placed, "wins": 0, "pnl": 0.0, "cum_pnl": cum_pnl
            })
            continue

        selected = fillable.head(placed)
        wins = int((~selected["yes_won"]).sum())  # NO wins
        total_bets += placed
        total_wins += wins

        # PnL per bet: maker sells YES at price p → capital at risk = (1-p) per token
        # Tokens per BET = BET / (1 - p)
        # If NO wins: profit per token = p → total profit = BET * p / (1 - p)
        # If YES wins: loss = -BET (lose all capital)
        # Spread edge: maker sells at ~1c higher YES price than mid
        SPREAD_EDGE = 0.01  # conservative: half the 2c median spread

        month_pnl = 0.0
        for row in selected.iter_rows(named=True):
            yes_p = row["trade_yes_price"]
            no_won = not row["yes_won"]
            if no_won:
                # Sell YES at yes_p + spread_edge
                effective_yes_price = yes_p + SPREAD_EDGE
                # Profit per $BET of capital = BET * p / (1-p)
                profit = BET_SIZE * effective_yes_price / (1.0 - effective_yes_price)
                month_pnl += profit
            else:
                month_pnl -= BET_SIZE

        cum_pnl += month_pnl
        monthly_results.append({
            "month": m, "available": month_df.height, "fillable": fillable.height,
            "placed": placed, "wins": wins, "pnl": month_pnl, "cum_pnl": cum_pnl,
        })

    return {
        "label": label,
        "fill_capture_pct": fill_capture_pct,
        "total_bets": total_bets,
        "total_wins": total_wins,
        "total_pnl": cum_pnl,
        "months": monthly_results,
    }


def simulate_taker(mkt_df: pl.DataFrame) -> dict:
    """Simulate month-by-month taker-NO with capital constraint (baseline S2)."""
    max_slots = int(CAPITAL / BET_SIZE)
    mkt_df = mkt_df.sort("resolved_at")

    months = sorted(mkt_df.filter(pl.col("month").is_not_null())["month"].unique().to_list())
    months = [m for m in months if m >= "2025-01"]

    cum_pnl = 0.0
    total_bets = 0
    total_wins = 0
    monthly_results = []

    for m in months:
        month_df = mkt_df.filter(pl.col("month") == m).sort("resolved_at")
        if month_df.height == 0:
            continue

        med_lockup = max(float(month_df["lockup_days"].median()), 1.0)
        capacity_days = max_slots * 30
        max_bets = int(capacity_days / med_lockup)
        placed = min(month_df.height, max_bets)

        if placed == 0:
            monthly_results.append({
                "month": m, "available": month_df.height, "fillable": month_df.height,
                "placed": placed, "wins": 0, "pnl": 0.0, "cum_pnl": cum_pnl
            })
            continue

        selected = month_df.head(placed)
        wins = int((~selected["yes_won"]).sum())
        total_bets += placed
        total_wins += wins

        month_pnl = 0.0
        for row in selected.iter_rows(named=True):
            no_p = row["no_entry_price"]
            no_won = not row["yes_won"]
            if no_won:
                profit = BET_SIZE * (1 - no_p) / no_p
                month_pnl += profit
            else:
                month_pnl -= BET_SIZE

        cum_pnl += month_pnl
        monthly_results.append({
            "month": m, "available": month_df.height, "fillable": month_df.height,
            "placed": placed, "wins": wins, "pnl": month_pnl, "cum_pnl": cum_pnl,
        })

    return {
        "label": "Taker-NO (baseline)",
        "fill_capture_pct": 1.0,
        "total_bets": total_bets,
        "total_wins": total_wins,
        "total_pnl": cum_pnl,
        "months": monthly_results,
    }


def main():
    markets, trades = load_markets_with_trades()

    mkt_df = compute_per_market_metrics(markets, trades)

    print(f"\nTarget universe: {mkt_df.height:,} markets (Will binary, YES 15-40%, 2025+)")
    print(f"  NO win rate: {float((~mkt_df['yes_won']).mean())*100:.1f}%")
    print(f"  Median lockup: {float(mkt_df['lockup_days'].median()):.1f} days")
    print(f"  Median YES-buy volume/market: ${float(mkt_df['yes_buy_volume'].median()):.0f}")
    print(f"  Median total volume/market: ${float(mkt_df['total_volume'].median()):.0f}")

    # Apply fast filter: lockup < 3 days (from insight #17)
    fast = mkt_df.filter(pl.col("lockup_days") < 3)
    print(f"\n  Fast subset (<3d lockup): {fast.height:,} markets ({fast.height/mkt_df.height*100:.0f}%)")
    print(f"    NO win rate: {float((~fast['yes_won']).mean())*100:.1f}%")
    print(f"    Median lockup: {float(fast['lockup_days'].median()):.1f} days")
    print(f"    Median YES-buy volume: ${float(fast['yes_buy_volume'].median()):.0f}")

    # Use fast markets for all simulations
    sim_df = fast

    # ===================================================================
    # Run simulations
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"  SIMULATIONS: ${CAPITAL:.0f} capital, ${BET_SIZE:.0f}/bet, fast markets (<3d)")
    print(f"{'='*70}")

    # Baseline: taker-NO
    taker = simulate_taker(sim_df)

    # MM scenarios with different fill capture rates
    mm_25 = simulate_mm(sim_df, 0.25, "MM 25% capture")
    mm_50 = simulate_mm(sim_df, 0.50, "MM 50% capture")
    mm_75 = simulate_mm(sim_df, 0.75, "MM 75% capture")
    mm_100 = simulate_mm(sim_df, 1.00, "MM 100% capture (theoretical)")

    scenarios = [taker, mm_25, mm_50, mm_75, mm_100]

    # ===================================================================
    # Monthly detail for baseline and 50% MM
    # ===================================================================
    for scenario in [taker, mm_50]:
        print(f"\n  --- {scenario['label']} ---")
        print(f"  {'Month':<10} {'Avail':>6} {'Fill':>6} {'Placed':>7} "
              f"{'HR':>7} {'PnL':>10} {'Cum':>10}")
        print(f"  {'-'*58}")
        for mr in scenario["months"]:
            if mr["placed"] > 0:
                hr = mr["wins"] / mr["placed"] * 100
                print(f"  {mr['month']:<10} {mr['available']:>6} {mr['fillable']:>6} "
                      f"{mr['placed']:>7} {hr:>6.1f}% ${mr['pnl']:>8,.0f} ${mr['cum_pnl']:>8,.0f}")
            else:
                print(f"  {mr['month']:<10} {mr['available']:>6} {mr['fillable']:>6} "
                      f"{mr['placed']:>7} {'---':>7} {'$0':>10} ${mr['cum_pnl']:>8,.0f}")

        total_bets = scenario["total_bets"]
        total_wins = scenario["total_wins"]
        hr = total_wins / total_bets * 100 if total_bets > 0 else 0
        n_months = len([m for m in scenario["months"] if m["placed"] > 0])
        avg_mo = scenario["total_pnl"] / n_months if n_months > 0 else 0
        roi_mo = avg_mo / CAPITAL * 100
        print(f"  {'-'*58}")
        print(f"  TOTAL: {total_bets} bets, {hr:.1f}% HR, ${scenario['total_pnl']:,.0f} PnL")
        print(f"  Avg/month: ${avg_mo:,.0f} ({roi_mo:.1f}% ROI on ${CAPITAL:.0f})")

    # ===================================================================
    # Summary comparison table
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"  SUMMARY: ${CAPITAL:.0f} capital, ${BET_SIZE:.0f}/bet")
    print(f"{'='*70}\n")

    print(f"  {'Scenario':<30} {'Bets':>6} {'HR':>7} {'Total PnL':>12} "
          f"{'$/month':>10} {'ROI/mo':>8}")
    print(f"  {'-'*76}")
    for s in scenarios:
        n = s["total_bets"]
        hr = s["total_wins"] / n * 100 if n > 0 else 0
        n_months = len([m for m in s["months"] if m["placed"] > 0])
        avg_mo = s["total_pnl"] / n_months if n_months > 0 else 0
        roi = avg_mo / CAPITAL * 100
        print(f"  {s['label']:<30} {n:>6} {hr:>6.1f}% ${s['total_pnl']:>10,.0f} "
              f"${avg_mo:>8,.0f} {roi:>6.1f}%")

    # ===================================================================
    # Risk analysis: worst months, drawdowns
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"  RISK PROFILE (MM 50% capture)")
    print(f"{'='*70}\n")

    mm = mm_50
    pnls = [m["pnl"] for m in mm["months"] if m["placed"] > 0]
    if pnls:
        import statistics
        pnls_sorted = sorted(pnls)
        print(f"  Monthly PnL distribution:")
        print(f"    Worst month:  ${pnls_sorted[0]:>8,.0f}")
        print(f"    P10:          ${pnls_sorted[max(0, len(pnls)//10)]:>8,.0f}")
        print(f"    Median:       ${pnls_sorted[len(pnls)//2]:>8,.0f}")
        print(f"    P90:          ${pnls_sorted[min(len(pnls)-1, 9*len(pnls)//10)]:>8,.0f}")
        print(f"    Best month:   ${pnls_sorted[-1]:>8,.0f}")
        print(f"    Std dev:      ${statistics.stdev(pnls):>8,.0f}")

        # Losing months
        losing = [p for p in pnls if p < 0]
        print(f"\n  Losing months: {len(losing)}/{len(pnls)} ({len(losing)/len(pnls)*100:.0f}%)")
        if losing:
            print(f"    Avg loss: ${statistics.mean(losing):>8,.0f}")
            print(f"    Worst loss: ${min(losing):>8,.0f}")

        # Max drawdown
        peak = 0.0
        max_dd = 0.0
        equity = 0.0
        for p in pnls:
            equity += p
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
        print(f"\n  Max drawdown: ${max_dd:,.0f} ({max_dd/CAPITAL*100:.0f}% of capital)")

    # ===================================================================
    # What $300 realistically means
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"  REALISTIC EXPECTATIONS AT ${CAPITAL:.0f}")
    print(f"{'='*70}\n")

    # Use 50% capture as the realistic scenario
    mm = mm_50
    n_months = len([m for m in mm["months"] if m["placed"] > 0])
    avg_mo = mm["total_pnl"] / n_months if n_months > 0 else 0
    taker_avg = taker["total_pnl"] / len([m for m in taker["months"] if m["placed"] > 0])

    print(f"  Conservative (25% capture):  ${mm_25['total_pnl']/len([m for m in mm_25['months'] if m['placed']>0]):,.0f}/month")
    print(f"  Moderate (50% capture):      ${avg_mo:,.0f}/month")
    print(f"  Optimistic (75% capture):    ${mm_75['total_pnl']/len([m for m in mm_75['months'] if m['placed']>0]):,.0f}/month")
    print(f"  Taker baseline:              ${taker_avg:,.0f}/month")
    print()
    print(f"  MM edge over taker at 50% capture: ${avg_mo - taker_avg:,.0f}/month ({(avg_mo/taker_avg - 1)*100:.0f}% more)")
    print()
    print(f"  Annualized at moderate scenario:")
    print(f"    ${avg_mo * 12:,.0f}/year ({avg_mo/CAPITAL*12*100:.0f}% annual ROI)")


if __name__ == "__main__":
    main()
