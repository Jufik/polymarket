-- S2 Enhancement 3: Market Volume at Entry Time (no look-ahead).
-- For each insider's test-period position, compute cumulative market volume
-- in trades_raw UP TO the insider's first trade in that market.
--
-- This avoids look-ahead bias: we only use volume that existed BEFORE the
-- insider's entry, not the total market volume (which includes future trades).
--
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end},
--             {min_positions}, {volume_filter_label}, {volume_filter_clause}
--
-- The volume_filter_clause applies to volume_at_entry.
-- Examples:
--   '1=1' for no filter (baseline)
--   'volume_at_entry >= 500'
--   'volume_at_entry >= 1000'
--   'volume_at_entry >= 5000'
--   'volume_at_entry >= 10000'
--
-- Strategy:
--   1. Get strict-tier insiders from training period
--   2. Get their test-period positions with first_trade timestamp
--   3. For each (condition_id, first_trade), compute cumulative market volume
--      from trades_raw up to that timestamp using a correlated subquery
--   4. Filter by volume threshold
--
-- Performance note: Uses hour-bucketed pre-aggregation for efficiency.
-- The hour bucket is conservative (counts volume up to start of the entry hour,
-- so we slightly undercount — this is safer than overcounting).

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

-- Train stats (strict tier)
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
       AND train_hr >= 0.75
       AND train_hr < 0.99
       AND high_pct >= 0.20
),

-- Test positions for strict-tier insiders (with first_trade timestamp)
test_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume,
        p.first_trade
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    INNER JOIN train_stats AS ts ON p.trader = ts.trader
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.resolved_at) < '{test_end}'
),

-- Get unique (condition_id, first_trade) pairs we need volume for
-- Group by condition_id to get the EARLIEST insider entry per market
market_entry_times AS (
    SELECT
        condition_id,
        min(first_trade) AS earliest_insider_entry
    FROM test_positions
    GROUP BY condition_id
),

-- Pre-aggregate trades_raw by (condition_id, hour) for efficiency
-- Only need markets that appear in our test set
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

-- Cumulative volume per market at the insider's entry time
market_volume_at_entry AS (
    SELECT
        condition_id,
        sum(hour_volume) AS volume_at_entry
    FROM hourly_volume
    GROUP BY condition_id
),

-- Join back to test positions and apply volume filter
filtered_positions AS (
    SELECT
        tp.trader,
        tp.condition_id,
        tp.position,
        tp.correct,
        tp.realized_pnl,
        tp.market_volume,
        coalesce(mve.volume_at_entry, 0) AS volume_at_entry
    FROM test_positions AS tp
    LEFT JOIN market_volume_at_entry AS mve ON tp.condition_id = mve.condition_id
    WHERE {volume_filter_clause}
)

SELECT
    '{test_start}' AS test_month,
    '{volume_filter_label}' AS volume_filter,
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
    round(avg(volume_at_entry), 0) AS avg_vol_at_entry,
    round(quantile(0.5)(volume_at_entry), 0) AS median_vol_at_entry,
    round(quantile(0.25)(volume_at_entry), 0) AS p25_vol_at_entry,
    round(quantile(0.75)(volume_at_entry), 0) AS p75_vol_at_entry
FROM filtered_positions
