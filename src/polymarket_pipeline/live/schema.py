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
