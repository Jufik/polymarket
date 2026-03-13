"""Backward-compat shim — re-exports DDL constants from pm_store migrations.

Consumers import DDL constants and apply_schema() from this module.
"""

from __future__ import annotations

# v001: trades_raw
from pm_store.clickhouse.migrations.versions.v001_trades_raw import (
    UP as TRADES_RAW_TABLE,  # noqa: F401
)

# v002: orderbook_l2
from pm_store.clickhouse.migrations.versions.v002_orderbook_l2 import (
    UP as ORDERBOOK_L2_TABLE,  # noqa: F401
)
from pm_store.clickhouse.migrations.versions.v003_orderbook_bars import (
    _BARS_1H_MV as ORDERBOOK_BARS_1H_MV,
)
from pm_store.clickhouse.migrations.versions.v003_orderbook_bars import (
    _BARS_1H_TABLE as ORDERBOOK_BARS_1H_TABLE,
)
from pm_store.clickhouse.migrations.versions.v003_orderbook_bars import (
    _BARS_1M_MV as ORDERBOOK_BARS_1M_MV,
)

# v003: orderbook bars (private vars in migration module, re-export here)
from pm_store.clickhouse.migrations.versions.v003_orderbook_bars import (  # noqa: F401
    _BARS_1M_TABLE as ORDERBOOK_BARS_1M_TABLE,
)

# v004: trader volumes + markets_resolved view
from pm_store.clickhouse.migrations.versions.v004_trader_volumes import (  # noqa: F401
    _MARKETS_RESOLVED_VIEW as MARKETS_RESOLVED_VIEW,
)
from pm_store.clickhouse.migrations.versions.v004_trader_volumes import (
    _TRADER_VOLUMES_MAKER_MV as TRADER_VOLUMES_MAKER_MV,
)
from pm_store.clickhouse.migrations.versions.v004_trader_volumes import (
    _TRADER_VOLUMES_TABLE as TRADER_VOLUMES_TABLE,
)
from pm_store.clickhouse.migrations.versions.v004_trader_volumes import (
    _TRADER_VOLUMES_TAKER_MV as TRADER_VOLUMES_TAKER_MV,
)
from pm_store.clickhouse.migrations.versions.v005_trader_positions import (
    _TRADER_MARKET_POSITIONS_MV as TRADER_MARKET_POSITIONS_MV,
)
from pm_store.clickhouse.migrations.versions.v005_trader_positions import (
    _TRADER_MARKET_POSITIONS_TABLE as TRADER_MARKET_POSITIONS_TABLE,
)
from pm_store.clickhouse.migrations.versions.v005_trader_positions import (
    _TRADER_POSITIONS_RESOLVED_VIEW as TRADER_POSITIONS_RESOLVED_VIEW,
)
from pm_store.clickhouse.migrations.versions.v005_trader_positions import (
    _TRADER_TRADE_AGG_MAKER_MV as TRADER_TRADE_AGG_MAKER_MV,
)

# v005: trader positions
from pm_store.clickhouse.migrations.versions.v005_trader_positions import (  # noqa: F401
    _TRADER_TRADE_AGG_TABLE as TRADER_TRADE_AGG_TABLE,
)
from pm_store.clickhouse.migrations.versions.v005_trader_positions import (
    _TRADER_TRADE_AGG_TAKER_MV as TRADER_TRADE_AGG_TAKER_MV,
)

# v006: exchange bars
from pm_store.clickhouse.migrations.versions.v006_exchange_bars import (
    UP as EXCHANGE_BARS_TABLE,  # noqa: F401
)

# v007: kafka engines (dynamic — uses up_fn)
from pm_store.clickhouse.migrations.versions.v007_kafka_engines import (
    _up as _kafka_up,  # noqa: F401
)

# Kafka engine DDL constants (for backward compat with code reading them directly)
# These were previously string constants with {broker_list} placeholder.
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

TRADES_KAFKA_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS trades_kafka_mv TO trades_raw AS
SELECT * FROM trades_kafka
"""

ORDERBOOK_KAFKA_TABLE = """
CREATE TABLE IF NOT EXISTS orderbook_kafka (
    condition_id    String,
    asset_id        String,
    best_bid        Float64,
    best_ask        Float64,
    bids            String,
    asks            String,
    bid_depth_usd   Float64,
    ask_depth_usd   Float64,
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
CREATE MATERIALIZED VIEW IF NOT EXISTS orderbook_kafka_mv TO orderbook_l2 AS
SELECT
    condition_id,
    asset_id,
    best_bid,
    best_ask,
    JSONExtract(bids, 'Array(Tuple(Float64, Float64))') AS bids,
    JSONExtract(asks, 'Array(Tuple(Float64, Float64))') AS asks,
    bid_depth_usd,
    ask_depth_usd,
    toDateTime64(timestamp, 3, 'UTC') AS timestamp
FROM orderbook_kafka
"""

EXCHANGE_BARS_KAFKA_TABLE = """
CREATE TABLE IF NOT EXISTS exchange_bars_kafka (
    exchange        String,
    symbol          String,
    ts              UInt32,
    open            Float64,
    high            Float64,
    low             Float64,
    close           Float64,
    volume          Float64,
    buy_vol         Float64,
    trades          UInt32
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = '{broker_list}',
    kafka_topic_list = 'exchange.bars',
    kafka_group_name = 'clickhouse-exchange-bars',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 2
"""

EXCHANGE_BARS_KAFKA_MV = """
CREATE MATERIALIZED VIEW IF NOT EXISTS exchange_bars_kafka_mv TO exchange_bars AS
SELECT
    exchange,
    symbol,
    toDateTime(ts, 'UTC') AS ts,
    open,
    high,
    low,
    close,
    volume,
    buy_vol,
    trades
FROM exchange_bars_kafka
"""

# Old orderbook drop statements
_DROP_OLD_ORDERBOOK = [
    "DROP VIEW IF EXISTS orderbook_kafka_mv",
    "DROP TABLE IF EXISTS orderbook_kafka",
    "DROP TABLE IF EXISTS orderbook_snapshots",
]


def apply_schema(clickhouse: object, broker_list: str = "localhost:19092") -> None:
    """Create all Kafka engine tables and materialized views.

    Backward-compat wrapper — delegates to MigrationRunner for new code,
    but preserves the exact same execution order for existing consumers.

    Args:
        clickhouse: ClickHouseSink instance with execute() method.
        broker_list: Redpanda broker address.
    """
    clickhouse.execute(TRADES_RAW_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(TRADES_KAFKA_TABLE.format(broker_list=broker_list))  # type: ignore[attr-defined]
    clickhouse.execute(TRADES_KAFKA_MV)  # type: ignore[attr-defined]

    # Drop old orderbook tables (safe — IF EXISTS)
    for stmt in _DROP_OLD_ORDERBOOK:
        clickhouse.execute(stmt)  # type: ignore[attr-defined]

    # New L2 orderbook tables
    clickhouse.execute(ORDERBOOK_L2_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(  # type: ignore[attr-defined]
        ORDERBOOK_KAFKA_TABLE.format(broker_list=broker_list)
    )
    clickhouse.execute(ORDERBOOK_KAFKA_MV)  # type: ignore[attr-defined]
    clickhouse.execute(ORDERBOOK_BARS_1M_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(ORDERBOOK_BARS_1M_MV)  # type: ignore[attr-defined]
    clickhouse.execute(ORDERBOOK_BARS_1H_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(ORDERBOOK_BARS_1H_MV)  # type: ignore[attr-defined]
    clickhouse.execute(EXCHANGE_BARS_TABLE)  # type: ignore[attr-defined]
    clickhouse.execute(  # type: ignore[attr-defined]
        EXCHANGE_BARS_KAFKA_TABLE.format(broker_list=broker_list)
    )
    clickhouse.execute(EXCHANGE_BARS_KAFKA_MV)  # type: ignore[attr-defined]

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
