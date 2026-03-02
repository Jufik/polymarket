-- Tag-based market susceptibility classification.
-- Uses the JOIN chain: markets -> events -> event_tags -> tags
-- instead of the market_categories table (which is a flat denormalized copy).
--
-- Susceptibility levels:
--   LOW  = gambling/random: crypto price short-term, up-or-down, coin flip
--   HIGH = insider-susceptible: politics, elections, geopolitics, regulatory,
--          plus question-text patterns (SEC, FDA, verdict, ruling, announce)
--   MEDIUM = everything else with signal potential: sports, esports, culture, finance
--
-- Usage: standalone distribution check, or as CTE in insider_pool.sql
-- Result: one row per condition_id with susceptibility label and primary tag

WITH market_tags AS (
    SELECT
        m.condition_id,
        m.question,
        groupArray(t.label) AS tags,
        -- Flag specific tag groups (any match = 1)
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
    FROM polymarket.markets m
    INNER JOIN polymarket.events e ON m.event_id = e.id
    INNER JOIN polymarket.event_tags et ON e.id = et.event_id
    INNER JOIN polymarket.tags t ON et.tag_id = t.id
    GROUP BY m.condition_id, m.question
)
SELECT
    condition_id,
    multiIf(
        -- LOW: gambling/random (check tags first, then question text)
        has_gambling_tag = 1,
        'LOW',
        question LIKE '%Up or Down%'
            OR question LIKE '%up or down%'
            OR question LIKE '%coin flip%'
            OR question LIKE '%5-min%'
            OR question LIKE '%15-min%'
            OR question LIKE '%next 5 min%'
            OR question LIKE '%next 15 min%',
        'LOW',
        -- HIGH: politics/regulatory (tags + question text patterns)
        has_high_tag = 1,
        'HIGH',
        question LIKE '%SEC %'
            OR question LIKE '%FDA %'
            OR question LIKE '%regulat%'
            OR question LIKE '%approv%'
            OR question LIKE '%election%'
            OR question LIKE '%president%'
            OR question LIKE '%indict%'
            OR question LIKE '%verdict%'
            OR question LIKE '%announce%'
            OR question LIKE '%ruling%',
        'HIGH',
        -- MEDIUM: sports, esports, culture, finance, etc.
        has_medium_tag = 1,
        'MEDIUM',
        -- Default: MEDIUM (anything with tags but not classified above)
        'MEDIUM'
    ) AS susceptibility
FROM market_tags
