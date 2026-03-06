"""TagHRProvider — computes qualified-trader pool per tag from ClickHouse.

At startup (compute), queries trailing hit rate per trader per tag and builds:
  - qualified_traders: dict[tag, frozenset[trader_address]]
  - market_tags: dict[condition_id, tag]   (preloaded for all target tags)

During replay, on_trade() is a no-op (pool is static for tick-by-tick validation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class TagHRProvider:
    """Feature provider for tag-based hit-rate copy strategy.

    Parameters
    ----------
    target_tags:
        List of tag labels to qualify traders for (e.g. ["Esports", "1H"]).
    lookback_months:
        Trailing window for HR computation (default 6).
    min_trades_default:
        Minimum trades per (trader, tag) — used if no per-tag override.
    excess_hr_pp_default:
        Min excess HR over base rate (pp) — used if no per-tag override.
    max_avg_entry_price_default:
        Max avg YES entry price — used if no per-tag override.
    use_trailing_base_rate:
        If True, compute per-tag base rate from training window.
    min_universe_size:
        Minimum market count in tag to include it.
    tag_params:
        Nested dict of per-tag overrides: {tag_label: {min_trades, excess_hr_pp, max_avg_entry_price}}
    ch_host / ch_port / ch_database:
        ClickHouse connection params.
    """

    name: str = "tag_hr_provider"

    def __init__(
        self,
        target_tags: list[str] | None = None,
        lookback_months: int = 6,
        min_trades: int = 50,
        min_trades_default: int = 50,
        excess_hr_pp: float = 15.0,
        excess_hr_pp_default: float = 15.0,
        max_avg_entry_price: float = 0.75,
        max_avg_entry_price_default: float = 0.75,
        use_trailing_base_rate: bool = True,
        min_universe_size: int = 50,
        tag_params: dict[str, Any] | None = None,
        ch_host: str = "192.168.0.148",
        ch_port: int = 18123,
        ch_database: str = "polymarket",
        **kwargs: Any,
    ) -> None:
        self._target_tags: list[str] = target_tags or ["Esports", "1H"]
        self._lookback_months = lookback_months
        # Resolve defaults (support both naming conventions)
        _min_trades = min_trades_default or min_trades
        _excess_hr_pp = excess_hr_pp_default or excess_hr_pp
        _max_price = max_avg_entry_price_default or max_avg_entry_price
        self._use_trailing_base_rate = use_trailing_base_rate
        self._min_universe_size = min_universe_size
        self._ch_host = ch_host
        self._ch_port = ch_port
        self._ch_database = ch_database

        # Per-tag param overrides from TOML nested table
        raw_tag_params: dict[str, Any] = tag_params or {}
        self._tag_params: dict[str, dict[str, Any]] = {}
        for tag in self._target_tags:
            tag_override = raw_tag_params.get(tag, {}) or {}
            self._tag_params[tag] = {
                "min_trades": int(tag_override.get("min_trades", _min_trades)),
                "excess_hr_pp": float(tag_override.get("excess_hr_pp", _excess_hr_pp)),
                "max_avg_entry_price": float(tag_override.get("max_avg_entry_price", _max_price)),
            }

        # Computed at startup
        self._qualified_traders: dict[str, frozenset[str]] = {}
        self._market_tags: dict[str, str] = {}
        self._tag_base_rates: dict[str, float] = {}
        self._initialized = False

    def _get_ch(self) -> Any:
        import clickhouse_connect
        return clickhouse_connect.get_client(
            host=self._ch_host,
            port=self._ch_port,
            database=self._ch_database,
        )

    async def compute(self, backend: Any) -> None:
        """Batch compute qualified trader pool at startup.

        Uses ClickHouse directly (ignores backend abstraction).
        Training window = the 6 months preceding now (static for replay validation).
        """
        import datetime

        ch = self._get_ch()

        train_end = datetime.datetime.now(datetime.timezone.utc)
        train_start = train_end - datetime.timedelta(days=30 * self._lookback_months)
        train_start_str = train_start.strftime("%Y-%m-%d")
        train_end_str = train_end.strftime("%Y-%m-%d")

        logger.info(
            "tag_hr_provider.compute",
            target_tags=self._target_tags,
            train_start=train_start_str,
            train_end=train_end_str,
        )

        # Step 1: Load all tagged markets (condition_id -> tag)
        tags_quoted = ", ".join(f"'{t}'" for t in self._target_tags)
        market_tag_sql = f"""
        SELECT DISTINCT m.condition_id, t.label AS tag
        FROM markets m
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE t.label IN ({tags_quoted})
        """
        rows = ch.query(market_tag_sql)
        market_tags: dict[str, str] = {}
        # Respect target_tags priority order for multi-tagged markets
        tag_priority = {t: i for i, t in enumerate(self._target_tags)}
        for row in rows.result_rows:
            cid, tag = str(row[0]), str(row[1])
            if cid not in market_tags:
                market_tags[cid] = tag
            else:
                # Use higher-priority tag
                if tag_priority.get(tag, 999) < tag_priority.get(market_tags[cid], 999):
                    market_tags[cid] = tag
        self._market_tags = market_tags
        logger.info("tag_hr_provider.market_tags_loaded", count=len(market_tags))

        # Step 2: For each tag, compute qualified traders
        for tag in self._target_tags:
            tag_p = self._tag_params[tag]
            min_trades = tag_p["min_trades"]
            excess_hr_pp = tag_p["excess_hr_pp"]
            max_avg_entry_price = tag_p["max_avg_entry_price"]

            qualified = self._compute_tag_pool(
                ch, tag, train_start_str, train_end_str,
                min_trades, excess_hr_pp, max_avg_entry_price,
            )
            self._qualified_traders[tag] = frozenset(qualified)
            logger.info(
                "tag_hr_provider.tag_pool",
                tag=tag,
                n_qualified=len(qualified),
                min_trades=min_trades,
                excess_hr_pp=excess_hr_pp,
                max_avg_entry_price=max_avg_entry_price,
            )

        self._initialized = True

    def _compute_tag_pool(
        self,
        ch: Any,
        tag: str,
        train_start: str,
        train_end: str,
        min_trades: int,
        excess_hr_pp: float,
        max_avg_entry_price: float,
    ) -> list[str]:
        """Compute qualified traders for a single tag."""
        # Tag-level base rate in training window
        base_rate_sql = f"""
        SELECT
            countIf(p.correct = 1) / greatest(count(*), 1) AS yes_hr
        FROM maker_positions_resolved_corrected p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE t.label = '{tag}'
          AND p.position = 'YES'
          AND toDate(p.resolved_at) >= '{train_start}'
          AND toDate(p.resolved_at) < '{train_end}'
          AND p.yes_won IS NOT NULL
        """
        base_rows = ch.query(base_rate_sql)
        base_rate = float(base_rows.result_rows[0][0]) if base_rows.result_rows else 0.38
        self._tag_base_rates[tag] = base_rate
        logger.info("tag_hr_provider.base_rate", tag=tag, base_rate=round(base_rate, 4))

        threshold_hr = base_rate + excess_hr_pp / 100.0

        # Step 1: Qualified traders by HR
        hr_sql = f"""
        SELECT
            p.trader,
            count(*) AS n_trades,
            countIf(p.correct = 1) / greatest(count(*), 1) AS hit_rate
        FROM maker_positions_resolved_corrected p
        INNER JOIN markets m ON p.condition_id = m.condition_id
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE t.label = '{tag}'
          AND p.position = 'YES'
          AND toDate(p.resolved_at) >= '{train_start}'
          AND toDate(p.resolved_at) < '{train_end}'
          AND p.yes_won IS NOT NULL
        GROUP BY p.trader
        HAVING n_trades >= {min_trades}
          AND hit_rate >= {threshold_hr:.6f}
        """
        hr_rows = ch.query(hr_sql)
        hr_qualified = {str(row[0]).lower() for row in hr_rows.result_rows}
        if not hr_qualified:
            return []

        # Step 2: Filter by avg entry price using Memory temp tables
        # (Avoids CH IN-clause size limits and alias-in-join bugs)
        traders_quoted = ", ".join(f"'{t}'" for t in hr_qualified)

        # Create temp tables for this tag's qualified traders and YES asset_ids
        ch.command(f"""
        CREATE OR REPLACE TABLE _tmp_thr_traders ENGINE = Memory AS
        SELECT lower(trader) AS trader
        FROM (SELECT arrayJoin([{traders_quoted}]) AS trader)
        """)

        ch.command(f"""
        CREATE OR REPLACE TABLE _tmp_thr_assets ENGINE = Memory AS
        SELECT DISTINCT tmm.asset_id
        FROM token_market_map tmm
        INNER JOIN markets m ON tmm.condition_id = m.condition_id
        INNER JOIN events e ON m.event_id = e.id
        INNER JOIN event_tags et ON e.id = et.event_id
        INNER JOIN tags t ON et.tag_id = t.id
        WHERE t.label = '{tag}' AND tmm.outcome = 'YES'
        """)

        price_sql = f"""
        SELECT
            lower(trader) AS t,
            sum(price_x_vol) / greatest(sum(volume), 0.001) AS avg_p
        FROM trader_trade_agg FINAL
        WHERE lower(trader) IN (SELECT trader FROM _tmp_thr_traders)
          AND asset_id IN (SELECT asset_id FROM _tmp_thr_assets)
          AND toDate(last_trade) >= '{train_start}'
          AND toDate(last_trade) < '{train_end}'
        GROUP BY trader
        HAVING avg_p <= {max_avg_entry_price:.6f}
        """
        price_rows = ch.query(price_sql)
        price_qualified = {str(row[0]).lower() for row in price_rows.result_rows}

        return list(hr_qualified & price_qualified)

    async def on_trade(self, trade: Any) -> None:
        """No-op for tick-by-tick replay (pool is static)."""
        pass

    async def refresh(self, backend: Any) -> None:
        """Re-compute pool (called periodically in live mode)."""
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        """Expose qualified traders and market tags for strategy consumption."""
        return {
            "qualified_traders": self._qualified_traders,
            "market_tags": self._market_tags,
            "tag_base_rates": self._tag_base_rates,
        }
