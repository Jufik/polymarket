-- Migration: main → featu/cleaning
-- Safe for Goldsky data (425M rows preserved)
-- Run against ClickHouse at 192.168.0.148:18123
--
-- Prerequisites:
--   1. Stop pm-live (to avoid Kafka engine conflicts)
--   2. Run this script
--   3. Deploy featu/cleaning branch
--   4. Start pm-live (apply_schema() will create any missing MVs/views)

-- ============================================================
-- Step 1: Add missing column to trades_raw
-- Goldsky rows get DEFAULT 0 (correct — no publish timestamp)
-- ============================================================
ALTER TABLE trades_raw ADD COLUMN IF NOT EXISTS published_at Float64 DEFAULT 0;

-- ============================================================
-- Step 2 (OPTIONAL): Upgrade Float32 → Float64 for precision
-- This doubles storage for these columns (~4 bytes → 8 bytes each)
-- Skip if storage is a concern — Float32 works fine for prices 0-1
-- ============================================================
-- ALTER TABLE trades_raw MODIFY COLUMN price Float64;
-- ALTER TABLE trades_raw MODIFY COLUMN size Float64;
-- ALTER TABLE trades_raw MODIFY COLUMN amount_usd Float64;
-- ALTER TABLE trades_raw MODIFY COLUMN fee_usd Float64;

-- ============================================================
-- Step 3 (OPTIONAL): Drop live data if you want a clean slate
-- Only drops RTDS + Alchemy trades, keeps Goldsky
-- ============================================================
-- ALTER TABLE trades_raw DELETE WHERE source IN ('rtds', 'alchemy');
-- Note: DELETE is async in ClickHouse (mutation). Verify with:
-- SELECT count() FROM trades_raw WHERE source IN ('rtds', 'alchemy');

-- ============================================================
-- Step 4: Clean up temp tables from old notebooks
-- ============================================================
DROP TABLE IF EXISTS _tmp_s1_positions;
DROP TABLE IF EXISTS _tmp_s1_enriched;

-- ============================================================
-- Step 5: Verify
-- ============================================================
SELECT 'trades_raw columns' AS check_name;
SELECT name, type FROM system.columns
WHERE database = 'polymarket' AND table = 'trades_raw'
ORDER BY position;

SELECT 'trades_raw count by source' AS check_name;
SELECT source, count() AS cnt FROM trades_raw GROUP BY source ORDER BY cnt DESC;

SELECT 'published_at status' AS check_name;
SELECT
    countIf(published_at = 0) AS zero_published_at,
    countIf(published_at > 0) AS nonzero_published_at
FROM trades_raw;
