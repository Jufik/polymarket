"""Latency probe — empirical measurement of RTDS vs Alchemy delivery timing.

Connects to both feeds simultaneously, matches trades on tx_hash,
and computes four key deltas:

  Delta A: rtds_event_ts - alchemy_block_ts   (Is RTDS pre-chain?)
  Delta B: rtds_arrival  - alchemy_arrival     (Who delivers first?)
  Delta C: rtds_arrival  - rtds_event_ts       (RTDS internal delay)
  Delta D: alchemy_arrival - alchemy_block_ts  (Alchemy propagation delay)

Also tests RTDS `activity/orders_matched` topic for earlier signal.

Usage:
  uv run python scripts/latency_probe.py
  uv run python scripts/latency_probe.py --duration 3600   # run 1 hour
  uv run python scripts/latency_probe.py --out results.json

Requires PM_ALCHEMY_WS_URL in .env (or as environment variable).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median, quantiles

import websockets
from dotenv import load_dotenv

# ── Configuration ────────────────────────────────────────────────────────

RTDS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL = 5

CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

EXCHANGE_ADDRS = frozenset(
    {
        CTF_EXCHANGE,
        NEGRISK_EXCHANGE,
    }
)

STATS_INTERVAL = 30  # print stats every N seconds
MATCH_WINDOW = 300  # discard unmatched entries older than N seconds


# ── Data structures ──────────────────────────────────────────────────────


@dataclass
class RTDSEvent:
    tx_hash: str
    event_ts: float  # payload.timestamp (trade time, seconds)
    delivery_ts: float  # msg.timestamp (RTDS delivery time, seconds)
    arrival: float  # time.time() when we received it
    condition_id: str
    price: float
    size: float
    side: str
    maker: str | None
    topic_type: str  # "trades" or "orders_matched"


@dataclass
class AlchemyEvent:
    tx_hash: str
    block_ts: float  # blockTimestamp (on-chain, seconds)
    arrival: float  # time.time() when we received it
    block_number: int
    maker: str | None
    taker: str | None


@dataclass
class MatchedTrade:
    tx_hash: str
    # RTDS
    rtds_event_ts: float
    rtds_delivery_ts: float
    rtds_arrival: float
    rtds_topic: str
    # Alchemy
    alchemy_block_ts: float
    alchemy_arrival: float
    # Computed deltas
    delta_a: float  # rtds_event_ts - alchemy_block_ts
    delta_b: float  # rtds_arrival - alchemy_arrival
    delta_c: float  # rtds_arrival - rtds_event_ts
    delta_d: float  # alchemy_arrival - alchemy_block_ts


@dataclass
class ProbeState:
    rtds_events: dict[str, RTDSEvent] = field(default_factory=dict)
    alchemy_events: dict[str, AlchemyEvent] = field(default_factory=dict)
    matched: list[MatchedTrade] = field(default_factory=list)

    # Counters
    rtds_trades_count: int = 0
    rtds_orders_matched_count: int = 0
    alchemy_count: int = 0
    rtds_no_txhash: int = 0

    # Watchdog
    rtds_last_msg: float = 0.0
    alchemy_last_msg: float = 0.0
    rtds_reconnects: int = 0

    def try_match(self, tx_hash: str) -> None:
        """Attempt to match an RTDS and Alchemy event by tx_hash."""
        if tx_hash not in self.rtds_events or tx_hash not in self.alchemy_events:
            return

        r = self.rtds_events.pop(tx_hash)
        a = self.alchemy_events.pop(tx_hash)

        self.matched.append(
            MatchedTrade(
                tx_hash=tx_hash,
                rtds_event_ts=r.event_ts,
                rtds_delivery_ts=r.delivery_ts,
                rtds_arrival=r.arrival,
                rtds_topic=r.topic_type,
                alchemy_block_ts=a.block_ts,
                alchemy_arrival=a.arrival,
                delta_a=r.event_ts - a.block_ts,
                delta_b=r.arrival - a.arrival,
                delta_c=r.arrival - r.event_ts,
                delta_d=a.arrival - a.block_ts,
            )
        )

    def gc_stale(self) -> None:
        """Remove unmatched entries older than MATCH_WINDOW."""
        cutoff = time.time() - MATCH_WINDOW
        self.rtds_events = {k: v for k, v in self.rtds_events.items() if v.arrival > cutoff}
        self.alchemy_events = {k: v for k, v in self.alchemy_events.items() if v.arrival > cutoff}


# ── RTDS feed ────────────────────────────────────────────────────────────


async def rtds_feed(state: ProbeState, stop: asyncio.Event) -> None:
    """Connect to RTDS and record trade arrivals."""
    backoff = 1.0
    while not stop.is_set():
        try:
            print("[RTDS] connecting...")
            async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
                backoff = 1.0
                print("[RTDS] connected")

                # Subscribe to both trades AND orders_matched
                subscribe = json.dumps(
                    {
                        "action": "subscribe",
                        "subscriptions": [
                            {"topic": "activity", "type": "trades"},
                            {"topic": "activity", "type": "orders_matched"},
                        ],
                    }
                )
                await ws.send(subscribe)

                ping_task = asyncio.create_task(_rtds_ping(ws, stop))
                watchdog_task = asyncio.create_task(_rtds_watchdog(state, ws, stop))

                try:
                    async for raw in ws:
                        if stop.is_set():
                            break
                        _handle_rtds_msg(state, raw)
                finally:
                    ping_task.cancel()
                    watchdog_task.cancel()

        except websockets.ConnectionClosed as e:
            state.rtds_reconnects += 1
            print(f"[RTDS] disconnected: {e} (reconnect #{state.rtds_reconnects})")
        except Exception as e:
            state.rtds_reconnects += 1
            print(f"[RTDS] error: {e} (reconnect #{state.rtds_reconnects})")

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


async def _rtds_ping(ws: websockets.WebSocketClientProtocol, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(PING_INTERVAL)
        try:
            await ws.send("PING")
        except Exception:
            return


async def _rtds_watchdog(
    state: ProbeState,
    ws: websockets.WebSocketClientProtocol,
    stop: asyncio.Event,
) -> None:
    """Force reconnect if no data messages for 120 seconds."""
    while not stop.is_set():
        await asyncio.sleep(30)
        if state.rtds_last_msg > 0 and time.time() - state.rtds_last_msg > 120:
            print(
                f"[RTDS] SILENT FREEZE detected — no data for "
                f"{time.time() - state.rtds_last_msg:.0f}s, forcing reconnect"
            )
            await ws.close()
            return


def _handle_rtds_msg(state: ProbeState, raw: str) -> None:
    """Process a single RTDS WebSocket message."""
    if raw in ("PING", "PONG", "pong"):
        return

    now = time.time()

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    msg_type = msg.get("type")
    if msg_type not in ("trades", "orders_matched"):
        return

    state.rtds_last_msg = now

    payload = msg.get("payload")
    if not payload:
        return

    tx_hash = payload.get("transactionHash") or msg.get("transactionHash")
    if not tx_hash:
        state.rtds_no_txhash += 1
        return

    tx_hash = tx_hash.lower()

    if msg_type == "trades":
        state.rtds_trades_count += 1
    else:
        state.rtds_orders_matched_count += 1

    # Don't overwrite an earlier arrival for the same tx_hash
    if tx_hash in state.rtds_events:
        existing = state.rtds_events[tx_hash]
        # Keep whichever arrived first
        if existing.arrival <= now:
            # But record if orders_matched beat trades
            if msg_type == "orders_matched" and existing.topic_type == "trades":
                pass  # trades arrived first, keep it
            elif msg_type == "trades" and existing.topic_type == "orders_matched":
                pass  # orders_matched arrived first, keep it
            return

    event_ts = float(payload.get("timestamp", 0))
    delivery_ts = float(msg.get("timestamp", 0))

    state.rtds_events[tx_hash] = RTDSEvent(
        tx_hash=tx_hash,
        event_ts=event_ts,
        delivery_ts=delivery_ts / 1000 if delivery_ts > 1e12 else delivery_ts,  # ms -> s
        arrival=now,
        condition_id=payload.get("conditionId", ""),
        price=float(payload.get("price", 0)),
        size=float(payload.get("size", 0)),
        side=payload.get("side", ""),
        maker=payload.get("proxyWallet"),
        topic_type=msg_type,
    )

    state.try_match(tx_hash)


# ── Alchemy feed ─────────────────────────────────────────────────────────


async def alchemy_feed(state: ProbeState, ws_url: str, stop: asyncio.Event) -> None:
    """Connect to Alchemy eth_subscribe and record OrderFilled arrivals."""
    backoff = 1.0
    while not stop.is_set():
        try:
            print("[Alchemy] connecting...")
            async with websockets.connect(ws_url, ping_interval=30) as ws:
                backoff = 1.0
                print("[Alchemy] connected")

                subscribe = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": [
                            "logs",
                            {"address": [CTF_EXCHANGE, NEGRISK_EXCHANGE]},
                        ],
                    }
                )
                await ws.send(subscribe)

                async for raw in ws:
                    if stop.is_set():
                        break
                    _handle_alchemy_msg(state, raw)

        except websockets.ConnectionClosed as e:
            print(f"[Alchemy] disconnected: {e}")
        except Exception as e:
            print(f"[Alchemy] error: {e}")

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _handle_alchemy_msg(state: ProbeState, raw: str) -> None:
    """Process a single Alchemy WebSocket message."""
    now = time.time()

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    # Skip subscription confirmations
    if msg.get("method") != "eth_subscription":
        return

    result = msg.get("params", {}).get("result")
    if not result:
        return

    topics = result.get("topics", [])
    if len(topics) < 4 or topics[0] != ORDER_FILLED_SIG:
        return

    # Drop taker-perspective duplicates
    taker = "0x" + topics[3][-40:]
    if taker.lower() in EXCHANGE_ADDRS:
        return

    state.alchemy_last_msg = now
    state.alchemy_count += 1

    tx_hash = result.get("transactionHash", "").lower()
    if not tx_hash:
        return

    block_ts_hex = result.get("blockTimestamp")
    block_ts = int(block_ts_hex, 16) if block_ts_hex else now

    maker = "0x" + topics[2][-40:]
    block_number = int(result.get("blockNumber", "0x0"), 16)

    # Don't overwrite (keep first arrival)
    if tx_hash in state.alchemy_events:
        return

    state.alchemy_events[tx_hash] = AlchemyEvent(
        tx_hash=tx_hash,
        block_ts=block_ts,
        arrival=now,
        block_number=block_number,
        maker=maker,
        taker=taker,
    )

    state.try_match(tx_hash)


# ── Stats printer ────────────────────────────────────────────────────────


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total > 0 else "n/a"


def print_stats(state: ProbeState) -> None:
    """Print current match statistics."""
    n = len(state.matched)
    pending_rtds = len(state.rtds_events)
    pending_alchemy = len(state.alchemy_events)

    print(f"\n{'=' * 72}")
    print(f"  Latency Probe — {time.strftime('%H:%M:%S')}")
    print(f"{'=' * 72}")
    print(f"  RTDS trades received:          {state.rtds_trades_count}")
    print(f"  RTDS orders_matched received:  {state.rtds_orders_matched_count}")
    print(f"  RTDS no tx_hash (skipped):     {state.rtds_no_txhash}")
    print(f"  RTDS reconnects:               {state.rtds_reconnects}")
    print(f"  Alchemy events received:       {state.alchemy_count}")
    print(f"  Matched trades:                {n}")
    print(f"  Pending RTDS (unmatched):      {pending_rtds}")
    print(f"  Pending Alchemy (unmatched):   {pending_alchemy}")

    if n == 0:
        print("\n  No matched trades yet — waiting for cross-feed matches on tx_hash...")
        print(f"{'=' * 72}\n")
        return

    deltas_a = [m.delta_a for m in state.matched]
    deltas_b = [m.delta_b for m in state.matched]
    deltas_c = [m.delta_c for m in state.matched]
    deltas_d = [m.delta_d for m in state.matched]

    rtds_first = sum(1 for m in state.matched if m.delta_b < 0)
    alchemy_first = sum(1 for m in state.matched if m.delta_b > 0)
    simultaneous = sum(1 for m in state.matched if m.delta_b == 0)

    # Topic breakdown for matched trades
    from_trades = sum(1 for m in state.matched if m.rtds_topic == "trades")
    from_orders_matched = sum(1 for m in state.matched if m.rtds_topic == "orders_matched")

    print("\n  --- Delivery Race (who arrives first at your machine?) ---")
    print(f"  RTDS first:    {rtds_first} ({_pct(rtds_first, n)})")
    print(f"  Alchemy first: {alchemy_first} ({_pct(alchemy_first, n)})")
    print(f"  Simultaneous:  {simultaneous}")

    print("\n  --- RTDS topic breakdown (matched) ---")
    print(f"  activity/trades:          {from_trades}")
    print(f"  activity/orders_matched:  {from_orders_matched}")

    def _stats_line(name: str, vals: list[float]) -> str:
        if not vals:
            return f"  {name}: no data"
        q = quantiles(vals, n=4) if len(vals) >= 2 else [vals[0]] * 3
        return (
            f"  {name}:\n"
            f"    mean={mean(vals):+.3f}s  median={median(vals):+.3f}s  "
            f"p25={q[0]:+.3f}s  p75={q[2]:+.3f}s  "
            f"min={min(vals):+.3f}s  max={max(vals):+.3f}s"
        )

    print("\n  --- Delta A: rtds_event_ts - alchemy_block_ts ---")
    print("  (negative = RTDS event time is BEFORE block, i.e. pre-chain)")
    print(_stats_line("Delta A", deltas_a))

    print("\n  --- Delta B: rtds_arrival - alchemy_arrival ---")
    print("  (negative = RTDS delivered FIRST to your machine)")
    print(_stats_line("Delta B", deltas_b))

    print("\n  --- Delta C: rtds_arrival - rtds_event_ts ---")
    print("  (RTDS internal processing + network delay)")
    print(_stats_line("Delta C", deltas_c))

    print("\n  --- Delta D: alchemy_arrival - alchemy_block_ts ---")
    print("  (Alchemy: block production + propagation to you)")
    print(_stats_line("Delta D", deltas_d))

    print(f"{'=' * 72}\n")


async def stats_loop(state: ProbeState, stop: asyncio.Event) -> None:
    """Print stats periodically and garbage-collect stale entries."""
    while not stop.is_set():
        await asyncio.sleep(STATS_INTERVAL)
        state.gc_stale()
        print_stats(state)


# ── Output ───────────────────────────────────────────────────────────────


def dump_results(state: ProbeState, out_path: str) -> None:
    """Write matched trade data to JSON."""
    results = {
        "summary": {
            "matched_count": len(state.matched),
            "rtds_trades_count": state.rtds_trades_count,
            "rtds_orders_matched_count": state.rtds_orders_matched_count,
            "rtds_no_txhash": state.rtds_no_txhash,
            "rtds_reconnects": state.rtds_reconnects,
            "alchemy_count": state.alchemy_count,
            "unmatched_rtds": len(state.rtds_events),
            "unmatched_alchemy": len(state.alchemy_events),
        },
        "matched_trades": [
            {
                "tx_hash": m.tx_hash,
                "rtds_event_ts": m.rtds_event_ts,
                "rtds_delivery_ts": m.rtds_delivery_ts,
                "rtds_arrival": m.rtds_arrival,
                "rtds_topic": m.rtds_topic,
                "alchemy_block_ts": m.alchemy_block_ts,
                "alchemy_arrival": m.alchemy_arrival,
                "delta_a": round(m.delta_a, 4),
                "delta_b": round(m.delta_b, 4),
                "delta_c": round(m.delta_c, 4),
                "delta_d": round(m.delta_d, 4),
            }
            for m in state.matched
        ],
    }

    Path(out_path).write_text(json.dumps(results, indent=2))
    print(f"\nResults written to {out_path}")


# ── Main ─────────────────────────────────────────────────────────────────


async def main(duration: int, out_path: str) -> None:
    load_dotenv()

    alchemy_url = os.environ.get("PM_ALCHEMY_WS_URL")
    if not alchemy_url or "YOUR_ALCHEMY_KEY" in alchemy_url:
        print("ERROR: Set PM_ALCHEMY_WS_URL in .env with a real Alchemy API key")
        sys.exit(1)

    state = ProbeState()
    stop = asyncio.Event()

    # Handle Ctrl-C gracefully
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    print(f"Starting latency probe (duration={duration}s, output={out_path})")
    print(f"  RTDS: {RTDS_URL}")
    print(f"  Alchemy: {alchemy_url[:60]}...")
    print("  Subscribing to: activity/trades + activity/orders_matched")
    print(f"  Stats every {STATS_INTERVAL}s, Ctrl-C to stop early\n")

    tasks = [
        asyncio.create_task(rtds_feed(state, stop)),
        asyncio.create_task(alchemy_feed(state, alchemy_url, stop)),
        asyncio.create_task(stats_loop(state, stop)),
    ]

    # Auto-stop after duration
    async def _timer() -> None:
        await asyncio.sleep(duration)
        print(f"\n[Probe] Duration {duration}s reached, stopping...")
        stop.set()

    tasks.append(asyncio.create_task(_timer()))

    await stop.wait()

    # Give feeds a moment to flush
    await asyncio.sleep(1)
    for t in tasks:
        t.cancel()

    # Final stats + dump
    print_stats(state)
    dump_results(state, out_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Polymarket latency probe")
    parser.add_argument("--duration", type=int, default=600, help="Run duration in seconds")
    parser.add_argument("--out", type=str, default="data/latency_probe.json", help="Output path")
    args = parser.parse_args()

    # Ensure output directory exists
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(main(args.duration, args.out))
