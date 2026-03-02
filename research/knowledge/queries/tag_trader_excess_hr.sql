-- Per-tag trader excess HR: do traders with high HR on specific tags
-- actually beat the TAG-SPECIFIC base rate (not global)?
-- This is the key question: is there tag-exploitable alpha?
--
-- Uses pre-materialized _tmp_excluded_sports_weather for speed.
-- Run materialize_excluded_tags_sql() first.

SELECT
    tag,
    n_markets AS tag_markets,
    tag_yes_rate,
    n_traders,
    round(avg_raw_hr, 3) AS avg_raw_hr,
    round(avg_excess_hr_global, 3) AS excess_global,
    round(avg_excess_hr_tag, 3) AS excess_tag,
    round(avg_excess_hr_tag - avg_excess_hr_global, 3) AS tag_vs_global_gap
FROM (
    SELECT
        tag,
        n_markets,
        tag_yes_rate,
        count(*) AS n_traders,
        avg(raw_hr) AS avg_raw_hr,
        avg(raw_hr - 0.381) AS avg_excess_hr_global,
        avg(raw_hr - tag_yes_rate) AS avg_excess_hr_tag
    FROM (
        -- Per trader per tag: HR and tag base rate
        SELECT
            lower(p.trader) AS trader,
            t.label AS tag,
            countIf(p.correct = 1) / count(*) AS raw_hr,
            count(*) AS positions
        FROM trader_positions_resolved p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE p.position = 'YES'
          AND toDate(p.resolved_at) >= '2025-01-01'
          AND toDate(p.resolved_at) < '2026-01-01'
          AND m.question NOT LIKE '%Up or Down%'
        GROUP BY trader, t.label
        HAVING count(*) >= 20
    ) trader_tag
    INNER JOIN (
        -- Tag base rates
        SELECT
            t.label AS tag,
            count(DISTINCT mr.condition_id) AS n_markets,
            countIf(mr.token_won = 1 AND mr.outcome = 'YES')
                / greatest(count(DISTINCT mr.condition_id), 1) AS tag_yes_rate
        FROM markets_resolved mr
        INNER JOIN markets m ON mr.condition_id = m.condition_id
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE mr.outcome = 'YES'
          AND mr.resolved_at IS NOT NULL
          AND toDate(mr.resolved_at) >= '2025-01-01'
          AND m.question NOT LIKE '%Up or Down%'
        GROUP BY t.label
        HAVING n_markets >= 100
    ) base ON trader_tag.tag = base.tag
    GROUP BY tag, n_markets, tag_yes_rate
    HAVING n_traders >= 10
)
ORDER BY excess_tag DESC
FORMAT PrettyCompactMonoBlock
