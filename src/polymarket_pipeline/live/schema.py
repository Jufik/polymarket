"""ClickHouse DDL for Kafka engine integration with Redpanda."""

from __future__ import annotations

TRADES_KAFKA_TABLE = """
CREATE TABLE IF NOT EXISTS trades_kafka (
    trade_id        String,
    condition_id    String,
    asset_id        String,
    side            String,
    price           Float64,
    size            Float64,
    amount_usd      Float64,
    fee_usd         Float64,
    maker           Nullable(String),
    taker           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    source          String,
    tx_hash         Nullable(String),
    order_hash      Nullable(String),
    block_number    Nullable(UInt64),
    is_backfill     UInt8,
    _version        UInt16,
    published_at    Float64
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = '{broker_list}',
    kafka_topic_list = 'trades.raw',
    kafka_group_name = 'clickhouse',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 4,
    date_time_input_format = 'best_effort'
"""

TRADES_RAW_TABLE = """
CREATE TABLE IF NOT EXISTS trades_raw (
    trade_id        String,
    condition_id    String,
    asset_id        String,
    side            String,
    price           Float64,
    size            Float64,
    amount_usd      Float64,
    fee_usd         Float64,
    maker           Nullable(String),
    taker           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    source          String,
    tx_hash         Nullable(String),
    order_hash      Nullable(String),
    block_number    Nullable(UInt64),
    is_backfill     UInt8,
    _version        UInt16,
    published_at    Float64 DEFAULT 0,
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(_version)
ORDER BY (condition_id, timestamp, trade_id)
PARTITION BY toYYYYMM(timestamp)
"""

TRADES_KAFKA_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trades_kafka_mv TO trades_raw AS
SELECT * FROM trades_kafka
"""

ORDERBOOK_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    condition_id    String,
    asset_id        String,
    best_bid        Float64,
    best_ask        Float64,
    timestamp       DateTime64(3, 'UTC'),
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC')
) ENGINE = ReplacingMergeTree(timestamp)
ORDER BY (condition_id, timestamp)
PARTITION BY toYYYYMMDD(timestamp)
TTL toDateTime(timestamp) + INTERVAL 7 DAY
"""

ORDERBOOK_KAFKA_TABLE = """
CREATE TABLE IF NOT EXISTS orderbook_kafka (
    condition_id    String,
    asset_id        String,
    best_bid        Float64,
    best_ask        Float64,
    timestamp       Float64
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = '{broker_list}',
    kafka_topic_list = 'orderbooks.raw',
    kafka_group_name = 'clickhouse-orderbook',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 2,
    date_time_input_format = 'best_effort'
"""

ORDERBOOK_KAFKA_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS orderbook_kafka_mv TO orderbook_snapshots AS
SELECT
    condition_id,
    asset_id,
    best_bid,
    best_ask,
    toDateTime64(timestamp, 3, 'UTC') AS timestamp
FROM orderbook_kafka
"""


# ======================================================================
# Derived feature tables (live-updating from trades_raw)
# ======================================================================

MARKETS_RESOLVED_VIEW = """
CREATE OR REPLACE VIEW markets_resolved AS
SELECT
    m.condition_id,
    m.resolution_value,
    m.winner_outcome,
    m.resolved_at,
    tm.asset_id,
    tm.outcome,
    tm.winner AS token_won
FROM markets m
INNER JOIN token_market_map tm ON m.condition_id = tm.condition_id
WHERE m.resolution_value = 1
"""

TRADER_VOLUMES_TABLE = """
CREATE TABLE IF NOT EXISTS trader_volumes (
    trader          String,
    maker_vol       Float64,
    taker_vol       Float64
) ENGINE = SummingMergeTree((maker_vol, taker_vol))
ORDER BY trader
"""

TRADER_VOLUMES_MAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_volumes_maker_mv
TO trader_volumes AS
SELECT
    maker AS trader,
    amount_usd AS maker_vol,
    0 AS taker_vol
FROM trades_raw
WHERE maker IS NOT NULL AND maker != ''
"""

TRADER_VOLUMES_TAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_volumes_taker_mv
TO trader_volumes AS
SELECT
    taker AS trader,
    0 AS maker_vol,
    amount_usd AS taker_vol
FROM trades_raw
WHERE taker IS NOT NULL AND taker != ''
"""

TRADER_TRADE_AGG_TABLE = """
CREATE TABLE IF NOT EXISTS trader_trade_agg (
    trader          String,
    condition_id    LowCardinality(String),
    asset_id        String,
    net_tokens      Float64,
    net_usd         Float64,
    total_fees      Float64,
    volume          Float64,
    trade_count     UInt64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3)),
    price_x_vol     Float64
) ENGINE = SummingMergeTree(
    (net_tokens, net_usd, total_fees, volume, trade_count, price_x_vol)
)
ORDER BY (trader, condition_id, asset_id)
"""

TRADER_TRADE_AGG_MAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_trade_agg_maker_mv
TO trader_trade_agg AS
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
FROM trades_raw
WHERE maker IS NOT NULL AND maker != ''
"""

TRADER_TRADE_AGG_TAKER_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_trade_agg_taker_mv
TO trader_trade_agg AS
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
FROM trades_raw
WHERE taker IS NOT NULL AND taker != ''
"""

# ======================================================================
# Trader market positions (chained from trader_trade_agg)
# Replaces ad-hoc _tmp_s1_positions / _tmp_s1_enriched notebooks.
# ======================================================================

TRADER_MARKET_POSITIONS_TABLE = """
CREATE TABLE IF NOT EXISTS trader_market_positions (
    trader          String,
    condition_id    LowCardinality(String),
    net_yes         Float64,
    net_no          Float64,
    volume          Float64,
    trade_count     UInt64,
    yes_px_vol      Float64,
    first_trade     SimpleAggregateFunction(min, DateTime64(3)),
    last_trade      SimpleAggregateFunction(max, DateTime64(3))
) ENGINE = SummingMergeTree(
    (net_yes, net_no, volume, trade_count, yes_px_vol)
)
ORDER BY (trader, condition_id)
"""

TRADER_MARKET_POSITIONS_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trader_market_positions_mv
TO trader_market_positions AS
SELECT
    a.trader,
    a.condition_id,
    if(tm.outcome = 'YES', a.net_tokens, 0)                          AS net_yes,
    if(tm.outcome = 'NO',  a.net_tokens, 0)                          AS net_no,
    a.volume,
    a.trade_count,
    if(tm.outcome = 'YES', a.price_x_vol, a.volume - a.price_x_vol)  AS yes_px_vol,
    a.first_trade,
    a.last_trade
FROM trader_trade_agg a
INNER JOIN token_market_map tm ON a.asset_id = tm.asset_id
"""

TRADER_POSITIONS_RESOLVED_VIEW = """
CREATE OR REPLACE VIEW trader_positions_resolved AS
SELECT
    p.trader,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN 'YES'
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 'NO'
        WHEN p.net_yes > 0.01 AND p.net_no > 0.01  THEN 'HEDGED'
        ELSE 'CLOSED'
    END AS position,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN wavg_yes
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN 1.0 - wavg_yes
        WHEN p.net_yes >= p.net_no                  THEN wavg_yes
        ELSE 1.0 - wavg_yes
    END AS dir_entry,
    CASE
        WHEN p.net_yes > 0.01 AND p.net_no <= 0.01 THEN mr.yes_won
        WHEN p.net_no > 0.01 AND p.net_yes <= 0.01 THEN NOT mr.yes_won
        WHEN p.net_yes >= p.net_no                  THEN mr.yes_won
        ELSE NOT mr.yes_won
    END AS correct,
    p.volume AS market_volume,
    p.trade_count,
    mr.resolved_at,
    formatDateTime(mr.resolved_at, '%Y-%m') AS month
FROM (
    SELECT trader, condition_id, net_yes, net_no, volume, trade_count,
           yes_px_vol / nullIf(volume, 0) AS wavg_yes
    FROM trader_market_positions FINAL
) p
INNER JOIN (
    SELECT condition_id, resolved_at,
           coalesce(token_won, false) AS yes_won
    FROM markets_resolved
    WHERE outcome = 'YES'
) mr ON p.condition_id = mr.condition_id
WHERE NOT (p.net_yes <= 0.01 AND p.net_no <= 0.01)
"""


def apply_schema(clickhouse: object, broker_list: str = "localhost:19092") -> None:
    """Create all Kafka engine tables and materialized views.

    Args:
        clickhouse: ClickHouseSink instance with execute() method.
        broker_list: Redpanda broker address.
    """
    clickhouse.execute(TRADES_RAW_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(TRADES_KAFKA_TABLE.format(broker_list=broker_list))  # type: ignore[attr-defined]
    clickhouse.execute(TRADES_KAFKA_MV)  # type: ignore[attr-defined]
    clickhouse.execute(ORDERBOOK_SNAPSHOTS_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(  # type: ignore[attr-defined]
        ORDERBOOK_KAFKA_TABLE.format(broker_list=broker_list)
    )
    clickhouse.execute(ORDERBOOK_KAFKA_MV)  # type: ignore[attr-defined]

    # Derived feature tables (must come after trades_raw)
    clickhouse.execute(TRADER_VOLUMES_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_TRADE_AGG_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(MARKETS_RESOLVED_VIEW)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_VOLUMES_MAKER_MV)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_VOLUMES_TAKER_MV)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_TRADE_AGG_MAKER_MV)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_TRADE_AGG_TAKER_MV)  # type: ignore[attr-defined]

    # Trader market positions (chained MV from trader_trade_agg)
    clickhouse.execute(TRADER_MARKET_POSITIONS_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_MARKET_POSITIONS_MV)  # type: ignore[attr-defined]
    clickhouse.execute(TRADER_POSITIONS_RESOLVED_VIEW)  # type: ignore[attr-defined]
