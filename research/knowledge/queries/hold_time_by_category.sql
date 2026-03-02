-- Hold time distribution by market category.
-- Usage: capital efficiency planning, position slot allocation.
-- Requires: markets table with closed_at, created_at.

SELECT
    multiIf(
        question LIKE '%Up or Down%', 'gambling',
        question LIKE '%vs.%' OR question LIKE '%Winner%'
            OR question LIKE '%NFL%' OR question LIKE '%NBA%'
            OR question LIKE '%MLB%' OR question LIKE '%NHL%',
        'sports',
        question LIKE '%Bitcoin%' OR question LIKE '%ETH %'
            OR question LIKE '%crypto%' OR question LIKE '%BTC%',
        'crypto',
        question LIKE '%Trump%' OR question LIKE '%election%'
            OR question LIKE '%president%',
        'politics',
        'other'
    ) AS cat,
    count(*) AS n,
    round(quantile(0.5)(dateDiff('hour', created_at, closed_at)), 0) AS med_hours,
    round(avg(dateDiff('hour', created_at, closed_at)), 0) AS avg_hours,
    round(quantile(0.9)(dateDiff('hour', created_at, closed_at)), 0) AS p90_hours
FROM markets
WHERE status = 'closed'
  AND closed_at > created_at
  AND closed_at >= '2025-01-01'
GROUP BY cat
ORDER BY med_hours
