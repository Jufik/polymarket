"""Tests for AssetRegistry -- Redis-backed orderbook subscription state."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def _make_registry():
    fakeredis = pytest.importorskip("fakeredis")
    redis = fakeredis.aioredis.FakeRedis()

    from polymarket_pipeline.live.asset_registry import AssetRegistry

    return AssetRegistry(redis=redis), redis


async def test_add_and_get_desired() -> None:
    reg, _ = _make_registry()

    added = await reg.add("asset_1", "0xcond_a", "YES")
    assert added is True

    desired = await reg.get_desired()
    assert desired == {"asset_1"}


async def test_add_idempotent() -> None:
    reg, _ = _make_registry()

    first = await reg.add("asset_1", "0xcond_a", "YES")
    second = await reg.add("asset_1", "0xcond_a", "YES")

    assert first is True
    assert second is False
    assert await reg.count() == 1


async def test_add_many_bulk() -> None:
    reg, _ = _make_registry()

    entries = [
        ("asset_1", "0xcond_a", "YES"),
        ("asset_2", "0xcond_a", "NO"),
        ("asset_3", "0xcond_b", "YES"),
    ]
    count = await reg.add_many(entries)
    assert count == 3
    assert await reg.count() == 3


async def test_add_many_empty() -> None:
    reg, _ = _make_registry()
    assert await reg.add_many([]) == 0


async def test_remove_by_condition() -> None:
    """Add two assets for the same condition, remove by condition, verify both gone."""
    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES")
    await reg.add("asset_2", "0xcond_a", "NO")
    await reg.add("asset_3", "0xcond_b", "YES")

    removed = await reg.remove_by_condition("0xcond_a")
    assert removed == {"asset_1", "asset_2"}

    desired = await reg.get_desired()
    assert desired == {"asset_3"}


async def test_remove_by_condition_unknown() -> None:
    """Removing a non-existent condition is a no-op."""
    reg, _ = _make_registry()
    removed = await reg.remove_by_condition("0xunknown")
    assert removed == set()


async def test_get_condition_id() -> None:
    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES")
    cid = await reg.get_condition_id("asset_1")
    assert cid == "0xcond_a"


async def test_get_condition_id_missing() -> None:
    reg, _ = _make_registry()
    assert await reg.get_condition_id("nonexistent") is None


async def test_redis_error_add_graceful() -> None:
    """Redis failure on add returns False instead of raising."""
    from polymarket_pipeline.live.asset_registry import AssetRegistry

    redis = AsyncMock()
    redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=redis)
    redis.pipeline.return_value.__aexit__ = AsyncMock()
    redis.pipeline.side_effect = RuntimeError("connection lost")

    reg = AssetRegistry(redis=redis)
    result = await reg.add("x", "y", "z")
    assert result is False


async def test_redis_error_remove_graceful() -> None:
    """Redis failure on remove returns empty set."""
    from polymarket_pipeline.live.asset_registry import AssetRegistry

    redis = AsyncMock()
    redis.smembers.side_effect = RuntimeError("connection lost")

    reg = AssetRegistry(redis=redis)
    result = await reg.remove_by_condition("0xcond")
    assert result == set()


# ---------------------------------------------------------------------------
# Group support tests
# ---------------------------------------------------------------------------


async def test_add_with_group() -> None:
    """Assets are added to both global and per-group desired sets."""
    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES", group="crypto")
    await reg.add("asset_2", "0xcond_b", "YES", group="default")

    # Global set has both
    assert await reg.get_desired() == {"asset_1", "asset_2"}

    # Per-group sets are correct
    assert await reg.get_desired_for_group("crypto") == {"asset_1"}
    assert await reg.get_desired_for_group("default") == {"asset_2"}


async def test_add_many_with_group() -> None:
    """Bulk add respects group parameter."""
    reg, _ = _make_registry()

    entries = [
        ("asset_1", "0xcond_a", "YES"),
        ("asset_2", "0xcond_a", "NO"),
    ]
    count = await reg.add_many(entries, group="crypto")
    assert count == 2

    assert await reg.get_desired_for_group("crypto") == {"asset_1", "asset_2"}
    assert await reg.get_desired_for_group("default") == set()


async def test_default_group() -> None:
    """Assets without explicit group go to 'default'."""
    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES")
    assert await reg.get_desired_for_group("default") == {"asset_1"}


async def test_remove_by_condition_cleans_group_set() -> None:
    """Removing by condition also cleans the per-group desired set."""
    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES", group="crypto")
    await reg.add("asset_2", "0xcond_a", "NO", group="crypto")
    await reg.add("asset_3", "0xcond_b", "YES", group="default")

    removed = await reg.remove_by_condition("0xcond_a")
    assert removed == {"asset_1", "asset_2"}

    # Per-group set should be clean
    assert await reg.get_desired_for_group("crypto") == set()
    assert await reg.get_desired_for_group("default") == {"asset_3"}


# ---------------------------------------------------------------------------
# FilteredRegistryView tests
# ---------------------------------------------------------------------------


async def test_filtered_view_group_only() -> None:
    """FilteredRegistryView returns only assets for the specified group."""
    from polymarket_pipeline.live.asset_registry import FilteredRegistryView

    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES", group="crypto")
    await reg.add("asset_2", "0xcond_b", "YES", group="default")

    view = FilteredRegistryView(reg, group="crypto")
    assert await view.get_desired() == {"asset_1"}


async def test_filtered_view_shard_only() -> None:
    """FilteredRegistryView with sharding splits assets deterministically."""
    from polymarket_pipeline.live.asset_registry import FilteredRegistryView

    reg, _ = _make_registry()

    # Add enough assets to have a meaningful split
    for i in range(20):
        await reg.add(f"asset_{i}", f"0xcond_{i}", "YES")

    all_assets = await reg.get_desired()
    assert len(all_assets) == 20

    # Split into 2 shards — union should equal the full set, no overlap
    view_0 = FilteredRegistryView(reg, shard=0, total_shards=2)
    view_1 = FilteredRegistryView(reg, shard=1, total_shards=2)

    shard_0 = await view_0.get_desired()
    shard_1 = await view_1.get_desired()

    assert shard_0 | shard_1 == all_assets
    assert shard_0 & shard_1 == set()
    assert len(shard_0) > 0
    assert len(shard_1) > 0


async def test_filtered_view_group_and_shard() -> None:
    """FilteredRegistryView with both group and shard filters correctly."""
    from polymarket_pipeline.live.asset_registry import FilteredRegistryView

    reg, _ = _make_registry()

    # Add assets to different groups
    for i in range(10):
        await reg.add(f"default_{i}", f"0xcond_d{i}", "YES", group="default")
    for i in range(5):
        await reg.add(f"crypto_{i}", f"0xcond_c{i}", "YES", group="crypto")

    # Crypto view should only see crypto assets
    crypto_view = FilteredRegistryView(reg, group="crypto")
    assert len(await crypto_view.get_desired()) == 5

    # Default shard 0/2 should only see default group, shard 0
    default_0 = FilteredRegistryView(reg, group="default", shard=0, total_shards=2)
    default_1 = FilteredRegistryView(reg, group="default", shard=1, total_shards=2)

    s0 = await default_0.get_desired()
    s1 = await default_1.get_desired()

    # All default assets covered, no overlap
    all_default = await reg.get_desired_for_group("default")
    assert s0 | s1 == all_default
    assert s0 & s1 == set()


async def test_filtered_view_no_filter() -> None:
    """FilteredRegistryView with no group/shard returns everything."""
    from polymarket_pipeline.live.asset_registry import FilteredRegistryView

    reg, _ = _make_registry()

    await reg.add("asset_1", "0xcond_a", "YES", group="crypto")
    await reg.add("asset_2", "0xcond_b", "YES", group="default")

    view = FilteredRegistryView(reg)
    assert await view.get_desired() == {"asset_1", "asset_2"}


# ---------------------------------------------------------------------------
# Strategy → Asset Registry bridge callback tests
# ---------------------------------------------------------------------------


def _make_bridge_cb(registry_mock: AsyncMock):
    """Build the same callback that strategy.py wires."""

    async def _register(provider_name: str, features: dict) -> None:
        provider_data = features.get(provider_name)
        if not isinstance(provider_data, dict):
            return
        tag_markets = provider_data.get("tag_markets")
        provider_token_map = provider_data.get("token_map")
        if not tag_markets or not provider_token_map:
            return
        entries: list[tuple[str, str, str]] = []
        for cid in tag_markets:
            tokens = provider_token_map.get(cid)
            if tokens:
                for outcome, asset_id in tokens.items():
                    entries.append((asset_id, cid, outcome))
        if entries:
            await registry_mock.add_many(entries)

    return _register


async def test_bridge_extracts_assets_from_features() -> None:
    """Bridge registers asset_ids from tag_markets + token_map."""
    registry = AsyncMock()
    registry.add_many = AsyncMock(return_value=4)
    cb = _make_bridge_cb(registry)

    features = {
        "consensus_v3_politics": {
            "pool": frozenset(["0xtrader1"]),
            "tag_markets": frozenset(["0xcond_a", "0xcond_b"]),
            "token_map": {
                "0xcond_a": {"YES": "aid_a_yes", "NO": "aid_a_no"},
                "0xcond_b": {"YES": "aid_b_yes", "NO": "aid_b_no"},
            },
        },
    }
    await cb("consensus_v3_politics", features)

    registry.add_many.assert_awaited_once()
    entries = registry.add_many.call_args[0][0]
    assert len(entries) == 4
    assert {e[0] for e in entries} == {"aid_a_yes", "aid_a_no", "aid_b_yes", "aid_b_no"}


async def test_bridge_skips_missing_tag_markets() -> None:
    """No-op when provider features lack tag_markets."""
    registry = AsyncMock()
    cb = _make_bridge_cb(registry)

    await cb("prov", {"prov": {"pool": frozenset()}})
    registry.add_many.assert_not_awaited()


async def test_bridge_skips_unknown_provider() -> None:
    """No-op when provider_name not in features dict."""
    registry = AsyncMock()
    cb = _make_bridge_cb(registry)

    await cb("missing", {"other": {}})
    registry.add_many.assert_not_awaited()


async def test_bridge_partial_token_map() -> None:
    """Markets in tag_markets but not in token_map are skipped."""
    registry = AsyncMock()
    registry.add_many = AsyncMock(return_value=2)
    cb = _make_bridge_cb(registry)

    features = {
        "prov": {
            "tag_markets": frozenset(["0xcond_a", "0xcond_missing"]),
            "token_map": {"0xcond_a": {"YES": "aid_yes", "NO": "aid_no"}},
        },
    }
    await cb("prov", features)

    entries = registry.add_many.call_args[0][0]
    assert len(entries) == 2
