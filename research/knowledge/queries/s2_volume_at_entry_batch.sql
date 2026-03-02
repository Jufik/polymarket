-- S2 Volume at Entry: Batch computation of cumulative volume before insider entry.
-- Given a temp table _tmp_s2_entry_times with (condition_id, entry_time),
-- compute the cumulative USD volume in trades_raw up to entry_time.
--
-- Pre-requisite: create _tmp_s2_entry_times first:
--   CREATE TABLE _tmp_s2_entry_times (
--       condition_id String,
--       entry_time DateTime64(3)
--   ) ENGINE = Memory;
--
-- Then INSERT the (condition_id, min(first_trade)) from the enriched positions.
--
-- Strategy: Use hourly pre-aggregation for efficiency.
-- The hour bucket is conservative: counts volume up to the start of the entry hour.

SELECT
    et.condition_id,
    coalesce(hv.total_volume, 0) AS volume_at_entry
FROM _tmp_s2_entry_times AS et
LEFT JOIN (
    SELECT
        tr.condition_id,
        sum(tr.amount_usd) AS total_volume
    FROM trades_raw AS tr
    INNER JOIN _tmp_s2_entry_times AS et2 ON tr.condition_id = et2.condition_id
    WHERE tr.timestamp < et2.entry_time
    GROUP BY tr.condition_id
) AS hv ON et.condition_id = hv.condition_id
