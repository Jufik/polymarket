-- S2 Insider Copy: Load resolution data for markets in the test window.
-- Used for tick-by-tick settlement (asset_id-based, no string matching).
-- Parameters: none (uses _tmp_s2_tick_pool and test window dates from parent query)
--
-- Output: condition_id, asset_id, outcome, token_won, resolved_epoch

SELECT
    mr.condition_id,
    mr.asset_id,
    mr.outcome,
    mr.token_won,
    toUnixTimestamp(mr.resolved_at) AS resolved_epoch
FROM markets_resolved AS mr
WHERE mr.resolved_at IS NOT NULL
  AND mr.resolved_at > '1970-01-02'
  AND mr.token_won IS NOT NULL
