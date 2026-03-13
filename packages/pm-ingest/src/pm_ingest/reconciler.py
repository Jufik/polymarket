"""Reconciliation loop -- syncs desired assets (Redis) with WS slot pool.

Runs every ``interval_s`` (default 30s) or immediately on :meth:`wake`.
Computes the diff between what the :class:`AssetRegistry` says we want and
what the :class:`ManagedSlot` pool is actually subscribed to, then
redistributes assets and signals slot reconnects as needed.

Also performs health checks: slots that haven't received a message within
their stale timeout are forced to reconnect.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import structlog

from pm_ingest.asset_registry import AssetRegistry
from pm_ingest.ingestors.managed_slot import ManagedSlot

log = structlog.get_logger()


class Reconciler:
    """Periodically diffs desired (registry) vs subscribed (slots).

    With ``redundancy > 1``, each asset is assigned to multiple slots for
    overlap coverage.  Slots within a redundancy group are staggered in
    startup time so their natural server-disconnect cadence is offset,
    ensuring at least one connection is alive at any moment.
    """

    def __init__(
        self,
        registry: AssetRegistry,
        slots: list[ManagedSlot],
        on_remove_asset: Callable[[str], None] | None = None,
        interval_s: float = 30.0,
        book_refresh_s: float = 300.0,
        redundancy: int = 1,
        # Deprecated — kept for backward compat, ignored if on_remove_asset set.
        books: dict[str, Any] | None = None,
        prev_tops: dict[str, Any] | None = None,
    ) -> None:
        self._registry = registry
        self._slots = slots
        self._on_remove_asset = on_remove_asset
        self._books = books  # legacy fallback
        self._prev_tops = prev_tops  # legacy fallback
        self._interval_s = interval_s
        self._book_refresh_s = book_refresh_s
        self._redundancy = max(1, redundancy)
        self._wake_event = asyncio.Event()
        self._reconcile_count: int = 0
        self._last_reconcile_at: float = 0.0
        self._last_added: int = 0
        self._last_removed: int = 0
        self._book_refreshes: int = 0

    def wake(self) -> None:
        """Trigger immediate reconciliation (non-blocking)."""
        self._wake_event.set()

    def stats(self) -> dict[str, Any]:
        """Stats for heartbeat / observability."""
        return {
            "reconcile_count": self._reconcile_count,
            "last_reconcile_ago_s": round(time.monotonic() - self._last_reconcile_at, 1)
            if self._last_reconcile_at > 0
            else -1,
            "last_added": self._last_added,
            "last_removed": self._last_removed,
            "book_refreshes": self._book_refreshes,
        }

    async def run(self) -> None:
        """Main reconciliation loop."""
        while True:
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._interval_s)
            except TimeoutError:
                pass  # Normal periodic tick
            self._wake_event.clear()

            try:
                await self._reconcile()
            except Exception:
                log.exception("reconciler.error")

    async def _reconcile(self) -> None:
        """Core reconciliation: diff desired vs subscribed, redistribute."""
        # 1. Get desired from registry
        try:
            desired = await self._registry.get_desired()
        except Exception:
            log.warning("reconciler.registry_unavailable")
            self._health_check()
            return

        # 2. Build per-asset slot membership map
        asset_slots: dict[str, set[int]] = {}
        for slot in self._slots:
            for aid in slot.desired_assets:
                asset_slots.setdefault(aid, set()).add(slot.slot_id)

        subscribed_anywhere = set(asset_slots.keys())

        # 3. Diff: removals + under-replicated additions
        to_remove = subscribed_anywhere - desired

        additions: list[str] = []
        for aid in desired:
            have = len(asset_slots.get(aid, set()))
            need = max(0, self._redundancy - have)
            additions.extend([aid] * need)

        self._last_added = len(additions)
        self._last_removed = len(to_remove)

        # 4. Handle removals
        if to_remove:
            self._handle_removals(to_remove)

        # 5. Handle additions (redundancy-aware)
        if additions:
            self._handle_additions_replicated(additions, asset_slots)

        # 6. Health check
        self._health_check()

        self._reconcile_count += 1
        self._last_reconcile_at = time.monotonic()

        if additions or to_remove:
            log.info(
                "reconciler.reconciled",
                added=len(additions),
                removed=len(to_remove),
                desired=len(desired),
                subscribed=sum(len(s.desired_assets) for s in self._slots),
                redundancy=self._redundancy,
            )

    def _handle_removals(self, to_remove: set[str]) -> None:
        """Remove assets from slots via dynamic unsubscribe (no reconnect)."""
        for slot in self._slots:
            overlap = slot.desired_assets & to_remove
            if overlap:
                slot.desired_assets -= overlap
                slot.signal_update(to_remove=overlap)

        for asset_id in to_remove:
            if self._on_remove_asset is not None:
                self._on_remove_asset(asset_id)
            else:
                if self._books is not None:
                    self._books.pop(asset_id, None)
                if self._prev_tops is not None:
                    self._prev_tops.pop(asset_id, None)

    def _handle_additions(self, to_add: list[str]) -> None:
        """Distribute new assets evenly across slots (redundancy=1 fast path)."""
        asset_slots: dict[str, set[int]] = {}
        for slot in self._slots:
            for aid in slot.desired_assets:
                asset_slots.setdefault(aid, set()).add(slot.slot_id)
        self._handle_additions_replicated(to_add, asset_slots)

    def _handle_additions_replicated(
        self,
        additions: list[str],
        asset_slots: dict[str, set[int]],
    ) -> None:
        """Distribute assets across slots, avoiding same-slot duplicates."""
        slot_adds: dict[int, set[str]] = {}

        for aid in additions:
            already_in = asset_slots.get(aid, set())
            eligible = sorted(
                [s for s in self._slots if s.headroom > 0 and s.slot_id not in already_in],
                key=lambda s: len(s.desired_assets),
            )
            if not eligible:
                continue
            slot = eligible[0]
            slot.desired_assets.add(aid)
            slot_adds.setdefault(slot.slot_id, set()).add(aid)
            asset_slots.setdefault(aid, set()).add(slot.slot_id)

        for slot in self._slots:
            added = slot_adds.get(slot.slot_id)
            if added:
                slot.signal_update(to_add=added)

        overflow = sum(1 for aid in additions if aid not in asset_slots)
        if overflow > 0:
            log.warning("reconciler.overflow", dropped=overflow)

    def _health_check(self) -> None:
        """Force reconnect on stale or long-running slots."""
        for slot in self._slots:
            if slot.is_stale():
                log.warning(
                    "reconciler.stale_slot",
                    slot_id=slot.slot_id,
                    last_msg_ago_s=round(time.monotonic() - slot._last_message_at, 1),
                )
                slot.signal_reconnect()

        if self._book_refresh_s > 0:
            most_overdue: ManagedSlot | None = None
            most_overdue_duration = 0.0
            for slot in self._slots:
                duration = slot.connected_duration_s
                if duration > self._book_refresh_s and duration > most_overdue_duration:
                    most_overdue = slot
                    most_overdue_duration = duration
            if most_overdue is not None:
                log.info(
                    "reconciler.book_refresh",
                    slot_id=most_overdue.slot_id,
                    connected_s=round(most_overdue_duration, 0),
                )
                most_overdue.signal_rotate()
                self._book_refreshes += 1
