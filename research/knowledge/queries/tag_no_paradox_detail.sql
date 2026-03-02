-- NO Paradox detail: In extreme-NO-bias tags, NO traders have negative
-- excess HR but positive PnL. Where exactly is the PnL coming from?
-- Break down by entry price to find the mispricing sweet spot.

SELECT
    tag,
    price_bucket,
    count(*) AS n_positions,
    round(countIf(correct = 1) / count(*), 3) AS hit_rate,
    round(avg(realized_pnl), 2) AS avg_pnl,
    round(sum(realized_pnl), 0) AS total_pnl,
    round(median(hold_days), 1) AS med_hold_d
FROM (
    SELECT
        t.label AS tag,
        p.correct,
        p.realized_pnl,
        p.avg_yes_price,
        dateDiff('day', p.first_trade, p.resolved_at) AS hold_days,
        multiIf(
            1.0 - p.avg_yes_price < 0.10, '<0.10',
            1.0 - p.avg_yes_price < 0.20, '0.10-0.20',
            1.0 - p.avg_yes_price < 0.30, '0.20-0.30',
            1.0 - p.avg_yes_price < 0.50, '0.30-0.50',
            '0.50+'
        ) AS price_bucket
    FROM trader_positions_resolved p
    INNER JOIN markets m ON p.condition_id = m.condition_id
    INNER JOIN events e ON m.event_id = e.id
    INNER JOIN event_tags et ON e.id = et.event_id
    INNER JOIN tags t ON et.tag_id = t.id
    WHERE p.position = 'NO'
      AND t.label IN ('Elections', 'Music', 'Movies', 'Culture')
      AND toDate(p.resolved_at) >= '2025-01-01'
      AND toDate(p.resolved_at) < '2026-01-01'
      AND m.question NOT LIKE '%Up or Down%'
)
GROUP BY tag, price_bucket
ORDER BY tag, price_bucket
FORMAT PrettyCompactMonoBlock
