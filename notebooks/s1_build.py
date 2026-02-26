import marimo

__generated_with = "0.20.2"
app = marimo.App(
    width="full",
    app_title="S1 Build — One-off Table Construction",
)


@app.cell
def imports():
    import marimo as mo
    import clickhouse_connect
    import time as time_mod

    return clickhouse_connect, mo, time_mod


@app.cell
def connect(clickhouse_connect, mo):
    CH_HOST = "192.168.0.148"
    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=18123, database="polymarket"
    )
    _ver = ch.query("SELECT version()").first_row[0]
    mo.md(f"**ClickHouse** {_ver} at `{CH_HOST}`")
    return (ch,)


# ═══════════════════════════════════════════════════════════════════════
#  OVERVIEW — Quick stats from raw data
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def overview(ch, mo):
    _r = ch.query("""
        SELECT
            count()            AS trades,
            uniq(condition_id) AS markets,
            uniq(maker)        AS makers,
            uniq(taker)        AS takers,
            min(timestamp)     AS t_min,
            max(timestamp)     AS t_max
        FROM trades_raw
    """).first_row

    _tk = ch.query("""
        SELECT tk.outcome, count()
        FROM trades_raw t
        JOIN token_market_map tk ON t.asset_id = tk.asset_id
        GROUP BY tk.outcome ORDER BY tk.outcome
    """).result_rows

    _res = ch.query("""
        SELECT
            count()          AS resolved,
            countIf(t.yes_won) AS yes_won
        FROM markets m
        LEFT JOIN (
            SELECT condition_id, true AS yes_won
            FROM token_market_map
            WHERE outcome = 'YES' AND winner = true
        ) t ON m.condition_id = t.condition_id
        WHERE m.resolution_value = 1
    """).first_row

    mo.md(f"""
    ## Raw Data Overview

    | | |
    |---|---|
    | **Trades** | {_r[0]:,} |
    | **Markets** | {_r[1]:,} |
    | **Makers / Takers** | {_r[2]:,} / {_r[3]:,} |
    | **Period** | `{_r[4]}` → `{_r[5]}` |

    **Token split:** YES = {_tk[1][1]:,} trades, NO = {_tk[0][1]:,} trades

    **Resolution:** {_res[0]:,} resolved markets —
    YES won {_res[1]:,} ({_res[1]/_res[0]:.1%}),
    NO won {_res[0]-_res[1]:,} ({(_res[0]-_res[1])/_res[0]:.1%})
    """)
    return


# ═══════════════════════════════════════════════════════════════════════
#  BUILD — _tmp_s1_positions
#
#  This is the heavy operation (~5 min).
#  Creates a (trader, condition_id) position table with granular flows.
#  Run once; the explorer notebook reads from it.
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def build_explain(mo):
    mo.md("""
    ## Build: `_tmp_s1_positions`

    Aggregates every trade into (trader, market) positions with granular flows.

    **What it computes per (trader, market):**

    | Column | Meaning |
    |--------|---------|
    | `yes_bought` / `yes_bought_vwap` | YES tokens acquired + avg price paid |
    | `yes_sold` / `yes_sold_vwap` | YES tokens disposed + avg price received |
    | `no_bought` / `no_bought_vwap` | NO tokens acquired + avg price paid |
    | `no_sold` / `no_sold_vwap` | NO tokens disposed + avg price received |
    | `net_yes` / `net_no` | bought - sold (net direction) |
    | `wavg_yes_price` | Volume-weighted implied YES probability |
    | `volume` / `n_trades` | Total USD volume + trade count |

    **Logic:**
    - Each trade → 2 participant rows (maker + taker, opposite directions)
    - `side = BUY` from maker perspective → taker is selling
    - VWAP = sum(price * size) / sum(size) per direction
    - Only resolved markets included

    See `notebooks/sql/s1_01_positions.sql` for full SQL.
    """)
    return


@app.cell
def build_sql():
    AGG_SQL = """
    CREATE TABLE IF NOT EXISTS _tmp_s1_positions
    ENGINE = MergeTree()
    ORDER BY (trader, condition_id)
    AS
    WITH
    token_info AS (
        SELECT asset_id, condition_id,
               outcome = 'YES' AS is_yes
        FROM token_market_map
    ),
    resolved AS (
        SELECT condition_id
        FROM markets
        WHERE resolution_value = 1
    ),
    enriched AS (
        SELECT
            t.condition_id, t.side, t.price, t.size,
            t.amount_usd, t.maker, t.taker,
            ti.is_yes
        FROM trades_raw t
        JOIN token_info ti ON t.asset_id = ti.asset_id
        WHERE t.condition_id IN (SELECT condition_id FROM resolved)
    ),
    participants AS (
        SELECT
            assumeNotNull(maker)                              AS trader,
            condition_id,
            if(is_yes AND side='BUY',  size, 0)              AS yes_buy_qty,
            if(is_yes AND side='BUY',  price * size, 0)      AS yes_buy_cost,
            if(is_yes AND side='SELL', size, 0)              AS yes_sell_qty,
            if(is_yes AND side='SELL', price * size, 0)      AS yes_sell_cost,
            if(NOT is_yes AND side='BUY',  size, 0)          AS no_buy_qty,
            if(NOT is_yes AND side='BUY',  price * size, 0)  AS no_buy_cost,
            if(NOT is_yes AND side='SELL', size, 0)          AS no_sell_qty,
            if(NOT is_yes AND side='SELL', price * size, 0)  AS no_sell_cost,
            amount_usd,
            if(is_yes, price, 1.0 - price) * amount_usd     AS yes_px_vol
        FROM enriched
        WHERE maker IS NOT NULL AND maker != ''

        UNION ALL

        SELECT
            assumeNotNull(taker),
            condition_id,
            if(is_yes AND side='SELL', size, 0),
            if(is_yes AND side='SELL', price * size, 0),
            if(is_yes AND side='BUY',  size, 0),
            if(is_yes AND side='BUY',  price * size, 0),
            if(NOT is_yes AND side='SELL', size, 0),
            if(NOT is_yes AND side='SELL', price * size, 0),
            if(NOT is_yes AND side='BUY',  size, 0),
            if(NOT is_yes AND side='BUY',  price * size, 0),
            amount_usd,
            if(is_yes, price, 1.0 - price) * amount_usd
        FROM enriched
        WHERE taker IS NOT NULL AND taker != ''
    )

    SELECT
        trader,
        condition_id,
        sum(yes_buy_qty)                                     AS yes_bought,
        sum(yes_buy_cost) / nullIf(sum(yes_buy_qty), 0)     AS yes_bought_vwap,
        sum(yes_sell_qty)                                    AS yes_sold,
        sum(yes_sell_cost) / nullIf(sum(yes_sell_qty), 0)   AS yes_sold_vwap,
        sum(no_buy_qty)                                      AS no_bought,
        sum(no_buy_cost) / nullIf(sum(no_buy_qty), 0)       AS no_bought_vwap,
        sum(no_sell_qty)                                     AS no_sold,
        sum(no_sell_cost) / nullIf(sum(no_sell_qty), 0)     AS no_sold_vwap,
        sum(yes_buy_qty) - sum(yes_sell_qty)                 AS net_yes,
        sum(no_buy_qty) - sum(no_sell_qty)                   AS net_no,
        sum(yes_px_vol) / nullIf(sum(amount_usd), 0)        AS wavg_yes_price,
        sum(amount_usd)                                      AS volume,
        toUInt32(count())                                    AS n_trades
    FROM participants
    GROUP BY trader, condition_id
    """
    return (AGG_SQL,)


@app.cell
def build_run(AGG_SQL, ch, mo, time_mod):
    """Check table status, build or report cached."""
    _n = 0
    _schema_ok = False
    try:
        _n = ch.query("SELECT count() FROM _tmp_s1_positions").first_row[0]
        if _n > 0:
            _cols = {
                r[0] for r in ch.query(
                    "SELECT name FROM system.columns "
                    "WHERE database = 'polymarket' AND table = '_tmp_s1_positions'"
                ).result_rows
            }
            _schema_ok = "yes_bought" in _cols
    except Exception:
        pass

    if _n > 0 and _schema_ok:
        _s = ch.query("""
            SELECT
                count()            AS total,
                uniq(trader)       AS traders,
                uniq(condition_id) AS markets,
                countIf(net_yes > 0.01 AND net_no <= 0.01) AS long_yes,
                countIf(net_no > 0.01 AND net_yes <= 0.01) AS long_no,
                countIf(net_yes > 0.01 AND net_no > 0.01)  AS hedged,
                countIf(net_yes <= 0.01 AND net_no <= 0.01) AS closed
            FROM _tmp_s1_positions
        """).first_row
        _result = mo.md(
            f"### `_tmp_s1_positions` — already built\n\n"
            f"**{_s[0]:,}** positions ({_s[1]:,} traders, {_s[2]:,} markets)\n\n"
            f"| Position | Count | % |\n|---|---|---|\n"
            f"| LONG YES | {_s[3]:,} | {_s[3]/_s[0]:.1%} |\n"
            f"| LONG NO | {_s[4]:,} | {_s[4]/_s[0]:.1%} |\n"
            f"| HEDGED | {_s[5]:,} | {_s[5]/_s[0]:.1%} |\n"
            f"| CLOSED | {_s[6]:,} | {_s[6]/_s[0]:.1%} |\n\n"
            f"To rebuild: `DROP TABLE _tmp_s1_positions` then re-run."
        )
    elif _n > 0 and not _schema_ok:
        ch.command("DROP TABLE IF EXISTS _tmp_s1_positions")
        _t0 = time_mod.time()
        ch.command(AGG_SQL)
        _elapsed = time_mod.time() - _t0
        _s = ch.query("SELECT count(), uniq(trader), uniq(condition_id) FROM _tmp_s1_positions").first_row
        _result = mo.md(
            f"### Rebuilt `_tmp_s1_positions` (schema upgrade)\n\n"
            f"**{_s[0]:,}** positions ({_s[1]:,} traders, {_s[2]:,} markets) "
            f"in **{_elapsed:.0f}s**"
        )
    else:
        _t0 = time_mod.time()
        ch.command(AGG_SQL)
        _elapsed = time_mod.time() - _t0
        _s = ch.query("SELECT count(), uniq(trader), uniq(condition_id) FROM _tmp_s1_positions").first_row
        _result = mo.md(
            f"### Built `_tmp_s1_positions`\n\n"
            f"**{_s[0]:,}** positions ({_s[1]:,} traders, {_s[2]:,} markets) "
            f"in **{_elapsed:.0f}s**"
        )
    _result
    return


# ═══════════════════════════════════════════════════════════════════════
#  VERIFY — Granular flow breakdown
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def verify(ch, mo):
    _r = ch.query("""
        SELECT
            countIf(yes_bought > 0.01) AS n_yes_bought,
            round(sum(yes_bought), 0) AS total_yes_bought,
            round(sum(yes_bought_vwap * yes_bought) / nullIf(sum(yes_bought), 0), 4) AS vwap_yes_bought,
            countIf(yes_sold > 0.01) AS n_yes_sold,
            round(sum(yes_sold), 0) AS total_yes_sold,
            round(sum(yes_sold_vwap * yes_sold) / nullIf(sum(yes_sold), 0), 4) AS vwap_yes_sold,
            countIf(no_bought > 0.01) AS n_no_bought,
            round(sum(no_bought), 0) AS total_no_bought,
            round(sum(no_bought_vwap * no_bought) / nullIf(sum(no_bought), 0), 4) AS vwap_no_bought,
            countIf(no_sold > 0.01) AS n_no_sold,
            round(sum(no_sold), 0) AS total_no_sold,
            round(sum(no_sold_vwap * no_sold) / nullIf(sum(no_sold), 0), 4) AS vwap_no_sold,
            count() AS total_positions
        FROM _tmp_s1_positions
    """).first_row

    mo.md(f"""
    ### Granular Flow Breakdown

    | Flow | Positions | Total Tokens | VWAP |
    |------|-----------|-------------|------|
    | **YES Bought** | {_r[0]:,} | {_r[1]:,.0f} | ${_r[2]:.4f} |
    | **YES Sold** | {_r[3]:,} | {_r[4]:,.0f} | ${_r[5]:.4f} |
    | **NO Bought** | {_r[6]:,} | {_r[7]:,.0f} | ${_r[8]:.4f} |
    | **NO Sold** | {_r[9]:,} | {_r[10]:,.0f} | ${_r[11]:.4f} |

    *{_r[12]:,} total positions.*

    Table is ready. Open **s1_pool_explorer.py** to explore.
    """)
    return


# ═══════════════════════════════════════════════════════════════════════
#  BUILD — _tmp_s1_enriched
#
#  Pre-materialized enriched positions for the explorer notebook.
#  Joins positions × resolution once, stores only needed columns.
#  Explorer reads this directly — no runtime JOIN, ~5× faster load.
# ═══════════════════════════════════════════════════════════════════════
@app.cell
def enriched_explain(mo):
    mo.md("""
    ## Build: `_tmp_s1_enriched`

    Pre-joins positions with resolution and stores only the columns the
    explorer actually uses. This avoids the expensive runtime JOIN.

    | Column | Source |
    |--------|--------|
    | `trader` | _tmp_s1_positions |
    | `position` | CASE on net_yes / net_no |
    | `dir_entry` | wavg_yes_price (or complement) |
    | `correct` | position vs yes_won |
    | `market_volume` | sum(amount_usd) per position |
    | `trade_count` | n_trades per position |
    | `resolved_at` | markets.resolved_at |
    | `month` | formatDateTime(resolved_at) |

    See `notebooks/sql/s1_03_enriched_positions.sql` for the full logic.
    """)
    return


@app.cell
def enriched_sql():
    ENRICHED_SQL = """
    CREATE TABLE IF NOT EXISTS _tmp_s1_enriched
    ENGINE = MergeTree()
    ORDER BY (resolved_at, trader)
    AS
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

        p.volume   AS market_volume,
        p.n_trades AS trade_count,
        assumeNotNull(r.resolved_at) AS resolved_at,
        formatDateTime(r.resolved_at, '%Y-%m') AS month

    FROM _tmp_s1_positions p
    JOIN (
        SELECT
            m.condition_id, m.resolved_at,
            coalesce(t.yes_won, false) AS yes_won
        FROM markets m
        LEFT JOIN (
            SELECT condition_id, true AS yes_won
            FROM token_market_map
            WHERE outcome = 'YES' AND winner = true
        ) t ON m.condition_id = t.condition_id
        WHERE m.resolution_value = 1
    ) r ON p.condition_id = r.condition_id

    WHERE NOT (p.net_yes <= 0.01 AND p.net_no <= 0.01)
    """
    return (ENRICHED_SQL,)


@app.cell
def enriched_run(ENRICHED_SQL, ch, mo, time_mod):
    """Build or report cached _tmp_s1_enriched."""
    _n = 0
    _ok = False
    try:
        _n = ch.query("SELECT count() FROM _tmp_s1_enriched").first_row[0]
        if _n > 0:
            _cols = {
                r[0] for r in ch.query(
                    "SELECT name FROM system.columns "
                    "WHERE database = 'polymarket' AND table = '_tmp_s1_enriched'"
                ).result_rows
            }
            _ok = "position" in _cols and "dir_entry" in _cols
    except Exception:
        pass

    if _n > 0 and _ok:
        _s = ch.query("""
            SELECT
                count()            AS total,
                uniq(trader)       AS traders,
                countIf(position = 'YES')  AS n_yes,
                countIf(position = 'NO')   AS n_no,
                countIf(position = 'HEDGED') AS n_hedged,
                min(resolved_at) AS t_min,
                max(resolved_at) AS t_max
            FROM _tmp_s1_enriched
        """).first_row
        _result = mo.md(
            f"### `_tmp_s1_enriched` — already built\n\n"
            f"**{_s[0]:,}** enriched positions ({_s[1]:,} traders)\n\n"
            f"| Position | Count | % |\n|---|---|---|\n"
            f"| YES | {_s[2]:,} | {_s[2]/_s[0]:.1%} |\n"
            f"| NO | {_s[3]:,} | {_s[3]/_s[0]:.1%} |\n"
            f"| HEDGED | {_s[4]:,} | {_s[4]/_s[0]:.1%} |\n\n"
            f"Period: `{_s[5]}` → `{_s[6]}`\n\n"
            f"To rebuild: `DROP TABLE _tmp_s1_enriched` then re-run."
        )
    else:
        if _n > 0 and not _ok:
            ch.command("DROP TABLE IF EXISTS _tmp_s1_enriched")
        _t0 = time_mod.time()
        ch.command(ENRICHED_SQL)
        _elapsed = time_mod.time() - _t0
        _s = ch.query(
            "SELECT count(), uniq(trader) FROM _tmp_s1_enriched"
        ).first_row
        _result = mo.md(
            f"### Built `_tmp_s1_enriched`\n\n"
            f"**{_s[0]:,}** positions ({_s[1]:,} traders) "
            f"in **{_elapsed:.0f}s**\n\n"
            f"Explorer notebook will now load from this table (no runtime JOIN)."
        )
    _result
    return


if __name__ == "__main__":
    app.run()
