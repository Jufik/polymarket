"""Tests for the immutable TokenMap class."""

from __future__ import annotations

from polymarket_pipeline.live.normalizers.token_map import TokenMap


class TestTokenMapLookup:
    """Test lookup hit and miss."""

    def test_lookup_hit(self) -> None:
        entries = {
            "asset_aaa": ("cond_111", "Yes"),
            "asset_bbb": ("cond_222", "No"),
        }
        tm = TokenMap(entries)
        assert tm.lookup("asset_aaa") == ("cond_111", "Yes")
        assert tm.lookup("asset_bbb") == ("cond_222", "No")

    def test_lookup_miss_returns_none(self) -> None:
        tm = TokenMap({"asset_aaa": ("cond_111", "Yes")})
        assert tm.lookup("nonexistent") is None


class TestTokenMapLen:
    """Test __len__."""

    def test_len_populated(self) -> None:
        entries = {
            "a1": ("c1", "Yes"),
            "a2": ("c2", "No"),
            "a3": ("c3", "Yes"),
        }
        tm = TokenMap(entries)
        assert len(tm) == 3

    def test_len_empty(self) -> None:
        tm = TokenMap({})
        assert len(tm) == 0


class TestTokenMapContains:
    """Test __contains__."""

    def test_contains_present(self) -> None:
        tm = TokenMap({"asset_x": ("cond_x", "Yes")})
        assert "asset_x" in tm

    def test_contains_absent(self) -> None:
        tm = TokenMap({"asset_x": ("cond_x", "Yes")})
        assert "missing" not in tm


class TestTokenMapEmpty:
    """Test empty map behavior."""

    def test_empty_lookup(self) -> None:
        tm = TokenMap({})
        assert tm.lookup("anything") is None

    def test_empty_len(self) -> None:
        tm = TokenMap({})
        assert len(tm) == 0

    def test_empty_contains(self) -> None:
        tm = TokenMap({})
        assert "x" not in tm


class TestTokenMapDefensiveCopy:
    """Mutating the input dict after construction must not affect the map."""

    def test_mutation_after_construction(self) -> None:
        original: dict[str, tuple[str, str]] = {
            "asset_1": ("cond_1", "Yes"),
        }
        tm = TokenMap(original)

        # Mutate the original dict
        original["asset_2"] = ("cond_2", "No")
        del original["asset_1"]

        # TokenMap should still reflect the snapshot at construction time
        assert len(tm) == 1
        assert tm.lookup("asset_1") == ("cond_1", "Yes")
        assert tm.lookup("asset_2") is None
        assert "asset_1" in tm
        assert "asset_2" not in tm
