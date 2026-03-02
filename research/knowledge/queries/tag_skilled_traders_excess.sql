-- Tag-aware skilled traders with positive PnL and excess HR >= 10pp above tag base.
-- Uses hardcoded tag base rates from tag_base_rates.md (2025 data).

SELECT
    tag, direction, n_traders,
    round(avg_hr, 3) AS avg_hr, round(tag_base, 3) AS base,
    round(avg_hr - tag_base, 3) AS excess,
    round(avg_pnl, 2) AS avg_pnl, round(agg_pnl, 0) AS agg_pnl, avg_pos
FROM (
    SELECT
        tag, direction,
        count(*) AS n_traders,
        avg(raw_hr) AS avg_hr,
        any(tag_base) AS tag_base,
        avg(avg_pnl) AS avg_pnl,
        sum(total_pnl) AS agg_pnl,
        round(avg(positions)) AS avg_pos
    FROM (
        SELECT
            t.label AS tag,
            p.position AS direction,
            lower(p.trader) AS trader,
            countIf(p.correct = 1) / count(*) AS raw_hr,
            count(*) AS positions,
            avg(p.realized_pnl) AS avg_pnl,
            sum(p.realized_pnl) AS total_pnl,
            -- Hardcoded tag base rates (from tag_base_rates.md)
            multiIf(
                p.position = 'YES', multiIf(
                    t.label = 'Earnings', 0.729, t.label = 'Earnings Calls', 0.588,
                    t.label = 'Tariffs', 0.479, t.label = 'Courts', 0.211,
                    t.label = 'Geopolitics', 0.270, t.label = 'Trump', 0.341,
                    t.label = 'Politics', 0.232, t.label = 'Crypto', 0.289,
                    t.label = 'Finance', 0.353, t.label = 'Economy', 0.234,
                    t.label = 'Culture', 0.117, t.label = 'Elections', 0.090,
                    t.label = 'Tech', 0.124, t.label = 'AI', 0.100,
                    t.label = 'Music', 0.088, t.label = 'Movies', 0.097,
                    0.381
                ),
                multiIf(
                    t.label = 'Earnings', 0.271, t.label = 'Earnings Calls', 0.412,
                    t.label = 'Tariffs', 0.521, t.label = 'Courts', 0.789,
                    t.label = 'Geopolitics', 0.730, t.label = 'Trump', 0.659,
                    t.label = 'Politics', 0.768, t.label = 'Crypto', 0.711,
                    t.label = 'Finance', 0.647, t.label = 'Economy', 0.766,
                    t.label = 'Culture', 0.883, t.label = 'Elections', 0.910,
                    t.label = 'Tech', 0.876, t.label = 'AI', 0.900,
                    t.label = 'Music', 0.912, t.label = 'Movies', 0.903,
                    0.619
                )
            ) AS tag_base
        FROM trader_positions_resolved p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE p.position IN ('YES', 'NO')
          AND toDate(p.resolved_at) >= '2025-01-01' AND toDate(p.resolved_at) < '2026-01-01'
          AND m.question NOT LIKE '%Up or Down%'
          AND t.label IN ('Earnings','Earnings Calls','Tariffs','Courts','Geopolitics',
            'Trump','Politics','Crypto','Finance','Economy','Culture','Elections',
            'Tech','AI','Music','Movies')
        GROUP BY t.label, p.position, trader
        HAVING count(*) >= 20
          AND sum(p.realized_pnl) > 0
          AND countIf(p.correct = 1) / count(*) - tag_base >= 0.10
    )
    GROUP BY tag, direction
)
WHERE n_traders >= 5
ORDER BY agg_pnl DESC
FORMAT PrettyCompactMonoBlock
