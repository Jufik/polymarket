"""Tests for CryptoOTMNoStrategy — event-driven and vectorized paths."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl

from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.strategies.context.memory import InMemoryContext
from polymarket_pipeline.strategies.protocol import Strategy, VectorizedStrategy
from polymarket_pipeline.strategies.types import MarketInfo
from polymarket_pipeline.strategies_impl.crypto_otm_no.config import CryptoOTMNoConfig
from polymarket_pipeline.strategies_impl.crypto_otm_no.strategy import (
    CryptoOTMNoStrategy,
    _extract_asset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_ASSETS = {"BTC", "ETH", "SOL", "XRP"}

CID_BTC = "0xcondition_btc_above_120k"
CID_ETH = "0xcondition_eth_above_5k"
CID_DOGE = "0xcondition_doge_above_1"
CID_POLITICS = "0xcondition_politics"


def _make_market(
    cid: str,
    question: str,
    category: str = "Crypto",
    yes_price: float | None = 0.10,
    active: bool = True,
) -> MarketInfo:
    """Build a MarketInfo fixture."""
    return MarketInfo(
        condition_id=cid,
        question=question,
        active=active,
        yes_price=yes_price,
        no_price=1.0 - yes_price if yes_price is not None else None,
        category=category,
    )


BTC_ABOVE_MARKET = _make_market(CID_BTC, "Bitcoin above $120,000 at 4PM ET?", yes_price=0.10)
ETH_ABOVE_MARKET = _make_market(CID_ETH, "Ethereum above $5,000 at 4PM ET?", yes_price=0.15)
DOGE_ABOVE_MARKET = _make_market(
    CID_DOGE, "Dogecoin above $1.00 at 4PM ET?", category="Crypto", yes_price=0.08
)
POLITICS_MARKET = _make_market(
    CID_POLITICS, "Will candidate X win?", category="Politics", yes_price=0.50
)


def _trade(
    cid: str = CID_BTC,
    ts: int = 1000,
    price: float = 0.10,
    maker: str = "0xmaker1",
) -> NormalizedTrade:
    """Build a NormalizedTrade fixture."""
    return NormalizedTrade(
        trade_id=f"test:{maker}:{cid}:{ts}",
        condition_id=cid,
        asset_id="asset_1",
        side=Side("BUY"),
        price=Decimal(str(price)),
        size=Decimal("100"),
        amount_usd=Decimal(str(price * 100)),
        fee_usd=Decimal("0"),
        maker=maker,
        taker="0xexchange",
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        source=Source.ALCHEMY,
        tx_hash=None,
        order_hash=None,
        block_number=None,
        is_backfill=False,
        version=2,
        published_at=float(ts),
    )


def _make_config(**overrides: object) -> CryptoOTMNoConfig:
    """Create a CryptoOTMNoConfig with test defaults."""
    defaults: dict[str, object] = {
        "assets": DEFAULT_ASSETS,
        "yes_price_min": 0.05,
        "yes_price_max": 0.25,
        "base_bet_usd": 100.0,
    }
    defaults.update(overrides)
    return CryptoOTMNoConfig(**defaults)  # type: ignore[arg-type]


def _make_strategy(**overrides: object) -> CryptoOTMNoStrategy:
    """Create a CryptoOTMNoStrategy with test defaults."""
    return CryptoOTMNoStrategy(_make_config(**overrides))


def _ctx_with_market(*markets: MarketInfo) -> InMemoryContext:
    """Build an InMemoryContext pre-loaded with markets."""
    ctx = InMemoryContext()
    for m in markets:
        ctx.set_market(m.condition_id, m)
    return ctx


# ---------------------------------------------------------------------------
# Asset extraction helper
# ---------------------------------------------------------------------------


class TestExtractAsset:
    def test_bitcoin(self) -> None:
        assert _extract_asset("Bitcoin above $120K at 4PM ET?") == "BTC"

    def test_ethereum(self) -> None:
        assert _extract_asset("Ethereum above $5,000 at 4PM ET?") == "ETH"

    def test_unknown(self) -> None:
        assert _extract_asset("Dogecoin above $1.00?") is None

    def test_empty(self) -> None:
        assert _extract_asset("") is None

    def test_ripple(self) -> None:
        assert _extract_asset("Ripple above $2.00?") == "XRP"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_strategy_protocol(self) -> None:
        strat = _make_strategy()
        assert isinstance(strat, Strategy)

    def test_satisfies_vectorized_protocol(self) -> None:
        strat = _make_strategy()
        assert isinstance(strat, VectorizedStrategy)


# ---------------------------------------------------------------------------
# Event-driven path (on_trade)
# ---------------------------------------------------------------------------


class TestOnTrade:
    async def test_matches_crypto_above_market(self) -> None:
        """Eligible trade on BTC above market -> TradeIntent."""
        strat = _make_strategy()
        ctx = _ctx_with_market(BTC_ABOVE_MARKET)

        result = await strat.on_trade(_trade(cid=CID_BTC, price=0.10), ctx)

        assert result is not None
        assert len(result) == 1
        intent = result[0]
        assert intent.strategy == "crypto_otm_no"
        assert intent.condition_id == CID_BTC
        assert intent.side == "BUY"
        assert intent.outcome == "NO"
        assert intent.size_usd == 100.0

    async def test_rejects_non_crypto_category(self) -> None:
        """Politics market -> None."""
        strat = _make_strategy()
        ctx = _ctx_with_market(POLITICS_MARKET)

        result = await strat.on_trade(_trade(cid=CID_POLITICS, price=0.10), ctx)
        assert result is None

    async def test_rejects_wrong_question_pattern(self) -> None:
        """Market without 'above'/'below' in question -> None."""
        strat = _make_strategy()
        no_pattern_market = _make_market(
            "0xcid_no_pattern", "Bitcoin to reach $120K?", category="Crypto"
        )
        ctx = _ctx_with_market(no_pattern_market)

        result = await strat.on_trade(_trade(cid="0xcid_no_pattern", price=0.10), ctx)
        assert result is None

    async def test_rejects_unsupported_asset(self) -> None:
        """DOGE (not in config assets) -> None."""
        strat = _make_strategy()
        ctx = _ctx_with_market(DOGE_ABOVE_MARKET)

        result = await strat.on_trade(_trade(cid=CID_DOGE, price=0.08), ctx)
        assert result is None

    async def test_rejects_yes_price_below_band(self) -> None:
        """YES at 0.03 (below min 0.05) -> None."""
        strat = _make_strategy()
        low_price_market = _make_market(
            "0xcid_low",
            "Bitcoin above $200,000 at 4PM ET?",
            category="Crypto",
            yes_price=0.03,
        )
        ctx = _ctx_with_market(low_price_market)

        result = await strat.on_trade(_trade(cid="0xcid_low", price=0.03), ctx)
        assert result is None

    async def test_rejects_yes_price_above_band(self) -> None:
        """YES at 0.30 (above max 0.25) -> None."""
        strat = _make_strategy()
        high_price_market = _make_market(
            "0xcid_high",
            "Bitcoin above $95,000 at 4PM ET?",
            category="Crypto",
            yes_price=0.30,
        )
        ctx = _ctx_with_market(high_price_market)

        result = await strat.on_trade(_trade(cid="0xcid_high", price=0.30), ctx)
        assert result is None

    async def test_only_fires_once_per_market(self) -> None:
        """Second trade on same condition_id -> None."""
        strat = _make_strategy()
        ctx = _ctx_with_market(BTC_ABOVE_MARKET)

        r1 = await strat.on_trade(_trade(cid=CID_BTC, ts=1000), ctx)
        r2 = await strat.on_trade(_trade(cid=CID_BTC, ts=1001, maker="0xmaker2"), ctx)

        assert r1 is not None
        assert r2 is None

    async def test_fires_for_different_markets(self) -> None:
        """Different condition_id -> both fire."""
        strat = _make_strategy()
        ctx = _ctx_with_market(BTC_ABOVE_MARKET, ETH_ABOVE_MARKET)

        r1 = await strat.on_trade(_trade(cid=CID_BTC, ts=1000), ctx)
        r2 = await strat.on_trade(_trade(cid=CID_ETH, ts=1001, price=0.15), ctx)

        assert r1 is not None
        assert r2 is not None
        assert r1[0].condition_id == CID_BTC
        assert r2[0].condition_id == CID_ETH

    async def test_intent_has_correct_fields(self) -> None:
        """Verify all TradeIntent fields are set correctly."""
        strat = _make_strategy(base_bet_usd=50.0)
        ctx = _ctx_with_market(BTC_ABOVE_MARKET)

        result = await strat.on_trade(_trade(cid=CID_BTC, ts=2000), ctx)

        assert result is not None
        intent = result[0]
        assert intent.side == "BUY"
        assert intent.outcome == "NO"
        assert intent.size_usd == 50.0
        assert intent.urgency == "patient"
        assert intent.max_price is None
        assert intent.signal_time == 2000.0
        assert "crypto_otm_no" in intent.reason

    async def test_no_market_metadata_returns_none(self) -> None:
        """Trade with no market in context -> None."""
        strat = _make_strategy()
        ctx = InMemoryContext()

        result = await strat.on_trade(_trade(cid="0xunknown"), ctx)
        assert result is None

    async def test_yes_price_from_trade_when_market_has_none(self) -> None:
        """When market.yes_price is None, falls back to trade price."""
        strat = _make_strategy()
        no_price_market = _make_market(
            "0xcid_noprice",
            "Bitcoin above $120,000 at 4PM ET?",
            category="Crypto",
            yes_price=None,
        )
        ctx = _ctx_with_market(no_price_market)

        # Trade price 0.10 is in band [0.05, 0.25]
        result = await strat.on_trade(_trade(cid="0xcid_noprice", price=0.10), ctx)
        assert result is not None

    async def test_on_market_update_returns_none(self) -> None:
        strat = _make_strategy()
        ctx = InMemoryContext()
        result = await strat.on_market_update({"price": 0.5}, ctx)
        assert result is None

    async def test_on_timer_returns_none(self) -> None:
        strat = _make_strategy()
        ctx = InMemoryContext()
        result = await strat.on_timer(1700000000.0, ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Vectorized path (compute_signals)
# ---------------------------------------------------------------------------


class TestComputeSignals:
    def test_filters_correctly(self) -> None:
        """Vectorized path returns correct markets matching all filters."""
        strat = _make_strategy()

        trades = pl.LazyFrame(
            {
                "condition_id": [CID_BTC, CID_ETH, CID_POLITICS],
                "price": [0.10, 0.15, 0.50],
                "published_at": [1000.0, 1001.0, 1002.0],
                "maker": ["0xa", "0xb", "0xc"],
                "side": ["BUY", "BUY", "BUY"],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": [CID_BTC, CID_ETH, CID_POLITICS],
                "question": [
                    "Bitcoin above $120,000 at 4PM ET?",
                    "Ethereum above $5,000 at 4PM ET?",
                    "Will candidate X win?",
                ],
                "category": ["Crypto", "Crypto", "Politics"],
            }
        )

        result = strat.compute_signals(trades, markets)

        # Should only have BTC and ETH, not politics
        assert len(result) == 2
        cids = set(result["condition_id"].to_list())
        assert cids == {CID_BTC, CID_ETH}
        assert result["outcome"].to_list() == ["NO", "NO"]
        assert result["side"].to_list() == ["BUY", "BUY"]

    def test_one_signal_per_market(self) -> None:
        """Multiple trades on same market -> one signal."""
        strat = _make_strategy()

        trades = pl.LazyFrame(
            {
                "condition_id": [CID_BTC, CID_BTC, CID_BTC],
                "price": [0.10, 0.12, 0.11],
                "published_at": [1000.0, 1001.0, 1002.0],
                "maker": ["0xa", "0xb", "0xc"],
                "side": ["BUY", "BUY", "BUY"],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": [CID_BTC],
                "question": ["Bitcoin above $120,000 at 4PM ET?"],
                "category": ["Crypto"],
            }
        )

        result = strat.compute_signals(trades, markets)

        assert len(result) == 1
        row = result.row(0, named=True)
        assert row["condition_id"] == CID_BTC
        assert row["signal_time"] == 1000.0  # first trade

    def test_price_band_filtering(self) -> None:
        """Trades outside price band should be excluded."""
        strat = _make_strategy(yes_price_min=0.05, yes_price_max=0.25)

        trades = pl.LazyFrame(
            {
                "condition_id": [CID_BTC, CID_ETH],
                "price": [0.03, 0.10],  # BTC price too low
                "published_at": [1000.0, 1001.0],
                "maker": ["0xa", "0xb"],
                "side": ["BUY", "BUY"],
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": [CID_BTC, CID_ETH],
                "question": [
                    "Bitcoin above $120,000 at 4PM ET?",
                    "Ethereum above $5,000 at 4PM ET?",
                ],
                "category": ["Crypto", "Crypto"],
            }
        )

        result = strat.compute_signals(trades, markets)

        assert len(result) == 1
        assert result["condition_id"][0] == CID_ETH

    def test_empty_trades(self) -> None:
        """No trades -> empty result."""
        strat = _make_strategy()

        trades = pl.LazyFrame(
            schema={
                "condition_id": pl.Utf8,
                "price": pl.Float64,
                "published_at": pl.Float64,
                "maker": pl.Utf8,
                "side": pl.Utf8,
            }
        )
        markets = pl.LazyFrame(
            {
                "condition_id": [CID_BTC],
                "question": ["Bitcoin above $120,000 at 4PM ET?"],
                "category": ["Crypto"],
            }
        )

        result = strat.compute_signals(trades, markets)
        assert len(result) == 0
