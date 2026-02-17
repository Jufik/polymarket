"""Tests for PostgreSQL sink.

Integration tests requiring a running PostgreSQL instance.
"""

from datetime import UTC, datetime

import pytest

from polymarket_pipeline.models import Event, Market, MarketStatus, Tag, TokenMarketEntry
from polymarket_pipeline.sinks.postgres import PostgresSink

DSN = "postgresql://polymarket:polymarket@192.168.0.148:15432/polymarket"

TEST_EVENT_ID = 999999
TEST_TAG_ID = 888888


def _make_event(**overrides: object) -> Event:
    defaults: dict = dict(
        id=TEST_EVENT_ID,
        slug="test-event-pg",
        title="Test Event",
        category="Test",
        neg_risk=False,
        active=True,
        closed=False,
        archived=False,
        liquidity=1000.0,
        volume=50000.0,
        start_date=datetime(2024, 6, 1, tzinfo=UTC),
        end_date=datetime(2024, 12, 31, tzinfo=UTC),
        created_at=datetime(2024, 6, 1, tzinfo=UTC),
        updated_at=datetime(2024, 6, 15, tzinfo=UTC),
    )
    defaults.update(overrides)
    return Event(**defaults)


def _make_market(**overrides: object) -> Market:
    defaults: dict = dict(
        condition_id="0xtest_pg",
        event_id=TEST_EVENT_ID,
        question="Test market?",
        slug="test-market",
        category="Test",
        token_yes="tok_yes_pg",
        token_no="tok_no_pg",
        neg_risk=False,
        status=MarketStatus.ACTIVE,
        created_at=datetime(2024, 6, 15, tzinfo=UTC),
        closed_at=None,
        resolved_at=None,
        updated_at=datetime(2024, 6, 15, tzinfo=UTC),
    )
    defaults.update(overrides)
    return Market(**defaults)


@pytest.fixture
async def sink():
    try:
        s = PostgresSink(dsn=DSN)
        await s.connect()
    except Exception:
        pytest.skip("PostgreSQL not available")

    # Clean test data (respect FK ordering)
    await s.query("DELETE FROM token_market_map WHERE condition_id = '0xtest_pg'")
    await s.query("DELETE FROM event_tags WHERE event_id = $1", TEST_EVENT_ID)
    await s.query("DELETE FROM markets WHERE condition_id = '0xtest_pg'")
    await s.query("DELETE FROM tags WHERE id = $1", TEST_TAG_ID)
    await s.query("DELETE FROM events WHERE id = $1", TEST_EVENT_ID)

    yield s
    await s.close()


@pytest.mark.integration
class TestPostgresSink:
    async def test_upsert_events(self, sink: PostgresSink) -> None:
        event = _make_event()
        await sink.upsert_events([event])

        rows = await sink.query("SELECT * FROM events WHERE id = $1", TEST_EVENT_ID)
        assert len(rows) == 1
        assert rows[0]["title"] == "Test Event"
        assert rows[0]["slug"] == "test-event-pg"

    async def test_upsert_events_idempotent(self, sink: PostgresSink) -> None:
        await sink.upsert_events([_make_event()])
        await sink.upsert_events([_make_event(title="Updated Event")])

        rows = await sink.query("SELECT * FROM events WHERE id = $1", TEST_EVENT_ID)
        assert len(rows) == 1
        assert rows[0]["title"] == "Updated Event"

    async def test_upsert_markets(self, sink: PostgresSink) -> None:
        await sink.upsert_events([_make_event()])
        market = _make_market()
        await sink.upsert_markets([market])

        rows = await sink.query("SELECT * FROM markets WHERE condition_id = $1", "0xtest_pg")
        assert len(rows) == 1
        assert rows[0]["question"] == "Test market?"
        assert rows[0]["status"] == "active"
        assert rows[0]["event_id"] == TEST_EVENT_ID

    async def test_upsert_markets_idempotent(self, sink: PostgresSink) -> None:
        await sink.upsert_events([_make_event()])
        await sink.upsert_markets([_make_market()])
        await sink.upsert_markets([_make_market(question="Updated question?")])

        rows = await sink.query("SELECT * FROM markets WHERE condition_id = $1", "0xtest_pg")
        assert len(rows) == 1
        assert rows[0]["question"] == "Updated question?"

    async def test_upsert_tags(self, sink: PostgresSink) -> None:
        tag = Tag(id=TEST_TAG_ID, label="Sports", slug="sports")
        await sink.upsert_tags([tag])

        rows = await sink.query("SELECT * FROM tags WHERE id = $1", TEST_TAG_ID)
        assert len(rows) == 1
        assert rows[0]["label"] == "Sports"
        assert rows[0]["slug"] == "sports"

    async def test_upsert_tags_idempotent(self, sink: PostgresSink) -> None:
        await sink.upsert_tags([Tag(id=TEST_TAG_ID, label="Sports", slug="sports")])
        await sink.upsert_tags([Tag(id=TEST_TAG_ID, label="Athletics", slug="sports")])

        rows = await sink.query("SELECT * FROM tags WHERE id = $1", TEST_TAG_ID)
        assert len(rows) == 1
        assert rows[0]["label"] == "Athletics"

    async def test_upsert_event_tags(self, sink: PostgresSink) -> None:
        await sink.upsert_events([_make_event()])
        await sink.upsert_tags([Tag(id=TEST_TAG_ID, label="Sports", slug="sports")])
        await sink.upsert_event_tags([(TEST_EVENT_ID, TEST_TAG_ID)])

        rows = await sink.query(
            "SELECT * FROM event_tags WHERE event_id = $1", TEST_EVENT_ID
        )
        assert len(rows) == 1
        assert rows[0]["tag_id"] == TEST_TAG_ID

    async def test_upsert_event_tags_idempotent(self, sink: PostgresSink) -> None:
        await sink.upsert_events([_make_event()])
        await sink.upsert_tags([Tag(id=TEST_TAG_ID, label="Sports", slug="sports")])
        await sink.upsert_event_tags([(TEST_EVENT_ID, TEST_TAG_ID)])
        await sink.upsert_event_tags([(TEST_EVENT_ID, TEST_TAG_ID)])

        rows = await sink.query(
            "SELECT * FROM event_tags WHERE event_id = $1", TEST_EVENT_ID
        )
        assert len(rows) == 1

    async def test_token_map(self, sink: PostgresSink) -> None:
        await sink.upsert_events([_make_event()])
        await sink.upsert_markets([_make_market()])

        entries = [
            TokenMarketEntry(asset_id="tok_yes_pg", condition_id="0xtest_pg", outcome="YES"),
            TokenMarketEntry(asset_id="tok_no_pg", condition_id="0xtest_pg", outcome="NO"),
        ]
        await sink.upsert_token_map(entries)

        rows = await sink.query(
            "SELECT * FROM token_market_map WHERE condition_id = $1", "0xtest_pg"
        )
        assert len(rows) == 2
        outcomes = {r["outcome"] for r in rows}
        assert outcomes == {"YES", "NO"}
