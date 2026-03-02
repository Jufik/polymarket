-- S2 Insider Copy: Consensus analysis using tag-based susceptibility.
-- Counts unique insiders per (condition_id, position) in test period,
-- then reports OOS HR/PnL by consensus bucket.
--
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end},
--             {min_positions}, {min_train_hr}, {max_train_hr}, {min_high_pct}

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
    HAVING count(*) >= {min_positions}
       AND train_hr >= {min_train_hr}
       AND train_hr < {max_train_hr}
       AND high_pct >= {min_high_pct}
),
-- Count unique insiders per (market, position) in test period
test_consensus AS (
    SELECT
        p.condition_id,
        p.position,
        uniqExact(p.trader) AS n_insiders,
        anyIf(p.correct, p.correct IN (0, 1)) AS correct,
        avg(p.realized_pnl) AS avg_pnl
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
    multiIf(
        n_insiders = 1, '1',
        n_insiders = 2, '2',
        n_insiders = 3, '3',
        n_insiders BETWEEN 4 AND 5, '4-5',
        n_insiders BETWEEN 6 AND 10, '6-10',
        '11+'
    ) AS consensus_bucket,
    count(*) AS n_market_sides,
    round(countIf(correct = 1) * 100.0 / count(*), 1) AS hr,
    round(avg(avg_pnl), 2) AS avg_pnl_per_pos
FROM test_consensus
GROUP BY consensus_bucket
ORDER BY consensus_bucket
