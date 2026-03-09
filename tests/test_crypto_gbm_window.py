"""Tests for BTC Up/Down window parsing."""

from __future__ import annotations

from datetime import UTC

from polymarket_pipeline.strategies_impl.crypto_gbm.window import (
    WindowInfo,
    is_btc_up_down,
    parse_window,
)


class TestIsBtcUpDown:
    def test_standard_question(self) -> None:
        assert is_btc_up_down("Bitcoin Up or Down - December 1, 10:00AM-10:15AM ET")

    def test_case_insensitive(self) -> None:
        assert is_btc_up_down("bitcoin up or down - January 5, 3:00PM-3:05PM ET")

    def test_btc_abbreviation_not_matched(self) -> None:
        """Real PM data uses 'Bitcoin', not 'BTC' abbreviation."""
        assert not is_btc_up_down("BTC Up or Down - March 1, 1:00AM-1:15AM ET")

    def test_not_btc(self) -> None:
        assert not is_btc_up_down("ETH Up or Down - March 1, 1:00AM-1:15AM ET")

    def test_unrelated(self) -> None:
        assert not is_btc_up_down("Will BTC reach $100k by December?")

    def test_empty(self) -> None:
        assert not is_btc_up_down("")


class TestParseWindow:
    def test_15min_window(self) -> None:
        q = "Bitcoin Up or Down - January 15, 2:00PM-2:15PM ET"
        w = parse_window(q, "cid1", "tok_yes", "tok_no")
        assert w is not None
        assert w.condition_id == "cid1"
        assert w.token_yes == "tok_yes"
        assert w.token_no == "tok_no"
        assert w.duration_min == 15

    def test_5min_window(self) -> None:
        q = "Bitcoin Up or Down - March 8, 10:00AM-10:05AM ET"
        w = parse_window(q, "cid2", "yes", "no")
        assert w is not None
        assert w.duration_min == 5

    def test_midnight_crossing(self) -> None:
        q = "Bitcoin Up or Down - June 1, 11:55PM-12:10AM ET"
        w = parse_window(q, "cid3", "yes", "no")
        assert w is not None
        assert w.duration_min == 15
        # End should be after start
        assert w.window_end_utc > w.window_start_utc

    def test_am_pm_boundary(self) -> None:
        q = "Bitcoin Up or Down - April 10, 11:50AM-12:05PM ET"
        w = parse_window(q, "cid4", "yes", "no")
        assert w is not None
        assert w.duration_min == 15

    def test_parses_any_asset_question(self) -> None:
        """parse_window doesn't filter by asset — is_btc_up_down does that."""
        q = "ETH Up or Down - March 8, 10:00AM-10:05AM ET"
        w = parse_window(q, "cid", "y", "n")
        assert w is not None  # parse_window just parses, doesn't filter by asset

    def test_rejects_non_standard_duration(self) -> None:
        """Only 5m and 15m windows are accepted."""
        q = "Bitcoin Up or Down - March 8, 10:00AM-10:30AM ET"
        w = parse_window(q, "cid", "y", "n")
        assert w is None

    def test_et_to_utc_conversion(self) -> None:
        """ET is UTC-5 (EST) or UTC-4 (EDT). Just verify start < end and offset > 0."""
        q = "Bitcoin Up or Down - January 15, 2:00PM-2:15PM ET"
        w = parse_window(q, "cid", "y", "n")
        assert w is not None
        # January is EST (UTC-5), so 2:00PM ET = 7:00PM UTC
        assert w.window_start_utc.hour == 19

    def test_window_info_properties(self) -> None:
        """Test is_active, minutes_elapsed, minutes_remaining."""
        import time
        from datetime import datetime

        now = time.time()
        # Create a window that started 3 minutes ago, ending in 12 minutes
        start = datetime.fromtimestamp(now - 180, tz=UTC)
        end = datetime.fromtimestamp(now + 720, tz=UTC)

        w = WindowInfo(
            condition_id="test",
            window_start_utc=start,
            window_end_utc=end,
            duration_min=15,
            token_yes="y",
            token_no="n",
        )

        assert w.is_active
        assert 2.5 < w.minutes_elapsed < 3.5
        assert 11.5 < w.minutes_remaining < 12.5

    def test_window_not_active_before(self) -> None:
        """Window not yet started."""
        import time
        from datetime import datetime

        now = time.time()
        start = datetime.fromtimestamp(now + 600, tz=UTC)
        end = datetime.fromtimestamp(now + 1500, tz=UTC)

        w = WindowInfo(
            condition_id="test",
            window_start_utc=start,
            window_end_utc=end,
            duration_min=15,
            token_yes="y",
            token_no="n",
        )

        assert not w.is_active

    def test_window_not_active_after(self) -> None:
        """Window already ended."""
        import time
        from datetime import datetime

        now = time.time()
        start = datetime.fromtimestamp(now - 1500, tz=UTC)
        end = datetime.fromtimestamp(now - 600, tz=UTC)

        w = WindowInfo(
            condition_id="test",
            window_start_utc=start,
            window_end_utc=end,
            duration_min=15,
            token_yes="y",
            token_no="n",
        )

        assert not w.is_active
