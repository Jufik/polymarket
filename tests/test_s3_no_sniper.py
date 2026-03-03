"""Tests for S3 NO Sniper production strategy."""

from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.models import NormalizedTrade
from polymarket_pipeline.strategies_impl.s3_no_sniper.strategy import (
    S3Config,
    S3NoSniperStrategy,
    create_s3_no_sniper_strategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trade(
    cid: str = "cid_tech",
    price: float = 0.30,
    ts: float = 1000.0,
    asset_id: str = "asset_yes_1",
    side: str = "BUY",
) -> NormalizedTrade:
    return NormalizedTrade(
        trade_id=f"test:{cid}:{ts}",
        condition_id=cid,
        asset_id=asset_id,
        side=side,
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(round(price * 100, 2))),
        fee_usd=Decimal("0"),
        maker="0xmaker",
        taker="0xtaker",
        timestamp=datetime.datetime.fromtimestamp(ts, tz=datetime.UTC),
        source="goldsky_subgraph",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=True,
        version=2,
        published_at=ts,
    )


def _make_ctx(
    tag_map: dict | None = None,
    token_map: dict | None = None,
    known_market_ids: frozenset | None = None,
) -> AsyncMock:
    """Create a mock StrategyContext with S3 provider features."""
    ctx = AsyncMock()
    features = {
        "tag_map": tag_map or {
            "cid_tech": "Tech",
            "cid_economy": "Economy",
            "cid_trump": "Trump",
            "cid_crypto": "Crypto",
        },
        "token_map": token_map or {
            "cid_tech": {"YES": "asset_yes_1", "NO": "asset_no_1"},
            "cid_economy": {"YES": "asset_yes_3", "NO": "asset_no_3"},
            "cid_trump": {"YES": "asset_yes_2", "NO": "asset_no_2"},
            "cid_crypto": {"YES": "asset_yes_4", "NO": "asset_no_4"},
        },
        "known_market_ids": known_market_ids or frozenset(),
    }
    ctx.get_features.return_value = features
    ctx.get_position.return_value = None
    return ctx


def _make_strat(**kwargs: object) -> S3NoSniperStrategy:
    cfg = S3Config(**kwargs)  # type: ignore[arg-type]
    return S3NoSniperStrategy(cfg)


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_creates_strategy(self) -> None:
        from polymarket_pipeline.strategies.config import StrategyConfig
        from polymarket_pipeline.strategies.types import ExecutionMode

        config = StrategyConfig(
            name="s3_no_sniper",
            enabled=True,
            mode=ExecutionMode.PAPER_DEV,
            capital_usd=1000,
            max_position_usd=10,
            max_open_positions=100,
            cooldown_s=0,
            params={
                "eligible_tags": ["Economy", "Tech"],
                "min_yes_price": 0.15,
                "max_yes_price": 0.50,
                "max_market_age_s": 300,
                "spread_buffer": 0.02,
                "position_size_usd": 10.0,
            },
        )
        strat = create_s3_no_sniper_strategy(config)
        assert strat.name == "s3_no_sniper"
        assert strat._cfg.eligible_tags == frozenset({"Economy", "Tech"})
        assert strat._cfg.min_yes_price == 0.15
        assert strat._cfg.max_yes_price == 0.50
        assert strat._cfg.max_market_age_s == 300

    def test_factory_uses_defaults(self) -> None:
        from polymarket_pipeline.strategies.config import StrategyConfig
        from polymarket_pipeline.strategies.types import ExecutionMode

        config = StrategyConfig(
            name="s3_test",
            enabled=True,
            mode=ExecutionMode.PAPER_DEV,
            capital_usd=1000,
            max_position_usd=10,
            max_open_positions=100,
            cooldown_s=0,
        )
        strat = create_s3_no_sniper_strategy(config)
        assert strat.name == "s3_test"
        assert strat._cfg.eligible_tags == frozenset({"Economy", "Tech"})
        assert strat._cfg.position_size_usd == 10.0


# ---------------------------------------------------------------------------
# Tag filter tests
# ---------------------------------------------------------------------------


class TestTagFilter:
    @pytest.mark.asyncio
    async def test_eligible_tag_enters(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.30, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is not None
        assert len(result) == 1
        assert result[0].side == "BUY"
        assert result[0].outcome == "NO"

    @pytest.mark.asyncio
    async def test_economy_tag_enters(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_economy", price=0.35, ts=1000.0, asset_id="asset_yes_3")
        result = await strat.on_trade(trade, ctx)
        assert result is not None
        assert result[0].outcome == "NO"

    @pytest.mark.asyncio
    async def test_ineligible_tag_rejected(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_crypto", price=0.30, ts=1000.0, asset_id="asset_yes_4")
        result = await strat.on_trade(trade, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_market_rejected(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_unknown", price=0.30, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Hardcoded safety tests
# ---------------------------------------------------------------------------


class TestHardcodedSafety:
    @pytest.mark.asyncio
    async def test_trump_excluded_even_if_in_eligible_tags(self) -> None:
        """Trump is rejected by _EXCLUDED_TAGS even if TOML includes it."""
        strat = _make_strat(eligible_tags=frozenset({"Economy", "Tech", "Trump"}))
        ctx = _make_ctx()
        trade = _make_trade("cid_trump", price=0.30, ts=1000.0, asset_id="asset_yes_2")
        result = await strat.on_trade(trade, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_crypto_excluded_even_if_in_eligible_tags(self) -> None:
        """Crypto is rejected by _EXCLUDED_TAGS even if TOML includes it."""
        strat = _make_strat(eligible_tags=frozenset({"Economy", "Crypto"}))
        ctx = _make_ctx()
        trade = _make_trade("cid_crypto", price=0.30, ts=1000.0, asset_id="asset_yes_4")
        result = await strat.on_trade(trade, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Price zone filter tests
# ---------------------------------------------------------------------------


class TestPriceZone:
    @pytest.mark.asyncio
    async def test_below_min_rejected(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.10, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_above_max_rejected(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.55, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_at_min_boundary_accepted(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.15, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_at_max_boundary_accepted(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.50, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is not None


# ---------------------------------------------------------------------------
# NO token price inversion tests
# ---------------------------------------------------------------------------


class TestNOTokenInversion:
    @pytest.mark.asyncio
    async def test_no_token_trade_price_inverted(self) -> None:
        """Trade for NO token at 0.70 → YES price = 0.30 (in zone)."""
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.70, ts=1000.0, asset_id="asset_no_1")
        result = await strat.on_trade(trade, ctx)
        assert result is not None
        assert result[0].outcome == "NO"
        # max_price = (1 - 0.30) + 0.02 = 0.72
        assert result[0].max_price == pytest.approx(0.72, abs=0.001)

    @pytest.mark.asyncio
    async def test_no_token_outside_zone_rejected(self) -> None:
        """NO token at 0.30 → YES price = 0.70, outside zone."""
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.30, ts=1000.0, asset_id="asset_no_1")
        result = await strat.on_trade(trade, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Market age filter tests
# ---------------------------------------------------------------------------


class TestMarketAge:
    @pytest.mark.asyncio
    async def test_first_trade_enters(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.30, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_within_window_enters(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        # First trade: too expensive
        t1 = _make_trade("cid_economy", price=0.60, ts=1000.0, asset_id="asset_yes_3")
        r1 = await strat.on_trade(t1, ctx)
        assert r1 is None
        # Second trade: 2 min later, good price
        t2 = _make_trade("cid_economy", price=0.35, ts=1120.0, asset_id="asset_yes_3")
        r2 = await strat.on_trade(t2, ctx)
        assert r2 is not None

    @pytest.mark.asyncio
    async def test_after_window_rejected(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        # First trade establishes market birth
        t1 = _make_trade("cid_tech", price=0.60, ts=1000.0)
        await strat.on_trade(t1, ctx)
        # 6 minutes later (360s > 300s)
        t2 = _make_trade("cid_tech", price=0.30, ts=1360.0)
        result = await strat.on_trade(t2, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_custom_max_age(self) -> None:
        strat = _make_strat(max_market_age_s=180)
        ctx = _make_ctx()
        t1 = _make_trade("cid_tech", price=0.60, ts=1000.0)
        await strat.on_trade(t1, ctx)
        # 240s > 180s
        t2 = _make_trade("cid_tech", price=0.30, ts=1240.0)
        result = await strat.on_trade(t2, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Known market skip tests
# ---------------------------------------------------------------------------


class TestKnownMarketSkip:
    @pytest.mark.asyncio
    async def test_known_market_always_rejected(self) -> None:
        """Markets with trades before startup are always too old."""
        strat = _make_strat()
        ctx = _make_ctx(known_market_ids=frozenset({"cid_tech"}))
        trade = _make_trade("cid_tech", price=0.30, ts=1000.0)
        result = await strat.on_trade(trade, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_new_market_after_startup_enters(self) -> None:
        """Markets NOT in known_market_ids are genuinely new."""
        strat = _make_strat()
        # cid_tech is known, but cid_economy is not
        ctx = _make_ctx(known_market_ids=frozenset({"cid_tech"}))
        trade = _make_trade("cid_economy", price=0.35, ts=1000.0, asset_id="asset_yes_3")
        result = await strat.on_trade(trade, ctx)
        assert result is not None


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------


class TestDedup:
    @pytest.mark.asyncio
    async def test_second_entry_blocked(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        t1 = _make_trade("cid_tech", price=0.30, ts=1000.0)
        r1 = await strat.on_trade(t1, ctx)
        assert r1 is not None
        # Second trade in same market
        t2 = _make_trade("cid_tech", price=0.25, ts=1060.0)
        r2 = await strat.on_trade(t2, ctx)
        assert r2 is None

    @pytest.mark.asyncio
    async def test_different_markets_both_enter(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        t1 = _make_trade("cid_tech", price=0.30, ts=1000.0)
        r1 = await strat.on_trade(t1, ctx)
        assert r1 is not None
        t2 = _make_trade("cid_economy", price=0.35, ts=1010.0, asset_id="asset_yes_3")
        r2 = await strat.on_trade(t2, ctx)
        assert r2 is not None


# ---------------------------------------------------------------------------
# SELL side ignored
# ---------------------------------------------------------------------------


class TestSellIgnored:
    @pytest.mark.asyncio
    async def test_sell_side_no_signal(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_tech", price=0.30, ts=1000.0, side="SELL")
        result = await strat.on_trade(trade, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Intent field correctness
# ---------------------------------------------------------------------------


class TestIntentFields:
    @pytest.mark.asyncio
    async def test_intent_fields_correct(self) -> None:
        strat = _make_strat()
        ctx = _make_ctx()
        trade = _make_trade("cid_economy", price=0.40, ts=1000.0, asset_id="asset_yes_3")
        result = await strat.on_trade(trade, ctx)
        assert result is not None
        intent = result[0]
        assert intent.strategy == "s3_no_sniper"
        assert intent.condition_id == "cid_economy"
        assert intent.side == "BUY"
        assert intent.outcome == "NO"
        assert intent.size_usd == 10.0
        assert intent.asset_id == "asset_no_3"
        # max_price = (1 - 0.40) + 0.02 = 0.62
        assert intent.max_price == pytest.approx(0.62, abs=0.001)
        assert "s3_snipe" in intent.reason
        assert "Economy" in intent.reason

    @pytest.mark.asyncio
    async def test_no_exit_logic(self) -> None:
        """on_timer and on_market_update return None (hold to resolution)."""
        strat = _make_strat()
        ctx = _make_ctx()
        assert await strat.on_timer(1000.0, ctx) is None
        assert await strat.on_market_update({}, ctx) is None
