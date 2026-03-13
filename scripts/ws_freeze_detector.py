#!/usr/bin/env python3
"""Polymarket CLOB WebSocket freeze detector.

Connects to the Polymarket CLOB WebSocket for a single asset and detects
when the orderbook (book/best_bid_ask) diverges from actual trades
(last_trade_price).  During "freeze" episodes the WS keeps sending events
but the book prices are stale — LTP is the only signal that stays live.

The smoking gun: in a healthy order book, LTP can never be below the best
bid (sellers would fill against the bid).  When LTP < best_bid, the book
is provably stale.

Requires only: websockets (pip install websockets)

Usage:
    python ws_freeze_detector.py <asset_id> [--duration 120]
    python ws_freeze_detector.py <asset_id> --duration 300 --threshold 0.03

Output:
    Live ticker showing BBA vs LTP with freeze detection.
    Summary with freeze episodes, durations, and max divergence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field

import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ── data structures ────────────────────────────────────────────────────

@dataclass
class BookState:
    """Running orderbook state from book + best_bid_ask events."""
    bid: float = 0.0
    ask: float = 0.0
    updated_at: float = 0.0
    update_count: int = 0
    source: str = ""  # "book" or "bba"


@dataclass
class LTPState:
    """Running last-trade-price state."""
    price: float = 0.0
    updated_at: float = 0.0
    update_count: int = 0


@dataclass
class FreezeEpisode:
    """A detected freeze episode."""
    started_at: float
    ended_at: float = 0.0
    peak_divergence: float = 0.0
    book_bid_at_peak: float = 0.0
    ltp_at_peak: float = 0.0
    book_updates_during: int = 0
    ltp_updates_during: int = 0

    @property
    def duration_s(self) -> float:
        end = self.ended_at or time.time()
        return end - self.started_at


@dataclass
class Stats:
    """Aggregate session statistics."""
    book_events: int = 0
    bba_events: int = 0
    ltp_events: int = 0
    price_change_events: int = 0
    other_events: int = 0
    freeze_episodes: list[FreezeEpisode] = field(default_factory=list)
    max_divergence: float = 0.0
    samples: list[dict] = field(default_factory=list)


# ── detection logic ────────────────────────────────────────────────────

def detect_freeze(
    book: BookState,
    ltp: LTPState,
    threshold: float,
) -> float | None:
    """Return divergence if freeze detected, else None.

    A freeze is detected when:
    1. Both book and LTP have data
    2. LTP < book.bid - threshold  (impossible in healthy order book)
       OR |book_mid - LTP| > threshold and book hasn't moved
    """
    if book.bid <= 0 or ltp.price <= 0:
        return None

    book_mid = (book.bid + book.ask) / 2 if book.ask > 0 else book.bid
    divergence = abs(book_mid - ltp.price)

    # Smoking gun: LTP below best bid (impossible if book is live)
    if ltp.price < book.bid - threshold:
        return divergence

    # Soft signal: large divergence between book mid and LTP
    if divergence > threshold:
        return divergence

    return None


# ── main loop ──────────────────────────────────────────────────────────

async def monitor(asset_id: str, duration: float, threshold: float, tick: float):
    book = BookState()
    ltp = LTPState()
    stats = Stats()
    current_freeze: FreezeEpisode | None = None
    t0 = time.time()
    last_print = 0.0

    print(f"Polymarket CLOB WS Freeze Detector")
    print(f"{'=' * 72}")
    print(f"Asset:     {asset_id}")
    print(f"Duration:  {duration}s")
    print(f"Threshold: {threshold}")
    print(f"{'=' * 72}")
    print(f"Connecting to {WS_URL} ...")

    try:
        async with websockets.connect(WS_URL, ping_interval=None) as ws:
            # Subscribe to single asset with custom features (LTP + BBA)
            await ws.send(json.dumps({
                "type": "market",
                "assets_ids": [asset_id],
                "custom_feature_enabled": True,
            }))

            # App-level ping (Polymarket requirement, every 10s)
            async def ping_loop():
                while True:
                    await asyncio.sleep(10)
                    try:
                        await ws.send("PING")
                    except Exception:
                        return

            ping_task = asyncio.create_task(ping_loop())

            print(f"Subscribed. Waiting for data...\n")
            header = (
                f"{'elapsed':>8s}  {'book_bid':>9s} {'book_ask':>9s}  "
                f"{'LTP':>9s}  {'diverge':>8s}  {'status':>8s}  {'events'}"
            )
            print(header)
            print("-" * len(header) + "-" * 20)

            try:
                while time.time() - t0 < duration:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue

                    now = time.time()
                    elapsed = now - t0

                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msgs = payload if isinstance(payload, list) else [payload]

                    for msg in msgs:
                        if not isinstance(msg, dict):
                            continue

                        # Only process events for our target asset
                        msg_asset = msg.get("asset_id", "")
                        event_type = msg.get("event_type", "")

                        if event_type == "book" and msg_asset == asset_id:
                            bids = msg.get("bids") or []
                            asks = msg.get("asks") or []
                            if bids:
                                book.bid = max(float(b["price"]) for b in bids)
                            if asks:
                                book.ask = min(float(a["price"]) for a in asks)
                            book.updated_at = now
                            book.update_count += 1
                            book.source = "book"
                            stats.book_events += 1
                            if current_freeze:
                                current_freeze.book_updates_during += 1

                        elif event_type == "best_bid_ask" and msg_asset == asset_id:
                            bb = float(msg.get("best_bid", 0))
                            ba = float(msg.get("best_ask", 0))
                            if bb > 0:
                                book.bid = bb
                            if ba > 0:
                                book.ask = ba
                            book.updated_at = now
                            book.update_count += 1
                            book.source = "bba"
                            stats.bba_events += 1
                            if current_freeze:
                                current_freeze.book_updates_during += 1

                        elif event_type == "last_trade_price" and msg_asset == asset_id:
                            ltp.price = float(msg.get("price", 0))
                            ltp.updated_at = now
                            ltp.update_count += 1
                            stats.ltp_events += 1
                            if current_freeze:
                                current_freeze.ltp_updates_during += 1

                        elif event_type == "price_change":
                            stats.price_change_events += 1

                        else:
                            stats.other_events += 1

                    # ── freeze detection + periodic output ──
                    if now - last_print < tick:
                        continue
                    last_print = now

                    div = detect_freeze(book, ltp, threshold)

                    if div is not None:
                        stats.max_divergence = max(stats.max_divergence, div)
                        if current_freeze is None:
                            current_freeze = FreezeEpisode(started_at=now)
                            stats.freeze_episodes.append(current_freeze)
                        current_freeze.peak_divergence = max(
                            current_freeze.peak_divergence, div
                        )
                        if div >= current_freeze.peak_divergence:
                            current_freeze.book_bid_at_peak = book.bid
                            current_freeze.ltp_at_peak = ltp.price
                        status = "\033[91mFROZEN\033[0m"
                    else:
                        if current_freeze is not None:
                            current_freeze.ended_at = now
                            current_freeze = None
                        status = "\033[92m  OK  \033[0m"

                    # Impossible-state flag
                    impossible = ""
                    if ltp.price > 0 and book.bid > 0 and ltp.price < book.bid - 0.005:
                        impossible = " \033[93mLTP<BID!\033[0m"

                    div_str = f"{div:+.4f}" if div is not None else "  0.00"
                    total = stats.book_events + stats.bba_events + stats.ltp_events

                    line = (
                        f"{elapsed:>7.1f}s  {book.bid:>9.4f} {book.ask:>9.4f}  "
                        f"{ltp.price:>9.4f}  {div_str:>8s}  {status}  "
                        f"bk={stats.book_events} bba={stats.bba_events} "
                        f"ltp={stats.ltp_events} pc={stats.price_change_events}"
                        f"{impossible}"
                    )
                    print(line)

                    stats.samples.append({
                        "elapsed": round(elapsed, 1),
                        "book_bid": book.bid,
                        "book_ask": book.ask,
                        "ltp": ltp.price,
                        "divergence": div,
                        "frozen": div is not None,
                    })

            finally:
                ping_task.cancel()

    except websockets.ConnectionClosed as e:
        print(f"\nConnection closed: code={e.code} reason={e.reason}")
    except Exception as e:
        print(f"\nError: {e}")

    # Close any open freeze episode
    if current_freeze and not current_freeze.ended_at:
        current_freeze.ended_at = time.time()

    # ── summary ────────────────────────────────────────────────────
    print(f"\n{'=' * 72}")
    print("SESSION SUMMARY")
    print(f"{'=' * 72}")

    total_events = (
        stats.book_events + stats.bba_events + stats.ltp_events
        + stats.price_change_events + stats.other_events
    )
    print(f"\nEvents received ({duration:.0f}s window):")
    print(f"  book snapshots:     {stats.book_events:>6d}")
    print(f"  best_bid_ask:       {stats.bba_events:>6d}")
    print(f"  last_trade_price:   {stats.ltp_events:>6d}")
    print(f"  price_change:       {stats.price_change_events:>6d}")
    print(f"  other:              {stats.other_events:>6d}")
    print(f"  TOTAL:              {total_events:>6d}")

    n_frozen = sum(1 for s in stats.samples if s["frozen"])
    n_total = len(stats.samples)
    frozen_pct = (n_frozen / n_total * 100) if n_total else 0

    print(f"\nFreeze detection (threshold={threshold}):")
    print(f"  Samples:            {n_total:>6d}")
    print(f"  Frozen samples:     {n_frozen:>6d}  ({frozen_pct:.1f}%)")
    print(f"  Max divergence:     {stats.max_divergence:>9.4f}")

    if stats.freeze_episodes:
        print(f"\n  Freeze episodes:    {len(stats.freeze_episodes)}")
        print(f"  {'#':>4s}  {'duration':>8s}  {'peak div':>8s}  "
              f"{'book bid':>8s}  {'LTP':>8s}  {'book evts':>9s}  {'LTP evts':>8s}")
        print(f"  {'-' * 62}")
        for i, ep in enumerate(stats.freeze_episodes, 1):
            print(
                f"  {i:>4d}  {ep.duration_s:>7.1f}s  {ep.peak_divergence:>8.4f}  "
                f"{ep.book_bid_at_peak:>8.4f}  {ep.ltp_at_peak:>8.4f}  "
                f"{ep.book_updates_during:>9d}  {ep.ltp_updates_during:>8d}"
            )
        total_frozen_s = sum(ep.duration_s for ep in stats.freeze_episodes)
        print(f"\n  Total frozen time:  {total_frozen_s:>7.1f}s / {duration:.0f}s "
              f"({total_frozen_s / duration * 100:.1f}%)")
    else:
        print(f"\n  No freeze episodes detected.")

    # Impossible-state count
    impossible_count = sum(
        1 for s in stats.samples
        if s["ltp"] > 0 and s["book_bid"] > 0 and s["ltp"] < s["book_bid"] - 0.005
    )
    if impossible_count:
        print(f"\n  \033[93mIMPOSSIBLE STATES (LTP < best_bid): {impossible_count} samples\033[0m")
        print(f"  This proves the book was stale — in a live order book,")
        print(f"  sellers would fill against the bid, never below it.")

    print(f"\n{'=' * 72}")
    if stats.freeze_episodes:
        print("CONCLUSION: Freeze(s) detected. CLOB WS served stale book data.")
        print("            LTP stayed live — use it as ground truth during freezes.")
    else:
        print("CONCLUSION: No freeze detected during this window.")
        print("            Book and LTP were consistent throughout.")
    print(f"{'=' * 72}")


# ── entry point ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Detect Polymarket CLOB WebSocket freeze episodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
What this detects:
  During fast market moves, the Polymarket CLOB WebSocket continues sending
  book/BBA events but with STALE prices (10-120+ seconds). The only event
  that stays live is last_trade_price (LTP).

  The smoking gun: if LTP < best_bid, the book is provably stale — in a
  healthy order book, any sell would fill against the bid, never below it.

Examples:
  # Monitor for 2 minutes with default threshold (0.03)
  python ws_freeze_detector.py 0x1234...abcd

  # Longer session, tighter threshold
  python ws_freeze_detector.py 0x1234...abcd --duration 600 --threshold 0.02

  # Faster tick rate for high-frequency monitoring
  python ws_freeze_detector.py 0x1234...abcd --tick 0.5
""",
    )
    parser.add_argument(
        "asset_id",
        help="Polymarket asset ID (the long numeric token ID)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=120,
        help="Monitoring duration in seconds (default: 120)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.03,
        help="Divergence threshold to flag as freeze (default: 0.03 = 3 cents)",
    )
    parser.add_argument(
        "--tick",
        type=float,
        default=1.0,
        help="Output interval in seconds (default: 1.0)",
    )
    args = parser.parse_args()

    if not args.asset_id:
        parser.error("asset_id is required")

    try:
        asyncio.run(monitor(args.asset_id, args.duration, args.threshold, args.tick))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
