"""Tests for Reconciler -- desired/subscribed diff + slot redistribution."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from polymarket_pipeline.live.ingestors.managed_slot import ManagedSlot
from polymarket_pipeline.live.ingestors.reconciler import Reconciler


def _make_registry(desired: set[str] | None = None):
    """Create a mock AssetRegistry returning *desired* from get_desired()."""
    reg = AsyncMock()
    reg.get_desired = AsyncMock(return_value=desired or set())
    return reg


def _make_slot(slot_id: int = 0, desired: set[str] | None = None) -> ManagedSlot:
    """Create a ManagedSlot with a no-op on_message callback."""

    async def _noop(raw: str) -> None:
        pass

    slot = ManagedSlot(
        slot_id=slot_id,
        ws_url="wss://test",
        on_message=_noop,
        stale_timeout_s=120.0,
    )
    if desired:
        slot.desired_assets = set(desired)
    return slot


async def test_reconcile_adds_to_empty_slots() -> None:
    """New assets from registry should be distributed to empty slots."""
    registry = _make_registry({"a1", "a2", "a3"})
    slots = [_make_slot(0), _make_slot(1)]

    rec = Reconciler(registry=registry, slots=slots, interval_s=30)
    await rec._reconcile()

    # All 3 assets should be distributed across the two slots
    all_assigned = slots[0].desired_assets | slots[1].desired_assets
    assert all_assigned == {"a1", "a2", "a3"}


async def test_reconcile_removes_resolved() -> None:
    """Assets no longer in registry should be removed from slots + books cleaned."""
    registry = _make_registry({"a1"})  # a2 resolved
    slot = _make_slot(0, desired={"a1", "a2"})
    books = {"a1": "book1", "a2": "book2"}
    prev_tops = {"a1": (1,), "a2": (2,)}

    rec = Reconciler(
        registry=registry,
        slots=[slot],
        books=books,
        prev_tops=prev_tops,
        interval_s=30,
    )
    await rec._reconcile()

    assert slot.desired_assets == {"a1"}
    assert "a2" not in books
    assert "a2" not in prev_tops
    # a1 should still be in books (not cleaned)
    assert "a1" in books


async def test_reconcile_removes_via_callback() -> None:
    """When on_remove_asset is set, it's called instead of books/prev_tops cleanup."""
    registry = _make_registry({"a1"})  # a2 resolved
    slot = _make_slot(0, desired={"a1", "a2"})
    removed: list[str] = []

    rec = Reconciler(
        registry=registry,
        slots=[slot],
        on_remove_asset=lambda aid: removed.append(aid),
        interval_s=30,
    )
    await rec._reconcile()

    assert slot.desired_assets == {"a1"}
    assert "a2" in removed


async def test_reconcile_no_change() -> None:
    """If desired == subscribed, no slots should be signalled."""
    registry = _make_registry({"a1", "a2"})
    slot = _make_slot(0, desired={"a1", "a2"})

    rec = Reconciler(registry=registry, slots=[slot], interval_s=30)
    await rec._reconcile()

    assert rec._last_added == 0
    assert rec._last_removed == 0


async def test_reconcile_stale_slot_reconnect() -> None:
    """A stale slot should be signalled to hard-reconnect (not rotate)."""
    registry = _make_registry({"a1"})
    slot = _make_slot(0, desired={"a1"})
    # Simulate connected + stale
    slot._connected = True
    slot._subscribed_assets = {"a1"}
    slot._last_message_at = time.monotonic() - 200  # 200s ago, well past 120s threshold

    rec = Reconciler(registry=registry, slots=[slot], interval_s=30)
    await rec._reconcile()

    # Stale → hard reconnect (not rotate)
    assert slot._reconnect_event.is_set()
    assert not slot._rotate_event.is_set()


async def test_distribute_fills_most_free_first() -> None:
    """New assets should go to slots with the most headroom first."""
    registry = _make_registry({"a1", "a2", "a3", "a4", "a5", "existing"})
    slot_full = _make_slot(0, desired={"existing"})
    slot_empty = _make_slot(1)

    rec = Reconciler(registry=registry, slots=[slot_full, slot_empty], interval_s=30)
    await rec._reconcile()

    # slot_empty had more headroom, should get filled first
    all_assigned = slot_full.desired_assets | slot_empty.desired_assets
    assert all_assigned == {"a1", "a2", "a3", "a4", "a5", "existing"}


async def test_registry_down_keeps_current() -> None:
    """If registry raises, reconciler should keep current subscriptions."""
    registry = AsyncMock()
    registry.get_desired = AsyncMock(side_effect=RuntimeError("Redis down"))

    slot = _make_slot(0, desired={"a1", "a2"})
    rec = Reconciler(registry=registry, slots=[slot], interval_s=30)

    await rec._reconcile()

    # Assets unchanged
    assert slot.desired_assets == {"a1", "a2"}


async def test_stats() -> None:
    """Stats should reflect reconciliation history."""
    registry = _make_registry({"a1"})
    slot = _make_slot(0)
    rec = Reconciler(registry=registry, slots=[slot], interval_s=30)

    stats = rec.stats()
    assert stats["reconcile_count"] == 0

    await rec._reconcile()

    stats = rec.stats()
    assert stats["reconcile_count"] == 1
    assert stats["last_added"] == 1
    assert stats["last_removed"] == 0


async def test_book_refresh_signals_rotate() -> None:
    """Slots connected longer than book_refresh_s should be hot-rotated (not reconnected)."""
    registry = _make_registry({"a1"})
    slot = _make_slot(0, desired={"a1"})
    # Simulate connected for 400s (past 300s book_refresh_s)
    slot._connected = True
    slot._connected_at = time.monotonic() - 400
    slot._subscribed_assets = {"a1"}
    slot._last_message_at = time.monotonic()  # not stale

    rec = Reconciler(registry=registry, slots=[slot], interval_s=30, book_refresh_s=300)
    await rec._reconcile()

    # Book refresh uses rotate (zero-gap), not reconnect
    assert slot._rotate_event.is_set()
    assert not slot._reconnect_event.is_set()
    assert rec._book_refreshes == 1


async def test_book_refresh_disabled_when_zero() -> None:
    """book_refresh_s=0 disables periodic reconnect."""
    registry = _make_registry({"a1"})
    slot = _make_slot(0, desired={"a1"})
    slot._connected = True
    slot._connected_at = time.monotonic() - 9999
    slot._subscribed_assets = {"a1"}
    slot._last_message_at = time.monotonic()

    rec = Reconciler(registry=registry, slots=[slot], interval_s=30, book_refresh_s=0)
    await rec._reconcile()

    assert not slot._reconnect_event.is_set()
    assert not slot._rotate_event.is_set()


async def test_book_refresh_staggers_one_per_cycle() -> None:
    """Only the most overdue slot should be rotated per reconcile cycle.

    Prevents thundering herd when all slots hit book_refresh_s simultaneously.
    """
    registry = _make_registry({"a1", "a2"})
    slot0 = _make_slot(0, desired={"a1"})
    slot1 = _make_slot(1, desired={"a2"})
    # Both connected past book_refresh_s, slot0 more overdue
    now = time.monotonic()
    for s in (slot0, slot1):
        s._connected = True
        s._subscribed_assets = set(s.desired_assets)
        s._last_message_at = now  # not stale

    slot0._connected_at = now - 500  # 500s, most overdue
    slot1._connected_at = now - 400  # 400s, also overdue

    rec = Reconciler(
        registry=registry, slots=[slot0, slot1], interval_s=30, book_refresh_s=300,
    )
    await rec._reconcile()

    # Only the most overdue (slot0) should be rotated
    assert slot0._rotate_event.is_set()
    assert not slot1._rotate_event.is_set()
    assert rec._book_refreshes == 1

    # Second cycle: now slot1 gets rotated
    slot0._rotate_event.clear()
    slot0._connected = False  # slot0 is reconnecting
    await rec._reconcile()

    assert slot1._rotate_event.is_set()
    assert rec._book_refreshes == 2


async def test_wake_sets_event() -> None:
    """wake() should set the internal event."""
    registry = _make_registry()
    rec = Reconciler(registry=registry, slots=[], interval_s=30)

    assert not rec._wake_event.is_set()
    rec.wake()
    assert rec._wake_event.is_set()
