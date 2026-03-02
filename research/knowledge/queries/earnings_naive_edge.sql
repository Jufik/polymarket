-- Earnings naive edge: what happens if you just buy YES on every Earnings market?
-- 73% YES base rate — is this directly tradeable?
-- Check by entry price bucket (are markets priced correctly?)

SELECT
    price_bucket,
    count(*) AS n_positions,
    round(countIf(correct = 1) / count(*), 3) AS hit_rate,
    round(avg(realized_pnl), 2) AS avg_pnl,
    round(sum(realized_pnl), 0) AS total_pnl,
    round(median(hold_days), 1) AS med_hold_d
FROM (
    SELECT
        p.condition_id,
        p.correct,
        p.realized_pnl,
        p.avg_yes_price,
        dateDiff('day', p.first_trade, p.resolved_at) AS hold_days,
        multiIf(
            p.avg_yes_price < 0.50, '<0.50',
            p.avg_yes_price < 0.60, '0.50-0.60',
            p.avg_yes_price < 0.70, '0.60-0.70',
            p.avg_yes_price < 0.80, '0.70-0.80',
            p.avg_yes_price < 0.90, '0.80-0.90',
            '0.90+'
        ) AS price_bucket
    FROM trader_positions_resolved p
    INNER JOIN markets m ON p.condition_id = m.condition_id
    INNER JOIN events e ON m.event_id = e.id
    INNER JOIN event_tags et ON e.id = et.event_id
    INNER JOIN tags t ON et.tag_id = t.id
    WHERE p.position = 'YES'
      AND t.label = 'Earnings'
      AND toDate(p.resolved_at) >= '2025-01-01'
      AND toDate(p.resolved_at) < '2026-01-01'
)
GROUP BY price_bucket
ORDER BY price_bucket
FORMAT PrettyCompactMonoBlock
