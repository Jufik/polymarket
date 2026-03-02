"""S1 Hit-Rate Copy Strategy -- ClickHouse-native walk-forward backtest.

Simulates $10 bets on qualified-trader positions that pass the validated
filter stack (L1+L2+L4+L7). Uses ClickHouse temp tables from S1d for
trader qualification and enriched position data.

Remote CH: 192.168.0.148:18123, database=polymarket

Outputs:
  - Monthly results table
  - Daily equity curve
  - Summary stats (Sharpe, drawdown, total PnL, trades/day)
  - research/output/s1_backtest_results.parquet

Usage:
    uv run python research/scripts/s1_backtest.py
"""

from __future__ import annotations

import math
from pathlib import Path

import httpx
import polars as pl

CH_URL = "http://192.168.0.148:18123/"
CH_DB = "polymarket"
OUTPUT_DIR = Path("research/output")
BET_SIZE = 10.0      # USD per trade
SLIPPAGE = 0.20      # 2% of $10 = $0.20 per trade (calibrated from alpha decay)
FEE = 0.0            # Most Polymarket markets are fee-free

# With consensus >= 4, ALL price bands from 0.60-0.90 are positive EV
MIN_DIR_PRICE = 0.60
MAX_DIR_PRICE = 0.90
MIN_CONSENSUS = 4    # Minimum qualified traders on same side


def query_ch(sql: str) -> pl.DataFrame:
    """Execute a query against remote ClickHouse, return as Polars DataFrame."""
    resp = httpx.post(
        CH_URL,
        params={"database": CH_DB},
        content=sql + " FORMAT TSVWithNamesAndTypes",
        timeout=120.0,
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if not text:
        return pl.DataFrame()

    lines = text.split("\n")
    if len(lines) < 3:
        return pl.DataFrame()

    header = lines[0].split("\t")
    type_row = lines[1].split("\t")
    rows = [line.split("\t") for line in lines[2:] if line.strip()]

    # Build Polars schema from CH types
    schema: dict[str, pl.DataType] = {}
    for col, ch_type in zip(header, type_row):
        ch_type = ch_type.strip()
        if "Int" in ch_type or "UInt" in ch_type:
            schema[col] = pl.Int64
        elif "Float" in ch_type or "Decimal" in ch_type:
            schema[col] = pl.Float64
        elif "Date" in ch_type and "Time" not in ch_type:
            schema[col] = pl.Utf8  # parse later
        else:
            schema[col] = pl.Utf8

    data: dict[str, list[str]] = {col: [] for col in header}
    for row in rows:
        for col, val in zip(header, row):
            data[col].append(val)

    df = pl.DataFrame(data)

    # Cast numeric columns
    cast_exprs = []
    for col, dtype in schema.items():
        if dtype == pl.Int64:
            cast_exprs.append(pl.col(col).cast(pl.Int64))
        elif dtype == pl.Float64:
            cast_exprs.append(pl.col(col).cast(pl.Float64))

    if cast_exprs:
        df = df.with_columns(cast_exprs)

    return df


def run_backtest() -> None:
    """Run the full S1 backtest against ClickHouse data."""

    print("=" * 70)
    print("S1 HIT-RATE COPY STRATEGY BACKTEST")
    print("=" * 70)
    print(f"Bet size: ${BET_SIZE:.0f} | Slippage: ${SLIPPAGE:.2f}/trade | Fee: ${FEE:.2f}")
    print(
        f"Filters: HR>=0.75, pos>=20, spec>=0.70, last5>0, "
        f"consensus>={MIN_CONSENSUS}, price {MIN_DIR_PRICE}-{MAX_DIR_PRICE}"
    )
    print(f"Period: Jul 2025 -- Feb 2026 (8 months OOS)")
    print()

    # -----------------------------------------------------------------------
    # 1. Per-position data: one entry per unique (market, side, month)
    #    This matches actual strategy behavior: we enter ONCE per market
    #    when consensus >= N traders agree on the same side.
    #    Entry price = avg across agreeing traders (conservative estimate).
    # -----------------------------------------------------------------------
    print("Querying deduplicated positions from ClickHouse...")
    min_p = MIN_DIR_PRICE
    max_p = MAX_DIR_PRICE
    min_cons = MIN_CONSENSUS
    trades_sql = f"""
    SELECT
        pos.res_date,
        pos.entry_date,
        pos.entry_price,
        pos.correct,
        pos.hold_days,
        pos.category,
        pos.cid,
        pos.res_month,
        pos.consensus,
        pos.n_traders
    FROM (
        SELECT
            any(sub.res_date) AS res_date,
            min(sub.entry_date) AS entry_date,
            avg(sub.entry_price) AS entry_price,
            any(sub.correct) AS correct,
            any(sub.hold_days) AS hold_days,
            any(sub.category) AS category,
            sub.cid AS cid,
            sub.position AS position,
            any(sub.res_month) AS res_month,
            sub.rm AS rm,
            c.n_traders_same_side AS consensus,
            count() AS n_traders
        FROM (
            SELECT
                e.res_date AS res_date,
                toDate(e.first_trade) AS entry_date,
                e.dir_entry_price AS entry_price,
                e.correct AS correct,
                e.hold_days AS hold_days,
                e.category AS category,
                e.cid AS cid,
                e.trader AS trd,
                formatDateTime(e.res_month, '%Y-%m') AS res_month,
                e.position AS position,
                e.res_month AS rm
            FROM _tmp_s1d_enriched e
            JOIN _tmp_s1d_train_stats ts ON e.trader = ts.trader AND e.res_month = ts.test_month
            JOIN _tmp_s1d_specialization sp ON e.trader = sp.trader AND ts.test_month = sp.test_month
            LEFT JOIN _tmp_s1d_streaks st ON e.trader = st.trader AND ts.test_month = st.test_month
            WHERE ts.train_hr >= 0.75
              AND sp.specialization >= 0.70
              AND coalesce(st.last5_correct, 5) > 0
              AND e.category <> 'gambling'
              AND e.dir_entry_price >= {min_p}
              AND e.dir_entry_price < {max_p}
              AND e.res_month >= '2025-07-01'
        ) sub
        LEFT JOIN _tmp_s1d_consensus c
            ON sub.rm = c.tmonth AND sub.cid = c.cid AND sub.position = c.pos
        WHERE c.n_traders_same_side >= {min_cons}
        GROUP BY sub.cid, sub.position, sub.rm, c.n_traders_same_side
    ) pos
    ORDER BY pos.res_date
    """

    df = query_ch(trades_sql)
    print(f"  Retrieved {len(df)} trades")

    # Parse dates
    df = df.with_columns([
        pl.col("res_date").str.to_date("%Y-%m-%d").alias("res_date"),
        pl.col("entry_date").str.to_date("%Y-%m-%d").alias("entry_date"),
    ])

    # Compute per-trade PnL
    df = df.with_columns([
        pl.when(pl.col("correct") == 1)
        .then(BET_SIZE * (1.0 / pl.col("entry_price") - 1.0) - SLIPPAGE - FEE)
        .otherwise(-BET_SIZE - SLIPPAGE - FEE)
        .alias("trade_pnl"),
    ])

    total_trades = len(df)
    total_correct = df.filter(pl.col("correct") == 1).height
    total_pnl = df["trade_pnl"].sum()
    avg_pnl = df["trade_pnl"].mean()

    print(f"  Total trades: {total_trades}")
    print(f"  Correct: {total_correct} ({total_correct/total_trades*100:.1f}%)")
    print(f"  Total PnL: ${total_pnl:.2f}")
    print(f"  Avg PnL/trade: ${avg_pnl:.4f}")
    print()

    # -----------------------------------------------------------------------
    # 2. Daily aggregation
    # -----------------------------------------------------------------------
    daily = (
        df.group_by("res_date")
        .agg([
            pl.len().alias("n_resolved"),
            pl.col("trade_pnl").sum().alias("daily_pnl"),
            pl.col("correct").mean().alias("daily_hr"),
        ])
        .sort("res_date")
    )

    # Equity curve
    daily = daily.with_columns([
        pl.col("daily_pnl").cum_sum().alias("cum_pnl"),
    ])

    # Drawdown
    daily = daily.with_columns([
        pl.col("cum_pnl").cum_max().alias("peak"),
    ])
    daily = daily.with_columns([
        (pl.col("cum_pnl") - pl.col("peak")).alias("drawdown"),
    ])

    max_drawdown = daily["drawdown"].min()
    total_days = daily.height
    min_date = daily["res_date"].min()
    max_date = daily["res_date"].max()

    # Daily Sharpe (annualized)
    daily_mean = daily["daily_pnl"].mean()
    daily_std = daily["daily_pnl"].std()
    if daily_std and daily_std > 0:
        daily_sharpe = daily_mean / daily_std
        annual_sharpe = daily_sharpe * math.sqrt(365)
    else:
        daily_sharpe = 0.0
        annual_sharpe = 0.0

    # Trades per day
    avg_trades_day = total_trades / total_days if total_days > 0 else 0

    # Capital usage estimation
    # At any given time, we have ~avg_hold_days * avg_entries_per_day positions open
    avg_hold = df["hold_days"].mean()
    avg_entries_per_day = total_trades / total_days if total_days > 0 else 0

    # Better: compute actual daily open positions
    # For each day, count positions where entry_date <= day < res_date
    # This is expensive, so estimate: open_positions = entries_per_day * avg_hold
    est_open = avg_entries_per_day * avg_hold
    est_capital = est_open * BET_SIZE

    # Winning/losing streaks
    winning_pnl = df.filter(pl.col("correct") == 1)["trade_pnl"].sum()
    losing_pnl = df.filter(pl.col("correct") == 0)["trade_pnl"].sum()
    profit_factor = abs(winning_pnl / losing_pnl) if losing_pnl != 0 else float("inf")

    # -----------------------------------------------------------------------
    # 3. Monthly breakdown
    # -----------------------------------------------------------------------
    monthly = (
        df.group_by("res_month")
        .agg([
            pl.len().alias("n_trades"),
            pl.col("correct").mean().alias("hr"),
            pl.col("trade_pnl").sum().alias("monthly_pnl"),
            pl.col("trade_pnl").mean().alias("avg_pnl_trade"),
            pl.col("entry_price").mean().alias("avg_entry"),
            pl.col("hold_days").mean().alias("avg_hold"),
            pl.col("hold_days").median().alias("med_hold"),
        ])
        .sort("res_month")
    )

    # Monthly Sharpe
    monthly_pnls = monthly["monthly_pnl"].to_list()
    if len(monthly_pnls) > 1:
        m_mean = sum(monthly_pnls) / len(monthly_pnls)
        m_var = sum((x - m_mean) ** 2 for x in monthly_pnls) / (len(monthly_pnls) - 1)
        m_std = math.sqrt(m_var)
        monthly_sharpe = m_mean / m_std if m_std > 0 else 0.0
    else:
        monthly_sharpe = 0.0

    # -----------------------------------------------------------------------
    # 4. Category breakdown
    # -----------------------------------------------------------------------
    by_cat = (
        df.group_by("category")
        .agg([
            pl.len().alias("n_trades"),
            pl.col("correct").mean().alias("hr"),
            pl.col("trade_pnl").sum().alias("total_pnl"),
            pl.col("trade_pnl").mean().alias("avg_pnl"),
            pl.col("hold_days").mean().alias("avg_hold"),
        ])
        .sort("total_pnl", descending=True)
    )

    # -----------------------------------------------------------------------
    # Print results
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("SUMMARY STATISTICS")
    print("=" * 70)
    print(f"  Period:              {min_date} to {max_date} ({total_days} trading days)")
    print(f"  Total trades:        {total_trades:,}")
    print(f"  Hit rate:            {total_correct/total_trades*100:.1f}%")
    print(f"  Total PnL:           ${total_pnl:,.2f}")
    print(f"  Avg PnL/trade:       ${avg_pnl:.4f}")
    print(f"  Trades/day:          {avg_trades_day:.1f}")
    print(f"  PnL/day:             ${total_pnl/total_days:.2f}")
    print(f"  Avg hold (days):     {avg_hold:.1f}")
    print(f"  Est open positions:  {est_open:.0f}")
    print(f"  Est capital needed:  ${est_capital:.0f}")
    print()
    print(f"  Daily Sharpe:        {daily_sharpe:.4f}")
    print(f"  Annualized Sharpe:   {annual_sharpe:.2f}")
    print(f"  Monthly Sharpe:      {monthly_sharpe:.2f}")
    print(f"  Max drawdown:        ${max_drawdown:.2f}")
    print(f"  Profit factor:       {profit_factor:.2f}")
    print(f"  Winning PnL:         ${winning_pnl:,.2f}")
    print(f"  Losing PnL:          ${losing_pnl:,.2f}")

    print()
    print("=" * 70)
    print("MONTHLY BREAKDOWN")
    print("=" * 70)
    print(f"  {'Month':<10} {'Trades':>7} {'HR':>7} {'PnL':>12} {'PnL/Trade':>10} "
          f"{'Avg Entry':>10} {'Avg Hold':>9}")
    print(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*10} {'-'*9}")
    for row in monthly.iter_rows(named=True):
        print(
            f"  {row['res_month']:<10} "
            f"{row['n_trades']:>7,} "
            f"{row['hr']*100:>6.1f}% "
            f"${row['monthly_pnl']:>10,.2f} "
            f"${row['avg_pnl_trade']:>9.4f} "
            f"{row['avg_entry']:>10.3f} "
            f"{row['avg_hold']:>8.1f}d"
        )
    print(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*10} {'-'*9}")
    print(
        f"  {'TOTAL':<10} "
        f"{total_trades:>7,} "
        f"{total_correct/total_trades*100:>6.1f}% "
        f"${total_pnl:>10,.2f} "
        f"${avg_pnl:>9.4f} "
        f"{df['entry_price'].mean():>10.3f} "
        f"{avg_hold:>8.1f}d"
    )

    pos_months = sum(1 for p in monthly_pnls if p > 0)
    neg_months = sum(1 for p in monthly_pnls if p <= 0)
    print(f"\n  Profitable months: {pos_months}/{len(monthly_pnls)}")

    print()
    print("=" * 70)
    print("CATEGORY BREAKDOWN")
    print("=" * 70)
    print(f"  {'Category':<12} {'Trades':>7} {'HR':>7} {'Total PnL':>12} "
          f"{'PnL/Trade':>10} {'Avg Hold':>9}")
    print(f"  {'-'*12} {'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*9}")
    for row in by_cat.iter_rows(named=True):
        print(
            f"  {row['category']:<12} "
            f"{row['n_trades']:>7,} "
            f"{row['hr']*100:>6.1f}% "
            f"${row['total_pnl']:>10,.2f} "
            f"${row['avg_pnl']:>9.4f} "
            f"{row['avg_hold']:>8.1f}d"
        )

    # -----------------------------------------------------------------------
    # Capital efficiency analysis
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("CAPITAL EFFICIENCY")
    print("=" * 70)

    # What if we limit to max 20 open positions?
    # That means max $200 capital at risk
    # Trades/day capped at 20 / avg_hold
    max_positions = 20
    capped_entries_day = min(avg_entries_per_day, max_positions / max(avg_hold, 0.1))
    capped_trades_total = capped_entries_day * total_days
    capped_pnl = capped_trades_total * avg_pnl
    capped_capital = max_positions * BET_SIZE
    capped_roi = capped_pnl / capped_capital if capped_capital > 0 else 0.0

    print(f"  Scenario A: Unlimited positions")
    print(f"    Capital needed: ~${est_capital:,.0f}")
    print(f"    Total PnL:      ${total_pnl:,.2f}")
    print(f"    ROI (8 months): {total_pnl/est_capital*100:.1f}%")
    print()
    print(f"  Scenario B: Max {max_positions} open positions, ${capped_capital:.0f} capital")
    print(f"    Est trades/day: {capped_entries_day:.1f}")
    print(f"    Est total PnL:  ${capped_pnl:,.2f}")
    print(f"    Est ROI:        {capped_roi*100:.1f}%")
    print()
    print(f"  Scenario C: $1,000 bankroll, max 20 pos @ $10 each")
    real_capital = 1000
    print(f"    Capital at risk: ${capped_capital:.0f} (max {capped_capital/real_capital*100:.0f}% deployed)")
    print(f"    Est PnL/month:   ${capped_pnl/8:,.2f}")
    print(f"    Est annual ROI:  {capped_pnl/8*12/real_capital*100:.1f}%")

    # -----------------------------------------------------------------------
    # Equity curve percentiles
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("EQUITY CURVE MILESTONES")
    print("=" * 70)
    final_cum = daily["cum_pnl"][-1]
    for pct in [10, 25, 50, 75, 90, 100]:
        target = final_cum * pct / 100
        row_idx = daily.filter(pl.col("cum_pnl") >= target).head(1)
        if row_idx.height > 0:
            dt = row_idx["res_date"][0]
            print(f"  {pct:>3}% of final PnL (${target:>8,.2f}): reached by {dt}")

    # -----------------------------------------------------------------------
    # Save results
    # -----------------------------------------------------------------------
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Save position-level data
    save_cols = ["res_date", "entry_date", "entry_price", "correct",
                 "hold_days", "category", "trade_pnl", "res_month"]
    if "consensus" in df.columns:
        save_cols.append("consensus")
    if "n_traders" in df.columns:
        save_cols.append("n_traders")
    save_df = df.select(save_cols)
    save_path = OUTPUT_DIR / "s1_backtest_results.parquet"
    save_df.write_parquet(save_path, use_pyarrow=False)
    print(f"\n  Saved {len(save_df)} trade records to {save_path}")

    # Save daily equity curve
    daily_path = OUTPUT_DIR / "s1_backtest_daily.parquet"
    daily.write_parquet(daily_path, use_pyarrow=False)
    print(f"  Saved {len(daily)} daily records to {daily_path}")

    # Save monthly summary
    monthly_path = OUTPUT_DIR / "s1_backtest_monthly.parquet"
    monthly.write_parquet(monthly_path, use_pyarrow=False)
    print(f"  Saved {len(monthly)} monthly records to {monthly_path}")


if __name__ == "__main__":
    run_backtest()
