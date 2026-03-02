-- S2 Per-Category Parameter Sweep: enriched positions with consensus counts.
-- Returns one row per (trader, condition_id, position) in the test period,
-- with category label and consensus count for Python-side slicing.
--
-- Usage: run for each (train_start, train_end, test_start, test_end) pair,
--        then slice in Python by consensus threshold, entry price, and category.
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end}
-- Output: test_month, cat, cid, pos, correct, rpnl, avg_px, hold_d, cons

WITH market_tags AS (
    SELECT
        m.condition_id,
        m.question,
        max(if(t.label IN ('Up or Down', 'Crypto Prices', '5M', '15M', 'Hit Price', 'Multi Strikes', '4H', '1H'), 1, 0)) AS has_gambling_tag,
        max(if(t.label IN ('Politics', 'Elections', 'Geopolitics', 'Global Elections', 'Midterms', 'Primaries', 'Trump', 'Trump Presidency', 'World Elections', 'US Election', 'USA Election', 'Nov 4 Elections', 'House Elections', 'Democratic Primary', 'Republican Primary', 'primary elections', 'U.S. Politics', 'Approval', 'Courts', 'Supreme Court', 'sec', 'court cases', 'regulation', 'ETF approval', 'approvals'), 1, 0)) AS has_high_tag,
        max(if(t.label IN ('Sports', 'Games', 'Basketball', 'Soccer', 'Esports', 'NBA', 'NCAA', 'Tennis', 'NFL', 'NCAA Basketball', 'Cricket', 'NHL', 'CFB', 'Hockey', 'MLB', 'Golf', 'EPL', 'UFC', 'Formula 1', 'f1', 'MLS', 'Olympics', 'counter strike 2', 'Dota 2', 'league of legends', 'Valorant', 'Honor of Kings', 'Culture', 'Movies', 'Music', 'Awards', 'Oscars', 'Grammys', 'Golden Globes', 'Weather', 'Science', 'Finance', 'Economy', 'Equities', 'Stocks', 'Earnings', 'Business', 'Tech', 'Big Tech', 'AI'), 1, 0)) AS has_medium_tag,
        max(if(t.label = 'Politics', 1, 0)) AS is_politics,
        max(if(t.label = 'Sports', 1, 0)) AS is_sports,
        max(if(t.label = 'Esports', 1, 0)) AS is_esports,
        max(if(t.label = 'Culture', 1, 0)) AS is_culture,
        max(if(t.label = 'Finance', 1, 0)) AS is_finance,
        max(if(t.label = 'Weather', 1, 0)) AS is_weather,
        max(if(t.label = 'Crypto', 1, 0)) AS is_crypto
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
            question LIKE '%Up or Down%' OR question LIKE '%up or down%' OR question LIKE '%coin flip%' OR question LIKE '%5-min%' OR question LIKE '%15-min%' OR question LIKE '%next 5 min%' OR question LIKE '%next 15 min%', 'LOW',
            has_high_tag = 1, 'HIGH',
            question LIKE '%SEC %' OR question LIKE '%FDA %' OR question LIKE '%regulat%' OR question LIKE '%approv%' OR question LIKE '%election%' OR question LIKE '%president%' OR question LIKE '%indict%' OR question LIKE '%verdict%' OR question LIKE '%announce%' OR question LIKE '%ruling%', 'HIGH',
            has_medium_tag = 1, 'MEDIUM',
            'MEDIUM'
        ) AS susceptibility,
        multiIf(
            is_politics = 1, 'politics',
            is_esports = 1, 'esports',
            is_sports = 1, 'sports',
            is_culture = 1, 'culture',
            is_finance = 1, 'finance',
            is_weather = 1, 'weather',
            is_crypto = 1, 'crypto',
            'other'
        ) AS primary_category
    FROM market_tags
),
train_stats AS (
    SELECT
        trader,
        count(*) AS train_n,
        countIf(correct = 1) / count(*) AS train_hr,
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct
    FROM (
        SELECT p.trader, p.correct, sm.susceptibility
        FROM (SELECT * FROM trader_positions_resolved) AS p
        INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
        WHERE sm.susceptibility != 'LOW'
          AND p.position IN ('YES', 'NO')
          AND toDate(p.resolved_at) >= '{train_start}'
          AND toDate(p.resolved_at) < '{train_end}'
    )
    GROUP BY trader
    HAVING count(*) >= 3
       AND train_hr >= 0.75
       AND train_hr < 0.99
       AND high_pct >= 0.20
),
consensus_counts AS (
    SELECT
        p.condition_id AS cc_cid,
        p.position AS cc_pos,
        uniqExact(p.trader) AS n_insiders
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    INNER JOIN train_stats AS ts ON p.trader = ts.trader
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.resolved_at) < '{test_end}'
    GROUP BY p.condition_id, p.position
)
SELECT
    '{test_start}' AS test_month,
    sm.primary_category AS cat,
    p.condition_id AS cid,
    p.position AS pos,
    p.correct AS correct,
    p.realized_pnl AS rpnl,
    p.avg_yes_price AS avg_px,
    dateDiff('day', p.first_trade, p.resolved_at) AS hold_d,
    cc.n_insiders AS cons
FROM (SELECT * FROM trader_positions_resolved) AS p
INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
INNER JOIN train_stats AS ts ON p.trader = ts.trader
INNER JOIN consensus_counts AS cc ON p.condition_id = cc.cc_cid AND p.position = cc.cc_pos
WHERE sm.susceptibility != 'LOW'
  AND p.position IN ('YES', 'NO')
  AND toDate(p.resolved_at) >= '{test_start}'
  AND toDate(p.resolved_at) < '{test_end}'
