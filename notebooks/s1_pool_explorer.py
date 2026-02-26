import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S1 Pool Explorer — Resolution-Based")


@app.cell
def load_libs():
    import marimo as mo
    import polars as pl
    import plotly.express as px
    import plotly.graph_objects as go
    from datetime import datetime
    from pathlib import Path

    mo.md("# S1 Pool Explorer — Resolution-Based\n"
           "Explore trader pools selected on **hit rate at resolution** "
           "(positions held at resolution only, `net_yes_tokens != 0`).")
    return Path, datetime, mo, pl


@app.cell
def load_data(Path, pl):
    DATA_DIR = Path("data/derived")

    pnl_raw = pl.read_parquet(DATA_DIR / "trader_market_pnl.parquet")
    resolved = pl.read_parquet(DATA_DIR / "markets_resolved.parquet")

    # Strip timezones
    for col in ["resolved_at"]:
        dtype = resolved.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            resolved = resolved.with_columns(pl.col(col).dt.replace_time_zone(None))
    for col in ["first_trade", "last_trade"]:
        dtype = pnl_raw.schema.get(col)
        if dtype is not None and isinstance(dtype, pl.Datetime) and dtype.time_zone:
            pnl_raw = pnl_raw.with_columns(pl.col(col).dt.replace_time_zone(None))

    # Join + filter: only positions HELD at resolution
    df = pnl_raw.join(resolved, on="condition_id", how="inner")
    df = df.filter(pl.col("net_yes_tokens") != 0)

    # Enrich
    df = df.with_columns(
        (pl.col("net_yes_tokens") > 0).alias("bet_yes"),
        pl.when(pl.col("net_yes_tokens") > 0)
        .then(pl.col("wavg_yes_entry_price"))
        .otherwise(1.0 - pl.col("wavg_yes_entry_price"))
        .alias("dir_entry"),
        (
            ((pl.col("net_yes_tokens") > 0) & pl.col("yes_won"))
            | ((pl.col("net_yes_tokens") <= 0) & ~pl.col("yes_won"))
        ).alias("correct"),
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("month"),
    )

    n_rows = df.height
    n_traders = df["trader"].n_unique()
    n_markets = df["condition_id"].n_unique()
    return df, n_markets, n_rows, n_traders


@app.cell
def show_data_stats(mo, n_markets, n_rows, n_traders):
    mo.md(f"""
    **Data loaded** (positions held at resolution only):
    - **{n_rows:,}** positions | **{n_traders:,}** traders | **{n_markets:,}** markets
    - Excluded: traders who sold before resolution (`net_yes_tokens == 0`)
    """)
    return


@app.cell
def controls(mo):
    mo.md("""
    ---
    ## Pool Selection Controls
    """)
    return


@app.cell
def time_window_control(mo):
    time_window = mo.ui.dropdown(
        options={
            "Last 3 months (Oct-Dec 2025)": "3m",
            "Last 6 months (Jul-Dec 2025)": "6m",
            "Last 9 months (Apr-Dec 2025)": "9m",
            "Last 12 months (Jan-Dec 2025)": "12m",
            "Full history (2023-2025)": "all",
        },
        value="Last 6 months (Jul-Dec 2025)",
        label="Training Window",
    )
    return (time_window,)


@app.cell
def pool_controls(mo):
    min_hr = mo.ui.slider(
        start=0.40, stop=0.85, step=0.05, value=0.55,
        label="Min Hit Rate (resolution accuracy)",
    )
    min_markets = mo.ui.slider(
        start=5, stop=100, step=5, value=30,
        label="Min Markets Traded",
    )
    min_months = mo.ui.slider(
        start=1, stop=12, step=1, value=6,
        label="Min Active Months (with HR > monthly threshold)",
    )
    monthly_hr = mo.ui.slider(
        start=0.40, stop=0.70, step=0.05, value=0.50,
        label="Monthly Min HR (for consistency check)",
    )
    return min_hr, min_markets, min_months, monthly_hr


@app.cell
def signal_controls(mo):
    mo.md("""
    ### Signal Filters (applied on holdout signals)
    """)
    return


@app.cell
def entry_controls(mo):
    entry_lo = mo.ui.slider(
        start=0.0, stop=0.90, step=0.05, value=0.0,
        label="Min Dir Entry Price",
    )
    entry_hi = mo.ui.slider(
        start=0.10, stop=1.0, step=0.05, value=1.0,
        label="Max Dir Entry Price",
    )
    direction_filter = mo.ui.dropdown(
        options={"ALL": "ALL", "NO only": "NO", "YES only": "YES"},
        value="ALL",
        label="Direction Filter",
    )
    return direction_filter, entry_hi, entry_lo


@app.cell
def compute_time_bounds(datetime, time_window):
    tw = time_window.value
    if tw == "3m":
        train_start = datetime(2023, 1, 1)
        train_end = datetime(2025, 10, 1)
        holdout_start = datetime(2025, 10, 1)
        holdout_end = datetime(2026, 1, 1)
    elif tw == "6m":
        train_start = datetime(2023, 1, 1)
        train_end = datetime(2025, 7, 1)
        holdout_start = datetime(2025, 7, 1)
        holdout_end = datetime(2026, 1, 1)
    elif tw == "9m":
        train_start = datetime(2023, 1, 1)
        train_end = datetime(2025, 4, 1)
        holdout_start = datetime(2025, 4, 1)
        holdout_end = datetime(2026, 1, 1)
    elif tw == "12m":
        train_start = datetime(2023, 1, 1)
        train_end = datetime(2025, 1, 1)
        holdout_start = datetime(2025, 1, 1)
        holdout_end = datetime(2026, 1, 1)
    else:  # all
        train_start = datetime(2023, 1, 1)
        train_end = datetime(2025, 7, 1)
        holdout_start = datetime(2025, 7, 1)
        holdout_end = datetime(2026, 1, 1)
    return holdout_end, holdout_start, train_end, train_start


@app.cell
def build_pool(
    df,
    min_hr,
    min_markets,
    min_months,
    monthly_hr,
    pl,
    train_end,
    train_start,
):
    train = df.filter(
        (pl.col("resolved_at") >= train_start)
        & (pl.col("resolved_at") < train_end)
    )

    # Overall per-trader stats
    trader_stats = train.group_by("trader").agg(
        pl.col("correct").mean().alias("hr"),
        pl.len().alias("n_markets"),
        pl.col("bet_yes").mean().alias("yes_frac"),
        pl.col("dir_entry").median().alias("median_entry"),
        pl.col("dir_entry").mean().alias("mean_entry"),
        pl.col("month").n_unique().alias("active_months"),
        pl.col("trade_count").mean().alias("avg_trades_per_market"),
        # Direction-specific HR
        (pl.col("correct").filter(pl.col("bet_yes"))).mean().alias("yes_hr"),
        (pl.col("correct").filter(~pl.col("bet_yes"))).mean().alias("no_hr"),
        pl.col("bet_yes").sum().alias("n_yes"),
        (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
        pl.col("market_pnl").sum().alias("total_pnl"),
    )

    # Monthly consistency
    monthly = train.with_columns(
        pl.col("resolved_at").dt.strftime("%Y%m").alias("ym")
    ).group_by(["trader", "ym"]).agg(
        pl.col("correct").mean().alias("month_hr"),
        pl.len().alias("month_n"),
    ).filter(pl.col("month_n") >= 3)

    m_good = monthly.filter(
        pl.col("month_hr") >= monthly_hr.value
    ).group_by("trader").agg(
        pl.len().alias("good_months"),
    )

    trader_stats = trader_stats.join(m_good, on="trader", how="left").with_columns(
        pl.col("good_months").fill_null(0),
    )

    # Apply filters
    pool_df = trader_stats.filter(
        (pl.col("n_markets") >= min_markets.value)
        & (pl.col("hr") >= min_hr.value)
        & (pl.col("active_months") >= min_months.value)
        & (pl.col("good_months") >= min_months.value)
    )

    pool_set = frozenset(pool_df["trader"].to_list())
    return pool_df, pool_set


@app.cell
def compute_holdout(
    df,
    direction_filter,
    entry_hi,
    entry_lo,
    holdout_end,
    holdout_start,
    pl,
    pool_set,
):
    FEE_PCT = 0.02
    BASE_BET = 100.0

    holdout = df.filter(
        (pl.col("resolved_at") >= holdout_start)
        & (pl.col("resolved_at") < holdout_end)
        & pl.col("trader").is_in(list(pool_set))
    )

    # Direction filter
    if direction_filter.value == "YES":
        holdout = holdout.filter(pl.col("bet_yes"))
    elif direction_filter.value == "NO":
        holdout = holdout.filter(~pl.col("bet_yes"))

    # Entry band filter
    holdout = holdout.filter(
        (pl.col("dir_entry") >= entry_lo.value)
        & (pl.col("dir_entry") <= entry_hi.value)
    )

    # Compute PnL per signal
    holdout = holdout.with_columns(
        pl.when(pl.col("correct"))
        .then(
            pl.lit(BASE_BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(BASE_BET) * FEE_PCT
        )
        .otherwise(-pl.lit(BASE_BET) - pl.lit(BASE_BET) * FEE_PCT)
        .alias("signal_pnl")
    )
    return


@app.cell
def compute_baseline(
    df,
    direction_filter,
    entry_hi,
    entry_lo,
    holdout_end,
    holdout_start,
    pl,
):
    FEE_PCT = 0.02
    BASE_BET = 100.0

    baseline = df.filter(
        (pl.col("resolved_at") >= holdout_start)
        & (pl.col("resolved_at") < holdout_end)
    )

    if direction_filter.value == "YES":
        baseline = baseline.filter(pl.col("bet_yes"))
    elif direction_filter.value == "NO":
        baseline = baseline.filter(~pl.col("bet_yes"))

    baseline = baseline.filter(
        (pl.col("dir_entry") >= entry_lo.value)
        & (pl.col("dir_entry") <= entry_hi.value)
    )

    baseline = baseline.with_columns(
        pl.when(pl.col("correct"))
        .then(
            pl.lit(BASE_BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(BASE_BET) * FEE_PCT
        )
        .otherwise(-pl.lit(BASE_BET) - pl.lit(BASE_BET) * FEE_PCT)
        .alias("signal_pnl")
    )
    return


app._unparsable_cell(
    """
    if holdout.height == 0:
        mo.md(\"**No signals match current filters.**\")
        return

    pool_hr = float(holdout[\"correct\"].mean())
    pool_pnl = float(holdout[\"signal_pnl\"].sum())
    pool_per = pool_pnl / holdout.height
    pool_avg_entry = float(holdout[\"dir_entry\"].mean())

    base_hr = float(baseline[\"correct\"].mean()) if baseline.height > 0 else 0
    base_per = float(baseline[\"signal_pnl\"].sum()) / baseline.height if baseline.height > 0 else 0
    mkt_implied = pool_avg_entry  # dir_entry IS market-implied probability

    edge_vs_all = pool_hr - base_hr
    edge_vs_mkt = pool_hr - mkt_implied

    pnl_color = \"green\" if pool_pnl > 0 else \"red\"
    edge_all_color = \"green\" if edge_vs_all > 0 else \"red\"
    edge_mkt_color = \"green\" if edge_vs_mkt > 0 else \"red\"

    mo.md(f\"\"\"
    ---
    ## Results

    | Metric | Pool | All Traders | Market Implied |
    |--------|------|-------------|----------------|
    | **Hit Rate** | **{pool_hr:.1%}** | {base_hr:.1%} | {mkt_implied:.1%} |
    | **$/signal** | **${pool_per:+.1f}** | ${base_per:+.1f} | — |
    | **Edge vs All** | **{edge_vs_all:+.1%}** | — | — |
    | **Edge vs Market** | **{edge_vs_mkt:+.1%}** | — | — |

    | | Value |
    |---|---|
    | **Pool size** | {len(pool_set):,} traders |
    | **Holdout signals** | {holdout.height:,} |
    | **Total PnL** | ${pool_pnl:+,.0f} |
    | **Avg entry** | {pool_avg_entry:.3f} |
    | **Baseline signals** | {baseline.height:,} |
    \"\"\")
    """,
    name="show_kpis"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    mo.md("---\n## Breakdown by Entry Price Band")
    """,
    name="entry_band_breakdown"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    bands = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
             (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80),
             (0.80, 0.90), (0.90, 1.0)]

    rows = []
    for lo, hi in bands:
        pool_band = holdout.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        base_band = baseline.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )

        if pool_band.height < 5:
            continue

        p_hr = float(pool_band["correct"].mean())
        p_per = float(pool_band["signal_pnl"].sum()) / pool_band.height
        b_hr = float(base_band["correct"].mean()) if base_band.height > 0 else 0
        b_per = float(base_band["signal_pnl"].sum()) / base_band.height if base_band.height > 0 else 0

        rows.append({
            "Band": f"{lo:.0%}-{hi:.0%}",
            "Pool N": pool_band.height,
            "Pool HR": f"{p_hr:.1%}",
            "Pool $/sig": f"${p_per:+.1f}",
            "All N": base_band.height,
            "All HR": f"{b_hr:.1%}",
            "All $/sig": f"${b_per:+.1f}",
            "Edge vs All": f"{p_hr - b_hr:+.1%}",
            "Edge vs Mkt": f"{p_hr - (lo + hi) / 2:+.1%}",
        })

    if rows:
        band_df = pl.DataFrame(rows)
        mo.ui.table(band_df.to_pandas(), label="Entry price bands")
    """,
    name="entry_band_table"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    bands = [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 1.0)]
    data = []
    for lo, hi in bands:
        mid = (lo + hi) / 2
        label = f"{lo:.0%}-{hi:.0%}"

        for source, src_df in [("Pool", holdout), ("All Traders", baseline)]:
            band = src_df.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )
            if band.height >= 10:
                data.append({
                    "Band": label, "Source": source,
                    "HR": float(band["correct"].mean()),
                    "$/sig": float(band["signal_pnl"].sum()) / band.height,
                    "N": band.height,
                })
        data.append({
            "Band": label, "Source": "Market Implied",
            "HR": mid, "$/sig": 0, "N": 0,
        })

    if data:
        chart_df = pl.DataFrame(data).to_pandas()
        fig = px.bar(
            chart_df, x="Band", y="HR", color="Source",
            barmode="group", title="Hit Rate by Entry Band: Pool vs All vs Market",
            text_auto=".1%",
        )
        fig.update_layout(yaxis_tickformat=".0%", height=400)
        return fig
    """,
    name="entry_band_chart"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return
    mo.md("---\n## Monthly Breakdown")
    """,
    name="monthly_header"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    monthly = holdout.group_by("month").agg(
        pl.len().alias("N"),
        pl.col("correct").mean().alias("HR"),
        pl.col("signal_pnl").sum().alias("PnL"),
        pl.col("dir_entry").mean().alias("Avg Entry"),
        pl.col("bet_yes").mean().alias("YES Frac"),
    ).sort("month")

    monthly = monthly.with_columns(
        (pl.col("PnL") / pl.col("N")).alias("$/sig"),
    )

    mo.ui.table(monthly.to_pandas(), label="Monthly performance")
    """,
    name="monthly_table"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    monthly = holdout.group_by("month").agg(
        pl.col("signal_pnl").sum().alias("PnL"),
        pl.col("correct").mean().alias("HR"),
        pl.len().alias("N"),
    ).sort("month").to_pandas()

    monthly["cumPnL"] = monthly["PnL"].cumsum()

    fig = px.bar(
        monthly, x="month", y="PnL",
        title="Monthly PnL ($ at $100/signal)",
        color="PnL",
        color_continuous_scale=["red", "gray", "green"],
        color_continuous_midpoint=0,
    )
    fig.update_layout(height=350)
    """,
    name="monthly_chart"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    monthly = holdout.group_by("month").agg(
        pl.col("signal_pnl").sum().alias("PnL"),
    ).sort("month").to_pandas()

    monthly["Cumulative"] = monthly["PnL"].cumsum()

    fig = px.line(
        monthly, x="month", y="Cumulative",
        title="Cumulative PnL",
        markers=True,
    )
    fig.update_layout(height=350)
    """,
    name="equity_curve"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return
    mo.md("---\n## By Average Trades per Market (trader activity level)")
    """,
    name="trades_header"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    # Bucket by trade_count (trader's trades in each market)
    buckets = [
        (1, 2, "1 trade"),
        (2, 5, "2-4 trades"),
        (5, 15, "5-14 trades"),
        (15, 50, "15-49 trades"),
        (50, 1_000_000, "50+ trades"),
    ]

    data = []
    for lo, hi, label in buckets:
        band = holdout.filter(
            (pl.col("trade_count") >= lo) & (pl.col("trade_count") < hi)
        )
        if band.height >= 10:
            data.append({
                "Bucket": label,
                "N": band.height,
                "HR": float(band["correct"].mean()),
                "$/sig": float(band["signal_pnl"].sum()) / band.height,
                "Avg Entry": float(band["dir_entry"].mean()),
                "YES Frac": float(band["bet_yes"].mean()),
            })

    if data:
        tdf = pl.DataFrame(data)
        mo.ui.table(tdf.to_pandas(), label="Performance by trader activity")

        fig = px.bar(
            tdf.to_pandas(), x="Bucket", y="$/sig",
            title="$/signal by Trade Count Bucket",
            color="$/sig",
            color_continuous_scale=["red", "gray", "green"],
            color_continuous_midpoint=0,
        )
        return fig
    """,
    name="trades_breakdown"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return
    mo.md("---\n## By YES/NO Ratio (trader direction preference)")
    """,
    name="buysell_header"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0 or pool_df.height == 0:
        return

    # Bucket traders by their yes_frac
    buckets = [
        (0.0, 0.10, "Heavy NO (0-10% YES)"),
        (0.10, 0.30, "NO-leaning (10-30%)"),
        (0.30, 0.50, "Slight NO (30-50%)"),
        (0.50, 0.70, "Slight YES (50-70%)"),
        (0.70, 0.90, "YES-leaning (70-90%)"),
        (0.90, 1.01, "Heavy YES (90-100%)"),
    ]

    # Get trader's yes_frac from pool stats
    traders_with_frac = pool_df.select("trader", "yes_frac")
    holdout_w = holdout.join(traders_with_frac, on="trader", how="left", suffix="_pool")

    data = []
    for lo, hi, label in buckets:
        band = holdout_w.filter(
            (pl.col("yes_frac") >= lo) & (pl.col("yes_frac") < hi)
        )
        if band.height >= 10:
            data.append({
                "Bucket": label,
                "N": band.height,
                "Traders": band["trader"].n_unique(),
                "HR": float(band["correct"].mean()),
                "$/sig": float(band["signal_pnl"].sum()) / band.height,
                "Avg Entry": float(band["dir_entry"].mean()),
            })

    if data:
        tdf = pl.DataFrame(data)
        mo.ui.table(tdf.to_pandas(), label="Performance by trader YES/NO preference")

        fig = px.bar(
            tdf.to_pandas(), x="Bucket", y="HR",
            title="Hit Rate by Trader Direction Preference",
            text_auto=".1%",
        )
        fig.update_layout(yaxis_tickformat=".0%", height=350)
        return fig
    """,
    name="buysell_breakdown"
)


app._unparsable_cell(
    r"""
    if pool_df.height == 0:
        return
    mo.md("---\n## Pool Trader Distributions")
    """,
    name="pool_dist_header"
)


app._unparsable_cell(
    r"""
    if pool_df.height == 0:
        return

    fig = px.histogram(
        pool_df.to_pandas(), x="hr", nbins=40,
        title=f"Pool Trader HR Distribution (n={pool_df.height:,})",
        labels={"hr": "Resolution Hit Rate"},
    )
    fig.update_layout(height=350)
    """,
    name="pool_hr_dist"
)


app._unparsable_cell(
    r"""
    if pool_df.height == 0:
        return

    fig = px.histogram(
        pool_df.to_pandas(), x="median_entry", nbins=40,
        title="Pool Trader Median Entry Price Distribution",
        labels={"median_entry": "Median Directional Entry"},
    )
    fig.update_layout(height=350)
    """,
    name="pool_entry_dist"
)


app._unparsable_cell(
    r"""
    if pool_df.height == 0:
        return

    pdf = pool_df.to_pandas()
    fig = px.scatter(
        pdf, x="yes_frac", y="hr",
        size="n_markets", color="median_entry",
        title="Pool: YES Fraction vs Hit Rate (size=markets, color=entry)",
        labels={"yes_frac": "YES Bet Fraction", "hr": "Resolution HR"},
        hover_data=["n_markets", "median_entry"],
        color_continuous_scale="RdYlGn",
    )
    fig.update_layout(height=450)
    """,
    name="pool_scatter"
)


@app.cell
def efficiency_header(mo):
    mo.md("""
    ---
    ## Market Price Efficiency
    "
           "Does the YES price accurately predict resolution? "
           "Points above the diagonal = market under-prices NO resolution.
    """)
    return


app._unparsable_cell(
    r"""
    if holdout.height < 100:
        return

    # Bin by dir_entry, compute actual resolution HR
    bins = 20
    for label, src in [("Pool", holdout), ("All Traders", baseline)]:
        pass  # will do both in one chart

    data = []
    for lo_i in range(bins):
        lo = lo_i / bins
        hi = (lo_i + 1) / bins

        for label, src in [("Pool", holdout), ("All Traders", baseline)]:
            band = src.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )
            if band.height >= 20:
                data.append({
                    "Implied": (lo + hi) / 2,
                    "Actual HR": float(band["correct"].mean()),
                    "Source": label,
                    "N": band.height,
                })

    if data:
        cdf = pl.DataFrame(data).to_pandas()
        fig = px.scatter(
            cdf, x="Implied", y="Actual HR", color="Source",
            size="N", title="Implied Probability vs Actual Resolution HR",
            labels={"Implied": "Dir Entry (Market Implied)", "Actual HR": "Actual Resolution HR"},
        )
        # Add diagonal (perfect calibration)
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines",
            line=dict(dash="dash", color="gray"), name="Perfect Calibration",
        ))
        fig.update_layout(height=500)
        return fig
    """,
    name="efficiency_chart"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return
    mo.md("---\n## By Market Volume (trader's volume in market)")
    """,
    name="volume_header"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    qs = holdout["market_volume"].quantile(0.25), holdout["market_volume"].quantile(0.50), holdout["market_volume"].quantile(0.75)

    buckets = [
        (0, qs[0], f"Q1 (0-{qs[0]:.0f})"),
        (qs[0], qs[1], f"Q2 ({qs[0]:.0f}-{qs[1]:.0f})"),
        (qs[1], qs[2], f"Q3 ({qs[1]:.0f}-{qs[2]:.0f})"),
        (qs[2], 1e15, f"Q4 ({qs[2]:.0f}+)"),
    ]

    data = []
    for lo, hi, label in buckets:
        band = holdout.filter(
            (pl.col("market_volume") >= lo) & (pl.col("market_volume") < hi)
        )
        if band.height >= 10:
            data.append({
                "Bucket": label,
                "N": band.height,
                "HR": float(band["correct"].mean()),
                "$/sig": float(band["signal_pnl"].sum()) / band.height,
                "Avg Entry": float(band["dir_entry"].mean()),
            })

    if data:
        tdf = pl.DataFrame(data)
        mo.ui.table(tdf.to_pandas(), label="Performance by market volume")
    """,
    name="volume_breakdown"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return
    mo.md("---\n## By Time to Resolution")
    """,
    name="ttr_header"
)


app._unparsable_cell(
    r"""
    if holdout.height == 0:
        return

    h = holdout.with_columns(
        (pl.col("resolved_at") - pl.col("first_trade")).dt.total_hours().alias("hours_to_res"),
    )

    buckets = [
        (0, 1, "<1 hour"),
        (1, 24, "1-24 hours"),
        (24, 168, "1-7 days"),
        (168, 720, "1-4 weeks"),
        (720, 8760, "1-12 months"),
    ]

    data = []
    for lo, hi, label in buckets:
        band = h.filter(
            (pl.col("hours_to_res") >= lo) & (pl.col("hours_to_res") < hi)
        )
        if band.height >= 10:
            data.append({
                "Bucket": label,
                "N": band.height,
                "HR": float(band["correct"].mean()),
                "$/sig": float(band["signal_pnl"].sum()) / band.height,
                "Avg Entry": float(band["dir_entry"].mean()),
            })

    if data:
        tdf = pl.DataFrame(data)
        mo.ui.table(tdf.to_pandas(), label="Performance by time to resolution")
    """,
    name="ttr_breakdown"
)


@app.cell
def raw_pool_header(mo, pool_df):
    mo.md(f"""
    ---\n## Raw Pool Table ({pool_df.height:,} traders)\n"
           "Top 50 by hit rate. Full table is sortable/searchable.
    """)
    return


app._unparsable_cell(
    r"""
    if pool_df.height == 0:
        return

    display = pool_df.select(
        "trader",
        pl.col("hr").round(3).alias("HR"),
        "n_markets",
        pl.col("yes_frac").round(3).alias("YES%"),
        pl.col("median_entry").round(3).alias("Med Entry"),
        "active_months",
        pl.col("avg_trades_per_market").round(1).alias("Avg Trades"),
        pl.col("yes_hr").round(3).alias("YES HR"),
        pl.col("no_hr").round(3).alias("NO HR"),
        "n_yes",
        "n_no",
        pl.col("total_pnl").round(0).alias("Total PnL"),
    ).sort("HR", descending=True).head(50)

    mo.ui.table(display.to_pandas(), label="Top 50 pool traders by HR")
    """,
    name="raw_pool_table"
)


if __name__ == "__main__":
    app.run()
