-- Migration 009: Temporal classification tables for trader and market taxonomy
--
-- Classifications are FUNCTIONS: f(cutoff) → rows.
-- Each row is tagged with `as_of` — the data cutoff date used to derive it.
-- This ensures point-in-time correctness for backtesting.
--
-- "Migration" means: create a rule .sql file and schedule its refresh.
-- The table is a materialized cache of function outputs.
--
-- Usage:
--   -- Bots as of a specific date (point-in-time)
--   SELECT trader FROM trader_classifications FINAL
--   WHERE label = 'bot' AND as_of = toDate('2025-09-01')
--
--   -- Compose: non-bot traders in non-gambling markets (temporal)
--   SELECT t.maker, t.condition_id
--   FROM (SELECT * FROM trades_raw FINAL) t
--   LEFT JOIN (SELECT trader FROM trader_classifications FINAL
--              WHERE label = 'bot' AND as_of = toDate('{cutoff}')) bots
--       ON t.maker = bots.trader
--   INNER JOIN (SELECT condition_id FROM market_classifications FINAL
--               WHERE label = 'susceptibility' AND tier >= 2
--                 AND as_of = toDate('{cutoff}')) mkt
--       ON t.condition_id = mkt.condition_id
--   WHERE bots.trader IS NULL

CREATE TABLE IF NOT EXISTS trader_classifications (
    trader          String,
    label           String,
    tier            UInt8,
    score           Float64 DEFAULT 0,
    as_of           Date,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, as_of, trader);

CREATE TABLE IF NOT EXISTS market_classifications (
    condition_id    String,
    label           String,
    tier            UInt8,
    score           Float64 DEFAULT 0,
    as_of           Date,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, as_of, condition_id);
