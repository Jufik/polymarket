"""Managed WebSocket slot -- a single orderbook connection with health monitoring.

Replaces the fire-and-forget ``_run_targeted()`` tasks.  Each slot owns up to
500 asset subscriptions and is controlled by the :class:`Reconciler`:

* The reconciler sets ``desired_assets`` and calls ``signal_reconnect()``
  for subscription changes or ``signal_rotate()`` for book refresh.
* ``signal_reconnect()`` hard-disconnects (clears books, reconnects).
* ``signal_rotate()`` opens a **new** WS alongside the old one, feeds from
  both until the new one is primed (received first message), then closes the
  old one.  Zero-gap book refresh — no pricing blackout.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
import websockets

log = structlog.get_logger()

RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
MAX_ASSETS_PER_SLOT = 500
_SWAP_TIMEOUT_S = 30.0
_APP_PING_INTERVAL_S = 10.0  # Polymarket requires app-level "PING" every 10s


@dataclasses.dataclass(frozen=True)
class SlotState:
    """Observable snapshot of a slot's current state."""

    slot_id: int
    desired_assets: int
    subscribed_assets: int
    last_message_at: float
    connected: bool
    msg_count: int
    reconnect_count: int


class ManagedSlot:
    """A single managed WS connection for up to 500 asset subscriptions.

    The reconciler sets :attr:`desired_assets` and calls
    :meth:`signal_reconnect` when the subscription set changes or a health
    check fails, or :meth:`signal_rotate` for periodic book refresh (hot swap).

    Hot rotation:
      Old WS continues serving data while a new WS connects and subscribes
      to the same assets.  Both connections feed into ``_on_message`` during
      the overlap window.  Once the new connection receives its first message
      (proving it's alive and receiving ``book`` snapshots), the old connection
      is closed.  The new connection becomes the active one — zero gap.
    """

    def __init__(
        self,
        slot_id: int,
        ws_url: str,
        on_message: Callable[[str], Awaitable[None]],
        stale_timeout_s: float = 120.0,
        on_reconnect: Callable[[set[str]], None] | None = None,
        startup_delay_s: float = 0.0,
    ) -> None:
        self.slot_id = slot_id
        self._ws_url = ws_url
        self._on_message = on_message
        self._stale_timeout_s = stale_timeout_s
        self._on_reconnect = on_reconnect
        self._startup_delay_s = startup_delay_s

        self.desired_assets: set[str] = set()
        self._subscribed_assets: set[str] = set()
        self._reconnect_event = asyncio.Event()
        self._rotate_event = asyncio.Event()
        self._update_event = asyncio.Event()
        self._pending_subs: set[str] = set()
        self._pending_unsubs: set[str] = set()
        self._last_message_at: float = 0.0
        self._connected: bool = False
        self._connected_at: float = 0.0
        self._msg_count: int = 0
        self._reconnect_count: int = 0
        self._rotation_count: int = 0

    @property
    def headroom(self) -> int:
        """How many more assets this slot can accept."""
        return MAX_ASSETS_PER_SLOT - len(self.desired_assets)

    @property
    def subscribed_assets(self) -> set[str]:
        return set(self._subscribed_assets)

    @property
    def state(self) -> SlotState:
        return SlotState(
            slot_id=self.slot_id,
            desired_assets=len(self.desired_assets),
            subscribed_assets=len(self._subscribed_assets),
            last_message_at=self._last_message_at,
            connected=self._connected,
            msg_count=self._msg_count,
            reconnect_count=self._reconnect_count,
        )

    def signal_reconnect(self) -> None:
        """Wake the slot to hard-reconnect with updated desired_assets.

        Clears book state (via ``on_reconnect`` callback) and reconnects.
        Use for stale detection or when dynamic update isn't possible.
        """
        self._reconnect_event.set()

    def signal_update(
        self, to_add: set[str] | None = None, to_remove: set[str] | None = None
    ) -> None:
        """Queue dynamic subscribe/unsubscribe on the live WS — no disconnect.

        The recv loop picks up the event and sends subscribe/unsubscribe
        messages to the active connection.  Falls back to signal_reconnect
        if the slot isn't connected yet.
        """
        if not self._connected:
            self._reconnect_event.set()
            return
        if to_add:
            self._pending_subs |= to_add
        if to_remove:
            self._pending_unsubs |= to_remove
        self._update_event.set()

    def signal_rotate(self) -> None:
        """Signal a hot rotation for fresh book snapshots.

        Opens a new WS alongside the old one, swaps when the new one
        is receiving data.  No book clearing, no pricing gap.
        Use for periodic book refresh (delta drift prevention).
        """
        self._rotate_event.set()

    @property
    def connected_duration_s(self) -> float:
        """Seconds since this slot connected (0 if not connected)."""
        if not self._connected or self._connected_at == 0.0:
            return 0.0
        return time.monotonic() - self._connected_at

    def is_stale(self) -> bool:
        """True if connected with assets but no message within stale timeout."""
        if not self._connected or not self._subscribed_assets:
            return False
        if self._last_message_at == 0.0:
            return False
        return (time.monotonic() - self._last_message_at) > self._stale_timeout_s

    # ── connection lifecycle ──────────────────────────────────────────

    async def _connect_and_subscribe(self) -> Any:
        """Open a new WS and subscribe to ``_subscribed_assets``."""
        # Disable protocol-level pings — Polymarket uses app-level "PING" text
        ws = await websockets.connect(self._ws_url, ping_interval=None)
        payload = {
            "type": "market",
            "markets": [],
            "assets_ids": list(self._subscribed_assets),
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(payload))
        return ws

    @staticmethod
    async def _ping_loop(ws: Any, slot_id: int) -> None:
        """Send application-level PING every 10s (Polymarket requirement)."""
        while True:
            await asyncio.sleep(_APP_PING_INTERVAL_S)
            try:
                await ws.send("PING")
            except Exception:
                return  # Connection dead — let recv_loop detect it

    async def run(self) -> None:
        """Main loop: connect -> recv -> reconnect/rotate on signal/stale/error."""
        if self._startup_delay_s > 0:
            log.info(
                "slot.startup_delay",
                slot=self.slot_id,
                delay_s=self._startup_delay_s,
            )
            await asyncio.sleep(self._startup_delay_s)
        backoff = RECONNECT_BASE
        # Track whether the next reconnect needs a full book clear.
        # Only clear on reconciler-initiated reconnects (subscription changes)
        # or stale detection — NOT on server-initiated closes where the book
        # data is still valid and fresh snapshots will replace it.
        need_clear = True  # First connect always clears (no prior state)
        while True:
            # Snapshot desired into subscribed
            self._subscribed_assets = set(self.desired_assets)

            if not self._subscribed_assets:
                # Idle slot -- wait until reconciler assigns assets
                self._connected = False
                await self._reconnect_event.wait()
                self._reconnect_event.clear()
                need_clear = True
                continue

            # Only clear book state on reconciler-initiated reconnects
            # (subscription changes / stale detection). Server-initiated
            # closes don't invalidate the book — fresh snapshots will
            # replace each asset individually via _process_book_snapshot.
            if need_clear and self._on_reconnect is not None:
                self._on_reconnect(self._subscribed_assets)
            need_clear = False  # Reset — next close won't clear unless signalled

            ws: Any = None
            ping_task: asyncio.Task[None] | None = None
            try:
                ws = await self._connect_and_subscribe()
                ping_task = asyncio.create_task(self._ping_loop(ws, self.slot_id))
                self._connected = True
                self._connected_at = time.monotonic()
                backoff = RECONNECT_BASE
                log.info(
                    "slot.connected",
                    slot=self.slot_id,
                    assets=len(self._subscribed_assets),
                )

                # Inner loop: recv with hot rotation support
                while True:
                    reason = await self._recv_loop(ws)

                    if reason == "rotate":
                        new_ws = await self._hot_swap(ws)
                        if new_ws is not None:
                            ws = new_ws
                            if ping_task is not None:
                                ping_task.cancel()
                            ping_task = asyncio.create_task(
                                self._ping_loop(ws, self.slot_id)
                            )
                            self._connected_at = time.monotonic()
                            self._rotation_count += 1
                            continue  # inner loop continues with new ws
                        # Rotation failed — fall through to normal reconnect
                        log.warning("slot.rotation_failed", slot=self.slot_id)

                    break  # stale, reconnect, closed, or failed rotation

                log.info(
                    "slot.disconnecting",
                    slot=self.slot_id,
                    reason=reason,
                    msgs=self._msg_count,
                )

                # Reconciler-initiated or stale → clear books on next reconnect
                if reason in ("reconnect", "stale"):
                    need_clear = True

            except websockets.ConnectionClosed as exc:
                log.warning(
                    "slot.closed",
                    slot=self.slot_id,
                    code=exc.code,
                    reason=exc.reason[:120] if exc.reason else "",
                )
            except Exception:
                log.exception("slot.error", slot=self.slot_id)
            finally:
                self._connected = False
                self._reconnect_count += 1
                if ping_task is not None:
                    ping_task.cancel()
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass

            # If reconnect was signalled, don't backoff
            if self._reconnect_event.is_set():
                self._reconnect_event.clear()
                need_clear = True  # Reconciler-initiated → clear
                continue

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    # ── recv loop ─────────────────────────────────────────────────────

    async def _recv_loop(
        self, ws: Any,
    ) -> str:
        """Receive messages until stale/reconnect/rotate/disconnect.

        Uses ``asyncio.wait`` to react to a WS message, a reconnect signal,
        a rotate signal, or a dynamic update signal — whichever comes first.
        Returns the reason for exiting: ``"reconnect"``, ``"rotate"``,
        ``"stale"``, or ``"closed"``.

        Dynamic updates (subscribe/unsubscribe) are handled inline without
        exiting — the recv loop sends the update message on the live WS
        and continues receiving.
        """
        recv_fut: asyncio.Future[Any] = asyncio.ensure_future(ws.recv())
        reconnect_fut: asyncio.Future[Any] = asyncio.ensure_future(
            self._reconnect_event.wait()
        )
        rotate_fut: asyncio.Future[Any] = asyncio.ensure_future(
            self._rotate_event.wait()
        )
        update_fut: asyncio.Future[Any] = asyncio.ensure_future(
            self._update_event.wait()
        )

        try:
            while True:
                done, _ = await asyncio.wait(
                    {recv_fut, reconnect_fut, rotate_fut, update_fut},
                    timeout=self._stale_timeout_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    # Timeout -- stale
                    return "stale"

                # Reconnect takes priority over everything
                if reconnect_fut in done:
                    recv_fut.cancel()
                    rotate_fut.cancel()
                    update_fut.cancel()
                    self._reconnect_event.clear()
                    return "reconnect"

                if rotate_fut in done:
                    recv_fut.cancel()
                    update_fut.cancel()
                    self._rotate_event.clear()
                    return "rotate"

                # Dynamic update — send subscribe/unsubscribe, stay connected
                if update_fut in done:
                    self._update_event.clear()
                    await self._apply_updates(ws)
                    update_fut = asyncio.ensure_future(
                        self._update_event.wait()
                    )
                    continue  # back to wait loop

                # recv_fut completed
                try:
                    raw = recv_fut.result()
                except websockets.ConnectionClosed as exc:
                    log.warning(
                        "slot.server_close",
                        slot=self.slot_id,
                        code=exc.code,
                        reason=exc.reason[:120] if exc.reason else "",
                        msgs=self._msg_count,
                    )
                    reconnect_fut.cancel()
                    rotate_fut.cancel()
                    update_fut.cancel()
                    return "closed"

                self._last_message_at = time.monotonic()
                self._msg_count += 1
                await self._on_message(str(raw))
                msg_dt = time.monotonic() - self._last_message_at
                if msg_dt > 0.005:
                    log.warning(
                        "slot.slow_msg",
                        slot=self.slot_id,
                        ms=round(msg_dt * 1000, 1),
                    )

                # Start next recv
                recv_fut = asyncio.ensure_future(ws.recv())

        finally:
            # Cleanup any pending futures
            if not recv_fut.done():
                recv_fut.cancel()
            if not reconnect_fut.done():
                reconnect_fut.cancel()
            if not rotate_fut.done():
                rotate_fut.cancel()
            if not update_fut.done():
                update_fut.cancel()

    async def _apply_updates(self, ws: Any) -> None:
        """Send queued subscribe/unsubscribe messages on the live WS."""
        subs = self._pending_subs
        unsubs = self._pending_unsubs
        self._pending_subs = set()
        self._pending_unsubs = set()

        if unsubs:
            msg = {"assets_ids": list(unsubs), "operation": "unsubscribe"}
            await ws.send(json.dumps(msg))
            self._subscribed_assets -= unsubs
            log.info(
                "slot.unsubscribed",
                slot=self.slot_id,
                count=len(unsubs),
                subscribed=len(self._subscribed_assets),
            )

        if subs:
            msg = {"assets_ids": list(subs), "operation": "subscribe"}
            await ws.send(json.dumps(msg))
            self._subscribed_assets |= subs
            log.info(
                "slot.subscribed",
                slot=self.slot_id,
                count=len(subs),
                subscribed=len(self._subscribed_assets),
            )

    # ── hot swap ──────────────────────────────────────────────────────

    async def _hot_swap(self, old_ws: Any) -> Any | None:
        """Open a new WS alongside the old, swap when the new one is primed.

        Both connections feed ``_on_message`` during the overlap window.
        The new connection's ``book`` snapshot replaces any drifted state
        in the L2 book.  Once the new connection receives its first message,
        the old connection is closed.

        Returns the new WS on success, ``None`` on failure (old WS still
        alive — caller can fall back to hard reconnect).
        """
        try:
            new_ws = await asyncio.wait_for(
                websockets.connect(self._ws_url, ping_interval=None),
                timeout=10.0,
            )
        except Exception:
            log.warning("slot.swap_connect_failed", slot=self.slot_id)
            return None

        try:
            payload = {
                "type": "market",
                "markets": [],
                "assets_ids": list(self._subscribed_assets),
                "custom_feature_enabled": True,
            }
            await new_ws.send(json.dumps(payload))
            log.info(
                "slot.swap_started",
                slot=self.slot_id,
                assets=len(self._subscribed_assets),
            )

            # Feed from both connections until new one gets its first message
            old_recv: asyncio.Future[Any] = asyncio.ensure_future(old_ws.recv())
            new_recv: asyncio.Future[Any] = asyncio.ensure_future(new_ws.recv())
            new_primed = False

            deadline = time.monotonic() + _SWAP_TIMEOUT_S
            while not new_primed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                pending: set[asyncio.Future[Any]] = set()
                if not old_recv.done():
                    pending.add(old_recv)
                if not new_recv.done():
                    pending.add(new_recv)
                if not pending:
                    break

                done, _ = await asyncio.wait(
                    pending,
                    timeout=min(remaining, 5.0),
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    continue  # timeout within wait, retry until deadline

                for fut in done:
                    try:
                        raw = fut.result()
                    except websockets.ConnectionClosed:
                        if fut is new_recv:
                            # New connection died during swap — abort
                            if not old_recv.done():
                                old_recv.cancel()
                            return None
                        # Old connection died — new one takes over regardless
                        new_primed = True
                        break

                    self._last_message_at = time.monotonic()
                    self._msg_count += 1
                    await self._on_message(str(raw))

                    if fut is new_recv:
                        new_primed = True
                    elif not new_primed:
                        # Keep draining old connection while waiting for new
                        old_recv = asyncio.ensure_future(old_ws.recv())

            # Cleanup pending futures
            if not old_recv.done():
                old_recv.cancel()
            if not new_recv.done():
                new_recv.cancel()

            if new_primed:
                # Close old connection — new one is live
                try:
                    await old_ws.close()
                except Exception:
                    pass
                log.info("slot.rotated", slot=self.slot_id)
                return new_ws

            # Swap timed out — abort, keep old connection
            log.warning(
                "slot.swap_timeout",
                slot=self.slot_id,
                timeout_s=_SWAP_TIMEOUT_S,
            )
            try:
                await new_ws.close()
            except Exception:
                pass
            return None

        except Exception:
            log.exception("slot.swap_error", slot=self.slot_id)
            try:
                await new_ws.close()
            except Exception:
                pass
            return None
