import marimo

__generated_with = "0.20.2"
app = marimo.App(width="full", app_title="S1 Pool Explorer — E2E from Raw Trades")


# ── Imports ──────────────────────────────────────────────────────────
@app.cell
def imports():
    import marimo as mo
    import polars as pl
    import plotly.express as px
    import plotly.graph_objects as go
    import clickhouse_connect
    import time as time_mod
    from datetime import datetime

    return clickhouse_connect, datetime, go, mo, pl, px, time_mod


# ── CH Connection ────────────────────────────────────────────────────
@app.cell
def connect(clickhouse_connect, mo):
    CH_HOST = "192.168.0.148"
    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=18123, database="polymarket"
    )
    ver = ch.query("SELECT version()").first_row[0]
    mo.md(f"Connected to ClickHouse **{ver}** at `{CH_HOST}`")
    return (ch,)


# ═══════════════════════════════════════════════════════════════════════
#  STEP 0 — Source data overview
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def step0_raw(ch, mo):
    _r = ch.query("""
        SELECT
            count()                AS trades,
            uniq(condition_id)     AS markets,
            uniq(maker)            AS makers,
            min(timestamp)         AS t_min,
            max(timestamp)         AS t_max
        FROM trades_raw
    """).first_row
    mo.md(f"""
## Step 0 — Raw Trade Data

| | |
|---|---|
| **Trades** | {_r[0]:,} |
| **Markets** | {_r[1]:,} |
| **Makers** | {_r[2]:,} |
| **Period** | `{_r[3]}` → `{_r[4]}` |
""")


# ═══════════════════════════════════════════════════════════════════════
#  STEP 1 — Resolution ground truth (pure SQL from PG via CH engine)
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def step1_resolution(ch, mo):
    """
    Resolution = which outcome won.

    - markets table  → resolution_value = 1  (resolved only)
    - token_market_map → outcome='YES' AND winner=true  ⟹  YES won
    - LEFT JOIN gives yes_won per condition_id

    Both tables are PostgreSQL engine tables reading live from PG.
    """

    RESOLUTION_SQL = """
    SELECT
        m.condition_id,
        m.resolved_at,
        m.winner_outcome,
        -- YES won if the YES-side token is the winner
        coalesce(t.yes_won, false)  AS yes_won
    FROM markets m
    LEFT JOIN (
        SELECT condition_id, true AS yes_won
        FROM token_market_map
        WHERE outcome = 'YES' AND winner = true
    ) t ON m.condition_id = t.condition_id
    WHERE m.resolution_value = 1
    """

    _stats = ch.query(f"""
        SELECT
            count()            AS resolved,
            countIf(yes_won)   AS n_yes,
            count() - countIf(yes_won) AS n_no
        FROM ({RESOLUTION_SQL})
    """).first_row

    mo.md(f"""
## Step 1 — Resolution Ground Truth

```sql
{RESOLUTION_SQL.strip()}
```

| | |
|---|---|
| Resolved markets | **{_stats[0]:,}** |
| YES won | {_stats[1]:,} ({_stats[1]/_stats[0]:.1%}) |
| NO won | {_stats[2]:,} ({_stats[2]/_stats[0]:.1%}) |
""")
    return (RESOLUTION_SQL,)


# ═══════════════════════════════════════════════════════════════════════
#  STEP 2 — Aggregate 385M trades → trader × market positions
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def step2_agg_sql(mo):
    """
    The core aggregation logic.

    For each trade:
      - YES-side price = price if YES token, 1-price if NO token
      - Maker BUY  → +yes_tokens;  Maker SELL → -yes_tokens  (if YES token)
      - Taker is opposite side of maker
      - Volume-weighted entry = Σ(yes_price × amount) / Σ(amount)

    We UNION maker + taker rows, then GROUP BY (trader, condition_id).
    """

    AGG_SQL = """
CREATE TABLE IF NOT EXISTS _tmp_s1_positions
ENGINE = MergeTree()
ORDER BY (trader, condition_id)
AS
WITH
-- ① Map each asset to its market + YES/NO side
token_info AS (
    SELECT asset_id, condition_id,
           outcome = 'YES' AS is_yes
    FROM token_market_map
),

-- ② Only resolved markets
resolved_cids AS (
    SELECT condition_id
    FROM markets
    WHERE resolution_value = 1
),

-- ③ Enrich every trade with YES-side price
enriched AS (
    SELECT
        t.condition_id, t.side, t.price, t.size,
        t.amount_usd, t.fee_usd,
        t.maker, t.taker, t.timestamp,
        ti.is_yes,
        if(ti.is_yes, t.price, 1.0 - t.price)  AS yes_price
    FROM trades_raw t
    JOIN token_info ti ON t.asset_id = ti.asset_id
    WHERE t.condition_id IN (SELECT condition_id FROM resolved_cids)
),

-- ④ Maker positions
maker AS (
    SELECT
        assumeNotNull(maker)               AS trader,
        condition_id, timestamp, amount_usd, fee_usd,
        yes_price * amount_usd             AS yes_px_vol,
        -- YES tokens gained by maker
        if(is_yes,
           if(side = 'BUY', size, -size),
           0)                              AS yes_tok
    FROM enriched
    WHERE maker IS NOT NULL AND maker != ''
),

-- ⑤ Taker positions (opposite side)
taker AS (
    SELECT
        assumeNotNull(taker)               AS trader,
        condition_id, timestamp, amount_usd, fee_usd,
        yes_price * amount_usd             AS yes_px_vol,
        if(is_yes,
           if(side = 'BUY', -size, size),
           0)                              AS yes_tok
    FROM enriched
    WHERE taker IS NOT NULL AND taker != ''
),

all_pos AS (
    SELECT * FROM maker
    UNION ALL
    SELECT * FROM taker
)

-- ⑥ Aggregate to one row per (trader, market)
SELECT
    trader,
    condition_id,
    sum(yes_tok)                                      AS net_yes_tokens,
    sum(yes_px_vol) / nullIf(sum(amount_usd), 0)     AS wavg_yes_entry_price,
    sum(amount_usd)                                   AS market_volume,
    toUInt32(count())                                 AS trade_count,
    min(timestamp)                                    AS first_trade,
    max(timestamp)                                    AS last_trade
FROM all_pos
GROUP BY trader, condition_id
"""

    mo.md(f"""
## Step 2 — Aggregate Trades → Positions

```sql
{AGG_SQL.strip()}
```
""")
    return (AGG_SQL,)


@app.cell
def step2_run(AGG_SQL, ch, mo, time_mod):
    """Execute the aggregation (skips if table already populated)."""
    _exists = False
    _n = 0
    try:
        _n = ch.query("SELECT count() FROM _tmp_s1_positions").first_row[0]
        _exists = _n > 0
    except Exception:
        pass

    if _exists:
        _stats = ch.query("""
            SELECT count(), uniq(trader), uniq(condition_id)
            FROM _tmp_s1_positions
        """).first_row
        mo.md(
            f"**Step 2** — `_tmp_s1_positions` cached: **{_stats[0]:,}** positions "
            f"({_stats[1]:,} traders, {_stats[2]:,} markets). "
            f"`DROP TABLE _tmp_s1_positions` to rebuild."
        )
    else:
        _t0 = time_mod.time()
        ch.command(AGG_SQL)
        _elapsed = time_mod.time() - _t0
        _stats = ch.query("""
            SELECT count(), uniq(trader), uniq(condition_id)
            FROM _tmp_s1_positions
        """).first_row
        mo.md(
            f"**Step 2** — Built `_tmp_s1_positions`: **{_stats[0]:,}** positions "
            f"({_stats[1]:,} traders, {_stats[2]:,} markets) in **{_elapsed:.0f}s**"
        )


# ═══════════════════════════════════════════════════════════════════════
#  STEP 3 — Pull enriched positions into Polars
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def step3_pull_sql(RESOLUTION_SQL, mo):
    """
    Join positions with resolution, compute:
      - bet_yes:  net_yes_tokens > 0
      - dir_entry: directional entry price (from maker's perspective)
      - correct:  bet direction matched resolution outcome
      - month:    YYYY-MM for time-series analysis

    Only positions HELD at resolution (net_yes_tokens ≠ 0).
    """

    PULL_SQL = f"""
    SELECT
        p.trader,
        p.condition_id,
        CAST(p.net_yes_tokens > 0 AS Bool)            AS bet_yes,

        if(p.net_yes_tokens > 0,
           p.wavg_yes_entry_price,
           1.0 - p.wavg_yes_entry_price)              AS dir_entry,

        CAST(
            (p.net_yes_tokens > 0 AND r.yes_won)
            OR (p.net_yes_tokens <= 0 AND NOT r.yes_won)
        AS Bool)                                       AS correct,

        p.trade_count,
        p.market_volume,
        toDateTime64(r.resolved_at, 3, 'UTC')          AS resolved_at,
        formatDateTime(r.resolved_at, '%Y-%m')          AS month

    FROM _tmp_s1_positions p
    JOIN (
        {RESOLUTION_SQL}
    ) r ON p.condition_id = r.condition_id
    WHERE p.net_yes_tokens != 0
    """

    mo.md(f"""
## Step 3 — Pull Enriched Positions

```sql
{PULL_SQL.strip()}
```
""")
    return (PULL_SQL,)


@app.cell
def step3_pull(PULL_SQL, ch, mo, pl, time_mod):
    """Pull all held-at-resolution positions into Polars."""
    _t0 = time_mod.time()
    _arrow = ch.query_arrow(PULL_SQL)
    df = pl.from_arrow(_arrow)

    # Ensure resolved_at is a proper timezone-naive datetime
    if "resolved_at" in df.columns:
        _dtype = df.schema["resolved_at"]
        if isinstance(_dtype, pl.Datetime) and _dtype.time_zone:
            df = df.with_columns(
                pl.col("resolved_at").dt.replace_time_zone(None)
            )
        elif _dtype in (pl.UInt32, pl.Int64, pl.UInt64):
            # Arrow sometimes returns DateTime as epoch seconds
            df = df.with_columns(
                pl.from_epoch(pl.col("resolved_at"), time_unit="s")
                .alias("resolved_at")
            )

    _elapsed = time_mod.time() - _t0
    _n_traders = df["trader"].n_unique()
    _n_markets = df["condition_id"].n_unique()
    _hr = float(df["correct"].mean())

    mo.md(f"""
**Step 3** — Pulled **{df.height:,}** positions
({_n_traders:,} traders, {_n_markets:,} markets) in **{_elapsed:.1f}s**

Overall HR: **{_hr:.1%}**
""")
    return (df,)


# ═══════════════════════════════════════════════════════════════════════
#  INTERACTIVE CONTROLS
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def section_controls(mo):
    mo.md("---\n## Pool Selection Controls")


@app.cell
def time_control(mo):
    time_window = mo.ui.dropdown(
        options={
            "3 months (Oct–Dec 2025)": "3m",
            "6 months (Jul–Dec 2025)": "6m",
            "9 months (Apr–Dec 2025)": "9m",
            "12 months (Jan–Dec 2025)": "12m",
            "Full history": "all",
        },
        value="6 months (Jul–Dec 2025)",
        label="Training window",
    )
    return (time_window,)


@app.cell
def pool_controls(mo):
    min_hr = mo.ui.slider(
        start=0.40, stop=0.85, step=0.05, value=0.55,
        label="Min hit rate",
    )
    min_markets = mo.ui.slider(
        start=5, stop=100, step=5, value=30,
        label="Min markets traded",
    )
    min_months = mo.ui.slider(
        start=1, stop=12, step=1, value=6,
        label="Min good months",
    )
    monthly_hr = mo.ui.slider(
        start=0.40, stop=0.70, step=0.05, value=0.50,
        label="Monthly min HR",
    )
    return min_hr, min_markets, min_months, monthly_hr


@app.cell
def signal_section(mo):
    mo.md("### Signal Filters (holdout)")


@app.cell
def signal_controls(mo):
    entry_lo = mo.ui.slider(
        start=0.0, stop=0.90, step=0.05, value=0.0,
        label="Min dir entry",
    )
    entry_hi = mo.ui.slider(
        start=0.10, stop=1.0, step=0.05, value=1.0,
        label="Max dir entry",
    )
    direction = mo.ui.dropdown(
        options={"ALL": "ALL", "NO only": "NO", "YES only": "YES"},
        value="ALL",
        label="Direction",
    )
    return direction, entry_hi, entry_lo


# ═══════════════════════════════════════════════════════════════════════
#  STEP 4 — Pool building (Polars, interactive)
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def compute_bounds(datetime, time_window):
    tw = time_window.value
    bounds = {
        "3m":  (datetime(2023, 1, 1), datetime(2025, 10, 1),
                datetime(2025, 10, 1), datetime(2026, 1, 1)),
        "6m":  (datetime(2023, 1, 1), datetime(2025, 7, 1),
                datetime(2025, 7, 1), datetime(2026, 1, 1)),
        "9m":  (datetime(2023, 1, 1), datetime(2025, 4, 1),
                datetime(2025, 4, 1), datetime(2026, 1, 1)),
        "12m": (datetime(2023, 1, 1), datetime(2025, 1, 1),
                datetime(2025, 1, 1), datetime(2026, 1, 1)),
        "all": (datetime(2023, 1, 1), datetime(2025, 7, 1),
                datetime(2025, 7, 1), datetime(2026, 1, 1)),
    }
    train_start, train_end, holdout_start, holdout_end = bounds[tw]
    return holdout_end, holdout_start, train_end, train_start


@app.cell
def build_pool(
    df, min_hr, min_markets, min_months, monthly_hr,
    pl, train_end, train_start,
):
    """
    Pool selection on RESOLUTION HIT RATE:
      1. Filter training window
      2. Per-trader stats: HR, n_markets, yes_frac, median entry, etc.
      3. Monthly consistency: count months with HR >= threshold (min 3 bets)
      4. Apply min_hr, min_markets, min_months filters
    """
    train = df.filter(
        (pl.col("resolved_at") >= train_start)
        & (pl.col("resolved_at") < train_end)
    )

    # Per-trader stats
    trader_stats = train.group_by("trader").agg(
        pl.col("correct").mean().alias("hr"),
        pl.len().alias("n_markets"),
        pl.col("bet_yes").mean().alias("yes_frac"),
        pl.col("dir_entry").median().alias("median_entry"),
        pl.col("dir_entry").mean().alias("mean_entry"),
        pl.col("month").n_unique().alias("active_months"),
        pl.col("trade_count").mean().alias("avg_trades"),
        # Direction-specific HR
        pl.col("correct").filter(pl.col("bet_yes")).mean().alias("yes_hr"),
        pl.col("correct").filter(~pl.col("bet_yes")).mean().alias("no_hr"),
        pl.col("bet_yes").sum().alias("n_yes"),
        (~pl.col("bet_yes")).cast(pl.Int64).sum().alias("n_no"),
    )

    # Monthly consistency
    monthly = (
        train.group_by(["trader", "month"])
        .agg(
            pl.col("correct").mean().alias("m_hr"),
            pl.len().alias("m_n"),
        )
        .filter(pl.col("m_n") >= 3)
    )
    good = (
        monthly.filter(pl.col("m_hr") >= monthly_hr.value)
        .group_by("trader")
        .agg(pl.len().alias("good_months"))
    )

    trader_stats = trader_stats.join(
        good, on="trader", how="left"
    ).with_columns(pl.col("good_months").fill_null(0))

    # Apply filters
    pool_df = trader_stats.filter(
        (pl.col("n_markets") >= min_markets.value)
        & (pl.col("hr") >= min_hr.value)
        & (pl.col("good_months") >= min_months.value)
    )
    pool_set = frozenset(pool_df["trader"].to_list())

    return pool_df, pool_set


# ═══════════════════════════════════════════════════════════════════════
#  STEP 5 — Holdout + Baseline
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def compute_holdout(
    df, direction, entry_hi, entry_lo,
    holdout_end, holdout_start, pl, pool_set,
):
    _FEE = 0.02
    _BET = 100.0

    holdout = df.filter(
        (pl.col("resolved_at") >= holdout_start)
        & (pl.col("resolved_at") < holdout_end)
        & pl.col("trader").is_in(list(pool_set))
    )

    if direction.value == "YES":
        holdout = holdout.filter(pl.col("bet_yes"))
    elif direction.value == "NO":
        holdout = holdout.filter(~pl.col("bet_yes"))

    holdout = holdout.filter(
        (pl.col("dir_entry") >= entry_lo.value)
        & (pl.col("dir_entry") <= entry_hi.value)
    )

    holdout = holdout.with_columns(
        pl.when(pl.col("correct"))
        .then(
            pl.lit(_BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(_BET) * _FEE
        )
        .otherwise(-pl.lit(_BET) - pl.lit(_BET) * _FEE)
        .alias("pnl")
    )
    return (holdout,)


@app.cell
def compute_baseline(
    df, direction, entry_hi, entry_lo,
    holdout_end, holdout_start, pl,
):
    _FEE = 0.02
    _BET = 100.0

    baseline = df.filter(
        (pl.col("resolved_at") >= holdout_start)
        & (pl.col("resolved_at") < holdout_end)
    )

    if direction.value == "YES":
        baseline = baseline.filter(pl.col("bet_yes"))
    elif direction.value == "NO":
        baseline = baseline.filter(~pl.col("bet_yes"))

    baseline = baseline.filter(
        (pl.col("dir_entry") >= entry_lo.value)
        & (pl.col("dir_entry") <= entry_hi.value)
    )

    baseline = baseline.with_columns(
        pl.when(pl.col("correct"))
        .then(
            pl.lit(_BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(_BET) * _FEE
        )
        .otherwise(-pl.lit(_BET) - pl.lit(_BET) * _FEE)
        .alias("pnl")
    )
    return (baseline,)


# ═══════════════════════════════════════════════════════════════════════
#  RESULTS — KPIs
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def show_kpis(baseline, holdout, mo, pool_set):
    mo.stop(holdout.height == 0, mo.md("**No signals match current filters.**"))

    _p_hr = float(holdout["correct"].mean())
    _p_pnl = float(holdout["pnl"].sum())
    _p_per = _p_pnl / holdout.height
    _p_entry = float(holdout["dir_entry"].mean())

    _b_hr = float(baseline["correct"].mean()) if baseline.height > 0 else 0
    _b_per = (
        float(baseline["pnl"].sum()) / baseline.height
        if baseline.height > 0
        else 0
    )

    mo.md(f"""
---
## Results

| Metric | Pool | All Traders | Market Implied |
|--------|------|-------------|----------------|
| **Hit Rate** | **{_p_hr:.1%}** | {_b_hr:.1%} | {_p_entry:.1%} |
| **$/signal** | **${_p_per:+.1f}** | ${_b_per:+.1f} | — |
| **Edge vs All** | **{_p_hr - _b_hr:+.1%}** | — | — |
| **Edge vs Market** | **{_p_hr - _p_entry:+.1%}** | — | — |

| | |
|---|---|
| **Pool size** | {len(pool_set):,} traders |
| **Holdout signals** | {holdout.height:,} |
| **Total PnL** | ${_p_pnl:+,.0f} |
| **Avg dir entry** | {_p_entry:.3f} |
| **Baseline signals** | {baseline.height:,} |
""")


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Entry Price Bands
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def entry_band_header(mo):
    mo.md("---\n## Entry Price Bands")


@app.cell
def entry_band_table(baseline, holdout, mo, pl):
    mo.stop(holdout.height == 0)

    _bands = [
        (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
        (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0),
    ]

    _rows = []
    for _lo, _hi in _bands:
        _pb = holdout.filter(
            (pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi)
        )
        _bb = baseline.filter(
            (pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi)
        )
        if _pb.height < 5:
            continue
        _p_hr = float(_pb["correct"].mean())
        _p_per = float(_pb["pnl"].sum()) / _pb.height
        _b_hr = float(_bb["correct"].mean()) if _bb.height > 0 else 0
        _b_per = (
            float(_bb["pnl"].sum()) / _bb.height if _bb.height > 0 else 0
        )
        _rows.append({
            "Band": f"{_lo:.0%}-{_hi:.0%}",
            "Pool N": _pb.height,
            "Pool HR": f"{_p_hr:.1%}",
            "Pool $/sig": f"${_p_per:+.1f}",
            "All N": _bb.height,
            "All HR": f"{_b_hr:.1%}",
            "All $/sig": f"${_b_per:+.1f}",
            "Edge": f"{_p_hr - _b_hr:+.1%}",
        })

    if _rows:
        mo.ui.table(pl.DataFrame(_rows).to_pandas(), label="Entry bands")


@app.cell
def entry_band_chart(baseline, go, holdout, mo, pl, px):
    mo.stop(holdout.height < 10)

    _bands = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    _data = []
    for _lo, _hi in _bands:
        _label = f"{_lo:.0%}-{_hi:.0%}"
        _mid = (_lo + _hi) / 2
        for _src, _sdf in [("Pool", holdout), ("All", baseline)]:
            _b = _sdf.filter(
                (pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi)
            )
            if _b.height >= 10:
                _data.append({
                    "Band": _label,
                    "Source": _src,
                    "HR": float(_b["correct"].mean()),
                    "N": _b.height,
                })
        _data.append({
            "Band": _label, "Source": "Market", "HR": _mid, "N": 0,
        })

    entry_band_fig = None
    if _data:
        entry_band_fig = px.bar(
            pl.DataFrame(_data).to_pandas(),
            x="Band", y="HR", color="Source",
            barmode="group",
            title="HR by Entry Band: Pool vs All vs Market",
            text_auto=".1%",
        )
        entry_band_fig.update_layout(yaxis_tickformat=".0%", height=400)
    return (entry_band_fig,)


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Monthly
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def monthly_header(mo):
    mo.md("---\n## Monthly Breakdown")


@app.cell
def monthly_table(holdout, mo, pl):
    mo.stop(holdout.height == 0)

    _m = (
        holdout.group_by("month")
        .agg(
            pl.len().alias("N"),
            pl.col("correct").mean().alias("HR"),
            pl.col("pnl").sum().alias("PnL"),
            pl.col("dir_entry").mean().alias("Avg Entry"),
            pl.col("bet_yes").mean().alias("YES%"),
        )
        .sort("month")
        .with_columns((pl.col("PnL") / pl.col("N")).alias("$/sig"))
    )
    mo.ui.table(_m.to_pandas(), label="Monthly performance")


@app.cell
def monthly_chart(holdout, mo, pl, px):
    mo.stop(holdout.height == 0)

    _m = (
        holdout.group_by("month")
        .agg(pl.col("pnl").sum().alias("PnL"))
        .sort("month")
        .to_pandas()
    )
    monthly_pnl_fig = px.bar(
        _m, x="month", y="PnL",
        title="Monthly PnL ($100/signal)",
        color="PnL",
        color_continuous_scale=["red", "gray", "green"],
        color_continuous_midpoint=0,
    )
    monthly_pnl_fig.update_layout(height=350)
    return (monthly_pnl_fig,)


@app.cell
def equity_curve(holdout, mo, pl, px):
    mo.stop(holdout.height == 0)

    _m = (
        holdout.group_by("month")
        .agg(pl.col("pnl").sum().alias("PnL"))
        .sort("month")
        .to_pandas()
    )
    _m["Cumulative"] = _m["PnL"].cumsum()
    equity_fig = px.line(
        _m, x="month", y="Cumulative",
        title="Cumulative PnL", markers=True,
    )
    equity_fig.update_layout(height=350)
    return (equity_fig,)


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Trade Activity
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def trades_header(mo):
    mo.md("---\n## By Trade Activity")


@app.cell
def trades_breakdown(holdout, mo, pl, px):
    mo.stop(holdout.height == 0)

    _buckets = [
        (1, 2, "1 trade"),
        (2, 5, "2-4"),
        (5, 15, "5-14"),
        (15, 50, "15-49"),
        (50, 1_000_000, "50+"),
    ]
    _data = []
    for _lo, _hi, _label in _buckets:
        _b = holdout.filter(
            (pl.col("trade_count") >= _lo) & (pl.col("trade_count") < _hi)
        )
        if _b.height >= 10:
            _data.append({
                "Trades": _label,
                "N": _b.height,
                "HR": float(_b["correct"].mean()),
                "$/sig": float(_b["pnl"].sum()) / _b.height,
            })

    trades_fig = None
    if _data:
        _tdf = pl.DataFrame(_data)
        mo.ui.table(_tdf.to_pandas(), label="By trade count")
        trades_fig = px.bar(
            _tdf.to_pandas(), x="Trades", y="$/sig",
            title="$/sig by Trade Count",
            color="$/sig",
            color_continuous_scale=["red", "gray", "green"],
            color_continuous_midpoint=0,
        )
        trades_fig.update_layout(height=350)
    return (trades_fig,)


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Direction Preference
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def direction_header(mo):
    mo.md("---\n## By Trader Direction Preference")


@app.cell
def direction_breakdown(holdout, mo, pl, pool_df, px):
    mo.stop(holdout.height == 0 or pool_df.height == 0)

    _buckets = [
        (0.00, 0.10, "Heavy NO (0-10%)"),
        (0.10, 0.30, "NO-leaning"),
        (0.30, 0.50, "Slight NO"),
        (0.50, 0.70, "Slight YES"),
        (0.70, 0.90, "YES-leaning"),
        (0.90, 1.01, "Heavy YES (90%+)"),
    ]

    _hw = holdout.join(
        pool_df.select("trader", "yes_frac"),
        on="trader", how="left", suffix="_p",
    )

    _data = []
    for _lo, _hi, _label in _buckets:
        _b = _hw.filter(
            (pl.col("yes_frac") >= _lo) & (pl.col("yes_frac") < _hi)
        )
        if _b.height >= 10:
            _data.append({
                "Bucket": _label,
                "N": _b.height,
                "Traders": _b["trader"].n_unique(),
                "HR": float(_b["correct"].mean()),
                "$/sig": float(_b["pnl"].sum()) / _b.height,
            })

    direction_fig = None
    if _data:
        _tdf = pl.DataFrame(_data)
        mo.ui.table(_tdf.to_pandas(), label="By direction preference")
        direction_fig = px.bar(
            _tdf.to_pandas(), x="Bucket", y="HR",
            title="HR by Direction Preference",
            text_auto=".1%",
        )
        direction_fig.update_layout(yaxis_tickformat=".0%", height=350)
    return (direction_fig,)


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Pool Distributions
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_dist_header(mo):
    mo.md("---\n## Pool Distributions")


@app.cell
def pool_hr_dist(mo, pool_df, px):
    mo.stop(pool_df.height == 0)
    hr_dist_fig = px.histogram(
        pool_df.to_pandas(), x="hr", nbins=40,
        title=f"Pool HR Distribution (n={pool_df.height:,})",
    )
    hr_dist_fig.update_layout(height=350)
    return (hr_dist_fig,)


@app.cell
def pool_entry_dist(mo, pool_df, px):
    mo.stop(pool_df.height == 0)
    entry_dist_fig = px.histogram(
        pool_df.to_pandas(), x="median_entry", nbins=40,
        title="Pool Median Entry Distribution",
    )
    entry_dist_fig.update_layout(height=350)
    return (entry_dist_fig,)


@app.cell
def pool_scatter(mo, pool_df, px):
    mo.stop(pool_df.height == 0)
    scatter_fig = px.scatter(
        pool_df.to_pandas(),
        x="yes_frac", y="hr",
        size="n_markets", color="median_entry",
        title="YES% vs HR (size=markets, color=entry)",
        color_continuous_scale="RdYlGn",
    )
    scatter_fig.update_layout(height=450)
    return (scatter_fig,)


# ═══════════════════════════════════════════════════════════════════════
#  CALIBRATION — Market Efficiency
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def calibration_header(mo):
    mo.md("---\n## Market Calibration (Implied vs Actual)")


@app.cell
def calibration_chart(baseline, go, holdout, mo, pl, px):
    mo.stop(holdout.height < 100)

    _data = []
    for _i in range(20):
        _lo, _hi = _i / 20, (_i + 1) / 20
        for _label, _src in [("Pool", holdout), ("All", baseline)]:
            _b = _src.filter(
                (pl.col("dir_entry") >= _lo) & (pl.col("dir_entry") < _hi)
            )
            if _b.height >= 20:
                _data.append({
                    "Implied": (_lo + _hi) / 2,
                    "Actual": float(_b["correct"].mean()),
                    "Source": _label,
                    "N": _b.height,
                })

    calibration_fig = None
    if _data:
        calibration_fig = px.scatter(
            pl.DataFrame(_data).to_pandas(),
            x="Implied", y="Actual",
            color="Source", size="N",
            title="Implied Prob vs Actual Resolution HR",
        )
        calibration_fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines",
            line=dict(dash="dash", color="gray"),
            name="Perfect",
        ))
        calibration_fig.update_layout(height=500)
    return (calibration_fig,)


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Market Volume
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def volume_header(mo):
    mo.md("---\n## By Market Volume")


@app.cell
def volume_breakdown(holdout, mo, pl):
    mo.stop(holdout.height == 0)

    _qs = [float(holdout["market_volume"].quantile(q)) for q in [0.25, 0.5, 0.75]]
    _buckets = [
        (0, _qs[0], f"Q1 (0-{_qs[0]:.0f})"),
        (_qs[0], _qs[1], f"Q2 ({_qs[0]:.0f}-{_qs[1]:.0f})"),
        (_qs[1], _qs[2], f"Q3 ({_qs[1]:.0f}-{_qs[2]:.0f})"),
        (_qs[2], 1e15, f"Q4 ({_qs[2]:.0f}+)"),
    ]

    _data = []
    for _lo, _hi, _label in _buckets:
        _b = holdout.filter(
            (pl.col("market_volume") >= _lo)
            & (pl.col("market_volume") < _hi)
        )
        if _b.height >= 10:
            _data.append({
                "Bucket": _label,
                "N": _b.height,
                "HR": float(_b["correct"].mean()),
                "$/sig": float(_b["pnl"].sum()) / _b.height,
            })

    if _data:
        mo.ui.table(pl.DataFrame(_data).to_pandas(), label="By volume")


# ═══════════════════════════════════════════════════════════════════════
#  RAW POOL TABLE
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_table_header(mo, pool_df):
    mo.md(f"---\n## Pool Table ({pool_df.height:,} traders)")


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
            "active_months",
            pl.col("avg_trades").round(1).alias("Avg Trades"),
            pl.col("yes_hr").round(3).alias("YES HR"),
            pl.col("no_hr").round(3).alias("NO HR"),
            "n_yes",
            "n_no",
        )
        .sort("HR", descending=True)
        .head(50)
    )
    mo.ui.table(_display.to_pandas(), label="Top 50 by HR")


if __name__ == "__main__":
    app.run()
