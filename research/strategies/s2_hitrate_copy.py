"""S2 Hit-Rate Copy — tiered conviction copy-trading strategy.

Copies trades from skilled traders (excess hit rate above direction-specific
base rate). Two-tier entry: seed position on low consensus, scale to full
on high consensus.

Usage:
    from research.strategies.s2_hitrate_copy import S2HitRateCopy, S2Config
    strat = S2HitRateCopy(S2Config(min_excess_hr=0.10, scale_threshold=4))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from polymarket_pipeline.strategies.types import TradeIntent

if TYPE_CHECKING:
    from polymarket_pipeline.models import NormalizedTrade
    from polymarket_pipeline.strategies.protocol import StrategyContext


# Base rates (from research/knowledge/data/market_base_rates.md)
YES_BASE_RATE = 0.381
NO_BASE_RATE = 0.619


@dataclass(frozen=True)
class S2Config:
    """Strategy parameters — all sweepable."""

    min_positions: int = 30
    min_excess_hr: float = 0.10
    seed_threshold: int = 1
    scale_threshold: int = 4
    seed_pct: float = 0.25
    seed_timeout_hours: float | None = None
    direction: Literal["YES", "NO", "BOTH"] = "BOTH"
    recency_months: int = 6
    position_size_usd: float = 100.0
    max_entry_price: float = 0.95
    exclude_tags: tuple[str, ...] = ("Sports", "Weather")
    max_signal_price: float = 0.85
    use_bayesian_hr: bool = True
    yes_weight: float = 1.0
    no_weight: float = 1.0
    max_consensus_window_hours: float | None = None
    max_hold_hours: float | None = None


class S2HitRateCopy:
    """Tiered conviction copy strategy."""

    name: str = "s2_hitrate_copy"

    def __init__(self, config: S2Config) -> None:
        self._cfg = config
        # Qualified traders: set of lowercase addresses
        self._qualified: set[str] = set()
        # Consensus: condition_id -> {direction -> set(trader_address)}
        self._consensus: dict[str, dict[str, set[str]]] = {}
        # Track which markets already have seed/full positions
        self._seeded: set[str] = set()
        self._scaled: set[str] = set()
        # Seed timestamps for timeout logic
        self._seed_times: dict[str, float] = {}
        # Token map: asset_id -> {"condition_id": str, "outcome": str}
        self._token_map: dict[str, dict[str, str]] = {}
        # First qualified trade time per market (for velocity check)
        self._first_trade_time: dict[str, float] = {}
        # Scale entry times (for hold timeout)
        self._scale_times: dict[str, float] = {}

    def set_qualified_traders(self, traders: set[str]) -> None:
        """Set the qualified trader pool (called by provider or test setup)."""
        self._qualified = {t.lower() for t in traders}

    def set_token_map(self, token_map: dict[str, dict[str, str]]) -> None:
        """Set asset_id -> {condition_id, outcome} mapping for direction-aware sizing."""
        self._token_map = token_map

    async def on_trade(
        self,
        trade: NormalizedTrade,
        ctx: StrategyContext,
    ) -> list[TradeIntent] | None:
        # 1. SELL is exit, not signal
        if trade.side != "BUY":
            return None

        # 2. Must be a qualified trader
        maker = (trade.maker or "").lower()
        if maker not in self._qualified:
            return None

        # 3. Filter expensive signals
        if float(trade.price) > self._cfg.max_signal_price:
            return None

        # 4. Already at full position
        cid = trade.condition_id
        if cid in self._scaled:
            return None

        # 5. Track consensus per condition_id (unique traders)
        if cid not in self._consensus:
            self._consensus[cid] = {"traders": set()}
        self._consensus[cid]["traders"].add(maker)
        count = len(self._consensus[cid]["traders"])

        now = trade.published_at
        price = float(trade.price)

        # 5b. Track first qualified trade time (for velocity check)
        if cid not in self._first_trade_time:
            self._first_trade_time[cid] = now

        # 6. Resolve outcome from token_map (for direction-aware sizing)
        outcome = "YES"  # default if no token_map
        token_info = self._token_map.get(trade.asset_id)
        if token_info:
            outcome = token_info.get("outcome", "YES")
        weight = self._cfg.yes_weight if outcome == "YES" else self._cfg.no_weight

        # 7. Check tiered entry
        if count >= self._cfg.scale_threshold and cid not in self._scaled:
            # Velocity gate: consensus must form within window
            if self._cfg.max_consensus_window_hours is not None:
                window_s = self._cfg.max_consensus_window_hours * 3600
                first = self._first_trade_time.get(cid, now)
                if now - first > window_s:
                    return None  # consensus too slow

            # Scale: emit remaining portion
            self._scaled.add(cid)
            self._scale_times[cid] = now
            remaining_pct = 1.0 - self._cfg.seed_pct if cid in self._seeded else 1.0
            size = self._cfg.position_size_usd * remaining_pct * weight
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome=outcome,
                    size_usd=size,
                    urgency="patient",
                    max_price=min(price + 0.02, 0.95),
                    reason=f"consensus={count} (scale)",
                    signal_time=now,
                    asset_id=trade.asset_id,
                ),
            ]

        if count >= self._cfg.seed_threshold and cid not in self._seeded:
            # Seed: emit small position
            self._seeded.add(cid)
            self._seed_times[cid] = now
            size = self._cfg.position_size_usd * self._cfg.seed_pct * weight
            return [
                TradeIntent(
                    strategy=self.name,
                    condition_id=cid,
                    side="BUY",
                    outcome=outcome,
                    size_usd=size,
                    urgency="patient",
                    max_price=min(price + 0.02, 0.95),
                    reason=f"consensus={count} (seed)",
                    signal_time=now,
                    asset_id=trade.asset_id,
                ),
            ]

        return None

    async def on_market_update(
        self, update: object, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        return None

    async def on_timer(
        self, now: float, ctx: StrategyContext
    ) -> list[TradeIntent] | None:
        exits: list[TradeIntent] = []

        # 1. Seed timeout: exit seed positions that never reached scale
        if self._cfg.seed_timeout_hours is not None:
            timeout_s = self._cfg.seed_timeout_hours * 3600
            expired_seeds = []
            for cid, seed_time in self._seed_times.items():
                if cid in self._scaled:
                    continue  # already at full position
                if now - seed_time >= timeout_s:
                    expired_seeds.append(cid)
                    exits.append(
                        TradeIntent(
                            strategy=self.name,
                            condition_id=cid,
                            side="SELL",
                            outcome="YES",
                            size_usd=self._cfg.position_size_usd * self._cfg.seed_pct,
                            urgency="patient",
                            max_price=None,
                            reason=f"seed_timeout ({self._cfg.seed_timeout_hours}h)",
                            signal_time=now,
                            asset_id=None,
                        ),
                    )
            for cid in expired_seeds:
                del self._seed_times[cid]
                self._seeded.discard(cid)

        # 2. Hold timeout: exit scaled positions held too long
        if self._cfg.max_hold_hours is not None:
            hold_timeout_s = self._cfg.max_hold_hours * 3600
            expired_holds = []
            for cid, scale_time in self._scale_times.items():
                if now - scale_time >= hold_timeout_s:
                    expired_holds.append(cid)
                    exits.append(
                        TradeIntent(
                            strategy=self.name,
                            condition_id=cid,
                            side="SELL",
                            outcome="YES",
                            size_usd=self._cfg.position_size_usd,  # full position
                            urgency="patient",
                            max_price=None,
                            reason=f"max_hold_timeout ({self._cfg.max_hold_hours}h)",
                            signal_time=now,
                            asset_id=None,
                        ),
                    )
            for cid in expired_holds:
                del self._scale_times[cid]
                self._scaled.discard(cid)

        return exits if exits else None

    @staticmethod
    def compute_base_rates_query(recency_months: int = 6) -> str:
        """CH SQL to compute YES/NO base rates for the recency window."""
        return f"""
            SELECT
                countIf(yes_won = 1) / count(*) AS yes_base_rate,
                countIf(yes_won = 0) / count(*) AS no_base_rate
            FROM (
                SELECT DISTINCT condition_id, yes_won
                FROM markets_resolved
                WHERE outcome = 'YES'
                  AND toDate(resolved_at) >= toDate(now()) - INTERVAL {recency_months} MONTH
            )
        """

    # Tag labels used for category exclusion (tag-based, not m.category which is 99% NULL)
    _SPORTS_TAGS = (
        "Sports", "Games", "Basketball", "Soccer", "Esports", "NBA", "NCAA",
        "Tennis", "NFL", "NCAA Basketball", "Cricket", "NHL", "CFB", "Hockey",
        "MLB", "Golf", "EPL", "UFC", "Formula 1", "f1", "MLS", "Olympics",
        "counter strike 2", "Dota 2", "league of legends", "Valorant",
        "Honor of Kings",
    )
    _WEATHER_TAGS = ("Weather", "Science")

    _CATEGORY_TAG_MAP: dict[str, tuple[str, ...]] = {
        "Sports": _SPORTS_TAGS,
        "Weather": _WEATHER_TAGS,
    }

    @staticmethod
    def qualified_traders_query(
        min_positions: int = 30,
        min_excess_hr: float = 0.10,
        recency_months: int = 6,
        direction: str = "BOTH",
        max_entry_price: float = 0.95,
        exclude_tags: tuple[str, ...] = ("Sports", "Weather"),
        yes_base_rate: float | None = None,
        no_base_rate: float | None = None,
        use_bayesian_hr: bool = False,
    ) -> str:
        """Build CH SQL for qualified traders with direction-aware excess HR.

        Returns trader, position (YES/NO), wins, total, hit_rate, excess_hr.
        Excess HR = hit_rate - base_rate(direction).

        Category exclusion uses tag-based joins (markets -> events -> event_tags -> tags)
        because markets.category is 99.3% NULL.
        """
        yes_br = yes_base_rate if yes_base_rate is not None else YES_BASE_RATE
        no_br = no_base_rate if no_base_rate is not None else NO_BASE_RATE

        dir_filter = ""
        if direction == "YES":
            dir_filter = "AND p.position = 'YES'"
        elif direction == "NO":
            dir_filter = "AND p.position = 'NO'"

        # Entry price filter: compute direction-adjusted entry price
        price_filter = ""
        if max_entry_price < 1.0:
            price_filter = f"""AND (
                      CASE WHEN position = 'YES' THEN avg_yes_price
                           ELSE 1.0 - avg_yes_price END
                  ) < {max_entry_price}"""

        # Tag-based category exclusion (builds a CTE of excluded condition_ids)
        category_cte = ""
        category_filter = ""
        if exclude_tags:
            # Collect all tag labels to exclude
            all_tags: list[str] = []
            for cat in exclude_tags:
                tags = S2HitRateCopy._CATEGORY_TAG_MAP.get(cat, (cat,))
                all_tags.extend(tags)
            tag_list = ", ".join(f"'{t}'" for t in all_tags)
            category_cte = f"""
            WITH excluded_markets AS (
                SELECT DISTINCT m2.condition_id
                FROM markets AS m2
                INNER JOIN events AS e2 ON m2.event_id = e2.id
                INNER JOIN event_tags AS et2 ON e2.id = et2.event_id
                INNER JOIN tags AS t2 ON et2.tag_id = t2.id
                WHERE t2.label IN ({tag_list})
            )"""
            category_filter = "AND p.condition_id NOT IN (SELECT condition_id FROM excluded_markets)"

        # HR formula: raw or Bayesian
        if use_bayesian_hr:
            hr_expr = (
                f"(if(p.position = 'YES', 3.81, 6.19) + countIf(p.correct = 1))"
                f" / (10.0 + count(*))"
            )
        else:
            hr_expr = "countIf(p.correct = 1) / count(*)"

        return f"""{category_cte}
            SELECT
                lower(p.trader) AS trader,
                p.position AS direction,
                countIf(p.correct = 1) AS wins,
                count(*) AS total,
                {hr_expr} AS hit_rate,
                {hr_expr} -
                    if(p.position = 'YES', {yes_br}, {no_br}) AS excess_hr
            FROM (
                SELECT * FROM trader_positions_resolved
                WHERE position IN ('YES', 'NO')
                  AND toDate(resolved_at) >= toDate(now()) - INTERVAL {recency_months} MONTH
                  {dir_filter}
                  {price_filter}
            ) AS p
            INNER JOIN markets AS m ON p.condition_id = m.condition_id
            WHERE m.question NOT LIKE '%Up or Down%'
              AND m.question NOT LIKE '%up or down%'
              {category_filter}
            GROUP BY trader, p.position
            HAVING count(*) >= {min_positions}
               AND excess_hr >= {min_excess_hr}
            ORDER BY excess_hr DESC
        """
