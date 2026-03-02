-- S2 Enriched Positions: Pull ALL data needed for enhancements in one query.
-- Returns one row per (trader, condition_id) in the test period with:
--   - Position outcome (correct, pnl, etc.)
--   - Trainer features (f1-f6)
--   - avg_yes_price (for entry price filter)
--   - first_trade (for volume-at-entry computation)
--
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end}, {min_positions}
--
-- Includes ALL tiers (train_hr >= 0.55, < 0.99) so Python can slice.

WITH market_tags AS (
    SELECT
        m.condition_id,
        m.question,
        max(if(t.label IN (
            'Up or Down', 'Crypto Prices', '5M', '15M',
            'Hit Price', 'Multi Strikes', '4H', '1H'
        ), 1, 0)) AS has_gambling_tag,
        max(if(t.label IN (
            'Politics', 'Elections', 'Geopolitics',
            'Global Elections', 'Midterms', 'Primaries',
            'Trump', 'Trump Presidency',
            'World Elections', 'US Election', 'USA Election',
            'Nov 4 Elections', 'House Elections',
            'Democratic Primary', 'Republican Primary',
            'primary elections', 'U.S. Politics',
            'Approval', 'Courts', 'Supreme Court',
            'sec', 'court cases', 'regulation',
            'ETF approval', 'approvals'
        ), 1, 0)) AS has_high_tag,
        max(if(t.label IN (
            'Sports', 'Games', 'Basketball', 'Soccer',
            'Esports', 'NBA', 'NCAA', 'Tennis', 'NFL',
            'NCAA Basketball', 'Cricket', 'NHL', 'CFB',
            'Hockey', 'MLB', 'Golf', 'EPL', 'UFC',
            'Formula 1', 'f1', 'MLS', 'Olympics',
            'counter strike 2', 'Dota 2', 'league of legends',
            'Valorant', 'Honor of Kings',
            'Culture', 'Movies', 'Music', 'Awards',
            'Oscars', 'Grammys', 'Golden Globes',
            'Weather', 'Science',
            'Finance', 'Economy', 'Equities', 'Stocks',
            'Earnings', 'Business', 'Tech', 'Big Tech', 'AI'
        ), 1, 0)) AS has_medium_tag
    FROM markets AS m
    INNER JOIN events AS e ON m.event_id = e.id
    INNER JOIN event_tags AS et ON e.id = et.event_id
    INNER JOIN tags AS t ON et.tag_id = t.id
    GROUP BY m.condition_id, m.question
),
susceptible_markets AS (
    SELECT
        condition_id,
        multiIf(
            has_gambling_tag = 1, 'LOW',
            question LIKE '%Up or Down%' OR question LIKE '%up or down%'
                OR question LIKE '%coin flip%' OR question LIKE '%5-min%'
                OR question LIKE '%15-min%' OR question LIKE '%next 5 min%'
                OR question LIKE '%next 15 min%', 'LOW',
            has_high_tag = 1, 'HIGH',
            question LIKE '%SEC %' OR question LIKE '%FDA %'
                OR question LIKE '%regulat%' OR question LIKE '%approv%'
                OR question LIKE '%election%' OR question LIKE '%president%'
                OR question LIKE '%indict%' OR question LIKE '%verdict%'
                OR question LIKE '%announce%' OR question LIKE '%ruling%', 'HIGH',
            has_medium_tag = 1, 'MEDIUM',
            'MEDIUM'
        ) AS susceptibility
    FROM market_tags
),

-- Training period: 6 features per trader
train_positions AS (
    SELECT
        p.trader, p.condition_id, p.position, p.correct,
        p.realized_pnl, p.market_volume, p.resolved_at, sm.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{train_start}'
      AND toDate(p.resolved_at) < '{train_end}'
),
train_stats AS (
    SELECT
        trader,
        count(*) AS train_n,
        countIf(correct = 1) / count(*) AS train_hr,
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct,
        greatest(
            (3.81 + countIf(position = 'YES' AND correct = 1))
                / (10.0 + countIf(position = 'YES')) - 0.381,
            (6.19 + countIf(position = 'NO' AND correct = 1))
                / (10.0 + countIf(position = 'NO')) - 0.619
        ) AS f1_hr_excess,
        avg(market_volume) AS f2_conviction_raw,
        1.0 / greatest(
            count(*) / greatest(
                dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
            ), 0.01
        ) AS f3_selectivity_raw,
        count(*) / greatest(
            dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
        ) AS f4_markets_per_month,
        avg(realized_pnl) AS f5_timing_raw,
        countIf(susceptibility = 'HIGH') / count(*) AS f6_susceptibility
    FROM train_positions
    GROUP BY trader
    HAVING count(*) >= {min_positions}
       AND train_hr >= 0.55
       AND train_hr < 0.99
),

-- Test period: direct SELECT (no CTE wrapper -- CH 24.8 has scoping issues
-- with CTE columns from multi-table joins).
test_positions AS (
    SELECT
        p.trader AS t_trader,
        p.condition_id AS t_cid,
        p.position AS t_position,
        p.correct AS t_correct,
        p.realized_pnl AS t_pnl,
        p.market_volume AS t_mkt_vol,
        p.avg_yes_price AS t_avg_yes_px,
        p.first_trade AS t_first_trade,
        sm.susceptibility AS t_susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.resolved_at) < '{test_end}'
)

SELECT
    tp.t_trader AS trader,
    tp.t_cid AS condition_id,
    tp.t_position AS position,
    tp.t_correct AS correct,
    tp.t_pnl AS realized_pnl,
    tp.t_mkt_vol AS market_volume,
    tp.t_avg_yes_px AS avg_yes_price,
    tp.t_first_trade AS first_trade,
    tp.t_susceptibility AS susceptibility,
    ts.train_n,
    ts.train_hr,
    ts.high_pct,
    ts.f1_hr_excess,
    ts.f2_conviction_raw,
    ts.f3_selectivity_raw,
    ts.f4_markets_per_month,
    ts.f5_timing_raw,
    ts.f6_susceptibility
FROM test_positions AS tp
INNER JOIN train_stats AS ts ON tp.t_trader = ts.trader
