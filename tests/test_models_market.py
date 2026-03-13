"""Tests for Event, Market, Tag, and TokenMarketEntry models."""

from polymarket_pipeline.models import (
    Event,
    Market,
    MarketStatus,
    Tag,
    TokenMarketEntry,
    _resolve_from_outcome_prices,
)


def _gamma_raw(**overrides: object) -> dict:
    """Minimal valid Gamma API market response."""
    base: dict = {
        "conditionId": "0xabc123",
        "question": "Will X happen?",
        "slug": "will-x-happen",
        "category": "Politics",
        "clobTokenIds": '["tok_yes", "tok_no"]',
        "marketType": "normal",
        "active": True,
        "closed": False,
        "createdAt": "2024-06-15T12:00:00Z",
        "closedAt": None,
        "resolvedAt": None,
        "updatedAt": "2024-06-16T08:00:00Z",
    }
    base.update(overrides)
    return base


def _gamma_event_raw(**overrides: object) -> dict:
    """Minimal valid Gamma API event response."""
    base: dict = {
        "id": "42",
        "slug": "test-event",
        "title": "Test Event",
        "category": "Politics",
        "negRisk": False,
        "active": True,
        "closed": False,
        "archived": False,
        "liquidity": 1000.5,
        "volume": 50000.0,
        "startDate": "2024-06-01T00:00:00Z",
        "endDate": "2024-12-31T00:00:00Z",
        "createdAt": "2024-06-01T00:00:00Z",
        "updatedAt": "2024-06-15T00:00:00Z",
    }
    base.update(overrides)
    return base


class TestEventFromGamma:
    def test_basic_parsing(self) -> None:
        e = Event.from_gamma(_gamma_event_raw())
        assert e is not None
        assert e.id == 42
        assert e.title == "Test Event"
        assert e.slug == "test-event"
        assert e.active is True
        assert e.closed is False

    def test_missing_id_returns_none(self) -> None:
        assert Event.from_gamma(_gamma_event_raw(id=None)) is None

    def test_neg_risk(self) -> None:
        e = Event.from_gamma(_gamma_event_raw(negRisk=True))
        assert e is not None
        assert e.neg_risk is True

    def test_volume_and_liquidity(self) -> None:
        e = Event.from_gamma(_gamma_event_raw(liquidity=999.99, volume=12345.67))
        assert e is not None
        assert e.liquidity == 999.99
        assert e.volume == 12345.67


class TestMarketFromGamma:
    def test_basic_parsing(self) -> None:
        m = Market.from_gamma(_gamma_raw())
        assert m is not None
        assert m.condition_id == "0xabc123"
        assert m.question == "Will X happen?"
        assert m.token_yes == "tok_yes"
        assert m.token_no == "tok_no"
        assert m.neg_risk is False
        assert m.status == MarketStatus.ACTIVE
        assert m.event_id is None

    def test_with_event_id(self) -> None:
        m = Market.from_gamma(_gamma_raw(), event_id=42)
        assert m is not None
        assert m.event_id == 42

    def test_neg_risk(self) -> None:
        m = Market.from_gamma(_gamma_raw(marketType="neg_risk"))
        assert m is not None
        assert m.neg_risk is True

    def test_closed_status(self) -> None:
        m = Market.from_gamma(_gamma_raw(active=False, closed=True, resolved=False))
        assert m is not None
        assert m.status == MarketStatus.CLOSED

    def test_resolved_status(self) -> None:
        m = Market.from_gamma(_gamma_raw(active=False, closed=True, resolved=True))
        assert m is not None
        assert m.status == MarketStatus.RESOLVED

    def test_missing_condition_id_returns_none(self) -> None:
        assert Market.from_gamma(_gamma_raw(conditionId="")) is None

    def test_missing_tokens_returns_none(self) -> None:
        assert Market.from_gamma(_gamma_raw(clobTokenIds=None)) is None

    def test_tokens_as_list(self) -> None:
        m = Market.from_gamma(_gamma_raw(clobTokenIds=["yes_id", "no_id"]))
        assert m is not None
        assert m.token_yes == "yes_id"
        assert m.token_no == "no_id"


class TestTagFromGamma:
    def test_basic_parsing(self) -> None:
        t = Tag.from_gamma({"id": "99", "label": "Sports", "slug": "sports"})
        assert t is not None
        assert t.id == 99
        assert t.label == "Sports"
        assert t.slug == "sports"

    def test_missing_id_returns_none(self) -> None:
        assert Tag.from_gamma({"label": "Sports", "slug": "sports"}) is None


class TestMarketNewFields:
    """Tests for end_date, description, resolution_source, outcomes, market_volume."""

    def test_end_date_parsed(self) -> None:
        m = Market.from_gamma(_gamma_raw(endDate="2024-12-31T00:00:00Z"))
        assert m is not None
        assert m.end_date is not None
        assert m.end_date.year == 2024

    def test_end_date_none_when_missing(self) -> None:
        m = Market.from_gamma(_gamma_raw())
        assert m is not None
        assert m.end_date is None

    def test_description(self) -> None:
        m = Market.from_gamma(_gamma_raw(description="Resolves Yes if X happens."))
        assert m is not None
        assert m.description == "Resolves Yes if X happens."

    def test_description_defaults_empty(self) -> None:
        m = Market.from_gamma(_gamma_raw())
        assert m is not None
        assert m.description == ""

    def test_resolution_source(self) -> None:
        m = Market.from_gamma(_gamma_raw(resolutionSource="https://example.com"))
        assert m is not None
        assert m.resolution_source == "https://example.com"

    def test_outcomes_string(self) -> None:
        m = Market.from_gamma(_gamma_raw(outcomes='["Yes", "No"]'))
        assert m is not None
        assert m.outcomes == '["Yes", "No"]'

    def test_outcomes_team_names(self) -> None:
        m = Market.from_gamma(_gamma_raw(outcomes='["Arizona State", "Nevada"]'))
        assert m is not None
        assert "Arizona State" in m.outcomes

    def test_market_volume(self) -> None:
        m = Market.from_gamma(_gamma_raw(volumeNum=12345.67))
        assert m is not None
        assert m.market_volume == 12345.67

    def test_market_volume_defaults_zero(self) -> None:
        m = Market.from_gamma(_gamma_raw())
        assert m is not None
        assert m.market_volume == 0.0


class TestResolveFromOutcomePrices:
    """Tests for _resolve_from_outcome_prices fallback."""

    def test_yes_wins(self) -> None:
        rv, wo = _resolve_from_outcome_prices({
            "outcomePrices": '["1", "0"]',
            "outcomes": '["Yes", "No"]',
        })
        assert rv == 1
        assert wo == "Yes"

    def test_no_wins(self) -> None:
        rv, wo = _resolve_from_outcome_prices({
            "outcomePrices": '["0", "1"]',
            "outcomes": '["Yes", "No"]',
        })
        assert rv == 1
        assert wo == "No"

    def test_team_names(self) -> None:
        rv, wo = _resolve_from_outcome_prices({
            "outcomePrices": '["1", "0"]',
            "outcomes": '["Arizona State", "Nevada"]',
        })
        assert rv == 1
        assert wo == "Arizona State"

    def test_ambiguous_prices(self) -> None:
        rv, wo = _resolve_from_outcome_prices({
            "outcomePrices": '["0.5", "0.5"]',
        })
        assert rv == 0
        assert wo == ""

    def test_missing_outcome_prices(self) -> None:
        rv, wo = _resolve_from_outcome_prices({})
        assert rv == 0
        assert wo == ""

    def test_none_outcome_prices(self) -> None:
        rv, wo = _resolve_from_outcome_prices({"outcomePrices": None})
        assert rv == 0
        assert wo == ""

    def test_malformed_json(self) -> None:
        rv, wo = _resolve_from_outcome_prices({"outcomePrices": "not json"})
        assert rv == 0
        assert wo == ""

    def test_defaults_to_yes_no_labels(self) -> None:
        """When outcomes field is missing, defaults to Yes/No."""
        rv, wo = _resolve_from_outcome_prices({
            "outcomePrices": '["0", "1"]',
        })
        assert rv == 1
        assert wo == "No"


class TestMarketResolutionFallback:
    """Tests for outcomePrices fallback in Market.from_gamma()."""

    def test_fallback_resolves_closed_market(self) -> None:
        m = Market.from_gamma(_gamma_raw(
            closed=True,
            active=False,
            outcomePrices='["0", "1"]',
            outcomes='["Yes", "No"]',
        ))
        assert m is not None
        assert m.resolution_value == 1
        assert m.winner_outcome == "No"
        assert m.status == MarketStatus.RESOLVED

    def test_no_fallback_when_clob_resolution_present(self) -> None:
        m = Market.from_gamma(
            _gamma_raw(
                closed=True,
                active=False,
                outcomePrices='["0", "1"]',
                outcomes='["Yes", "No"]',
            ),
            resolution_value=1,
        )
        assert m is not None
        assert m.resolution_value == 1
        # winner_outcome comes from CLOB enrichment, not fallback
        assert m.winner_outcome == ""

    def test_no_fallback_when_market_not_closed(self) -> None:
        m = Market.from_gamma(_gamma_raw(
            closed=False,
            active=True,
            outcomePrices='["0.5", "0.5"]',
        ))
        assert m is not None
        assert m.resolution_value == 0
        assert m.status == MarketStatus.ACTIVE

    def test_fallback_with_resolved_at(self) -> None:
        m = Market.from_gamma(_gamma_raw(
            closed=True,
            active=False,
            closedTime="2024-06-20T12:00:00+00:00",
            outcomePrices='["1", "0"]',
        ))
        assert m is not None
        assert m.resolution_value == 1
        assert m.resolved_at is not None


class TestTokenMarketEntry:
    def test_creation(self) -> None:
        entry = TokenMarketEntry(asset_id="tok_yes", condition_id="0xabc", outcome="YES")
        assert entry.asset_id == "tok_yes"
        assert entry.condition_id == "0xabc"
        assert entry.outcome == "YES"
