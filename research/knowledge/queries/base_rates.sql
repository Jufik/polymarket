-- Market base rates: what fraction of markets are won by YES vs NO?
-- Usage: establishes baseline for any directional signal evaluation.
-- Result: ~38.1% YES, ~61.9% NO across 390K+ resolved markets.

SELECT
    countIf(token_won = 1 AND outcome = 'YES') AS yes_won,
    countIf(token_won = 1 AND outcome = 'NO') AS no_won,
    round(yes_won / (yes_won + no_won) * 100, 1) AS yes_pct,
    round(no_won / (yes_won + no_won) * 100, 1) AS no_pct
FROM markets_resolved
WHERE resolved_at IS NOT NULL AND resolved_at > '1970-01-02'
