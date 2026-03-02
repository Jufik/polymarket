"""S2 Insider Copy — Data Exploration & Vectorized Discovery.

Marimo notebook for Phase 1-2 of the insider copy strategy research.
Run: uv run marimo edit research/notebooks/S2_insider_exploration.py
"""
import marimo

__generated_with = "0.10.0"
app = marimo.App(width="full")


@app.cell
def imports():
    import marimo as mo
    import polars as pl
    import clickhouse_connect

    return mo, pl, clickhouse_connect


@app.cell
def connect(clickhouse_connect):
    """Connect to remote ClickHouse."""
    ch = clickhouse_connect.get_client(
        host="192.168.0.148", port=18123, database="polymarket"
    )
    # Verify connection
    row_count = ch.query("SELECT count() FROM trades_raw").result_rows[0][0]
    print(f"Connected. trades_raw: {row_count:,} rows")
    return (ch,)


@app.cell
def market_classification(ch, pl, mo):
    """Stage 1: Classify markets by insider susceptibility."""
    df = pl.from_pandas(ch.query_df("""
        SELECT
            m.condition_id,
            m.question,
            m.category,
            multiIf(
                m.question LIKE '%Up or Down%' OR m.question LIKE '%up or down%'
                    OR m.question LIKE '%coin flip%'
                    OR m.question LIKE '%5-min%' OR m.question LIKE '%15-min%',
                'LOW',
                m.question LIKE '%SEC %' OR m.question LIKE '%FDA %'
                    OR m.question LIKE '%regulat%' OR m.question LIKE '%approv%'
                    OR m.question LIKE '%election%' OR m.question LIKE '%president%'
                    OR m.question LIKE '%indict%' OR m.question LIKE '%verdict%',
                'HIGH',
                m.category IN ('Politics', 'Government', 'Legal', 'Regulatory'),
                'HIGH',
                m.category IN ('Sports', 'Entertainment', 'Esports'),
                'MEDIUM',
                'MEDIUM'
            ) AS susceptibility
        FROM markets AS m
        WHERE m.resolution_value = 1
    """))

    tier_counts = df.group_by("susceptibility").len().sort("len", descending=True)
    mo.md(f"""## Market Susceptibility Distribution
    {tier_counts}

    Total resolved markets: {len(df):,}
    """)
    return (df,)


@app.cell
def insider_pool_query(ch, pl, mo):
    """Stage 2: Compute insider scores from ClickHouse."""
    # Read the SQL query from file
    from pathlib import Path
    sql = Path("research/knowledge/queries/insider_pool.sql").read_text()

    # Replace parameters
    lookback = 6
    min_pos = 3
    sql_exec = sql.replace("{lookback_months:UInt32}", str(lookback))
    sql_exec = sql_exec.replace("{min_positions:UInt32}", str(min_pos))

    pool_df = pl.from_pandas(ch.query_df(sql_exec))
    mo.md(f"""## Insider Pool (lookback={lookback}mo, min_positions={min_pos})
    Traders found: {len(pool_df):,}
    """)
    print(pool_df.head(20))
    return pool_df, lookback, min_pos


@app.cell
def score_distribution(pool_df, pl, mo):
    """Analyze insider score distribution."""
    from research.strategies.s2_insider_copy import compute_insider_scores

    # Prepare input DataFrame
    stats = pool_df.rename({
        "avg_realized_pnl": "avg_timing_edge",
    })
    scored = compute_insider_scores(stats)

    mo.md(f"""## Insider Score Distribution
    - Mean: {scored['insider_score'].mean():.4f}
    - Median: {scored['insider_score'].median():.4f}
    - P95: {scored['insider_score'].quantile(0.95):.4f}
    - P99: {scored['insider_score'].quantile(0.99):.4f}
    """)

    # Top 50 insiders
    top50 = scored.sort("insider_score", descending=True).head(50)
    print(top50.select([
        "trader", "insider_score", "effective_hr", "best_direction",
        "total_positions", "avg_position_usd", "markets_per_month",
        "high_market_ratio", "total_pnl",
    ]))
    return scored, top50


@app.cell
def sanity_check(top50, ch, pl, mo):
    """Sanity check: inspect top insiders' actual trades."""
    top_traders = top50["trader"].head(5).to_list()
    traders_sql = ", ".join(f"'{t}'" for t in top_traders)

    trades_df = pl.from_pandas(ch.query_df(f"""
        SELECT
            lower(p.trader) AS trader,
            p.condition_id,
            p.position,
            p.correct,
            p.realized_pnl,
            p.volume AS market_volume,
            m.question
        FROM (SELECT * FROM trader_positions_resolved) AS p
        INNER JOIN markets AS m ON p.condition_id = m.condition_id
        WHERE lower(p.trader) IN ({traders_sql})
          AND p.position IN ('YES', 'NO')
        ORDER BY p.trader, p.resolved_at
    """))

    for trader in top_traders:
        subset = trades_df.filter(pl.col("trader") == trader)
        mo.md(f"### Trader: `{trader[:10]}...`")
        print(subset.select([
            "question", "position", "correct", "realized_pnl", "market_volume",
        ]))
    return (trades_df,)


@app.cell
def parameter_sweep_placeholder(scored, mo):
    """Phase 2: Parameter sweep (vectorized upper bound).

    TODO: After manual review of insider pool, implement:
    1. Build insider pool at various score thresholds
    2. Replay trades and count insider copy opportunities
    3. Compute hit rate, edge, trade frequency at each threshold
    4. Compare single vs consensus triggers
    """
    mo.md("""## Phase 2: Vectorized Parameter Sweep
    _Run this cell after reviewing the insider pool above._
    """)
    return ()


if __name__ == "__main__":
    app.run()
