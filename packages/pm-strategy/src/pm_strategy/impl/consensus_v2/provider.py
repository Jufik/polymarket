"""ConsensusV2Provider -- builds composite-ranked trader pool from ClickHouse."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_GAMBLING_PATTERNS = [
    "lower(slug) LIKE '%updown%'",
    "lower(slug) LIKE '%up-or-down%'",
    """(
        (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (
            lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
            OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
            OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%sol%'
            OR lower(slug) LIKE '%-close-%'
            OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%nvda%'
            OR lower(slug) LIKE '%aapl%' OR lower(slug) LIKE '%amzn%'
        )
    )""",
]
_GAMBLING_WHERE = " OR ".join(_GAMBLING_PATTERNS)


class ConsensusV2Provider:
    """Feature provider for consensus-v2 strategy."""

    name: str = "consensus_v2"

    def __init__(
        self,
        tag: str = "Sports",
        k: int = 25,
        lookback_months: int = 12,
        min_markets: int = 20,
        min_conviction: float = 0.90,
        ch_host: str = "192.168.0.148",
        ch_port: int = 18123,
        ch_database: str = "polymarket",
        **kwargs: Any,
    ) -> None:
        if "name" in kwargs:
            self.name = kwargs.pop("name")
        self._tag = tag
        self._k = k
        self._lookback_months = lookback_months
        self._min_markets = min_markets
        self._min_conviction = min_conviction
        self._ch_host = ch_host
        self._ch_port = ch_port
        self._ch_database = ch_database

        self._pool: frozenset[str] = frozenset()
        self._tag_markets: frozenset[str] = frozenset()
        self._gambling_markets: frozenset[str] = frozenset()
        self._token_map: dict[str, dict[str, str]] = {}
        self._initialized = False

    def _get_ch(self) -> Any:
        import clickhouse_connect

        return clickhouse_connect.get_client(
            host=self._ch_host,
            port=self._ch_port,
            database=self._ch_database,
        )

    async def compute(self, backend: Any) -> None:
        import asyncio

        await asyncio.get_event_loop().run_in_executor(None, self._compute_sync)

    def _compute_sync(self) -> None:
        import datetime

        ch = self._get_ch()

        train_end = datetime.datetime.now(datetime.UTC)
        train_start = train_end - datetime.timedelta(days=30 * self._lookback_months)
        train_start_str = train_start.strftime("%Y-%m-%d")
        train_end_str = train_end.strftime("%Y-%m-%d")

        logger.info(
            "consensus_v2_provider.compute",
            tag=self._tag,
            k=self._k,
            train_window=f"{train_start_str} to {train_end_str}",
        )

        gambling_rows = ch.query(f"""
            SELECT condition_id FROM markets
            WHERE {_GAMBLING_WHERE}
        """)
        self._gambling_markets = frozenset(str(r[0]) for r in gambling_rows.result_rows)

        tag_rows = ch.query(f"""
            SELECT DISTINCT m.condition_id
            FROM markets m
            INNER JOIN events e ON m.event_id = e.id
            INNER JOIN event_tags et ON e.id = et.event_id
            INNER JOIN tags t ON et.tag_id = t.id
            WHERE t.label = '{self._tag}'
              AND m.condition_id NOT IN (
                  SELECT condition_id FROM markets WHERE {_GAMBLING_WHERE}
              )
        """)
        self._tag_markets = frozenset(str(r[0]) for r in tag_rows.result_rows)

        token_rows = ch.query("""
            SELECT condition_id, outcome, asset_id
            FROM token_market_map
        """)
        token_map: dict[str, dict[str, str]] = {}
        for r in token_rows.result_rows:
            cid = str(r[0])
            if cid not in token_map:
                token_map[cid] = {}
            token_map[cid][str(r[1])] = str(r[2])
        self._token_map = token_map

        pool = self._build_composite_pool(ch, train_start_str, train_end_str)
        self._pool = frozenset(pool)

        self._initialized = True
        logger.info(
            "consensus_v2_provider.ready",
            tag=self._tag,
            pool_size=len(self._pool),
            tag_markets=len(self._tag_markets),
            gambling_excluded=len(self._gambling_markets),
        )

    def _build_composite_pool(
        self,
        ch: Any,
        train_start: str,
        train_end: str,
    ) -> set[str]:
        tag = self._tag
        k = self._k
        min_mkts = self._min_markets
        min_conv = self._min_conviction

        base_row = ch.query(f"""
            SELECT countIf(p.correct = 1) / greatest(count(*), 1) AS base_rate
            FROM (SELECT * FROM maker_positions_resolved_corrected FINAL) p
            INNER JOIN markets m ON p.condition_id = m.condition_id
            INNER JOIN events e ON m.event_id = e.id
            INNER JOIN event_tags et ON e.id = et.event_id
            INNER JOIN tags t ON et.tag_id = t.id
            WHERE t.label = '{tag}'
              AND p.position = 'YES'
              AND toDate(p.resolved_at) >= '{train_start}'
              AND toDate(p.resolved_at) < '{train_end}'
              AND p.yes_won IS NOT NULL
              AND NOT ({_GAMBLING_WHERE.replace("slug", "m.slug")})
        """)
        base_rate = float(base_row.result_rows[0][0]) if base_row.result_rows else 0.38

        ch.command(f"""
            CREATE OR REPLACE TABLE _cv2_train ENGINE = Memory AS
            SELECT
                p.trader,
                count(DISTINCT p.condition_id) AS n_markets,
                countIf(p.correct = 1) / greatest(count(*), 1) AS hit_rate,
                (countIf(p.correct = 1) / greatest(count(*), 1)) - {base_rate:.6f} AS excess_hr,
                median(p.net_usd) AS avg_edge_usd
            FROM (SELECT * FROM maker_positions_resolved_corrected FINAL) p
            INNER JOIN markets m ON p.condition_id = m.condition_id
            INNER JOIN events e ON m.event_id = e.id
            INNER JOIN event_tags et ON e.id = et.event_id
            INNER JOIN tags t ON et.tag_id = t.id
            WHERE t.label = '{tag}'
              AND p.position = 'YES'
              AND toDate(p.resolved_at) >= '{train_start}'
              AND toDate(p.resolved_at) < '{train_end}'
              AND p.yes_won IS NOT NULL
              AND NOT ({_GAMBLING_WHERE.replace("slug", "m.slug")})
            GROUP BY p.trader
            HAVING n_markets >= {min_mkts}
              AND countIf(p.volume > 0) / greatest(count(*), 1) >= {min_conv:.2f}
              AND count(*) < 10000
              AND excess_hr > 0
        """)

        ch.command(f"""
            CREATE OR REPLACE TABLE _cv2_consistency ENGINE = Memory AS
            SELECT sub.trader AS trader, sub.consistency_sharpe AS consistency_sharpe
            FROM (
                SELECT
                    monthly.trader AS trader,
                    avg(monthly.month_hr) / (
                        if(stddevSamp(monthly.month_hr) > 0,
                           stddevSamp(monthly.month_hr), 0.05)
                        + 0.05) AS consistency_sharpe
                FROM (
                    SELECT
                        p.trader AS trader,
                        toStartOfMonth(p.resolved_at) AS month,
                        countIf(p.correct = 1) / greatest(count(*), 1) AS month_hr,
                        count(DISTINCT p.condition_id) AS n_pos
                    FROM (SELECT * FROM maker_positions_resolved_corrected FINAL) AS p
                    INNER JOIN _cv2_train AS tr ON p.trader = tr.trader
                    INNER JOIN markets AS m ON p.condition_id = m.condition_id
                    INNER JOIN events AS e ON m.event_id = e.id
                    INNER JOIN event_tags AS et ON e.id = et.event_id
                    INNER JOIN tags AS tg ON et.tag_id = tg.id
                    WHERE tg.label = '{tag}'
                      AND p.position = 'YES'
                      AND toDate(p.resolved_at) >= '{train_start}'
                      AND toDate(p.resolved_at) < '{train_end}'
                      AND p.yes_won IS NOT NULL
                    GROUP BY p.trader, month
                    HAVING n_pos >= 5
                ) AS monthly
                GROUP BY monthly.trader
                HAVING count(*) >= 6
            ) AS sub
        """)

        rows = ch.query(f"""
            WITH base AS (
                SELECT
                    t.trader,
                    t.excess_hr,
                    t.avg_edge_usd,
                    coalesce(c.consistency_sharpe, 0.0) AS consistency_sharpe
                FROM _cv2_train t
                LEFT JOIN _cv2_consistency c ON t.trader = c.trader
            ),
            ranked AS (
                SELECT *,
                    percent_rank() OVER (ORDER BY excess_hr) AS pr_excess_hr,
                    percent_rank() OVER (ORDER BY consistency_sharpe) AS pr_consistency,
                    percent_rank() OVER (ORDER BY avg_edge_usd) AS pr_edge
                FROM base
            ),
            scored AS (
                SELECT
                    trader,
                    (0.50 * pr_excess_hr + 0.30 * pr_consistency
                     + 0.20 * pr_edge) AS composite_score
                FROM ranked
            )
            SELECT trader
            FROM scored
            ORDER BY composite_score DESC
            LIMIT {k}
        """)

        pool = {str(r[0]).lower() for r in rows.result_rows}
        logger.info(
            "consensus_v2_provider.pool_built",
            tag=tag,
            base_rate=round(base_rate, 4),
            pool_size=len(pool),
        )
        return pool

    async def on_trade(self, trade: Any) -> None:
        """No-op -- pool is static between refreshes."""

    async def refresh(self, backend: Any) -> None:
        await self.compute(backend)

    def get_features(self) -> dict[str, Any]:
        return {
            self.name: {
                "pool": self._pool,
                "tag_markets": self._tag_markets,
                "gambling_markets": self._gambling_markets,
                "token_map": self._token_map,
            },
            "pool_traders": self._pool,
        }
