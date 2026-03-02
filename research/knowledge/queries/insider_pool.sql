-- research/knowledge/queries/insider_pool.sql
-- Compute insider scores for all traders on susceptible markets.
-- Parameters: {lookback_months}, {min_positions}
--
-- Stage 1: Classify markets by susceptibility using tags chain
--          (markets -> events -> event_tags -> tags)
-- Stage 2: Compute 6-feature score per trader
--
-- Replaces old market_categories-based classification (2026-03-02).
-- See also: tag_susceptibility.sql for standalone distribution query.

WITH market_tags AS (
    SELECT
        m.condition_id,
        m.question,
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
    FROM market_tags
),
-- Filter to only susceptible resolved markets
resolved_susceptible AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        p.trade_count,
        p.avg_yes_price,
        p.resolved_at,
        p.yes_won,
        sm.susceptibility
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= toDate(now()) - INTERVAL {lookback_months:UInt32} MONTH
),
-- Per-trader stats
trader_stats AS (
    SELECT
        trader,
        -- F1 inputs
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        count(*) AS total_positions,
        -- F2: bet conviction
        sum(market_volume) / count(*) AS avg_position_usd,
        -- F3: selectivity (markets per month)
        count(*) / greatest(
            dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
        ) AS markets_per_month,
        -- F5: timing edge (avg realized PnL as proxy)
        avg(realized_pnl) AS avg_realized_pnl,
        -- F6: susceptibility concentration
        countIf(susceptibility = 'HIGH') / count(*) AS high_market_ratio,
        -- Extra stats
        sum(realized_pnl) AS total_pnl,
        avg(market_volume) AS avg_volume
    FROM resolved_susceptible
    GROUP BY trader
    HAVING count(*) >= {min_positions:UInt32}
),
-- F1: Bayesian hit rate
scored AS (
    SELECT
        *,
        -- YES posterior mean: (3.81 + yes_wins) / (10 + yes_total)
        (3.81 + yes_wins) / (10.0 + yes_total) AS bayesian_yes_hr,
        -- NO posterior mean: (6.19 + no_wins) / (10 + no_total)
        (6.19 + no_wins) / (10.0 + no_total) AS bayesian_no_hr,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total),
            (6.19 + no_wins) / (10.0 + no_total)
        ) AS effective_hr,
        if(
            (3.81 + yes_wins) / (10.0 + yes_total) >= (6.19 + no_wins) / (10.0 + no_total),
            'YES', 'NO'
        ) AS best_direction,
        -- F1: excess over base rate
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total) - 0.381,
            (6.19 + no_wins) / (10.0 + no_total) - 0.619
        ) AS hr_excess
    FROM trader_stats
)
SELECT
    trader,
    total_positions,
    yes_wins, yes_total,
    no_wins, no_total,
    effective_hr,
    best_direction,
    hr_excess,
    avg_position_usd,
    markets_per_month,
    avg_realized_pnl,
    high_market_ratio,
    total_pnl,
    avg_volume
FROM scored
ORDER BY hr_excess DESC, avg_position_usd DESC
LIMIT 500
