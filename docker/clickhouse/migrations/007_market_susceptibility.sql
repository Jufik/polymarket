-- 007_market_susceptibility.sql
-- Pre-computed market susceptibility classification via tag chain.
--
-- Uses: markets -> events -> event_tags -> tags
-- Replaces ad-hoc CTE in insider_pool.sql queries — centralizes logic
-- so strategies can JOIN directly.
--
-- Susceptibility levels:
--   LOW    = gambling/random: crypto price short-term, up-or-down, coin flip
--   HIGH   = insider-susceptible: politics, elections, geopolitics, regulatory
--   MEDIUM = signal potential: sports, esports, culture, finance, other
--
-- Requires: markets, events, event_tags, tags (all PG-replicated)

CREATE OR REPLACE VIEW polymarket.market_susceptibility AS
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
    FROM polymarket.markets AS m
    INNER JOIN polymarket.events AS e ON m.event_id = e.id
    INNER JOIN polymarket.event_tags AS et ON e.id = et.event_id
    INNER JOIN polymarket.tags AS t ON et.tag_id = t.id
    GROUP BY m.condition_id, m.question
)
SELECT
    condition_id,
    multiIf(
        -- LOW: gambling/random (tags first, then question text fallback)
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
FROM market_tags;
