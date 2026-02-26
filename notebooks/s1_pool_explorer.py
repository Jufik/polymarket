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
    import time as _time
    from datetime import datetime

    return _time, clickhouse_connect, datetime, go, mo, pl, px


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
    r = ch.query("""
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
| **Trades** | {r[0]:,} |
| **Markets** | {r[1]:,} |
| **Makers** | {r[2]:,} |
| **Period** | `{r[3]}` → `{r[4]}` |
""")
    return


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

    stats = ch.query(f"""
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
| Resolved markets | **{stats[0]:,}** |
| YES won | {stats[1]:,} ({stats[1]/stats[0]:.1%}) |
| NO won | {stats[2]:,} ({stats[2]/stats[0]:.1%}) |
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
        maker                              AS trader,
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
        taker                              AS trader,
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
def step2_run(AGG_SQL, _time, ch, mo):
    """Execute the aggregation (skips if table already populated)."""
    exists = False
    n = 0
    try:
        n = ch.query("SELECT count() FROM _tmp_s1_positions").first_row[0]
        exists = n > 0
    except Exception:
        pass

    if exists:
        stats = ch.query("""
            SELECT count(), uniq(trader), uniq(condition_id)
            FROM _tmp_s1_positions
        """).first_row
        mo.md(
            f"**Step 2** — `_tmp_s1_positions` cached: **{stats[0]:,}** positions "
            f"({stats[1]:,} traders, {stats[2]:,} markets). "
            f"`DROP TABLE _tmp_s1_positions` to rebuild."
        )
    else:
        t0 = _time.time()
        ch.command(AGG_SQL)
        elapsed = _time.time() - t0
        stats = ch.query("""
            SELECT count(), uniq(trader), uniq(condition_id)
            FROM _tmp_s1_positions
        """).first_row
        mo.md(
            f"**Step 2** — Built `_tmp_s1_positions`: **{stats[0]:,}** positions "
            f"({stats[1]:,} traders, {stats[2]:,} markets) in **{elapsed:.0f}s**"
        )
    return


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
        r.resolved_at,
        formatDateTime(r.resolved_at, '%Y-%m')         AS month

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
def step3_pull(PULL_SQL, _time, ch, mo, pl):
    """Pull all held-at-resolution positions into Polars."""
    t0 = _time.time()
    arrow = ch.query_arrow(PULL_SQL)
    df = pl.from_arrow(arrow)

    # Strip timezone from resolved_at for clean datetime comparisons
    if "resolved_at" in df.columns:
        dtype = df.schema["resolved_at"]
        if isinstance(dtype, pl.Datetime) and dtype.time_zone:
            df = df.with_columns(
                pl.col("resolved_at").dt.replace_time_zone(None)
            )

    elapsed = _time.time() - t0
    n_traders = df["trader"].n_unique()
    n_markets = df["condition_id"].n_unique()
    hr = float(df["correct"].mean())

    mo.md(f"""
**Step 3** — Pulled **{df.height:,}** positions
({n_traders:,} traders, {n_markets:,} markets) in **{elapsed:.1f}s**

Overall HR: **{hr:.1%}**
""")
    return (df,)


# ═══════════════════════════════════════════════════════════════════════
#  INTERACTIVE CONTROLS
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def section_controls(mo):
    mo.md("---\n## Pool Selection Controls")
    return


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
    return


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
      3. Monthly consistency: count months with HR ≥ threshold (min 3 bets)
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
    FEE = 0.02
    BET = 100.0

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
            pl.lit(BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(BET) * FEE
        )
        .otherwise(-pl.lit(BET) - pl.lit(BET) * FEE)
        .alias("pnl")
    )
    return (holdout,)


@app.cell
def compute_baseline(
    df, direction, entry_hi, entry_lo,
    holdout_end, holdout_start, pl,
):
    FEE = 0.02
    BET = 100.0

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
            pl.lit(BET) * (1.0 - pl.col("dir_entry")) / pl.col("dir_entry")
            - pl.lit(BET) * FEE
        )
        .otherwise(-pl.lit(BET) - pl.lit(BET) * FEE)
        .alias("pnl")
    )
    return (baseline,)


# ═══════════════════════════════════════════════════════════════════════
#  RESULTS — KPIs
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def show_kpis(baseline, holdout, mo, pool_set):
    if holdout.height == 0:
        mo.md("**No signals match current filters.**")
        return

    p_hr = float(holdout["correct"].mean())
    p_pnl = float(holdout["pnl"].sum())
    p_per = p_pnl / holdout.height
    p_entry = float(holdout["dir_entry"].mean())

    b_hr = float(baseline["correct"].mean()) if baseline.height > 0 else 0
    b_per = (
        float(baseline["pnl"].sum()) / baseline.height
        if baseline.height > 0
        else 0
    )

    mo.md(f"""
---
## Results

| Metric | Pool | All Traders | Market Implied |
|--------|------|-------------|----------------|
| **Hit Rate** | **{p_hr:.1%}** | {b_hr:.1%} | {p_entry:.1%} |
| **$/signal** | **${p_per:+.1f}** | ${b_per:+.1f} | — |
| **Edge vs All** | **{p_hr - b_hr:+.1%}** | — | — |
| **Edge vs Market** | **{p_hr - p_entry:+.1%}** | — | — |

| | |
|---|---|
| **Pool size** | {len(pool_set):,} traders |
| **Holdout signals** | {holdout.height:,} |
| **Total PnL** | ${p_pnl:+,.0f} |
| **Avg dir entry** | {p_entry:.3f} |
| **Baseline signals** | {baseline.height:,} |
""")
    return


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Entry Price Bands
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def entry_band_header(mo):
    mo.md("---\n## Entry Price Bands")
    return


@app.cell
def entry_band_table(baseline, holdout, mo, pl):
    if holdout.height == 0:
        return

    bands = [
        (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
        (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0),
    ]

    rows = []
    for lo, hi in bands:
        pb = holdout.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        bb = baseline.filter(
            (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
        )
        if pb.height < 5:
            continue
        p_hr = float(pb["correct"].mean())
        p_per = float(pb["pnl"].sum()) / pb.height
        b_hr = float(bb["correct"].mean()) if bb.height > 0 else 0
        b_per = (
            float(bb["pnl"].sum()) / bb.height if bb.height > 0 else 0
        )
        rows.append({
            "Band": f"{lo:.0%}-{hi:.0%}",
            "Pool N": pb.height,
            "Pool HR": f"{p_hr:.1%}",
            "Pool $/sig": f"${p_per:+.1f}",
            "All N": bb.height,
            "All HR": f"{b_hr:.1%}",
            "All $/sig": f"${b_per:+.1f}",
            "Edge": f"{p_hr - b_hr:+.1%}",
        })

    if rows:
        mo.ui.table(pl.DataFrame(rows).to_pandas(), label="Entry bands")
    return


@app.cell
def entry_band_chart(baseline, go, holdout, pl, px):
    if holdout.height < 10:
        return

    bands = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    data = []
    for lo, hi in bands:
        label = f"{lo:.0%}-{hi:.0%}"
        mid = (lo + hi) / 2
        for src, sdf in [("Pool", holdout), ("All", baseline)]:
            b = sdf.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )
            if b.height >= 10:
                data.append({
                    "Band": label,
                    "Source": src,
                    "HR": float(b["correct"].mean()),
                    "N": b.height,
                })
        data.append({
            "Band": label, "Source": "Market", "HR": mid, "N": 0,
        })

    if data:
        fig = px.bar(
            pl.DataFrame(data).to_pandas(),
            x="Band", y="HR", color="Source",
            barmode="group",
            title="HR by Entry Band: Pool vs All vs Market",
            text_auto=".1%",
        )
        fig.update_layout(yaxis_tickformat=".0%", height=400)
        return fig


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Monthly
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def monthly_header(mo):
    mo.md("---\n## Monthly Breakdown")
    return


@app.cell
def monthly_table(holdout, mo, pl):
    if holdout.height == 0:
        return

    m = (
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
    mo.ui.table(m.to_pandas(), label="Monthly performance")
    return


@app.cell
def monthly_chart(holdout, pl, px):
    if holdout.height == 0:
        return

    m = (
        holdout.group_by("month")
        .agg(pl.col("pnl").sum().alias("PnL"))
        .sort("month")
        .to_pandas()
    )
    fig = px.bar(
        m, x="month", y="PnL",
        title="Monthly PnL ($100/signal)",
        color="PnL",
        color_continuous_scale=["red", "gray", "green"],
        color_continuous_midpoint=0,
    )
    fig.update_layout(height=350)
    return fig


@app.cell
def equity_curve(holdout, pl, px):
    if holdout.height == 0:
        return

    m = (
        holdout.group_by("month")
        .agg(pl.col("pnl").sum().alias("PnL"))
        .sort("month")
        .to_pandas()
    )
    m["Cumulative"] = m["PnL"].cumsum()
    fig = px.line(
        m, x="month", y="Cumulative",
        title="Cumulative PnL", markers=True,
    )
    fig.update_layout(height=350)
    return fig


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Trade Activity
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def trades_header(mo):
    mo.md("---\n## By Trade Activity")
    return


@app.cell
def trades_breakdown(holdout, mo, pl, px):
    if holdout.height == 0:
        return

    buckets = [
        (1, 2, "1 trade"),
        (2, 5, "2-4"),
        (5, 15, "5-14"),
        (15, 50, "15-49"),
        (50, 1_000_000, "50+"),
    ]
    data = []
    for lo, hi, label in buckets:
        b = holdout.filter(
            (pl.col("trade_count") >= lo) & (pl.col("trade_count") < hi)
        )
        if b.height >= 10:
            data.append({
                "Trades": label,
                "N": b.height,
                "HR": float(b["correct"].mean()),
                "$/sig": float(b["pnl"].sum()) / b.height,
            })

    if data:
        tdf = pl.DataFrame(data)
        mo.ui.table(tdf.to_pandas(), label="By trade count")
        fig = px.bar(
            tdf.to_pandas(), x="Trades", y="$/sig",
            title="$/sig by Trade Count",
            color="$/sig",
            color_continuous_scale=["red", "gray", "green"],
            color_continuous_midpoint=0,
        )
        fig.update_layout(height=350)
        return fig


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Direction Preference
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def direction_header(mo):
    mo.md("---\n## By Trader Direction Preference")
    return


@app.cell
def direction_breakdown(holdout, mo, pl, pool_df, px):
    if holdout.height == 0 or pool_df.height == 0:
        return

    buckets = [
        (0.00, 0.10, "Heavy NO (0-10%)"),
        (0.10, 0.30, "NO-leaning"),
        (0.30, 0.50, "Slight NO"),
        (0.50, 0.70, "Slight YES"),
        (0.70, 0.90, "YES-leaning"),
        (0.90, 1.01, "Heavy YES (90%+)"),
    ]

    hw = holdout.join(
        pool_df.select("trader", "yes_frac"),
        on="trader", how="left", suffix="_p",
    )

    data = []
    for lo, hi, label in buckets:
        b = hw.filter(
            (pl.col("yes_frac") >= lo) & (pl.col("yes_frac") < hi)
        )
        if b.height >= 10:
            data.append({
                "Bucket": label,
                "N": b.height,
                "Traders": b["trader"].n_unique(),
                "HR": float(b["correct"].mean()),
                "$/sig": float(b["pnl"].sum()) / b.height,
            })

    if data:
        tdf = pl.DataFrame(data)
        mo.ui.table(tdf.to_pandas(), label="By direction preference")
        fig = px.bar(
            tdf.to_pandas(), x="Bucket", y="HR",
            title="HR by Direction Preference",
            text_auto=".1%",
        )
        fig.update_layout(yaxis_tickformat=".0%", height=350)
        return fig


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Pool Distributions
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_dist_header(mo):
    mo.md("---\n## Pool Distributions")
    return


@app.cell
def pool_hr_dist(pool_df, px):
    if pool_df.height == 0:
        return
    fig = px.histogram(
        pool_df.to_pandas(), x="hr", nbins=40,
        title=f"Pool HR Distribution (n={pool_df.height:,})",
    )
    fig.update_layout(height=350)
    return fig


@app.cell
def pool_entry_dist(pool_df, px):
    if pool_df.height == 0:
        return
    fig = px.histogram(
        pool_df.to_pandas(), x="median_entry", nbins=40,
        title="Pool Median Entry Distribution",
    )
    fig.update_layout(height=350)
    return fig


@app.cell
def pool_scatter(pool_df, px):
    if pool_df.height == 0:
        return
    fig = px.scatter(
        pool_df.to_pandas(),
        x="yes_frac", y="hr",
        size="n_markets", color="median_entry",
        title="YES% vs HR (size=markets, color=entry)",
        color_continuous_scale="RdYlGn",
    )
    fig.update_layout(height=450)
    return fig


# ═══════════════════════════════════════════════════════════════════════
#  CALIBRATION — Market Efficiency
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def calibration_header(mo):
    mo.md("---\n## Market Calibration (Implied vs Actual)")
    return


@app.cell
def calibration_chart(baseline, go, holdout, pl, px):
    if holdout.height < 100:
        return

    data = []
    for i in range(20):
        lo, hi = i / 20, (i + 1) / 20
        for label, src in [("Pool", holdout), ("All", baseline)]:
            b = src.filter(
                (pl.col("dir_entry") >= lo) & (pl.col("dir_entry") < hi)
            )
            if b.height >= 20:
                data.append({
                    "Implied": (lo + hi) / 2,
                    "Actual": float(b["correct"].mean()),
                    "Source": label,
                    "N": b.height,
                })

    if data:
        fig = px.scatter(
            pl.DataFrame(data).to_pandas(),
            x="Implied", y="Actual",
            color="Source", size="N",
            title="Implied Prob vs Actual Resolution HR",
        )
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines",
            line=dict(dash="dash", color="gray"),
            name="Perfect",
        ))
        fig.update_layout(height=500)
        return fig


# ═══════════════════════════════════════════════════════════════════════
#  BREAKDOWNS — Market Volume
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def volume_header(mo):
    mo.md("---\n## By Market Volume")
    return


@app.cell
def volume_breakdown(holdout, mo, pl):
    if holdout.height == 0:
        return

    qs = [float(holdout["market_volume"].quantile(q)) for q in [0.25, 0.5, 0.75]]
    buckets = [
        (0, qs[0], f"Q1 (0-{qs[0]:.0f})"),
        (qs[0], qs[1], f"Q2 ({qs[0]:.0f}-{qs[1]:.0f})"),
        (qs[1], qs[2], f"Q3 ({qs[1]:.0f}-{qs[2]:.0f})"),
        (qs[2], 1e15, f"Q4 ({qs[2]:.0f}+)"),
    ]

    data = []
    for lo, hi, label in buckets:
        b = holdout.filter(
            (pl.col("market_volume") >= lo)
            & (pl.col("market_volume") < hi)
        )
        if b.height >= 10:
            data.append({
                "Bucket": label,
                "N": b.height,
                "HR": float(b["correct"].mean()),
                "$/sig": float(b["pnl"].sum()) / b.height,
            })

    if data:
        mo.ui.table(pl.DataFrame(data).to_pandas(), label="By volume")
    return


# ═══════════════════════════════════════════════════════════════════════
#  RAW POOL TABLE
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def pool_table_header(mo, pool_df):
    mo.md(f"---\n## Pool Table ({pool_df.height:,} traders)")
    return


@app.cell
def pool_table(mo, pl, pool_df):
    if pool_df.height == 0:
        return

    display = (
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
    mo.ui.table(display.to_pandas(), label="Top 50 by HR")
    return


if __name__ == "__main__":
    app.run()
