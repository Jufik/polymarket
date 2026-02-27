#!/usr/bin/env bash
# clean_restart.sh — Full clean slate restart for Polymarket pipeline
# Prerequisites: all pm-live, pm-strategy processes STOPPED, Docker services RUNNING
set -euo pipefail

CH="docker exec polymarket-clickhouse-1 clickhouse-client --multiquery -q"
RPK="docker exec polymarket-redpanda-1 rpk"
VENV="cd /mnt/nvme/git/polymarket/polymarket && .venv/bin"

echo "=========================================="
echo "Phase 1: Detach CH Kafka engine tables"
echo "=========================================="
$CH "DETACH TABLE IF EXISTS polymarket.trades_kafka;
     DETACH TABLE IF EXISTS polymarket.orderbook_kafka;"
echo "  -> Kafka engine tables detached (CH stops consuming)"

echo ""
echo "=========================================="
echo "Phase 2: Purge Redpanda topics"
echo "=========================================="
TOPICS="trades.raw orderbooks.raw markets.events pipeline.status pipeline.quality strategy.intents"
for t in $TOPICS; do
    $RPK topic delete "$t" 2>/dev/null || true
done
echo "  -> All topics deleted"
sleep 2
for t in $TOPICS; do
    $RPK topic create "$t" -p 1 -r 1
done
echo "  -> All topics recreated (empty)"

# Reset consumer group offsets (they reference deleted topics)
$RPK group delete clickhouse 2>/dev/null || true
$RPK group delete clickhouse-orderbook 2>/dev/null || true
$RPK group delete market-events 2>/dev/null || true
$RPK group delete quality-gate 2>/dev/null || true
$RPK group delete strategy-market-events 2>/dev/null || true
$RPK group delete strategy-runner 2>/dev/null || true
echo "  -> Consumer groups cleaned"

echo ""
echo "=========================================="
echo "Phase 3: Clean ClickHouse data"
echo "=========================================="
# Delete straggler RTDS/Alchemy rows (only ~21K, mutation is fast)
$CH "ALTER TABLE polymarket.trades_raw DELETE WHERE source IN ('rtds', 'alchemy');"
echo "  -> RTDS/Alchemy stragglers queued for deletion"

# Truncate contaminated derived tables
$CH "TRUNCATE TABLE IF EXISTS polymarket.trader_trade_agg;
     TRUNCATE TABLE IF EXISTS polymarket.trader_volumes;
     TRUNCATE TABLE IF EXISTS polymarket.orderbook_snapshots;"
echo "  -> Derived tables truncated (trader_trade_agg, trader_volumes, orderbook_snapshots)"

# Drop temp research tables
$CH "DROP TABLE IF EXISTS polymarket._tmp_hold_first;
     DROP TABLE IF EXISTS polymarket._tmp_s1_enriched;
     DROP TABLE IF EXISTS polymarket._tmp_s1_positions;"
echo "  -> Temp tables dropped"

echo ""
echo "=========================================="
echo "Phase 4: Rebuild derived tables from clean trades_raw"
echo "  (~419M rows — may take 5-15 minutes)"
echo "=========================================="

echo "  [1/4] trader_volumes (maker side)..."
$CH "INSERT INTO polymarket.trader_volumes
SELECT
    maker AS trader,
    amount_usd AS maker_vol,
    0 AS taker_vol
FROM polymarket.trades_raw
WHERE maker IS NOT NULL AND maker != ''
SETTINGS max_insert_block_size = 1048576;"
echo "  -> Done"

echo "  [2/4] trader_volumes (taker side)..."
$CH "INSERT INTO polymarket.trader_volumes
SELECT
    taker AS trader,
    0 AS maker_vol,
    amount_usd AS taker_vol
FROM polymarket.trades_raw
WHERE taker IS NOT NULL AND taker != ''
SETTINGS max_insert_block_size = 1048576;"
echo "  -> Done"

echo "  [3/4] trader_trade_agg (maker side)..."
$CH "INSERT INTO polymarket.trader_trade_agg
SELECT
    maker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', toFloat64(size), -toFloat64(size)) AS net_tokens,
    if(side = 'BUY', -toFloat64(amount_usd), toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM polymarket.trades_raw
WHERE maker IS NOT NULL AND maker != ''
SETTINGS max_insert_block_size = 1048576;"
echo "  -> Done"

echo "  [4/4] trader_trade_agg (taker side)..."
$CH "INSERT INTO polymarket.trader_trade_agg
SELECT
    taker AS trader,
    condition_id,
    asset_id,
    if(side = 'BUY', -toFloat64(size), toFloat64(size)) AS net_tokens,
    if(side = 'BUY', toFloat64(amount_usd), -toFloat64(amount_usd)) AS net_usd,
    toFloat64(fee_usd) AS total_fees,
    toFloat64(amount_usd) AS volume,
    toUInt64(1) AS trade_count,
    timestamp AS first_trade,
    timestamp AS last_trade,
    toFloat64(price) * toFloat64(amount_usd) AS price_x_vol
FROM polymarket.trades_raw
WHERE taker IS NOT NULL AND taker != ''
SETTINGS max_insert_block_size = 1048576;"
echo "  -> Done"

echo ""
echo "=========================================="
echo "Phase 5: Re-attach Kafka engine tables"
echo "=========================================="
$CH "ATTACH TABLE IF NOT EXISTS polymarket.trades_kafka;
     ATTACH TABLE IF NOT EXISTS polymarket.orderbook_kafka;"
echo "  -> Kafka engine tables re-attached (will consume from new empty topics)"

echo ""
echo "=========================================="
echo "Phase 6: Verify"
echo "=========================================="
$CH "SELECT 'trades_raw' AS tbl, count() AS rows FROM polymarket.trades_raw
     UNION ALL SELECT 'trader_trade_agg', count() FROM polymarket.trader_trade_agg
     UNION ALL SELECT 'trader_volumes', count() FROM polymarket.trader_volumes
     UNION ALL SELECT 'orderbook_snapshots', count() FROM polymarket.orderbook_snapshots
     FORMAT PrettyCompact;"

$CH "SELECT source, count() AS rows FROM polymarket.trades_raw GROUP BY source ORDER BY rows DESC FORMAT PrettyCompact;"

$RPK topic list
$RPK group list

echo ""
echo "=========================================="
echo "Phase 7: Manual steps (run these yourself)"
echo "=========================================="
echo ""
echo "  # 1. Sync metadata (Gamma API -> PostgreSQL)"
echo "  $VENV/pm-sync"
echo ""
echo "  # 2. Recover subgraph gaps (if needed — check for gaps first)"
echo "  #    Direct-to-CH mode (fast, but derived MVs won't fire for new data):"
echo "  #    $VENV/pm-recover"
echo "  #    Through Kafka (slower, but MVs fire):"
echo "  #    $VENV/pm-recover --redpanda"
echo ""
echo "  # 3. Start live pipeline"
echo "  $VENV/supervisorctl start ingestion"
echo ""
echo "  # 4. Start strategies"
echo "  $VENV/supervisorctl start strategies:will_no"
echo ""
echo "=========================================="
echo "Clean restart complete!"
echo "=========================================="
