-- S2 Insider Copy: Build insider pool for tick-by-tick validation.
-- Returns strict-tier traders with their best direction for copying.
-- Parameters: {train_start}, {train_end}
--
-- Output: trader, best_direction, effective_hr, hr_excess, high_pct, train_n
-- Used by: research/scripts/s2_tick_validation.py

WITH market_tags AS (
    SELECT
        m.condition_id,
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
        ), 1, 0)) AS has_high_tag
    FROM markets AS m
    INNER JOIN events AS e ON m.event_id = e.id
    INNER JOIN event_tags AS et ON e.id = et.event_id
    INNER JOIN tags AS t ON et.tag_id = t.id
    GROUP BY m.condition_id
),
train_positions AS (
    SELECT
        p.trader,
        p.condition_id,
        p.position,
        p.correct,
        p.avg_yes_price,
        mt.has_high_tag
    FROM (SELECT * FROM trader_positions_resolved) AS p
    INNER JOIN market_tags AS mt ON p.condition_id = mt.condition_id
    WHERE mt.has_gambling_tag = 0
      AND p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '{train_start}'
      AND toDate(p.resolved_at) < '{train_end}'
),
train_stats AS (
    SELECT
        trader,
        count(*) AS train_n,
        countIf(correct = 1) / count(*) AS train_hr,
        countIf(has_high_tag = 1) / count(*) AS high_pct,
        -- Bayesian HR per direction
        countIf(position = 'YES' AND correct = 1) AS yes_wins,
        countIf(position = 'YES') AS yes_total,
        countIf(position = 'NO' AND correct = 1) AS no_wins,
        countIf(position = 'NO') AS no_total,
        -- Entry price stats
        avg(avg_yes_price) AS mean_entry_price
    FROM train_positions
    GROUP BY trader
    HAVING count(*) >= 3
),
scored AS (
    SELECT
        *,
        -- Bayesian posterior mean per direction
        (3.81 + yes_wins) / (10.0 + yes_total) AS bayesian_yes_hr,
        (6.19 + no_wins) / (10.0 + no_total) AS bayesian_no_hr,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total),
            (6.19 + no_wins) / (10.0 + no_total)
        ) AS effective_hr,
        if(
            (3.81 + yes_wins) / (10.0 + yes_total) >= (6.19 + no_wins) / (10.0 + no_total),
            'YES', 'NO'
        ) AS best_direction,
        greatest(
            (3.81 + yes_wins) / (10.0 + yes_total) - 0.381,
            (6.19 + no_wins) / (10.0 + no_total) - 0.619
        ) AS hr_excess
    FROM train_stats
)
SELECT
    lower(trader) AS trader,
    best_direction,
    effective_hr,
    hr_excess,
    high_pct,
    train_n,
    train_hr,
    mean_entry_price
FROM scored
WHERE train_hr >= 0.75
  AND train_hr < 0.99
  AND high_pct >= 0.20
ORDER BY hr_excess DESC
