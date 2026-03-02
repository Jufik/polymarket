-- S2 Insider Copy: Walk-forward vectorized upper bounds using tag-based susceptibility.
-- For each test month M, train on [M-12mo, M), test on [M, M+1).
-- Computes OOS hit rate and PnL for different insider pool tiers.
--
-- Usage: run from Python with clickhouse_connect, iterating over test months.
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end}, {min_positions}
--
-- Output: per-tier aggregated metrics for the test month.

-- Step 1: Tag-based susceptibility (shared CTE)
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

-- Step 2: Training period trader stats
train_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        sm.susceptibility
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
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct
    FROM train_positions
    GROUP BY trader
    HAVING count(*) >= {min_positions}
),

-- Step 3: Test period positions for trained traders
test_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        sm.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.resolved_at) < '{test_end}'
),

-- Step 4: Join test positions with train stats, compute per-tier metrics
enriched AS (
    SELECT
        tp.trader,
        tp.condition_id,
        tp.position,
        tp.correct,
        tp.realized_pnl,
        tp.market_volume,
        tp.susceptibility,
        ts.train_hr,
        ts.high_pct,
        ts.train_n
    FROM test_positions AS tp
    INNER JOIN train_stats AS ts ON tp.trader = ts.trader
)

SELECT
    '{test_start}' AS test_month,
    -- Tier classification
    multiIf(
        train_hr >= 0.75 AND high_pct >= 0.20 AND train_hr < 0.99, 'strict',
        train_hr >= 0.65 AND train_hr < 0.99, 'medium',
        train_hr >= 0.55 AND train_hr < 0.99, 'loose',
        train_hr < 0.55, 'baseline',
        'bot'
    ) AS tier,
    count(*) AS n_positions,
    round(countIf(correct = 1) * 100.0 / count(*), 1) AS hr,
    round(countIf(position = 'YES' AND correct = 1) * 100.0
        / greatest(countIf(position = 'YES'), 1), 1) AS yes_hr,
    round(countIf(position = 'NO' AND correct = 1) * 100.0
        / greatest(countIf(position = 'NO'), 1), 1) AS no_hr,
    countIf(position = 'YES') AS yes_n,
    countIf(position = 'NO') AS no_n,
    round(avg(realized_pnl), 2) AS avg_pnl,
    round(sum(realized_pnl), 0) AS total_pnl,
    uniqExact(trader) AS n_traders,
    uniqExact(condition_id) AS n_markets,
    round(avg(market_volume), 2) AS avg_volume
FROM enriched
GROUP BY tier
ORDER BY tier
