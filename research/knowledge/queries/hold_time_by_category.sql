-- Hold time distribution by market tag.
-- Usage: capital efficiency planning, position slot allocation.
-- Uses tag chain (not m.category which is 99.3% NULL — see pitfalls/category_column_null.md).

SELECT
    t.label AS tag,
    count(*) AS n,
    round(quantile(0.5)(dateDiff('hour', m.created_at, m.closed_at)), 0) AS med_hours,
    round(avg(dateDiff('hour', m.created_at, m.closed_at)), 0) AS avg_hours,
    round(quantile(0.9)(dateDiff('hour', m.created_at, m.closed_at)), 0) AS p90_hours
FROM markets m
INNER JOIN events e ON m.event_id = e.id
INNER JOIN event_tags et ON e.id = et.event_id
INNER JOIN tags t ON et.tag_id = t.id
WHERE m.status = 'closed'
  AND m.closed_at > m.created_at
  AND m.closed_at >= '2025-01-01'
  AND m.question NOT LIKE '%Up or Down%'
GROUP BY tag
HAVING n >= 100
ORDER BY med_hours
