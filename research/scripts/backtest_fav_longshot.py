"""Walk-forward backtest of the Favorite-Longshot Arbitrage strategy.

Edge: binary markets with early YES price in 15-45% are systematically
overpricing YES (actual YES rate is 5-9pp lower than implied).
Strategy: buy NO on these markets at the early price.

Walk-forward: each month, compute calibration on ALL prior resolved markets,
then bet NO on new markets where the early YES price falls in the edge zone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

DATA_DIR = Path("data")
OUT_DIR = Path("insights/overpriceNo")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Step 1: Compute early price per market
# ---------------------------------------------------------------------------

def compute_early_prices(
    prices_path: Path,
    pct_lifetime: float = 0.30,
    min_trades: int = 3,
) -> pl.DataFrame:
    """Compute the median YES price in the first `pct_lifetime` of each market's life.

    Returns DataFrame with (condition_id, early_yes_price, n_early_trades,
    market_open_ts, market_close_ts, lifetime_s).
    """
    print("  Loading prices (340M rows)...")
    prices = pl.scan_parquet(str(prices_path))

    # Market open/close timestamps
    print("  Computing market spans...")
    spans = (
        prices.group_by("condition_id")
        .agg(
            pl.col("timestamp").min().alias("market_open_ts"),
            pl.col("timestamp").max().alias("market_close_ts"),
            pl.len().alias("total_trades"),
        )
        .with_columns(
            (pl.col("market_close_ts") - pl.col("market_open_ts")).alias("lifetime_s")
        )
        .filter(pl.col("lifetime_s") > 0)
        .collect()
    )
    print(f"  {spans.height:,} markets with positive lifetime")

    # Filter to early trades (first pct_lifetime of lifetime)
    print(f"  Filtering to first {pct_lifetime*100:.0f}% of each market's lifetime...")
    early = (
        prices.join(pl.LazyFrame(spans), on="condition_id", how="inner")
        .filter(
            pl.col("timestamp")
            <= pl.col("market_open_ts") + pl.col("lifetime_s") * pct_lifetime
        )
        .group_by("condition_id")
        .agg(
            pl.col("yes_price").median().alias("early_yes_price"),
            pl.len().alias("n_early_trades"),
        )
        .filter(pl.col("n_early_trades") >= min_trades)
        .collect()
    )
    print(f"  {early.height:,} markets with >= {min_trades} early trades")

    return early.join(spans, on="condition_id", how="inner")


# ---------------------------------------------------------------------------
# Step 2: Build calibration curve from historical data
# ---------------------------------------------------------------------------

def build_calibration(
    resolved: pl.DataFrame,
    n_bins: int = 20,
) -> pl.DataFrame:
    """Build calibration curve: for each price bin, what fraction resolves YES?

    Returns DataFrame with (bin_lo, bin_hi, implied_yes, actual_yes, count, no_edge).
    """
    binned = resolved.with_columns(
        (pl.col("early_yes_price") * n_bins).floor().cast(pl.Int64).clip(0, n_bins - 1).alias("bin")
    )

    calib = (
        binned.group_by("bin")
        .agg(
            pl.len().alias("count"),
            pl.col("yes_won").mean().alias("actual_yes"),
            pl.col("early_yes_price").mean().alias("implied_yes"),
        )
        .sort("bin")
        .with_columns(
            (pl.col("bin") / n_bins).alias("bin_lo"),
            ((pl.col("bin") + 1) / n_bins).alias("bin_hi"),
            (pl.col("implied_yes") - pl.col("actual_yes")).alias("no_edge"),
        )
    )
    return calib


# ---------------------------------------------------------------------------
# Step 3: Walk-forward backtest
# ---------------------------------------------------------------------------

@dataclass
class BetResult:
    condition_id: str
    resolved_at: datetime
    entry_month: str
    early_yes_price: float
    implied_yes: float
    actual_won_no: bool
    bet_size: float
    pnl: float
    calibration_edge: float  # expected edge from calibration


def walk_forward_backtest(
    resolved_with_prices: pl.DataFrame,
    min_edge: float = 0.03,       # minimum calibration edge to bet
    entry_lo: float = 0.10,       # min YES price to consider
    entry_hi: float = 0.50,       # max YES price to consider
    bet_size: float = 100.0,
    fee_pct: float = 0.00,        # most Polymarket markets have 0 fees
    n_bins: int = 20,
    train_start: str = "2023-01-01",
    test_start: str = "2025-01-01",
    test_end: str = "2026-02-01",
) -> tuple[list[BetResult], list[dict]]:
    """Walk-forward monthly backtest.

    For each month from test_start to test_end:
    1. Build calibration curve on all data from train_start to current month
    2. Identify markets that resolved this month with early YES price in [entry_lo, entry_hi]
    3. Check if calibration edge > min_edge for that price bin
    4. Bet NO if edge exists, compute PnL
    """
    bets: list[BetResult] = []
    monthly_stats: list[dict] = []

    # Add month column
    df = resolved_with_prices.with_columns(
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("resolve_month")
    )

    # Get sorted list of months
    all_months = sorted(df["resolve_month"].unique().drop_nulls().to_list())
    test_months = [m for m in all_months if m >= test_start[:7] and m < test_end[:7]]

    print(f"\nWalk-forward: {len(test_months)} test months ({test_months[0]} to {test_months[-1]})")
    print(f"  Entry range: YES price {entry_lo:.0%}-{entry_hi:.0%}")
    print(f"  Min calibration edge: {min_edge:.0%}")
    print(f"  Bet size: ${bet_size:.0f}, Fee: {fee_pct:.1%}\n")

    for month in test_months:
        # Training data: everything before this month
        train = df.filter(pl.col("resolve_month") < month)
        if train.height < 1000:
            continue

        # Build calibration on training data
        calib = build_calibration(train, n_bins=n_bins)
        calib_dict = {
            row["bin"]: {
                "implied_yes": row["implied_yes"],
                "actual_yes": row["actual_yes"],
                "no_edge": row["no_edge"],
                "count": row["count"],
            }
            for row in calib.iter_rows(named=True)
        }

        # Test data: markets resolving this month in the entry range
        test = df.filter(
            (pl.col("resolve_month") == month)
            & (pl.col("early_yes_price") >= entry_lo)
            & (pl.col("early_yes_price") <= entry_hi)
        )

        if test.height == 0:
            monthly_stats.append({
                "month": month, "n_bets": 0, "n_wins": 0, "pnl": 0.0,
                "train_size": train.height,
            })
            continue

        # For each test market, check calibration edge
        month_bets = 0
        month_wins = 0
        month_pnl = 0.0

        for row in test.iter_rows(named=True):
            price = row["early_yes_price"]
            bin_idx = min(int(price * n_bins), n_bins - 1)

            if bin_idx not in calib_dict:
                continue

            edge = calib_dict[bin_idx]["no_edge"]
            if edge < min_edge:
                continue

            # Bet NO at price = 1 - early_yes_price
            no_price = 1.0 - price
            yes_won = row["yes_won"]
            won_no = not yes_won

            fee = bet_size * fee_pct
            if won_no:
                # Won: pay no_price, receive 1.0 per unit
                # PnL = bet_size * (1 - no_price) / no_price - fee
                # Simplified: bet NO at no_price, win = bet * yes_price / no_price
                pnl = bet_size * price / no_price - fee
            else:
                # Lost: lose bet
                pnl = -bet_size - fee

            bets.append(BetResult(
                condition_id=row["condition_id"],
                resolved_at=row["resolved_at"],
                entry_month=month,
                early_yes_price=price,
                implied_yes=calib_dict[bin_idx]["implied_yes"],
                actual_won_no=won_no,
                bet_size=bet_size,
                pnl=pnl,
                calibration_edge=edge,
            ))

            month_bets += 1
            month_wins += int(won_no)
            month_pnl += pnl

        monthly_stats.append({
            "month": month,
            "n_bets": month_bets,
            "n_wins": month_wins,
            "hit_rate": month_wins / month_bets if month_bets > 0 else 0.0,
            "pnl": month_pnl,
            "avg_pnl_per_bet": month_pnl / month_bets if month_bets > 0 else 0.0,
            "train_size": train.height,
        })

    return bets, monthly_stats


# ---------------------------------------------------------------------------
# Step 4: Compute aggregate metrics
# ---------------------------------------------------------------------------

def compute_metrics(bets: list[BetResult], monthly_stats: list[dict]) -> dict:
    """Compute aggregate backtest metrics."""
    if not bets:
        return {"error": "no bets"}

    pnls = [b.pnl for b in bets]
    wins = [b for b in bets if b.actual_won_no]

    total_pnl = sum(pnls)
    n_bets = len(bets)
    hit_rate = len(wins) / n_bets

    # Daily PnL for Sharpe
    daily_pnl: dict[str, float] = {}
    for b in bets:
        day = b.resolved_at.strftime("%Y-%m-%d") if b.resolved_at else "unknown"
        daily_pnl[day] = daily_pnl.get(day, 0.0) + b.pnl

    daily_vals = list(daily_pnl.values())
    if len(daily_vals) > 1:
        mean_d = np.mean(daily_vals)
        std_d = np.std(daily_vals, ddof=1)
        sharpe = float(mean_d / std_d * np.sqrt(252)) if std_d > 0 else 0.0
    else:
        sharpe = 0.0

    # Monthly Sharpe
    monthly_pnls = [s["pnl"] for s in monthly_stats if s["n_bets"] > 0]
    if len(monthly_pnls) > 1:
        m_mean = np.mean(monthly_pnls)
        m_std = np.std(monthly_pnls, ddof=1)
        monthly_sharpe = float(m_mean / m_std * np.sqrt(12)) if m_std > 0 else 0.0
    else:
        monthly_sharpe = 0.0

    # Avg calibration edge on bets taken
    avg_edge = np.mean([b.calibration_edge for b in bets])

    # Capital efficiency: avg NO price paid
    avg_no_price = np.mean([1 - b.early_yes_price for b in bets])

    # Max drawdown on cumulative PnL
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

    # Win/loss ratio
    avg_win = np.mean([b.pnl for b in bets if b.actual_won_no]) if wins else 0.0
    avg_loss = np.mean([b.pnl for b in bets if not b.actual_won_no]) if len(bets) > len(wins) else 0.0

    return {
        "n_bets": n_bets,
        "n_wins": len(wins),
        "hit_rate": round(hit_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_bet": round(total_pnl / n_bets, 2),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
        "win_loss_ratio": round(abs(float(avg_win) / float(avg_loss)), 2) if avg_loss != 0 else 0.0,
        "daily_sharpe": round(sharpe, 2),
        "monthly_sharpe": round(monthly_sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_calibration_edge": round(float(avg_edge), 4),
        "avg_no_price": round(float(avg_no_price), 4),
        "n_months_active": sum(1 for s in monthly_stats if s["n_bets"] > 0),
        "avg_bets_per_month": round(n_bets / max(sum(1 for s in monthly_stats if s["n_bets"] > 0), 1), 1),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("FAVORITE-LONGSHOT ARBITRAGE: Walk-Forward Backtest")
    print("=" * 70)

    # Load data
    print("\n1. Loading market data...")
    mr = pl.read_parquet(DATA_DIR / "derived/markets_resolved.parquet")
    mkt = pl.read_parquet(DATA_DIR / "metadata/markets.parquet")

    # Binary markets only
    binary_ids = set(mkt.filter(pl.col("neg_risk") == False)["condition_id"].to_list())
    resolved_binary = mr.filter(pl.col("condition_id").is_in(list(binary_ids)))
    print(f"  Binary resolved markets: {resolved_binary.height:,}")

    # Compute early prices
    print("\n2. Computing early prices...")
    early_prices = compute_early_prices(
        DATA_DIR / "derived/market_prices.parquet",
        pct_lifetime=0.30,
        min_trades=3,
    )

    # Join
    resolved_with_prices = resolved_binary.join(
        early_prices.select("condition_id", "early_yes_price", "n_early_trades", "lifetime_s"),
        on="condition_id",
        how="inner",
    )
    print(f"\n  Binary resolved with early prices: {resolved_with_prices.height:,}")

    # Overall calibration (full dataset, for reference)
    print("\n3. Full-dataset calibration (reference only)...")
    full_calib = build_calibration(resolved_with_prices, n_bins=20)
    print(f"  {'Bin':<8} {'Implied':>8} {'Actual':>8} {'NO Edge':>8} {'Count':>8}")
    print("  " + "-" * 45)
    for row in full_calib.iter_rows(named=True):
        print(
            f"  {row['bin_lo']:.2f}-{row['bin_hi']:.2f}"
            f"  {row['implied_yes']:>7.1%}"
            f"  {row['actual_yes']:>7.1%}"
            f"  {row['no_edge']:>+7.1%}"
            f"  {row['count']:>7,}"
        )

    # Run multiple configs
    configs = [
        # (name, entry_lo, entry_hi, min_edge, fee_pct)
        ("wide_0fee", 0.10, 0.50, 0.03, 0.00),
        ("sweet_spot_0fee", 0.15, 0.45, 0.05, 0.00),
        ("tight_0fee", 0.20, 0.35, 0.05, 0.00),
        ("wide_2fee", 0.10, 0.50, 0.03, 0.02),
        ("sweet_spot_2fee", 0.15, 0.45, 0.05, 0.02),
    ]

    all_results = {}
    for name, lo, hi, min_edge, fee in configs:
        print(f"\n{'=' * 70}")
        print(f"CONFIG: {name}")
        print(f"{'=' * 70}")

        bets, monthly = walk_forward_backtest(
            resolved_with_prices,
            min_edge=min_edge,
            entry_lo=lo,
            entry_hi=hi,
            bet_size=100.0,
            fee_pct=fee,
            n_bins=20,
            test_start="2025-01-01",
            test_end="2026-02-01",
        )

        metrics = compute_metrics(bets, monthly)
        all_results[name] = metrics

        print(f"\n  RESULTS:")
        for k, v in metrics.items():
            print(f"    {k}: {v}")

        # Monthly breakdown
        print(f"\n  MONTHLY:")
        print(f"  {'Month':<10} {'Bets':>6} {'Wins':>6} {'HR':>8} {'PnL':>10} {'PnL/bet':>10}")
        print("  " + "-" * 55)
        for s in monthly:
            if s["n_bets"] > 0:
                print(
                    f"  {s['month']:<10} {s['n_bets']:>6} {s['n_wins']:>6}"
                    f"  {s['hit_rate']:>7.1%} ${s['pnl']:>9,.0f} ${s['avg_pnl_per_bet']:>9,.1f}"
                )

    # Save results
    print("\n\n" + "=" * 70)
    print("SUMMARY COMPARISON")
    print("=" * 70)
    print(f"{'Config':<20} {'Bets':>6} {'HR':>8} {'PnL':>10} {'$/bet':>8} {'Sharpe':>8} {'DD':>8}")
    print("-" * 75)
    for name, m in all_results.items():
        if "error" in m:
            print(f"{name:<20} NO BETS")
            continue
        print(
            f"{name:<20} {m['n_bets']:>6} {m['hit_rate']:>7.1%}"
            f" ${m['total_pnl']:>9,.0f} ${m['avg_pnl_per_bet']:>7.1f}"
            f" {m['monthly_sharpe']:>7.2f} ${m['max_drawdown']:>7,.0f}"
        )

    json_path = OUT_DIR / "03_fav_longshot_backtest.json"
    json_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nSaved to {json_path}")


if __name__ == "__main__":
    main()
