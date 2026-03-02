-- S2 Enhancement 2: Feature Weight Optimization.
-- Returns per-trader features AND their OOS correctness for one walk-forward window.
-- Python will compute correlations and optimize weights.
--
-- Parameters: {train_start}, {train_end}, {test_start}, {test_end}, {min_positions}
--
-- Output: one row per (trader, condition_id) in test period with:
--   - 6 raw feature values from training period
--   - OOS correct (0/1) for each test position
--   - Used to compute feature-outcome correlations in Python

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

-- Training period: compute 6 raw features per trader
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
        -- F1: Bayesian HR excess (best direction)
        greatest(
            (3.81 + countIf(position = 'YES' AND correct = 1))
                / (10.0 + countIf(position = 'YES')) - 0.381,
            (6.19 + countIf(position = 'NO' AND correct = 1))
                / (10.0 + countIf(position = 'NO')) - 0.619
        ) AS f1_hr_excess,
        -- Raw train HR for tier filtering
        countIf(correct = 1) / count(*) AS train_hr,
        -- F2: Conviction (avg bet size in USD)
        avg(market_volume) AS f2_conviction_raw,
        -- F3: Selectivity (inverse markets/month)
        1.0 / greatest(
            count(*) / greatest(
                dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
            ), 0.01
        ) AS f3_selectivity_raw,
        -- F4: Anomaly components (will be combined into z-score in Python)
        count(*) / greatest(
            dateDiff('month', min(resolved_at), max(resolved_at)) + 1, 1
        ) AS f4_markets_per_month,
        -- F5: Timing edge (avg realized PnL)
        avg(realized_pnl) AS f5_timing_raw,
        -- F6: Susceptibility concentration
        countIf(susceptibility = 'HIGH') / count(*) AS f6_susceptibility,
        -- High pct for tier filtering
        countIf(susceptibility = 'HIGH') / count(*) AS high_pct
    FROM train_positions
    GROUP BY trader
    HAVING count(*) >= {min_positions}
),

-- Test period: each insider's OOS positions
test_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.realized_pnl,
        p.market_volume
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN susceptible_markets AS sm ON p.condition_id = sm.condition_id
    WHERE sm.susceptibility != 'LOW'
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{test_start}'
      AND toDate(p.resolved_at) < '{test_end}'
)

-- Join: each test position gets the trader's 6 features
SELECT
    tp.trader,
    tp.condition_id,
    tp.position,
    tp.correct,
    tp.realized_pnl,
tp.market_volume,
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
INNER JOIN train_stats AS ts ON tp.trader = ts.trader
WHERE ts.train_hr >= 0.55   -- include all tiers for feature analysis
  AND ts.train_hr < 0.99    -- exclude bots
