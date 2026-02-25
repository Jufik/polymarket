"""Tests for the will-no (favorite-longshot) strategy."""

from __future__ import annotations

import time
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock

import polars as pl
import pytest

from polymarket_pipeline.strategies.types import MarketInfo
from polymarket_pipeline.strategies_impl.will_no.config import (
    DEFAULT_PRICE_BANDS,
    WillNoConfig,
)
from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy

# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_will_no_config_defaults() -> None:
    cfg = WillNoConfig()
    assert cfg.yes_price_min == 0.15
    assert cfg.yes_price_max == 0.40
    assert cfg.base_bet_usd == 50.0
    assert cfg.fee_pct == 0.0
    assert cfg.avoid_keywords == frozenset({"reach", "hit"})
    assert cfg.prefer_keywords == frozenset({
        "between", "mlb", "prix", "grand",
        "league", "park", "traded", "fed",
    })
    assert cfg.max_volume_usd == 1000.0
    assert cfg.question_pattern == r"^Will\b"
    assert cfg.price_bands == DEFAULT_PRICE_BANDS
    assert cfg.max_bucket == "med"
    assert cfg.dual_sided is False


def test_will_no_config_custom() -> None:
    cfg = WillNoConfig(
        yes_price_min=0.10,
        yes_price_max=0.50,
        prefer_keywords=["above", "below"],
        avoid_keywords=["reach"],
        max_volume_usd=5000.0,
        price_bands=((0.10, 0.30, 1.0),),
    )
    assert cfg.yes_price_min == 0.10
    assert cfg.prefer_keywords == frozenset({"above", "below"})
    assert cfg.avoid_keywords == frozenset({"reach"})
    assert cfg.max_volume_usd == 5000.0
    assert cfg.price_bands == ((0.10, 0.30, 1.0),)


def test_will_no_config_is_frozen() -> None:
    cfg = WillNoConfig()
    with pytest.raises(AttributeError):
        cfg.yes_price_min = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# band_multiplier tests
# ---------------------------------------------------------------------------


def test_band_multiplier_sweet_spot() -> None:
    """Price in the 30-35% band should get multiplier 1.0 (data-derived)."""
    cfg = WillNoConfig()
    assert cfg.band_multiplier(0.32) == 1.00


def test_band_multiplier_lower_bands() -> None:
    cfg = WillNoConfig()
    assert cfg.band_multiplier(0.17) == 0.35
    assert cfg.band_multiplier(0.22) == 0.60


def test_band_multiplier_upper_bands() -> None:
    cfg = WillNoConfig()
    assert cfg.band_multiplier(0.27) == 0.80
    assert cfg.band_multiplier(0.37) == 0.90


def test_band_multiplier_outside_all_bands() -> None:
    """Prices outside all bands should return 0.0."""
    cfg = WillNoConfig()
    assert cfg.band_multiplier(0.05) == 0.0
    assert cfg.band_multiplier(0.60) == 0.0
    assert cfg.band_multiplier(0.41) == 0.0


def test_band_multiplier_boundary_lower() -> None:
    """Lower boundary of a band is inclusive."""
    cfg = WillNoConfig()
    assert cfg.band_multiplier(0.15) == 0.35
    assert cfg.band_multiplier(0.20) == 0.60  # goes to next band [0.20, 0.25)
    assert cfg.band_multiplier(0.25) == 0.80


def test_band_multiplier_boundary_upper_last() -> None:
    """Upper boundary of the last band is inclusive."""
    cfg = WillNoConfig()
    assert cfg.band_multiplier(0.40) == 0.90


def test_band_multiplier_empty_bands() -> None:
    """With no bands, band_multiplier always returns 0.0."""
    cfg = WillNoConfig(price_bands=())
    assert cfg.band_multiplier(0.25) == 0.0


def test_band_multiplier_custom_bands() -> None:
    cfg = WillNoConfig(price_bands=((0.10, 0.30, 0.5), (0.30, 0.50, 1.5)))
    assert cfg.band_multiplier(0.20) == 0.5
    assert cfg.band_multiplier(0.40) == 1.5
    assert cfg.band_multiplier(0.05) == 0.0


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

# Minimal config for tests that don't need prefer/avoid filtering.
_BARE_CFG_KWARGS: dict[str, Any] = {
    "prefer_keywords": frozenset(),
    "avoid_keywords": frozenset(),
}


def _make_trade(
    *,
    condition_id: str = "0xabc",
    price: float = 0.25,
    maker: str | None = "0xmaker1",
    side: str = "BUY",
    published_at: float | None = None,
) -> Any:
    """Create a minimal NormalizedTrade for testing."""
    from datetime import datetime
    from decimal import Decimal

    from polymarket_pipeline.models import NormalizedTrade

    return NormalizedTrade(
        trade_id=f"test:{condition_id}:{time.time()}",
        condition_id=condition_id,
        asset_id="0xasset",
        side=side,
        price=Decimal(str(price)),
        size=Decimal("10"),
        amount_usd=Decimal("100"),
        fee_usd=Decimal("0"),
        maker=maker,
        taker=None,
        timestamp=datetime.now(UTC),
        source="websocket",
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=1,
        published_at=published_at or time.time(),
    )


class _MockCtx:
    """Minimal StrategyContext mock."""

    def __init__(
        self,
        market: MarketInfo | None = None,
        features: dict[str, Any] | None = None,
    ) -> None:
        self._market = market
        self._features = features or {}

    async def get_market(self, condition_id: str) -> MarketInfo | None:
        return self._market

    async def get_features(self, key: str) -> Any:
        return self._features.get(key)

    async def get_position(self, condition_id: str) -> None:
        return None

    async def get_orderbook(self, condition_id: str) -> None:
        return None

    async def get_price(self, condition_id: str, outcome: str) -> float | None:
        return None

    async def now(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Event-driven tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_will_no_fires_on_qualifying_market() -> None:
    """Should emit BUY NO intent on a 'Will' market with YES in band."""
    cfg = WillNoConfig(**_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Trump visit Japan in 2026?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert len(result) == 1
    assert result[0].side == "BUY"
    assert result[0].outcome == "NO"
    assert result[0].strategy == "will_no"


@pytest.mark.asyncio
async def test_will_no_skips_non_will_question() -> None:
    """Questions not starting with 'Will' should be skipped."""
    cfg = WillNoConfig(**_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Bitcoin above $120K at 4PM ET?",
            active=True,
            yes_price=0.25,
            category="Crypto",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_skips_yes_price_outside_band() -> None:
    """YES price outside the configured band should be skipped."""
    cfg = WillNoConfig(**_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    # YES price too high (60%)
    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will inflation drop below 2%?",
            active=True,
            yes_price=0.60,
            category="Economics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.60)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_skips_avoid_keyword() -> None:
    """Markets with avoid keywords should be skipped."""
    cfg = WillNoConfig(avoid_keywords={"reach", "hit"}, prefer_keywords=frozenset())
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Bitcoin reach $500K?",
            active=True,
            yes_price=0.20,
            category="Crypto",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.20)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_skips_default_avoid_keywords() -> None:
    """Default avoid keywords ('reach', 'hit') are skipped."""
    cfg = WillNoConfig(prefer_keywords=frozenset())
    strategy = WillNoStrategy(config=cfg)

    for question in [
        "Will Bitcoin reach $500K?",
        "Will ETH hit $10K?",
    ]:
        ctx = _MockCtx(
            market=MarketInfo(
                condition_id=f"0x{hash(question) & 0xFFFF:04x}",
                question=question,
                active=True,
                yes_price=0.25,
                category="Crypto",
            ),
        )
        trade = _make_trade(
            condition_id=ctx._market.condition_id, price=0.25  # type: ignore[union-attr]
        )
        strategy._signaled.clear()
        result = await strategy.on_trade(trade, ctx)
        assert result is None, f"Should have been skipped: {question}"


@pytest.mark.asyncio
async def test_will_no_fires_once_per_market() -> None:
    """Signal should fire only once per condition_id."""
    cfg = WillNoConfig(**_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Trump visit Japan?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
    )
    trade1 = _make_trade(condition_id="0xabc", price=0.25)
    trade2 = _make_trade(condition_id="0xabc", price=0.22)

    r1 = await strategy.on_trade(trade1, ctx)
    r2 = await strategy.on_trade(trade2, ctx)
    assert r1 is not None
    assert r2 is None


@pytest.mark.asyncio
async def test_will_no_dual_sided_emits_two_intents() -> None:
    """With dual_sided=True, should emit both BUY NO and SELL YES."""
    cfg = WillNoConfig(dual_sided=True, base_bet_usd=50.0, **_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will X happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert len(result) == 2

    outcomes = {(r.side, r.outcome) for r in result}
    assert ("BUY", "NO") in outcomes
    assert ("SELL", "YES") in outcomes

    # Each side gets half the band-adjusted bet size
    # yes_price=0.25 → band [0.25, 0.30) → mult=0.80 → size=40 → half=20
    for intent in result:
        assert intent.size_usd == 20.0


@pytest.mark.asyncio
async def test_will_no_single_sided_default() -> None:
    """Default (dual_sided=False) should emit only BUY NO."""
    cfg = WillNoConfig(**_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Y happen?",
            active=True,
            yes_price=0.32,  # band [0.30, 0.35) → mult=1.0
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.32)
    result = await strategy.on_trade(trade, ctx)

    assert result is not None
    assert len(result) == 1
    assert result[0].side == "BUY"
    assert result[0].outcome == "NO"
    assert result[0].size_usd == 50.0


@pytest.mark.asyncio
async def test_will_no_on_market_update_returns_none() -> None:
    cfg = WillNoConfig()
    strategy = WillNoStrategy(config=cfg)
    ctx = _MockCtx()
    result = await strategy.on_market_update({"price": 0.5}, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_will_no_on_timer_returns_none() -> None:
    cfg = WillNoConfig()
    strategy = WillNoStrategy(config=cfg)
    ctx = _MockCtx()
    result = await strategy.on_timer(1700000000.0, ctx)
    assert result is None


# ---------------------------------------------------------------------------
# Band-based sizing (event-driven)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_band_sizing_sweet_spot() -> None:
    """YES=32% falls in 30-35% band → mult=1.0 → full base_bet."""
    cfg = WillNoConfig(base_bet_usd=100.0, **_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will X happen?",
            active=True,
            yes_price=0.32,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.32)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert result[0].size_usd == 100.0


@pytest.mark.asyncio
async def test_band_sizing_edge_band() -> None:
    """YES=17% falls in 15-20% band → mult=0.35 → reduced size."""
    cfg = WillNoConfig(base_bet_usd=100.0, **_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will X happen?",
            active=True,
            yes_price=0.17,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.17)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert result[0].size_usd == pytest.approx(35.0)


@pytest.mark.asyncio
async def test_band_sizing_negative_edge_excluded() -> None:
    """YES=42% is outside all bands (negative edge) → no signal."""
    cfg = WillNoConfig(base_bet_usd=100.0, **_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will X happen?",
            active=True,
            yes_price=0.42,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.42)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_flat_pricing_fallback() -> None:
    """With price_bands=(), should fall back to flat yes_price_min/max."""
    cfg = WillNoConfig(
        yes_price_min=0.10,
        yes_price_max=0.50,
        base_bet_usd=100.0,
        price_bands=(),
        **_BARE_CFG_KWARGS,
    )
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will X happen?",
            active=True,
            yes_price=0.42,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.42)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert result[0].size_usd == 100.0  # flat, no band adjustment


# ---------------------------------------------------------------------------
# Prefer keywords (event-driven)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefer_keywords_filters_non_matching() -> None:
    """When prefer_keywords set, question must contain at least one."""
    cfg = WillNoConfig(prefer_keywords={"above", "below", "today"}, avoid_keywords=frozenset())
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Trump visit Japan?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is None  # no prefer keyword → skipped


@pytest.mark.asyncio
async def test_prefer_keywords_allows_matching() -> None:
    """Question containing a prefer keyword should pass."""
    cfg = WillNoConfig(prefer_keywords={"above", "below", "today"}, avoid_keywords=frozenset())
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will BTC close above $100K today?",
            active=True,
            yes_price=0.25,
            category="Crypto",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None


@pytest.mark.asyncio
async def test_empty_prefer_keywords_allows_all() -> None:
    """Empty prefer_keywords should not filter anything."""
    cfg = WillNoConfig(prefer_keywords=frozenset(), avoid_keywords=frozenset())
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will something happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None


@pytest.mark.asyncio
async def test_production_defaults_end_to_end() -> None:
    """Production config: prefer + avoid + bands + max_bucket work together."""
    cfg = WillNoConfig()  # all production defaults
    strategy = WillNoStrategy(config=cfg)

    # "between" matches prefer, no avoid keywords, yes_price in sweet spot band
    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will the match between Roma and Milan end in a draw?",
            active=True,
            yes_price=0.32,
            category="Sports",
        ),
        # Provide market_size_bucket to satisfy max_bucket="med"
        features={"market_size_bucket": {"0xabc": "thin"}},
    )
    trade = _make_trade(condition_id="0xabc", price=0.32)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None
    assert result[0].size_usd == 50.0  # base=50, mult=1.0


@pytest.mark.asyncio
async def test_production_defaults_skips_generic_question() -> None:
    """Production config rejects questions without prefer keywords."""
    cfg = WillNoConfig()
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will Trump win the election?",
            active=True,
            yes_price=0.32,
            category="Politics",
        ),
    )
    trade = _make_trade(condition_id="0xabc", price=0.32)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_production_defaults_skips_heavy_bucket() -> None:
    """Production config (max_bucket='med') rejects heavy markets."""
    cfg = WillNoConfig()
    strategy = WillNoStrategy(config=cfg)

    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xabc",
            question="Will the match between PSG and Lyon end in a draw?",
            active=True,
            yes_price=0.32,
            category="Sports",
        ),
        features={"market_size_bucket": {"0xabc": "heavy"}},
    )
    trade = _make_trade(condition_id="0xabc", price=0.32)
    result = await strategy.on_trade(trade, ctx)
    assert result is None


# ---------------------------------------------------------------------------
# Vectorized path
# ---------------------------------------------------------------------------


class TestWillNoVectorized:
    def test_filters_will_markets(self) -> None:
        """Vectorized path returns only 'Will' markets."""
        cfg = WillNoConfig(**_BARE_CFG_KWARGS)
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2", "0x3"],
                "price": [0.20, 0.25, 0.30],
                "published_at": [1000.0, 1001.0, 1002.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2", "0x3"],
                "question": [
                    "Will BTC hit $200K?",
                    "Bitcoin above $120K?",
                    "Will ETH drop below $2K?",
                ],
            }
        )

        result = strategy.compute_signals(trades, markets)
        cids = set(result["condition_id"].to_list())
        assert "0x1" in cids
        assert "0x3" in cids
        assert "0x2" not in cids

    def test_avoid_keywords_filter(self) -> None:
        """Avoid keywords should be filtered in vectorized path."""
        cfg = WillNoConfig(avoid_keywords={"reach"}, prefer_keywords=frozenset())
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "price": [0.20, 0.25],
                "published_at": [1000.0, 1001.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "question": [
                    "Will BTC close above $200K?",
                    "Will ETH reach $10K?",
                ],
            }
        )

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 1
        assert result["condition_id"][0] == "0x1"

    def test_price_band_filter(self) -> None:
        """Only trades within YES price band should match."""
        cfg = WillNoConfig(**_BARE_CFG_KWARGS)
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "price": [0.05, 0.25],
                "published_at": [1000.0, 1001.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "question": [
                    "Will BTC close above $200K?",
                    "Will ETH close above $10K?",
                ],
            }
        )

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 1
        assert result["condition_id"][0] == "0x2"

    def test_band_adjusted_sizing_vectorized(self) -> None:
        """Vectorized path should produce different sizes per price band."""
        cfg = WillNoConfig(base_bet_usd=100.0, **_BARE_CFG_KWARGS)
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2", "0x3"],
                "price": [0.17, 0.32, 0.37],  # bands: 0.35, 1.00, 0.90
                "published_at": [1000.0, 1001.0, 1002.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2", "0x3"],
                "question": [
                    "Will A happen?",
                    "Will B happen?",
                    "Will C happen?",
                ],
            }
        )

        result = strategy.compute_signals(trades, markets)
        sizes = dict(zip(result["condition_id"].to_list(), result["size_usd"].to_list()))
        assert sizes["0x1"] == pytest.approx(35.0)    # 100 * 0.35
        assert sizes["0x2"] == pytest.approx(100.0)   # 100 * 1.00
        assert sizes["0x3"] == pytest.approx(90.0)    # 100 * 0.90

    def test_prefer_keywords_vectorized(self) -> None:
        """Prefer keywords should filter in vectorized path."""
        cfg = WillNoConfig(prefer_keywords={"above", "below"}, avoid_keywords=frozenset())
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "price": [0.25, 0.25],
                "published_at": [1000.0, 1001.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "question": [
                    "Will BTC close above $100K?",
                    "Will Trump visit Japan?",
                ],
            }
        )

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 1
        assert result["condition_id"][0] == "0x1"

    def test_volume_filter_vectorized(self) -> None:
        """max_volume_usd should filter high-volume markets when column present."""
        cfg = WillNoConfig(max_volume_usd=5000.0, **_BARE_CFG_KWARGS)
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "price": [0.25, 0.25],
                "published_at": [1000.0, 1001.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "question": [
                    "Will A happen?",
                    "Will B happen?",
                ],
                "event_volume": [3000.0, 50000.0],
            }
        )

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 1
        assert result["condition_id"][0] == "0x1"

    def test_flat_pricing_vectorized(self) -> None:
        """With price_bands=(), vectorized path uses flat sizing."""
        cfg = WillNoConfig(
            yes_price_min=0.10,
            yes_price_max=0.50,
            base_bet_usd=100.0,
            price_bands=(),
            **_BARE_CFG_KWARGS,
        )
        strategy = WillNoStrategy(config=cfg)

        trades = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "price": [0.15, 0.45],
                "published_at": [1000.0, 1001.0],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": ["0x1", "0x2"],
                "question": [
                    "Will A happen?",
                    "Will B happen?",
                ],
            }
        )

        result = strategy.compute_signals(trades, markets)
        assert len(result) == 2
        # All get flat base_bet_usd
        for size in result["size_usd"].to_list():
            assert size == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_will_market_provider_filters_correctly() -> None:
    """Provider should only include 'Will' questions."""
    from polymarket_pipeline.strategies_impl.will_no.providers import WillMarketProvider

    provider = WillMarketProvider()

    markets_df = pl.DataFrame(
        {
            "condition_id": ["0x1", "0x2", "0x3", "0x4"],
            "question": [
                "Will Bitcoin hit $200K?",
                "Bitcoin above $120K at 4PM?",
                "Will inflation drop below 2%?",
                "Will SOL reach $500?",
            ],
            "active": [True, True, True, True],
            "yes_price": [0.25, 0.15, 0.30, 0.20],
            "no_price": [0.75, 0.85, 0.70, 0.80],
            "event_id": ["e1", "e2", "e3", "e4"],
            "category": ["Crypto", "Crypto", "Economics", "Crypto"],
        }
    )

    backend = AsyncMock()
    backend.query_markets = AsyncMock(return_value=markets_df)

    await provider.compute(backend)

    features = provider.get_features()
    will_markets = features["will_markets"]

    # 0x1, 0x3 match "Will" pattern. 0x4 has "reach" but provider doesn't filter that
    assert "0x1" in will_markets
    assert "0x3" in will_markets
    assert "0x2" not in will_markets  # doesn't start with "Will"
    assert "0x4" in will_markets  # provider doesn't filter avoid keywords


@pytest.mark.asyncio
async def test_will_market_provider_on_trade_is_noop() -> None:
    """on_trade should be a no-op (market set refreshed periodically)."""
    from polymarket_pipeline.strategies_impl.will_no.providers import WillMarketProvider

    provider = WillMarketProvider()
    trade = _make_trade()
    await provider.on_trade(trade)  # should not raise


# ---------------------------------------------------------------------------
# Market size bucket filter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_will_no_filters_by_market_size_bucket() -> None:
    """WillNo should skip markets classified as 'heavy' when max_bucket is set."""
    cfg = WillNoConfig(max_bucket="med", **_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)
    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xmkt1",
            question="Will X happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
        features={
            "market_size_bucket": {"0xmkt1": "heavy"},
        },
    )

    trade = _make_trade(condition_id="0xmkt1", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is None  # heavy market, should be skipped


@pytest.mark.asyncio
async def test_will_no_allows_market_within_bucket() -> None:
    """WillNo should allow markets at or below max_bucket."""
    cfg = WillNoConfig(max_bucket="thick", **_BARE_CFG_KWARGS)
    strategy = WillNoStrategy(config=cfg)
    ctx = _MockCtx(
        market=MarketInfo(
            condition_id="0xmkt2",
            question="Will Y happen?",
            active=True,
            yes_price=0.25,
            category="Politics",
        ),
        features={
            "market_size_bucket": {"0xmkt2": "med"},
        },
    )

    trade = _make_trade(condition_id="0xmkt2", price=0.25)
    result = await strategy.on_trade(trade, ctx)
    assert result is not None  # med <= thick, should fire
