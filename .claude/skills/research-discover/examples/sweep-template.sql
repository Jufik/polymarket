-- Vectorized signal sweep template (composable pattern)
--
-- RULE: JOIN classification tables, never re-derive inline.
-- If a classification you need doesn't exist, propose it in notes.md.
--
-- Replace {SIGNAL_COLUMN}, {THRESHOLD} with hypothesis-specific values.
-- Required output: condition_id, signal_value, outcome, resolved_at
-- Compare hit rate against base rate: NO wins 62%, YES wins 38%

-- Step 1: Filter entities via classification tables
WITH filtered_traders AS (
    SELECT trader
    FROM (SELECT trader FROM trader_classifications FINAL WHERE label = 'bot') bots
    -- Example: exclude bots. Adapt to your needs.
    -- For inclusion: WHERE label = 'insider_score' AND tier <= 2
),
filtered_markets AS (
    SELECT condition_id
    FROM (SELECT condition_id, tier FROM market_classifications FINAL
          WHERE label = 'susceptibility') mc
    WHERE mc.tier >= 2  -- exclude gambling (tier=1)
),

-- Step 2: Compute signal per market (should be SHORT — the hard filtering is above)
signal AS (
    SELECT
        tp.condition_id,
        {SIGNAL_COLUMN} AS signal_value,
        mr.outcome,
        mr.resolved_epoch
    FROM (SELECT * FROM trader_market_positions FINAL) tp
    INNER JOIN filtered_markets fm ON tp.condition_id = fm.condition_id
    LEFT JOIN filtered_traders bots ON tp.trader = bots.trader
    INNER JOIN (SELECT * FROM markets_resolved) mr ON tp.condition_id = mr.condition_id
    WHERE bots.trader IS NULL  -- exclude filtered traders
      AND mr.resolved_epoch > 0
    GROUP BY tp.condition_id, mr.outcome, mr.resolved_epoch
    HAVING signal_value >= {THRESHOLD}
)

-- Step 3: Aggregate and compare against base rates
SELECT
    signal_value,
    count() AS n_markets,
    countIf(outcome = 'NO') / count() AS no_hit_rate,
    countIf(outcome = 'YES') / count() AS yes_hit_rate,
    countIf(outcome = 'NO') / count() - 0.619 AS excess_no_hr,
    countIf(outcome = 'YES') / count() - 0.381 AS excess_yes_hr
FROM signal
GROUP BY signal_value
ORDER BY signal_value;
