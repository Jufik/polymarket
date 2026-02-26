-- s1_02_resolution.sql
-- Determine which outcome won for each resolved market.
--
-- Output columns:
--   condition_id, resolved_at, winner_outcome, yes_won
--
-- Logic:
--   markets.resolution_value = 1 → market is resolved
--   token_market_map: outcome='YES' AND winner=true → YES won
--   LEFT JOIN gives yes_won per condition_id

SELECT
    m.condition_id,
    m.resolved_at,
    m.winner_outcome,
    coalesce(t.yes_won, false) AS yes_won
FROM markets m
LEFT JOIN (
    SELECT condition_id, true AS yes_won
    FROM token_market_map
    WHERE outcome = 'YES' AND winner = true
) t ON m.condition_id = t.condition_id
WHERE m.resolution_value = 1
