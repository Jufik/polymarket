-- Migration 009: Classification tables for trader and market taxonomy
--
-- Flat classification tables that replace inline CTEs in research queries.
-- Researcher proposes new labels, Architect writes INSERT migrations.
--
-- Usage:
--   -- Find all bots
--   SELECT trader FROM trader_classifications FINAL WHERE label = 'bot'
--
--   -- Exclude gambling markets
--   SELECT condition_id FROM market_classifications FINAL
--   WHERE label = 'susceptibility' AND tier = 1  -- LOW = gambling
--
--   -- Compose: non-bot traders in non-gambling markets
--   SELECT t.maker, t.condition_id
--   FROM trades_raw FINAL AS t
--   LEFT JOIN (SELECT trader FROM trader_classifications FINAL WHERE label = 'bot') bots
--       ON t.maker = bots.trader
--   INNER JOIN (SELECT condition_id FROM market_classifications FINAL
--               WHERE label = 'susceptibility' AND tier >= 2) mkt
--       ON t.condition_id = mkt.condition_id
--   WHERE bots.trader IS NULL  -- exclude bots

CREATE TABLE IF NOT EXISTS trader_classifications (
    trader          String,
    label           String,
    tier            UInt8,
    score           Float64 DEFAULT 0,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, trader);

CREATE TABLE IF NOT EXISTS market_classifications (
    condition_id    String,
    label           String,
    tier            UInt8,
    score           Float64 DEFAULT 0,
    rule_version    UInt16 DEFAULT 1,
    computed_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(rule_version)
ORDER BY (label, condition_id);
