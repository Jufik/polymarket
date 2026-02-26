import marimo

__generated_with = "0.20.2"
app = marimo.App(
    width="full",
    app_title="S1 Pool Explorer",
)


@app.cell
def imports():
    import marimo as mo
    import polars as pl
    import plotly.express as px
    import plotly.graph_objects as go
    import numpy as np
    import clickhouse_connect
    import time as time_mod
    from datetime import datetime, date

    return clickhouse_connect, date, datetime, go, mo, np, pl, px, time_mod


@app.cell
def connect(clickhouse_connect, mo, time_mod):
    CH_HOST = "192.168.0.148"
    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=18123, database="polymarket"
    )
    # Check for pre-materialized enriched table (fast path)
    use_enriched = False
    try:
        _n = ch.query("SELECT count() FROM _tmp_s1_enriched").first_row[0]
        if _n > 0:
            use_enriched = True
    except Exception:
        _n = 0

    if not use_enriched:
        # Fallback: check raw positions table
        try:
            _n = ch.query("SELECT count() FROM _tmp_s1_positions").first_row[0]
            assert _n > 0
        except Exception:
            mo.md("**No data tables found.** Run `s1_build.py` first.")
            mo.stop(True)

    _src = "_tmp_s1_enriched" if use_enriched else "_tmp_s1_positions (JOIN)"
    mo.md(f"**ClickHouse** connected — reading from `{_src}` ({_n:,} rows)")
    return use_enriched, ch


# ═══════════════════════════════════════════════════════════════════════
#  LOAD — Pull enriched positions into Polars (~10s, once)
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def load_data(use_enriched, ch, mo, pl, time_mod):
    _t0 = time_mod.time()

    if use_enriched:
        # Fast path: read pre-materialized table (no JOIN, only needed columns)
        _arrow = ch.query_arrow("""
        SELECT trader, position, dir_entry, correct,
               market_volume, trade_count, resolved_at, month
        FROM _tmp_s1_enriched
        """)
    else:
        # Fallback: runtime JOIN (slow, ~10s+)
        _arrow = ch.query_arrow("""
        SELECT
            p.trader,
            CASE
                WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
                WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
                WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
                ELSE 'CLOSED'
            END AS position,
            CASE
                WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN p.wavg_yes_price
                WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 1.0 - p.wavg_yes_price
                WHEN p.net_yes >= p.net_no                  THEN p.wavg_yes_price
                ELSE 1.0 - p.wavg_yes_price
            END AS dir_entry,
            CASE
                WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN r.yes_won
                WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN NOT r.yes_won
                WHEN p.net_yes >= p.net_no                  THEN r.yes_won
                ELSE NOT r.yes_won
            END AS correct,
            p.volume AS market_volume,
            toUInt32(p.n_trades) AS trade_count,
            r.resolved_at,
            formatDateTime(r.resolved_at, '%Y-%m') AS month
        FROM _tmp_s1_positions p
        JOIN (
            SELECT m.condition_id, m.resolved_at,
                   coalesce(t.yes_won, false) AS yes_won
            FROM markets m
            LEFT JOIN (
                SELECT condition_id, true AS yes_won
                FROM token_market_map WHERE outcome = 'YES' AND winner = true
            ) t ON m.condition_id = t.condition_id
            WHERE m.resolution_value = 1
        ) r ON p.condition_id = r.condition_id
        WHERE NOT (p.net_yes <= 0.01 AND p.net_no <= 0.01)
        """)

    df = pl.from_arrow(_arrow)

    if "resolved_at" in df.columns:
        _dtype = df.schema["resolved_at"]
        if isinstance(_dtype, pl.Datetime) and _dtype.time_zone:
            df = df.with_columns(pl.col("resolved_at").dt.replace_time_zone(None))
        elif _dtype in (pl.UInt32, pl.Int64, pl.UInt64):
            df = df.with_columns(
                pl.from_epoch(pl.col("resolved_at"), time_unit="s").alias("resolved_at")
            )
    if df.schema.get("correct") not in (pl.Boolean,):
        df = df.with_columns(pl.col("correct").cast(pl.Boolean))

    _elapsed = time_mod.time() - _t0
    _pos = df["position"].value_counts().sort("position")
    _min_d = df["resolved_at"].min()
    _max_d = df["resolved_at"].max()

    _src = "enriched" if use_enriched else "positions+JOIN"
    mo.md(f"""
    ## Data: {df.height:,} positions in {_elapsed:.1f}s ({_src})
    {df['trader'].n_unique():,} traders
    — resolved **{_min_d:%Y-%m-%d}** to **{_max_d:%Y-%m-%d}**

    | Position | Count | % | HR |
    |---|---|---|---|
    """ + "\n".join(
        f"| {r['position']} | {r['count']:,} | "
        f"{r['count']/df.height:.1%} | "
        f"{float(df.filter(pl.col('position') == r['position'])['correct'].mean()):.1%} |"
        for r in _pos.iter_rows(named=True)
    ))
    return (df,)


# ═══════════════════════════════════════════════════════════════════════
#  CLASSIFICATION — Annotated charts with threshold cut lines
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def classification_header(mo):
    mo.md("""
    ## Trader Classification
    Drag sliders — cut lines on charts update live.
    """)
    return


@app.cell
def classification_controls(mo):
    max_entry = mo.ui.slider(
        start=0.70, stop=1.0, step=0.01, value=0.90,
        label="Max entry price (safe-bet cutoff)",
    )
    max_positions = mo.ui.slider(
        start=100, stop=10000, step=100, value=5000,
        label="Max positions (BOT cutoff)",
    )
    min_positions = mo.ui.slider(
        start=1, stop=50, step=1, value=3,
        label="Min positions (ONE_OFF cutoff)",
    )
    min_trades_per_pos = mo.ui.slider(
        start=1, stop=20, step=1, value=1,
        label="Min trades per position (noise filter)",
    )
    min_market_volume = mo.ui.slider(
        start=0, stop=10000, step=100, value=0,
        label="Min market volume USD (thin market cutoff)",
    )
    mo.vstack([max_entry, max_positions, min_positions, min_trades_per_pos, min_market_volume])
    return max_entry, max_positions, min_market_volume, min_positions, min_trades_per_pos


@app.cell
def classification_charts(df, go, max_entry, max_positions, min_positions, mo, np, pl):
    """Annotated distribution charts with threshold cut lines."""
    _directional = df.filter(pl.col("position").is_in(["YES", "NO"]))

    _profiles = (
        _directional.group_by("trader").agg(
            pl.len().alias("total_positions"),
        )
    )

    # ---- Chart 1: Positions per trader (log) with BOT + ONE_OFF lines ----
    _tp = _profiles["total_positions"].to_numpy()
    _bins = np.logspace(0, np.log10(max(_tp.max(), 1)), 80)
    _c, _e = np.histogram(_tp, bins=_bins)
    _mids = [(_e[i] + _e[i+1]) / 2 for i in range(len(_c))]

    _n_bots = int((_tp > max_positions.value).sum())
    _n_oneoff = int((_tp < min_positions.value).sum())

    fig_trades = go.Figure()
    fig_trades.add_trace(go.Bar(
        x=_mids, y=_c.tolist(), marker_color="steelblue",
        width=[_e[i+1] - _e[i] for i in range(len(_c))],
        name="Traders",
    ))
    # BOT cutoff line
    fig_trades.add_vline(
        x=max_positions.value, line_dash="dash", line_color="red", line_width=2,
        annotation_text=f"BOT > {max_positions.value:,} ({_n_bots:,})",
        annotation_position="top right",
        annotation_font_color="red",
    )
    # ONE_OFF cutoff line
    fig_trades.add_vline(
        x=min_positions.value, line_dash="dash", line_color="orange", line_width=2,
        annotation_text=f"ONE_OFF < {min_positions.value} ({_n_oneoff:,})",
        annotation_position="top left",
        annotation_font_color="orange",
    )
    fig_trades.update_layout(
        title="Positions per Trader",
        xaxis_title="Positions", yaxis_title="Traders",
        xaxis_type="log", height=350, template="plotly_white",
        showlegend=False,
    )

    # ---- Chart 2: Entry price distribution with SAFE-BET line ----
    _ep = _directional["dir_entry"].to_numpy()
    _c2, _e2 = np.histogram(_ep, bins=100, range=(0, 1))
    _mids2 = [(_e2[i] + _e2[i+1]) / 2 for i in range(len(_c2))]

    _n_safe = int((_ep > max_entry.value).sum())
    _pct_safe = _n_safe / len(_ep)

    fig_entry = go.Figure()
    fig_entry.add_trace(go.Bar(x=_mids2, y=_c2.tolist(), marker_color="coral", name="Positions"))
    # SAFE-BET cutoff line
    fig_entry.add_vline(
        x=max_entry.value, line_dash="dash", line_color="red", line_width=2,
        annotation_text=f"SAFE-BET > {max_entry.value:.2f} ({_n_safe:,} = {_pct_safe:.1%})",
        annotation_position="top left",
        annotation_font_color="red",
    )
    fig_entry.update_layout(
        title="Entry Price Distribution",
        xaxis_title="dir_entry", yaxis_title="Positions",
        height=350, template="plotly_white",
        showlegend=False,
    )

    mo.hstack([fig_trades, fig_entry])
    return


@app.cell
def classify(df, max_entry, max_positions, min_market_volume, min_positions, min_trades_per_pos, mo, pl):
    _s0 = df.filter(pl.col("position").is_in(["YES", "NO"]))
    _n0 = _s0.height

    # Stage 1: entry price filter
    _s1 = _s0.filter(pl.col("dir_entry") <= max_entry.value)
    _rm_safe = _n0 - _s1.height

    # Stage 1b: thin market filter
    _s1b = _s1.filter(pl.col("market_volume") >= min_market_volume.value)
    _rm_vol = _s1.height - _s1b.height

    # Stage 1c: min trades per position
    _s1c = _s1b.filter(pl.col("trade_count") >= min_trades_per_pos.value)
    _rm_trades = _s1b.height - _s1c.height

    # Stage 2: trader-level filters
    _counts = _s1c.group_by("trader").agg(pl.len().alias("n"))
    _bots = set(_counts.filter(pl.col("n") > max_positions.value)["trader"].to_list())
    _oneoffs = set(_counts.filter(pl.col("n") < min_positions.value)["trader"].to_list())

    _s2 = _s1c.filter(~pl.col("trader").is_in(list(_bots)))
    _rm_bot = _s1c.height - _s2.height
    clean_df = _s2.filter(~pl.col("trader").is_in(list(_oneoffs)))
    _rm_oneoff = _s2.height - clean_df.height

    mo.md(f"""
    | Step | Positions | Removed |
    |------|-----------|---------|
    | Directional | {_n0:,} | — |
    | Entry <= {max_entry.value:.2f} | {_s1.height:,} | -{_rm_safe:,} safe-bets |
    | Volume >= ${min_market_volume.value:,} | {_s1b.height:,} | -{_rm_vol:,} thin markets |
    | Trades >= {min_trades_per_pos.value} | {_s1c.height:,} | -{_rm_trades:,} low-trade |
    | <= {max_positions.value:,} pos | {_s2.height:,} | -{_rm_bot:,} bot ({len(_bots):,} traders) |
    | >= {min_positions.value} pos | {clean_df.height:,} | -{_rm_oneoff:,} one-off ({len(_oneoffs):,} traders) |

    **{clean_df.height:,}** positions from **{clean_df['trader'].n_unique():,}** qualified traders
    """)
    return (clean_df,)


# ═══════════════════════════════════════════════════════════════════════
#  POOL SELECTION — Every parameter exposed
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_header(mo):
    mo.md("---\n## Pool Selection\nAll controls update simulation instantly.")
    return


@app.cell
def pool_controls(date, mo):
    train_start_ui = mo.ui.date(value=date(2024, 1, 1), label="Lookback FROM")
    train_end_ui = mo.ui.date(value=date(2025, 11, 1), label="Lookback TO (= simulation start)")
    holdout_end_ui = mo.ui.date(value=date(2026, 3, 1), label="Simulation end")
    min_hr = mo.ui.slider(start=0.40, stop=0.85, step=0.01, value=0.55, label="Min hit rate (pool gate)")
    min_markets = mo.ui.slider(start=5, stop=200, step=5, value=30, label="Min markets traded")
    min_months = mo.ui.slider(start=1, stop=18, step=1, value=3, label="Min good months")
    monthly_hr = mo.ui.slider(start=0.40, stop=0.70, step=0.05, value=0.50, label="Monthly min HR")
    monthly_min_bets = mo.ui.slider(start=1, stop=20, step=1, value=3, label="Min bets per month (for good month)")
    max_yes_frac = mo.ui.slider(start=0.0, stop=1.0, step=0.05, value=1.0, label="Max YES fraction (1.0 = no filter)")
    min_median_entry = mo.ui.slider(start=0.0, stop=0.80, step=0.05, value=0.0, label="Min median entry")
    max_median_entry = mo.ui.slider(start=0.20, stop=1.0, step=0.05, value=1.0, label="Max median entry")
    mo.vstack([
        mo.hstack([train_start_ui, train_end_ui, holdout_end_ui]),
        mo.hstack([min_hr, min_markets, min_months]),
        mo.hstack([monthly_hr, monthly_min_bets]),
        mo.hstack([max_yes_frac, min_median_entry, max_median_entry]),
    ])
    return (
        holdout_end_ui, max_median_entry, max_yes_frac, min_hr, min_markets,
        min_median_entry, min_months, monthly_hr, monthly_min_bets,
        train_end_ui, train_start_ui,
    )


@app.cell
def signal_controls(mo):
    mo.md("### Signal Filters (applied to holdout)")
    entry_lo = mo.ui.slider(start=0.0, stop=0.90, step=0.05, value=0.0, label="Min entry")
    entry_hi = mo.ui.slider(start=0.10, stop=1.0, step=0.05, value=1.0, label="Max entry")
    direction = mo.ui.dropdown(
        options={"ALL": "ALL", "NO only": "NO", "YES only": "YES"},
        value="ALL", label="Direction",
    )
    fee_rate = mo.ui.slider(start=0.0, stop=0.05, step=0.005, value=0.02, label="Fee rate")
    bet_size = mo.ui.slider(start=10, stop=500, step=10, value=100, label="Bet size ($)")
    mo.vstack([
        mo.hstack([entry_lo, entry_hi, direction]),
        mo.hstack([fee_rate, bet_size]),
    ])
    return bet_size, direction, entry_hi, entry_lo, fee_rate


@app.cell
def compute_bounds(datetime, holdout_end_ui, train_end_ui, train_start_ui):
    train_start = datetime.combine(train_start_ui.value, datetime.min.time())
    train_end = datetime.combine(train_end_ui.value, datetime.min.time())
    holdout_start = train_end
    holdout_end = datetime.combine(holdout_end_ui.value, datetime.min.time())
    return holdout_end, holdout_start, train_end, train_start


@app.cell
def build_pool(
    clean_df, max_median_entry, max_yes_frac, min_hr, min_markets,
    min_median_entry, min_months, mo, monthly_hr, monthly_min_bets,
    pl, train_end, train_start,
):
    _train = clean_df.filter(
        (pl.col("resolved_at") >= train_start) & (pl.col("resolved_at") < train_end)
    )

    _stats = _train.group_by("trader").agg(
        pl.col("correct").mean().alias("hr"),
        pl.len().alias("n_markets"),
        (pl.col("position") == "YES").mean().alias("yes_frac"),
        pl.col("dir_entry").median().alias("median_entry"),
        pl.col("dir_entry").mean().alias("mean_entry"),
        pl.col("month").n_unique().alias("active_months"),
        pl.col("trade_count").mean().alias("avg_trades"),
        pl.col("market_volume").median().alias("med_volume"),
        pl.col("correct").filter(pl.col("position") == "YES").mean().alias("yes_hr"),
        pl.col("correct").filter(pl.col("position") == "NO").mean().alias("no_hr"),
        (pl.col("position") == "YES").sum().alias("n_yes"),
        (pl.col("position") == "NO").sum().alias("n_no"),
    )

    _monthly = (
        _train.group_by(["trader", "month"]).agg(
            pl.col("correct").mean().alias("m_hr"), pl.len().alias("m_n"),
        ).filter(pl.col("m_n") >= monthly_min_bets.value)
    )
    _good = (
        _monthly.filter(pl.col("m_hr") >= monthly_hr.value)
        .group_by("trader").agg(pl.len().alias("good_months"))
    )
    _stats = _stats.join(_good, on="trader", how="left").with_columns(
        pl.col("good_months").fill_null(0)
    )

    pool_df = _stats.filter(
        (pl.col("hr") >= min_hr.value)
        & (pl.col("n_markets") >= min_markets.value)
        & (pl.col("good_months") >= min_months.value)
        & (pl.col("yes_frac") <= max_yes_frac.value)
        & (pl.col("median_entry") >= min_median_entry.value)
        & (pl.col("median_entry") <= max_median_entry.value)
    )
    pool_set = frozenset(pool_df["trader"].to_list())

    # Compute lookback window duration
    _days = (train_end - train_start).days
    _months_approx = _days / 30.44

    mo.md(f"""
    **Lookback window:** {train_start:%Y-%m-%d} → {train_end:%Y-%m-%d}
    ({_months_approx:.0f} months, {_days:,} days)
    — {_train.height:,} positions from {_train['trader'].n_unique():,} traders

    **Pool:** {pool_df.height:,} traders passing all gates:
    HR >= {min_hr.value:.0%} |
    >= {min_markets.value} mkts |
    >= {min_months.value} good months ({monthly_min_bets.value}+ bets, HR >= {monthly_hr.value:.0%}) |
    YES% <= {max_yes_frac.value:.0%} |
    entry [{min_median_entry.value:.2f}, {max_median_entry.value:.2f}]
    """)
    return pool_df, pool_set


# ═══════════════════════════════════════════════════════════════════════
#  POOL DISTRIBUTION — Annotated with thresholds
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_hr_chart(go, min_hr, mo, np, pool_df):
    mo.stop(pool_df.height == 0)
    _hr = pool_df["hr"].to_numpy()
    _c, _e = np.histogram(_hr, bins=40, range=(0.4, 1.0))
    _mids = [(_e[i] + _e[i+1]) / 2 for i in range(len(_c))]

    _fig = go.Figure()
    _fig.add_trace(go.Bar(x=_mids, y=_c.tolist(), marker_color="seagreen"))
    _fig.add_vline(
        x=min_hr.value, line_dash="dash", line_color="red", line_width=2,
        annotation_text=f"Pool gate: HR >= {min_hr.value:.0%}",
        annotation_position="top right", annotation_font_color="red",
    )
    _fig.update_layout(
        title=f"Pool HR Distribution (n={pool_df.height:,})",
        xaxis_title="HR", yaxis_title="Traders",
        height=300, template="plotly_white",
    )
    return


@app.cell
def pool_scatter(go, max_yes_frac, min_hr, mo, pool_df, px):
    mo.stop(pool_df.height == 0)
    _fig = px.scatter(
        pool_df.to_pandas(),
        x="yes_frac", y="hr", size="n_markets", color="median_entry",
        title="YES% vs HR (size=markets, color=entry)",
        color_continuous_scale="RdYlGn",
    )
    # HR threshold line
    _fig.add_hline(
        y=min_hr.value, line_dash="dash", line_color="red", line_width=1,
        annotation_text=f"HR = {min_hr.value:.0%}",
        annotation_position="bottom right", annotation_font_color="red",
    )
    # YES frac threshold line
    if max_yes_frac.value < 1.0:
        _fig.add_vline(
            x=max_yes_frac.value, line_dash="dash", line_color="blue", line_width=1,
            annotation_text=f"YES% = {max_yes_frac.value:.0%}",
            annotation_position="top left", annotation_font_color="blue",
        )
    _fig.update_layout(height=450)
    return


# ═══════════════════════════════════════════════════════════════════════
#  HOLDOUT SIMULATION
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def compute_holdout(bet_size, clean_df, direction, entry_hi, entry_lo, fee_rate, holdout_end, holdout_start, pl, pool_set):
    _fee = fee_rate.value
    _bet = bet_size.value
    holdout = clean_df.filter(
        (pl.col("resolved_at") >= holdout_start)
        & (pl.col("resolved_at") < holdout_end)
        & pl.col("trader").is_in(list(pool_set))
    )
    if direction.value != "ALL":
        holdout = holdout.filter(pl.col("position") == direction.value)
    holdout = holdout.filter(
        (pl.col("dir_entry") >= entry_lo.value) & (pl.col("dir_entry") <= entry_hi.value)
    )
    holdout = holdout.with_columns(
        pl.when(pl.col("correct"))
        .then(pl.lit(_bet) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry") - pl.lit(_bet) * _fee)
        .otherwise(-pl.lit(_bet) - pl.lit(_bet) * _fee)
        .alias("pnl")
    )
    return (holdout,)


@app.cell
def compute_baseline(bet_size, clean_df, direction, entry_hi, entry_lo, fee_rate, holdout_end, holdout_start, pl):
    _fee = fee_rate.value
    _bet = bet_size.value
    baseline = clean_df.filter(
        (pl.col("resolved_at") >= holdout_start) & (pl.col("resolved_at") < holdout_end)
    )
    if direction.value != "ALL":
        baseline = baseline.filter(pl.col("position") == direction.value)
    baseline = baseline.filter(
        (pl.col("dir_entry") >= entry_lo.value) & (pl.col("dir_entry") <= entry_hi.value)
    )
    baseline = baseline.with_columns(
        pl.when(pl.col("correct"))
        .then(pl.lit(_bet) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry") - pl.lit(_bet) * _fee)
        .otherwise(-pl.lit(_bet) - pl.lit(_bet) * _fee)
        .alias("pnl")
    )
    return (baseline,)


@app.cell
def show_kpis(baseline, bet_size, fee_rate, holdout, holdout_end, holdout_start, mo, pool_set):
    mo.stop(holdout.height == 0, mo.md("**No signals match current filters.**"))

    _p_hr = float(holdout["correct"].mean())
    _p_pnl = float(holdout["pnl"].sum())
    _p_per = _p_pnl / holdout.height
    _p_entry = float(holdout["dir_entry"].mean())

    _b_hr = float(baseline["correct"].mean()) if baseline.height > 0 else 0
    _b_per = float(baseline["pnl"].sum()) / baseline.height if baseline.height > 0 else 0

    mo.md(f"""
    ---
    ## Simulation: {holdout_start:%Y-%m-%d} → {holdout_end:%Y-%m-%d}
    Fee: {fee_rate.value:.1%} | Bet: ${bet_size.value}

    | Metric | Pool | All Traders | Market Implied |
    |--------|------|-------------|----------------|
    | **Hit Rate** | **{_p_hr:.1%}** | {_b_hr:.1%} | {_p_entry:.1%} |
    | **$/signal** | **${_p_per:+.1f}** | ${_b_per:+.1f} | — |
    | **Edge vs All** | **{_p_hr - _b_hr:+.1%}** | — | — |
    | **Edge vs Market** | **{_p_hr - _p_entry:+.1%}** | — | — |

    | | |
    |---|---|
    | Pool size | {len(pool_set):,} traders |
    | Signals | {holdout.height:,} |
    | Total PnL | ${_p_pnl:+,.0f} |
    | Avg entry | {_p_entry:.3f} |
    """)
    return


# ═══════════════════════════════════════════════════════════════════════
#  CHARTS
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def entry_band_table(baseline, holdout, mo, pl):
    mo.stop(holdout.height == 0)
    _bands = [
        (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
        (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0),
    ]
    _rows = []
    for _lo, _hi in _bands:
        _pb = holdout.filter((pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi))
        _bb = baseline.filter((pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi))
        if _pb.height < 5:
            continue
        _p_hr = float(_pb["correct"].mean())
        _b_hr = float(_bb["correct"].mean()) if _bb.height > 0 else 0
        _rows.append({
            "Band": f"{_lo:.0%}-{_hi:.0%}",
            "Pool N": _pb.height,
            "Pool HR": f"{_p_hr:.1%}",
            "Pool $/sig": f"${float(_pb['pnl'].sum()) / _pb.height:+.1f}",
            "All HR": f"{_b_hr:.1%}",
            "Edge": f"{_p_hr - _b_hr:+.1%}",
        })
    if _rows:
        mo.ui.table(pl.DataFrame(_rows).to_pandas(), label="Entry bands")
    return


@app.cell
def entry_band_chart(baseline, entry_hi, entry_lo, go, holdout, mo, pl, px):
    mo.stop(holdout.height < 10)
    _bands = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    _data = []
    for _lo, _hi in _bands:
        _label = f"{_lo:.0%}-{_hi:.0%}"
        for _src, _sdf in [("Pool", holdout), ("All", baseline)]:
            _b = _sdf.filter((pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi))
            if _b.height >= 10:
                _data.append({"Band": _label, "Source": _src, "HR": float(_b["correct"].mean())})
        _data.append({"Band": _label, "Source": "Market", "HR": (_lo + _hi) / 2})
    if _data:
        _fig = px.bar(
            pl.DataFrame(_data).to_pandas(), x="Band", y="HR", color="Source",
            barmode="group", title="HR by Entry Band", text_auto=".1%",
        )
        _fig.update_layout(yaxis_tickformat=".0%", height=400)
    return


@app.cell
def weekly_table(bet_size, holdout, mo, pl):
    mo.stop(holdout.height == 0)
    _bet = bet_size.value
    _w = (
        holdout
        .with_columns(
            pl.col("resolved_at").dt.truncate("1w").alias("week"),
        )
        .group_by("week").agg(
            pl.len().alias("Trades"),
            pl.col("correct").mean().alias("HR"),
            pl.col("pnl").sum().alias("PnL"),
            pl.col("dir_entry").mean().alias("Avg Entry"),
            (pl.col("position") == "YES").mean().alias("YES%"),
        ).sort("week")
        .with_columns(
            pl.col("week").dt.strftime("%Y-%m-%d").alias("Week"),
            (pl.col("PnL") / (pl.col("Trades") * pl.lit(_bet)) * 100).alias("ROI%"),
        )
        .select("Week", "Trades", "HR", "ROI%", "PnL", "Avg Entry", "YES%")
        .with_columns(
            pl.col("HR").round(3),
            pl.col("ROI%").round(1),
            pl.col("PnL").round(1),
            pl.col("Avg Entry").round(3),
            pl.col("YES%").round(3),
        )
    )
    mo.ui.table(_w.to_pandas(), label="Weekly performance", page_size=20)
    return


@app.cell
def weekly_chart(bet_size, go, holdout, mo, pl, px):
    mo.stop(holdout.height == 0)
    _bet = bet_size.value
    _w = (
        holdout
        .with_columns(pl.col("resolved_at").dt.truncate("1w").alias("week"))
        .group_by("week").agg(
            pl.len().alias("Trades"),
            pl.col("correct").mean().alias("HR"),
            pl.col("pnl").sum().alias("PnL"),
        ).sort("week")
        .with_columns(
            (pl.col("PnL") / (pl.col("Trades") * pl.lit(_bet)) * 100).alias("ROI%"),
        )
        .to_pandas()
    )

    fig = go.Figure()
    # ROI% bars
    fig.add_trace(go.Bar(
        x=_w["week"], y=_w["ROI%"], name="ROI%",
        marker_color=[
            "rgb(34,139,34)" if v >= 0 else "rgb(220,50,50)" for v in _w["ROI%"]
        ],
        yaxis="y",
    ))
    # HR line
    fig.add_trace(go.Scatter(
        x=_w["week"], y=_w["HR"] * 100, name="HR%",
        mode="lines+markers", line=dict(color="steelblue", width=2),
        yaxis="y2",
    ))
    # Trades scatter (size)
    _max_t = _w["Trades"].max() or 1
    fig.add_trace(go.Scatter(
        x=_w["week"], y=_w["ROI%"], name="Trades",
        mode="markers",
        marker=dict(
            size=(_w["Trades"] / _max_t * 20 + 4).tolist(),
            color="rgba(100,100,100,0.3)", line=dict(width=1, color="gray"),
        ),
        yaxis="y",
        hovertemplate="Week: %{x}<br>Trades: %{text}<extra></extra>",
        text=_w["Trades"].tolist(),
    ))
    fig.update_layout(
        title="Weekly: ROI% (bars) + HR% (line) + Trades (bubble size)",
        yaxis=dict(title="ROI%", zeroline=True, zerolinecolor="gray"),
        yaxis2=dict(title="HR%", overlaying="y", side="right", range=[0, 100]),
        height=400, template="plotly_white",
        legend=dict(orientation="h", y=1.12),
    )
    return


@app.cell
def equity_curve(bet_size, holdout, mo, pl, px):
    mo.stop(holdout.height == 0)
    _bet = bet_size.value
    _w = (
        holdout
        .with_columns(pl.col("resolved_at").dt.truncate("1w").alias("week"))
        .group_by("week").agg(
            pl.col("pnl").sum().alias("PnL"),
            pl.len().alias("Trades"),
        ).sort("week")
        .with_columns(
            pl.col("PnL").cum_sum().alias("Cumulative"),
            (pl.col("PnL") / (pl.col("Trades") * pl.lit(_bet)) * 100).alias("ROI%"),
        )
        .to_pandas()
    )
    _fig = px.line(
        _w, x="week", y="Cumulative", title="Cumulative PnL (weekly)",
        markers=True, hover_data=["Trades", "ROI%"],
    )
    _fig.update_layout(height=350)
    return


@app.cell
def direction_breakdown(holdout, mo, pl, pool_df):
    mo.stop(holdout.height == 0 or pool_df.height == 0)
    _buckets = [
        (0.00, 0.10, "Heavy NO"), (0.10, 0.30, "NO-lean"),
        (0.30, 0.50, "Slight NO"), (0.50, 0.70, "Slight YES"),
        (0.70, 0.90, "YES-lean"), (0.90, 1.01, "Heavy YES"),
    ]
    _hw = holdout.join(pool_df.select("trader", "yes_frac"), on="trader", how="left", suffix="_p")
    _data = []
    for _lo, _hi, _label in _buckets:
        _b = _hw.filter((pl.col("yes_frac") >= _lo) & (pl.col("yes_frac") < _hi))
        if _b.height >= 10:
            _data.append({
                "Bucket": _label, "N": _b.height,
                "Traders": _b["trader"].n_unique(),
                "HR": float(_b["correct"].mean()),
                "$/sig": float(_b["pnl"].sum()) / _b.height,
            })
    if _data:
        mo.ui.table(pl.DataFrame(_data).to_pandas(), label="By direction preference")
    return


@app.cell
def calibration_chart(baseline, go, holdout, mo, pl, px):
    mo.stop(holdout.height < 100)
    _data = []
    for _i in range(20):
        _lo, _hi = _i / 20, (_i + 1) / 20
        for _label, _src in [("Pool", holdout), ("All", baseline)]:
            _b = _src.filter((pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi))
            if _b.height >= 20:
                _data.append({
                    "Implied": (_lo + _hi) / 2,
                    "Actual": float(_b["correct"].mean()),
                    "Source": _label, "N": _b.height,
                })
    if _data:
        _fig = px.scatter(
            pl.DataFrame(_data).to_pandas(),
            x="Implied", y="Actual", color="Source", size="N",
            title="Calibration: Implied vs Actual HR",
        )
        _fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines",
            line=dict(dash="dash", color="gray"), name="Perfect",
        ))
        _fig.update_layout(height=450)
    return


# ═══════════════════════════════════════════════════════════════════════
#  POOL TABLE — All traders, paginated
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_table(mo, pl, pool_df):
    mo.stop(pool_df.height == 0)
    _display = (
        pool_df.select(
            "trader",
            pl.col("hr").round(3).alias("HR"),
            "n_markets",
            pl.col("yes_frac").round(3).alias("YES%"),
            pl.col("median_entry").round(3).alias("Med Entry"),
            pl.col("med_volume").round(0).alias("Med Vol"),
            "good_months",
            pl.col("yes_hr").round(3).alias("YES HR"),
            pl.col("no_hr").round(3).alias("NO HR"),
            "n_yes", "n_no",
        ).sort("HR", descending=True)
    )
    mo.vstack([
        mo.md(f"---\n## Pool Table — all {pool_df.height:,} traders"),
        mo.ui.table(
            _display.to_pandas(),
            label=f"Pool ({pool_df.height:,})",
            page_size=25,
            selection=None,
        ),
    ])
    return


# ═══════════════════════════════════════════════════════════════════════
#  EXPORT CONFIG — Copy-paste to share or reload
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def export_config(
    baseline, bet_size, direction, entry_hi, entry_lo, fee_rate,
    holdout, holdout_end, holdout_start, max_entry, max_median_entry,
    max_positions, max_yes_frac, min_hr, min_market_volume,
    min_markets, min_median_entry, min_months, min_positions,
    min_trades_per_pos, mo, monthly_hr, monthly_min_bets,
    pool_df, pool_set, train_end, train_start,
):
    import json as _json

    _config = {
        "classification": {
            "max_entry": max_entry.value,
            "max_positions": max_positions.value,
            "min_positions": min_positions.value,
            "min_trades_per_pos": min_trades_per_pos.value,
            "min_market_volume": min_market_volume.value,
        },
        "pool": {
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "holdout_end": holdout_end.strftime("%Y-%m-%d"),
            "min_hr": min_hr.value,
            "min_markets": min_markets.value,
            "min_months": min_months.value,
            "monthly_hr": monthly_hr.value,
            "monthly_min_bets": monthly_min_bets.value,
            "max_yes_frac": max_yes_frac.value,
            "min_median_entry": min_median_entry.value,
            "max_median_entry": max_median_entry.value,
        },
        "signal": {
            "entry_lo": entry_lo.value,
            "entry_hi": entry_hi.value,
            "direction": direction.value,
            "fee_rate": fee_rate.value,
            "bet_size": bet_size.value,
        },
        "results": {
            "pool_size": pool_df.height,
            "holdout_signals": holdout.height,
            "holdout_hr": round(float(holdout["correct"].mean()), 4) if holdout.height > 0 else None,
            "holdout_roi_pct": round(
                float(holdout["pnl"].sum()) / (holdout.height * bet_size.value) * 100, 2
            ) if holdout.height > 0 else None,
            "holdout_pnl": round(float(holdout["pnl"].sum()), 1) if holdout.height > 0 else None,
            "baseline_hr": round(float(baseline["correct"].mean()), 4) if baseline.height > 0 else None,
            "baseline_signals": baseline.height,
        },
    }

    _json_str = _json.dumps(_config, indent=2)
    mo.vstack([
        mo.md("---\n## Export Config\nCopy the JSON below to share or reload later."),
        mo.ui.code_editor(value=_json_str, language="json"),
    ])
    return


if __name__ == "__main__":
    app.run()
