-- Per-tag edge by direction: where is the actual PnL?
-- Compares YES traders vs NO traders per tag with tag-specific base rates.
-- This finds which tag+direction combos have real edge.

SELECT
    tag,
    direction,
    n_traders,
    n_positions,
    round(avg_hr, 3) AS avg_hr,
    round(tag_base, 3) AS tag_base,
    round(avg_hr - tag_base, 3) AS excess_hr,
    round(avg_pnl_per_pos, 2) AS avg_pnl,
    round(median_hold_days, 1) AS med_hold_d,
    -- Compounding: excess * edge / hold
    round((avg_hr - tag_base) * avg_pnl_per_pos / greatest(median_hold_days, 0.5), 3) AS compound
FROM (
    SELECT
        t.label AS tag,
        p.position AS direction,
        count(DISTINCT lower(p.trader)) AS n_traders,
        count(*) AS n_positions,
        countIf(p.correct = 1) / count(*) AS avg_hr,
        avg(p.realized_pnl) AS avg_pnl_per_pos,
        median(dateDiff('day', p.first_trade, p.resolved_at)) AS median_hold_days
    FROM trader_positions_resolved p
    INNER JOIN markets m ON p.condition_id = m.condition_id
    INNER JOIN events e ON m.event_id = e.id
    INNER JOIN event_tags et ON e.id = et.event_id
    INNER JOIN tags t ON et.tag_id = t.id
    WHERE p.position IN ('YES', 'NO')
      AND toDate(p.resolved_at) >= '2025-01-01'
      AND toDate(p.resolved_at) < '2026-01-01'
      AND m.question NOT LIKE '%Up or Down%'
      AND t.label IN (
        'Earnings', 'Earnings Calls', 'Politics', 'Elections',
        'Culture', 'Movies', 'Music', 'Crypto', 'Finance',
        'Trump', 'Trump Presidency', 'Geopolitics', 'Economy',
        'AI', 'Tech', 'Weather', 'Sports', 'Tariffs',
        'Neg Risk', 'Approval', 'Courts'
      )
    GROUP BY t.label, p.position
) results
INNER JOIN (
    SELECT
        t.label AS tag,
        if(p.position = 'YES',
            countIf(mr.token_won = 1 AND mr.outcome = 'YES') / greatest(count(DISTINCT mr.condition_id), 1),
            1.0 - countIf(mr.token_won = 1 AND mr.outcome = 'YES') / greatest(count(DISTINCT mr.condition_id), 1)
        ) AS tag_base,
        p.position AS direction
    FROM markets_resolved mr
    INNER JOIN markets m ON mr.condition_id = m.condition_id
    INNER JOIN events e ON m.event_id = e.id
    INNER JOIN event_tags et ON e.id = et.event_id
    INNER JOIN tags t ON et.tag_id = t.id
    CROSS JOIN (SELECT 'YES' AS position UNION ALL SELECT 'NO') p
    WHERE mr.outcome = 'YES' AND mr.resolved_at IS NOT NULL
      AND toDate(mr.resolved_at) >= '2025-01-01'
      AND m.question NOT LIKE '%Up or Down%'
      AND t.label IN (
        'Earnings', 'Earnings Calls', 'Politics', 'Elections',
        'Culture', 'Movies', 'Music', 'Crypto', 'Finance',
        'Trump', 'Trump Presidency', 'Geopolitics', 'Economy',
        'AI', 'Tech', 'Weather', 'Sports', 'Tariffs',
        'Neg Risk', 'Approval', 'Courts'
      )
    GROUP BY t.label, p.position
) base ON results.tag = base.tag AND results.direction = base.direction
ORDER BY compound DESC
FORMAT PrettyCompactMonoBlock
