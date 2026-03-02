-- S2 Combined Enhancements: Entry price + Volume at entry + Feature weights.
-- Applies all three filters simultaneously for the final parameter recommendation.
--
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end},
--             {min_positions}, {price_filter_clause}, {volume_filter_clause},
--             {combo_label}
--
-- This query outputs per-position data with all enhancement columns,
-- so the Python runner can slice by any combination.

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

-- Train: 6 features per trader
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
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct,
        -- 6 features
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
       AND train_hr >= 0.75 AND train_hr < 0.99
       AND high_pct >= 0.20
),

-- Test positions with first_trade
test_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        p.avg_yes_price,
        p.first_trade
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    INNER JOIN train_stats AS ts ON p.trader = ts.trader
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.resolved_at) < '{test_end}'
),

-- Cumulative volume at entry
market_entry_times AS (
    SELECT condition_id, min(first_trade) AS earliest_insider_entry
    FROM test_positions
    GROUP BY condition_id
),
hourly_volume AS (
    SELECT
        tr.condition_id,
        toStartOfHour(tr.timestamp) AS hour_bucket,
        sum(tr.amount_usd) AS hour_volume
    FROM trades_raw AS tr
    INNER JOIN market_entry_times AS met ON tr.condition_id = met.condition_id
    WHERE tr.timestamp < met.earliest_insider_entry
    GROUP BY tr.condition_id, hour_bucket
),
market_volume_at_entry AS (
    SELECT condition_id, sum(hour_volume) AS volume_at_entry
    FROM hourly_volume
    GROUP BY condition_id
),

-- Enrich test positions with volume_at_entry and features
enriched AS (
    SELECT
        tp.trader,
        tp.condition_id,
        tp.position,
        tp.correct,
        tp.realized_pnl,
        tp.market_volume,
        tp.avg_yes_price,
        coalesce(mve.volume_at_entry, 0) AS volume_at_entry,
        ts.f1_hr_excess,
        ts.f2_conviction_raw,
        ts.f3_selectivity_raw,
        ts.f4_markets_per_month,
        ts.f5_timing_raw,
        ts.f6_susceptibility
    FROM test_positions AS tp
    LEFT JOIN market_volume_at_entry AS mve ON tp.condition_id = mve.condition_id
    INNER JOIN train_stats AS ts ON tp.trader = ts.trader
    WHERE tp.avg_yes_price IS NOT NULL
      AND {price_filter_clause}
      AND {volume_filter_clause}
)

SELECT
    '{test_start}' AS test_month,
    '{combo_label}' AS combo,
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
    round(avg(avg_yes_price), 4) AS mean_entry_price,
    round(avg(volume_at_entry), 0) AS avg_vol_at_entry
FROM enriched
