# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo",
#     "polars",
#     "clickhouse-connect",
#     "plotly",
#     "numpy",
# ]
# ///

import marimo

__generated_with = "0.20.2"
app = marimo.App(
    width="full",
    app_title="S1: High Hit-Rate Trader Copy-Trading Exploration",
)

with app.setup:
    import clickhouse_connect
    import polars as pl
    import numpy as np

    CH_HOST = "192.168.0.148"
    CH_PORT = 18123
    CH_DB = "polymarket"

    client = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, database=CH_DB
    )

    def ch_query(sql: str) -> pl.DataFrame:
        """Execute ClickHouse query and return as Polars DataFrame."""
        r = client.query(sql)
        if not r.result_rows:
            return pl.DataFrame()
        return pl.DataFrame(
            {col: [row[i] for row in r.result_rows] for i, col in enumerate(r.column_names)}
        )

    # Also load local parquet extracts for fast iteration
    DERIVED = "data/derived"


@app.cell
def title():
    import marimo as mo

    mo.md(
        """
    # S1: High Hit-Rate Trader Copy-Trading Strategy

    **Hypothesis**: Some traders on Polymarket consistently pick correct outcomes
    at non-trivial prices. Identifying these traders via historical hit rate and
    copying their future trades generates positive expected value.

    **Falsifiable claim**: Traders in the top decile by historical hit rate
    (filtered for non-safe-bet entries and minimum sample size) achieve an
    out-of-sample hit rate significantly above the bottom decile, and generate
    positive OOS PnL.

    **Economic intuition**: Skilled information aggregators or domain experts
    repeatedly enter markets where they have an edge. Their track record is
    a weak-but-persistent signal of future performance (skill persistence).

    ---

    **Data**: Remote ClickHouse at 192.168.0.148:18123, database polymarket
    - 434M trades, Nov 2022 -- Feb 2026 (3+ years)
    - 32M resolved positions across 393K markets, 1.6M unique traders
    - $23B total trading volume
    """
    )
    return (mo,)


@app.cell
def data_overview(mo):
    mo.md("## 1. Data Inventory")

    overview = ch_query("""
    SELECT 'trades_raw' as tbl, count() as rows FROM trades_raw
    UNION ALL SELECT 'trader_trade_agg', count() FROM trader_trade_agg FINAL
    UNION ALL SELECT 'trader_market_positions', count() FROM trader_market_positions FINAL
    UNION ALL SELECT 'trader_positions_resolved', count() FROM trader_positions_resolved
    UNION ALL SELECT 'markets', count() FROM markets
    UNION ALL SELECT 'markets_resolved', count() FROM markets_resolved
    UNION ALL SELECT 'trader_volumes', count() FROM trader_volumes FINAL
    UNION ALL SELECT 'token_market_map', count() FROM token_market_map
    """)

    mo.ui.table(overview)
    return


@app.cell
def base_rates(mo):
    mo.md(
        """
    ## 2. Resolution Base Rates

    Global Polymarket: ~40% YES-won, ~60% NO-won across 393K resolved markets.
    """
    )

    base = ch_query("""
    SELECT
        sum(toUInt32(yw)) as yes_won,
        sum(toUInt32(NOT yw)) as no_won,
        count() as total_markets,
        round(sum(toUInt32(yw)) / count(), 4) as yes_pct
    FROM (
        SELECT condition_id, any(yes_won) as yw
        FROM trader_positions_resolved
        GROUP BY condition_id
    )
    """)

    mo.ui.table(base)

    mo.md(
        f"""
    - **YES-won**: {base['yes_won'][0]:,} ({float(base['yes_pct'][0]):.1%})
    - **NO-won**: {base['no_won'][0]:,} ({1 - float(base['yes_pct'][0]):.1%})
    - **Total resolved markets**: {base['total_markets'][0]:,}
    """
    )
    return


@app.cell
def position_profile(mo):
    mo.md(
        """
    ## 3. Position Type Profile

    Position classification: YES (net long YES), NO (net long NO),
    HEDGED (both sides), CLOSED (no net position at resolution).
    """
    )

    pos_profile = ch_query("""
    SELECT
        position,
        count() as cnt,
        sum(toUInt32(correct)) as correct_cnt,
        round(sum(toUInt32(correct)) / count(), 4) as hit_rate,
        round(avg(realized_pnl), 2) as avg_pnl,
        round(sum(realized_pnl), 2) as total_pnl,
        round(avg(market_volume), 2) as avg_volume
    FROM trader_positions_resolved
    GROUP BY position
    ORDER BY cnt DESC
    """)

    mo.ui.table(pos_profile)

    mo.md(
        """
    **Key findings (full 3-year dataset)**:
    - **NO positions**: 55.9% hit rate, -$8.22 avg PnL -- high accuracy but losers are large
    - **YES positions**: 40.0% hit rate, -$12.18 avg PnL -- below base rate, net negative
    - **HEDGED positions**: 58.1% hit rate, +$8.44 avg PnL -- profitable on average
    - Total market PnL is deeply negative: the house (market makers) extracts value from
      directional traders. This is the "adversarial baseline" any copy strategy must beat.
    """
    )
    return


@app.cell
def crypto_gambling_filter(mo):
    mo.md(
        """
    ## 4. Market Category Classification

    The `category` field in PG is empty for recent markets. Using regex-based
    classification on question text to segment the market universe.
    """
    )

    gambling = ch_query("""
    SELECT
        CASE
            WHEN m.question LIKE '%Up or Down%' THEN 'crypto_5min'
            WHEN m.question LIKE '%price of Bitcoin%' OR m.question LIKE '%price of BTC%'
                 OR m.question LIKE '%price of Ethereum%' OR m.question LIKE '%price of ETH%'
                 OR m.question LIKE '%price of Solana%' OR m.question LIKE '%price of SOL%'
            THEN 'crypto_price'
            WHEN m.question LIKE '%NBA%' OR m.question LIKE '%NFL%'
                 OR m.question LIKE '%MLB%' OR m.question LIKE '%NHL%'
                 OR m.question LIKE '% win on %' OR m.question LIKE '% beat %'
                 OR m.question LIKE '%vs %' OR m.question LIKE '%NCAAB%'
                 OR m.question LIKE '%UFC%'
            THEN 'sports'
            WHEN m.question LIKE '%Trump%' OR m.question LIKE '%Biden%'
                 OR m.question LIKE '%election%' OR m.question LIKE '%tariff%'
            THEN 'politics'
            WHEN m.question LIKE '%temperature%' OR m.question LIKE '%weather%'
            THEN 'weather'
            ELSE 'other'
        END as market_type,
        count(DISTINCT p.condition_id) as markets,
        count() as positions,
        count(DISTINCT lower(p.trader)) as traders,
        round(sum(toUInt32(p.correct)) / count(), 4) as hit_rate,
        round(avg(p.realized_pnl), 2) as avg_pnl
    FROM trader_positions_resolved p
    INNER JOIN markets m ON p.condition_id = m.condition_id
    WHERE p.position IN ('YES', 'NO')
    GROUP BY market_type
    ORDER BY positions DESC
    """)

    mo.ui.table(gambling)

    mo.md(
        """
    **Key findings**:
    - **crypto_5min** ("Up or Down"): 98K markets, 12.5M positions, 47.7% HR, -$3.73 avg PnL
      -- 48% of all directional positions. Essentially coin flips.
    - **sports**: 53K markets, 2.1M positions, 40.1% HR, -$36.64 avg PnL
      -- sports bettors lose heavily
    - **politics**: 14K markets, 1.5M positions, 48.5% HR, -$3.34 avg PnL
    - **crypto_price**: 18K markets, 959K positions, 57.7% HR (biased by daily thresholds)

    **Filter decision**: Exclude `crypto_5min` ("Up or Down") for strategy research.
    These are noise markets where skill signal is minimal.
    """
    )
    return


@app.cell
def safe_bet_analysis(mo):
    mo.md(
        """
    ## 5. Safe Bet Price Threshold Analysis

    A trader buying YES at 0.95 in a market that resolves YES is "correct" but
    provides almost no alpha. We sweep directional entry price thresholds.

    **Directional entry price**: For YES positions = `avg_yes_price`.
    For NO positions = `1 - avg_yes_price`.
    """
    )

    _rows = []
    for thresh in [1.00, 0.95, 0.90, 0.85, 0.80, 0.70, 0.60, 0.50]:
        _r = ch_query(f"""
        SELECT
            count() as cnt,
            sum(toUInt32(correct)) as wins,
            round(sum(toUInt32(correct)) / count(), 4) as hr,
            round(avg(realized_pnl), 4) as avg_pnl,
            round(sum(realized_pnl), 2) as total_pnl
        FROM trader_positions_resolved p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        WHERE p.position IN ('YES', 'NO')
          AND CASE WHEN p.position = 'YES' THEN p.avg_yes_price
                   ELSE 1.0 - p.avg_yes_price END < {thresh}
          AND NOT (m.question LIKE '%Up or Down%')
        """)
        _rows.append({
            "threshold": thresh,
            "positions": _r["cnt"][0],
            "wins": _r["wins"][0],
            "hit_rate": float(_r["hr"][0]),
            "avg_pnl": float(_r["avg_pnl"][0]),
            "total_pnl": float(_r["total_pnl"][0]),
        })

    safe_bet_df = pl.DataFrame(_rows)
    mo.ui.table(safe_bet_df)

    mo.md(
        """
    **Key observation**: Dropping safe bets (entry > 0.90) removes ~3.5M positions
    (25% of non-gambling) and drops the hit rate from 47.4% to 30.3%.
    This confirms the safe-bet filter is removing trivially easy wins.

    At threshold 0.90 we retain 10.2M positions -- sufficient sample size.

    **Decision**: Use **0.90** as safe-bet cutoff.
    """
    )
    return


@app.cell
def address_dedup(mo):
    mo.md(
        """
    ## 6. Address Case Duplication

    Ethereum addresses are case-insensitive but stored with mixed checksummed case.
    """
    )

    addr_check = ch_query("""
    SELECT
        count(DISTINCT trader) as raw_distinct,
        count(DISTINCT lower(trader)) as lower_distinct,
        count(DISTINCT trader) - count(DISTINCT lower(trader)) as duplicates
    FROM trader_market_positions FINAL
    """)

    mo.ui.table(addr_check)

    mo.md(
        f"""
    **{addr_check['duplicates'][0]:,} duplicate addresses** from case variation
    ({addr_check['raw_distinct'][0]:,} raw vs {addr_check['lower_distinct'][0]:,} lowered).

    All queries use `lower(trader)` for grouping.
    """
    )
    return


@app.cell
def trader_distribution(mo):
    import plotly.express as px

    mo.md(
        """
    ## 7. Trader Position Count Distribution

    How many filtered (non-gambling, entry < 0.90, directional) positions
    do traders accumulate? This determines the feasibility of statistical
    skill measurement.
    """
    )

    df = pl.read_parquet(f"{DERIVED}/trader_features_filtered.parquet")

    mo.md(
        f"""
    **Total filtered traders**: {len(df):,}
    - >= 10 positions: {len(df.filter(pl.col('n_positions')>=10)):,}
    - >= 20 positions: {len(df.filter(pl.col('n_positions')>=20)):,}
    - >= 50 positions: {len(df.filter(pl.col('n_positions')>=50)):,}
    - >= 100 positions: {len(df.filter(pl.col('n_positions')>=100)):,}
    """
    )

    # Position count histogram (log scale)
    df_plot = df.filter(pl.col("n_positions") >= 3)
    _fig = px.histogram(
        df_plot.to_pandas(),
        x="n_positions",
        nbins=100,
        title="Positions per Trader (>=3 positions, non-gambling, entry<0.90)",
        labels={"n_positions": "# Resolved Positions"},
        log_y=True,
    )
    mo.ui.plotly(_fig)
    return df, px


@app.cell
def hit_rate_distribution(df, mo, px):
    mo.md(
        """
    ## 8. Hit Rate Distribution

    Core question: Is there a meaningful right tail of high-hit-rate traders,
    or is it all noise from small samples?

    Showing traders with >= 10 resolved positions to reduce noise.
    """
    )

    hr_df = df.filter(pl.col("n_positions") >= 10)

    mo.md(
        f"""
    **{len(hr_df):,} traders** with >= 10 filtered resolved positions.
    - Mean hit rate: {hr_df['hit_rate'].mean():.4f}
    - Median hit rate: {hr_df['hit_rate'].median():.4f}
    - Std hit rate: {hr_df['hit_rate'].std():.4f}
    """
    )

    fig_hist = px.histogram(
        hr_df.to_pandas(),
        x="hit_rate",
        nbins=50,
        title="Hit Rate Distribution (>= 10 positions)",
        labels={"hit_rate": "Hit Rate", "count": "Traders"},
    )
    fig_hist.add_vline(x=0.50, line_dash="dash", annotation_text="50% baseline")
    mo.ui.plotly(fig_hist)
    return (hr_df,)


@app.cell
def hr_bucket_analysis(hr_df, mo, px):
    mo.md(
        """
    ### 8a. Hit Rate Buckets vs PnL

    Does higher hit rate actually translate to higher PnL?
    """
    )

    hr_binned = hr_df.with_columns(
        pl.when(pl.col("hit_rate") < 0.1).then(pl.lit("0.0-0.1"))
        .when(pl.col("hit_rate") < 0.2).then(pl.lit("0.1-0.2"))
        .when(pl.col("hit_rate") < 0.3).then(pl.lit("0.2-0.3"))
        .when(pl.col("hit_rate") < 0.4).then(pl.lit("0.3-0.4"))
        .when(pl.col("hit_rate") < 0.5).then(pl.lit("0.4-0.5"))
        .when(pl.col("hit_rate") < 0.6).then(pl.lit("0.5-0.6"))
        .when(pl.col("hit_rate") < 0.7).then(pl.lit("0.6-0.7"))
        .when(pl.col("hit_rate") < 0.8).then(pl.lit("0.7-0.8"))
        .when(pl.col("hit_rate") < 0.9).then(pl.lit("0.8-0.9"))
        .otherwise(pl.lit("0.9-1.0"))
        .alias("hr_bucket")
    )

    bucket_stats = hr_binned.group_by("hr_bucket").agg(
        pl.count().alias("traders"),
        pl.col("n_positions").sum().alias("total_positions"),
        pl.col("total_pnl").mean().alias("avg_total_pnl"),
        pl.col("avg_pnl").mean().alias("avg_per_position_pnl"),
        pl.col("total_volume").mean().alias("avg_volume"),
    ).sort("hr_bucket")

    mo.ui.table(bucket_stats)

    fig_bar = px.bar(
        bucket_stats.to_pandas(),
        x="hr_bucket",
        y="avg_per_position_pnl",
        title="Average PnL Per Position by Hit Rate Bucket (>=10 pos)",
        labels={"hr_bucket": "Hit Rate Bucket", "avg_per_position_pnl": "Avg PnL ($)"},
        color="avg_per_position_pnl",
        color_continuous_scale="RdYlGn",
    )
    mo.ui.plotly(fig_bar)

    mo.md(
        """
    **Strong monotonic relationship**: Higher hit rate -> higher avg PnL.
    - Bottom decile (0.0-0.1): -$2,449 avg PnL per trader
    - 0.5-0.6 decile: +$1,015 avg PnL -- the crossover to profitability
    - Top decile (0.9-1.0): +$14,795 avg PnL (but only 772 traders, small N risk)
    - The 1.0 bucket (126 traders, 100% HR): +$401K avg PnL -- extremely concentrated
    """
    )
    return


@app.cell
def mvf_analysis(mo):
    mo.md(
        """
    ## 9. Maker Volume Fraction (MVF) vs Hit Rate

    Does the maker/taker profile predict hit rate? Pure takers are directional
    traders; pure makers provide liquidity. A priori, we might expect takers
    to have more directional signal.

    Data from `data/derived/trader_volumes.parquet` joined to filtered features.
    """
    )

    feat = pl.read_parquet(f"{DERIVED}/trader_features_filtered.parquet")
    vol = pl.read_parquet(f"{DERIVED}/trader_volumes.parquet")

    joined = feat.filter(pl.col("n_positions") >= 10).join(
        vol.with_columns(
            (pl.col("maker_vol") / (pl.col("maker_vol") + pl.col("taker_vol"))).alias("mvf")
        ).filter(pl.col("maker_vol") + pl.col("taker_vol") > 0),
        on="trader",
        how="inner",
    )

    mvf_buckets = joined.with_columns(
        pl.when(pl.col("mvf") < 0.1).then(pl.lit("a_pure_taker"))
        .when(pl.col("mvf") < 0.3).then(pl.lit("b_mostly_taker"))
        .when(pl.col("mvf") < 0.5).then(pl.lit("c_mixed_taker"))
        .when(pl.col("mvf") < 0.7).then(pl.lit("d_mixed_maker"))
        .when(pl.col("mvf") < 0.9).then(pl.lit("e_mostly_maker"))
        .otherwise(pl.lit("f_pure_maker"))
        .alias("mvf_bucket")
    ).group_by("mvf_bucket").agg(
        pl.count().alias("traders"),
        pl.col("hit_rate").mean().alias("avg_hr"),
        pl.col("total_pnl").mean().alias("avg_pnl"),
        pl.col("n_positions").mean().alias("avg_pos"),
    ).sort("mvf_bucket")

    mo.ui.table(mvf_buckets)

    mo.md(
        """
    **Key finding**: Hit rate increases monotonically with MVF.
    - Pure takers (MVF < 0.1): 25.8% HR, -$1,499 avg PnL -- worst performers
    - Mixed makers (0.5-0.7): 43.2% HR, -$1,331 avg PnL
    - Mostly makers (0.7-0.9): 45.0% HR, +$2,939 avg PnL -- **best PnL bucket**

    **Surprising**: Takers do NOT have better directional signal. Makers who
    occasionally take directional positions show the highest skill.

    **Explanation**: Pure takers include many retail traders making uninformed bets.
    Makers who take directional positions are likely doing so based on genuine
    information, since their default behavior is market-making.
    """
    )
    return


@app.cell
def walk_forward_core(mo):
    mo.md(
        """
    ## 10. Walk-Forward Stability Test (Key Result)

    **The critical question**: Does historical hit rate predict future hit rate?

    **Design**: Train on all resolved positions before 2025-07-01 (min 20 positions).
    Test on resolved positions >= 2025-07-01 (min 10 positions).
    Both periods filtered: non-gambling, entry < 0.90, directional only.
    """
    )

    wf = ch_query("""
    WITH
    p1 AS (
        SELECT lower(p.trader) as trader,
               count() as n_pos_p1, sum(toUInt32(p.correct)) / count() as hr_p1,
               sum(p.realized_pnl) as pnl_p1
        FROM trader_positions_resolved p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        WHERE p.position IN ('YES', 'NO')
          AND NOT (m.question LIKE '%Up or Down%')
          AND CASE WHEN p.position = 'YES' THEN p.avg_yes_price
                   ELSE 1.0 - p.avg_yes_price END < 0.90
          AND p.resolved_at < '2025-07-01'
        GROUP BY lower(p.trader)
        HAVING count() >= 20
    ),
    p2 AS (
        SELECT lower(p.trader) as trader,
               count() as n_pos_p2, sum(toUInt32(p.correct)) / count() as hr_p2,
               sum(p.realized_pnl) as pnl_p2,
               avg(p.realized_pnl) as avg_pnl_p2
        FROM trader_positions_resolved p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        WHERE p.position IN ('YES', 'NO')
          AND NOT (m.question LIKE '%Up or Down%')
          AND CASE WHEN p.position = 'YES' THEN p.avg_yes_price
                   ELSE 1.0 - p.avg_yes_price END < 0.90
          AND p.resolved_at >= '2025-07-01'
        GROUP BY lower(p.trader)
        HAVING count() >= 10
    )
    SELECT
        CASE
            WHEN p1.hr_p1 < 0.10 THEN 'a_00-10'
            WHEN p1.hr_p1 < 0.20 THEN 'b_10-20'
            WHEN p1.hr_p1 < 0.30 THEN 'c_20-30'
            WHEN p1.hr_p1 < 0.40 THEN 'd_30-40'
            WHEN p1.hr_p1 < 0.50 THEN 'e_40-50'
            WHEN p1.hr_p1 < 0.60 THEN 'f_50-60'
            WHEN p1.hr_p1 < 0.70 THEN 'g_60-70'
            WHEN p1.hr_p1 < 0.80 THEN 'h_70-80'
            ELSE 'i_80+'
        END as train_decile,
        count() as traders,
        round(avg(p1.hr_p1), 4) as train_hr,
        round(avg(p2.hr_p2), 4) as test_hr,
        round(avg(p2.hr_p2) - avg(p1.hr_p1), 4) as decay,
        round(avg(p1.n_pos_p1), 1) as train_pos,
        round(avg(p2.n_pos_p2), 1) as test_pos,
        round(avg(p2.avg_pnl_p2), 4) as test_avg_pnl,
        round(sum(p2.pnl_p2), 2) as test_total_pnl,
        sum(p2.n_pos_p2) as test_total_pos
    FROM p1 INNER JOIN p2 ON p1.trader = p2.trader
    GROUP BY train_decile
    ORDER BY train_decile
    """)

    mo.ui.table(wf)

    # Bar chart: train HR vs test HR
    wf_pd = wf.to_pandas()
    import plotly.graph_objects as go
    _fig = go.Figure()
    _fig.add_trace(go.Bar(name="Train HR", x=wf_pd["train_decile"], y=wf_pd["train_hr"],
                         marker_color="steelblue"))
    _fig.add_trace(go.Bar(name="Test HR", x=wf_pd["train_decile"], y=wf_pd["test_hr"],
                         marker_color="coral"))
    _fig.update_layout(title="Walk-Forward: Train HR vs Out-of-Sample Test HR",
                      barmode="group", yaxis_title="Hit Rate", xaxis_title="Training Decile")
    mo.ui.plotly(_fig)

    mo.md(
        """
    **CRITICAL RESULT -- Hit rate is persistent but decays toward the mean**:

    | Train Decile | Train HR | OOS HR | Decay | OOS PnL/Position |
    |:---|:---:|:---:|:---:|:---:|
    | Bottom (0-10%) | 5.1% | 12.0% | +6.9pp | -$60.11 |
    | 40-50% | 44.5% | 42.3% | -2.2pp | -$44.14 |
    | **50-60%** | **54.3%** | **50.0%** | **-4.3pp** | **+$36.48** |
    | 60-70% | 64.1% | 56.1% | -8.0pp | +$36.80 |
    | 70-80% | 74.1% | 66.3% | -7.8pp | +$49.94 |
    | 80%+ | 83.7% | 68.4% | -15.3pp | +$87.59 |

    **Top half (train HR >= 50%)**: 1,434 traders, 296K OOS positions, **+$2.4M OOS PnL** (+$8.18/pos)
    **Bottom half (train HR < 50%)**: 4,404 traders, 891K OOS positions, **-$30.2M OOS PnL** (-$33.93/pos)

    The **$41/position spread** between top half and bottom half is the copy-trading edge.
    Even after regression to the mean, top-decile traders maintain OOS HR > 50%.
    """
    )
    return


@app.cell
def quarterly_stability(mo):
    mo.md(
        """
    ## 11. Quarterly Walk-Forward Stability

    Does the signal persist across different time periods?
    Train on quarter Q, test on quarter Q+1.
    """
    )

    _rows = []
    quarters = [
        ('2024-07-01', '2024-10-01', '2025-01-01', 'Q3-24->Q4'),
        ('2024-10-01', '2025-01-01', '2025-04-01', 'Q4-24->Q1'),
        ('2025-01-01', '2025-04-01', '2025-07-01', 'Q1-25->Q2'),
        ('2025-04-01', '2025-07-01', '2025-10-01', 'Q2-25->Q3'),
        ('2025-07-01', '2025-10-01', '2026-01-01', 'Q3-25->Q4'),
        ('2025-10-01', '2026-01-01', '2026-04-01', 'Q4-25->Q1'),
    ]

    for train_start, train_end, test_end, label in quarters:
        _r = ch_query(f"""
        WITH
        p1 AS (
            SELECT lower(p.trader) as trader,
                   count() as n, sum(toUInt32(p.correct)) / count() as hr
            FROM trader_positions_resolved p
            INNER JOIN markets m ON p.condition_id = m.condition_id
            WHERE p.position IN ('YES', 'NO')
              AND NOT (m.question LIKE '%Up or Down%')
              AND CASE WHEN p.position = 'YES' THEN p.avg_yes_price
                       ELSE 1.0 - p.avg_yes_price END < 0.90
              AND p.resolved_at >= '{train_start}' AND p.resolved_at < '{train_end}'
            GROUP BY lower(p.trader) HAVING count() >= 20
        ),
        p2 AS (
            SELECT lower(p.trader) as trader,
                   count() as n, sum(toUInt32(p.correct)) / count() as hr,
                   sum(p.realized_pnl) as pnl
            FROM trader_positions_resolved p
            INNER JOIN markets m ON p.condition_id = m.condition_id
            WHERE p.position IN ('YES', 'NO')
              AND NOT (m.question LIKE '%Up or Down%')
              AND CASE WHEN p.position = 'YES' THEN p.avg_yes_price
                       ELSE 1.0 - p.avg_yes_price END < 0.90
              AND p.resolved_at >= '{train_end}' AND p.resolved_at < '{test_end}'
            GROUP BY lower(p.trader) HAVING count() >= 5
        )
        SELECT
            count() as traders,
            round(avg(CASE WHEN p1.hr >= 0.50 THEN p1.hr END), 4) as top_train,
            round(avg(CASE WHEN p1.hr >= 0.50 THEN p2.hr END), 4) as top_test,
            round(avg(CASE WHEN p1.hr < 0.30 THEN p1.hr END), 4) as bot_train,
            round(avg(CASE WHEN p1.hr < 0.30 THEN p2.hr END), 4) as bot_test,
            round(sum(CASE WHEN p1.hr >= 0.50 THEN p2.pnl ELSE 0 END), 2) as top_pnl
        FROM p1 INNER JOIN p2 ON p1.trader = p2.trader
        """)
        _row = _r.to_dicts()[0] if len(_r) > 0 else {}
        top_train = float(_row.get('top_train') or 0)
        top_test = float(_row.get('top_test') or 0)
        bot_train = float(_row.get('bot_train') or 0)
        bot_test = float(_row.get('bot_test') or 0)
        _rows.append({
            "period": label,
            "traders": _row.get('traders', 0),
            "top_train_hr": top_train,
            "top_test_hr": top_test,
            "top_decay": round(top_test - top_train, 4),
            "bot_train_hr": bot_train,
            "bot_test_hr": bot_test,
            "top_oos_pnl": float(_row.get('top_pnl') or 0),
        })

    qdf = pl.DataFrame(_rows)
    mo.ui.table(qdf)

    mo.md(
        """
    **Signal is persistent across all 6 quarters tested**:
    - Top-half train HR consistently decays 5-10pp but stays above 50%
    - Bottom train HR stays well below 20% out-of-sample
    - Top half OOS PnL is positive in 4/6 quarters
    - The decay magnitude is ~5-10pp -- substantial but still leaves a tradeable spread
    """
    )
    return


@app.cell
def position_size_analysis(mo):
    mo.md(
        """
    ## 12. Position Size vs Hit Rate

    Do larger positions correlate with higher hit rates?
    (Conviction-weighted skill hypothesis)
    """
    )

    size_df = ch_query("""
    SELECT
        CASE
            WHEN market_volume < 10 THEN 'a_tiny (<$10)'
            WHEN market_volume < 50 THEN 'b_small ($10-50)'
            WHEN market_volume < 200 THEN 'c_medium ($50-200)'
            WHEN market_volume < 1000 THEN 'd_large ($200-1K)'
            WHEN market_volume < 10000 THEN 'e_whale ($1K-10K)'
            ELSE 'f_mega (>$10K)'
        END as size_bucket,
        count() as positions,
        round(sum(toUInt32(correct)) / count(), 4) as hit_rate,
        round(avg(realized_pnl), 4) as avg_pnl,
        round(sum(realized_pnl), 2) as total_pnl
    FROM trader_positions_resolved p
    INNER JOIN markets m ON p.condition_id = m.condition_id
    WHERE p.position IN ('YES', 'NO')
      AND NOT (m.question LIKE '%Up or Down%')
      AND CASE WHEN p.position = 'YES' THEN p.avg_yes_price
               ELSE 1.0 - p.avg_yes_price END < 0.90
    GROUP BY size_bucket
    ORDER BY size_bucket
    """)

    mo.ui.table(size_df)

    mo.md(
        """
    **Hit rate increases with position size**:
    - Tiny (<$10): 20.0% HR -- many are dust/test positions
    - Small ($10-50): 37.9% HR
    - Medium ($50-200): 40.4% HR
    - Large ($200-1K): 40.7% HR
    - Whale ($1K-10K): 41.6% HR
    - Mega (>$10K): 43.7% HR

    Larger positions show slightly higher accuracy, but all sizes are net negative PnL.
    The house always wins on average -- strategy must select skilled subsets.
    """
    )
    return


@app.cell
def feature_brainstorm(mo):
    mo.md("""
    ## 13. Feature Brainstorm

    ### Tier 1: Core Signal (must have)
    | Feature | Description | Why |
    |---------|-------------|-----|
    | **hit_rate** | wins / total resolved | Primary signal (validated in walk-forward) |
    | **avg_edge** | mean(realized_pnl) | Captures accuracy AND sizing |
    | **n_resolved** | Total filtered positions | Sample size for significance |
    | **n_markets** | Distinct markets | Diversity proxy |

    ### Tier 2: Profile / Filter
    | Feature | Description | Why |
    |---------|-------------|-----|
    | **mvf** | maker_vol / total_vol | Makers-who-take are most skilled |
    | **avg_dir_entry** | Mean directional entry price | Safe-bet detection |
    | **direction_bias** | % YES vs NO positions | Structural vs skill |

    ### Tier 3: Temporal / Stability
    | Feature | Description | Why |
    |---------|-------------|-----|
    | **quarterly_hr_std** | Std of per-quarter hit rates | Consistent > lucky |
    | **recency_weighted_hr** | EWM hit rate | Recent performance matters |
    | **active_quarters** | N quarters with trades | Longevity |

    ### Tier 4: PnL / Risk
    | Feature | Description | Why |
    |---------|-------------|-----|
    | **pnl_per_dollar** | total_pnl / total_volume | Capital efficiency |
    | **profit_factor** | sum(wins) / sum(losses) | Win/loss asymmetry |

    ### Tier 5: Interaction
    | Feature | Description | Why |
    |---------|-------------|-----|
    | **market_overlap** | Cosine similarity with other top traders | Crowding |
    | **consensus_agreement** | Fraction agreeing with majority | Contrarian signal |
    """)
    return


@app.cell
def strategy_design(mo):
    mo.md("""
    ## 14. Walk-Forward Strategy Design

    ### Signal Construction (no lookahead)
    At time T, for each trader:
    1. Collect all resolved positions where `resolved_at < T`
    2. Apply filters: non-gambling, entry < 0.90, directional
    3. Require minimum N resolved positions
    4. Compute hit_rate, avg_edge, n_positions
    5. Rank traders; select top decile as "copy pool"

    ### Entry Signal
    When a copy-pool trader makes a new trade:
    1. Copy direction (BUY YES or BUY NO)
    2. Fixed size $10 per copy trade (or edge-proportional)
    3. max_price = current_price + slippage_buffer
    4. Hold until resolution

    ### Parameters to Sweep
    | Parameter | Range | Default |
    |-----------|-------|---------|
    | min_positions | 10, 20, 50 | 20 |
    | min_hit_rate | 0.50, 0.55, 0.60, 0.65 | 0.55 |
    | safe_bet_threshold | 0.85, 0.90 | 0.90 |
    | mvf_filter | none, <0.5, >0.5 | none |
    | retrain_freq | 30d, 90d, expanding | expanding |
    | position_size | $10, $25, edge-proportional | $10 |

    ### Evaluation
    - OOS hit rate vs base rate (binomial test, p < 0.05)
    - EV per trade after RealisticFillSimulator slippage
    - Sharpe ratio, max drawdown, profit factor
    - Min 1,000 OOS trades for statistical power
    """)
    return


@app.cell
def summary(mo):
    mo.md("""
    ## 15. Summary and Key Conclusions

    ### Validated findings on 434M trades, 32M resolved positions, 3+ years:

    1. **Base rates**: 40.2% YES-won, 59.8% NO-won across 393K markets.

    2. **Crypto gambling dominates**: "Up or Down" = 98K markets, 48% of directional
       positions. Must be filtered. Regex `LIKE '%Up or Down%'` is sufficient.

    3. **Hit rate is persistent (THE KEY FINDING)**:
       - Train decile HR predicts OOS HR monotonically
       - Top-half traders (train HR >= 50%): +$8.18/pos OOS, +$2.4M total
       - Bottom-half traders: -$33.93/pos OOS, -$30.2M total
       - Spread of ~$42/pos is the exploitable edge
       - Signal decays 5-10pp toward the mean but remains > 50% for top decile

    4. **Quarterly stability**: Signal persists across all 6 quarters tested
       (Q3-2024 through Q1-2026). Not a one-time artifact.

    5. **MVF matters inversely to expectation**: Makers-who-take (MVF 0.5-0.9) show
       the highest skill. Pure takers (MVF < 0.1) have the worst hit rates.

    6. **Position size weakly predicts**: Larger positions show ~4pp higher HR
       (20% for <$10 vs 44% for >$10K), but all sizes are net negative on average.

    7. **Data quality**: 125K address case duplicates (5.6%). `resolved_at` is NULL
       for 41.5% of positions. Category field empty for recent markets.

    ### Strategy decision table

    | Decision | Choice | Evidence |
    |----------|--------|----------|
    | Safe-bet threshold | 0.90 | 25% of non-gambling positions are safe bets |
    | Gambling filter | Exclude "Up or Down" | 48% of data, near-zero signal |
    | Address handling | lower() all | 125K duplicates |
    | Min train positions | 20 | Sufficient for walk-forward signal |
    | Min train HR | 0.50-0.55 | OOS crossover to +PnL at 0.50 |
    | MVF filter | Consider MVF > 0.3 | Pure takers have worst HR |
    | Position sizing | Start $10 fixed | Simple, revisit with Kelly later |

    ### Next steps
    1. Implement walk-forward backtester using Strategy protocol
    2. Use `research/harness.py` with `RealisticFillSimulator` for slippage
    3. Statistical significance tests (binomial, bootstrap CI)
    4. Parameter sweep across min_positions, min_hr, retrain_freq
    5. Explore combining hit_rate with avg_edge and MVF as multi-feature signal
    """)
    return


if __name__ == "__main__":
    app.run()
