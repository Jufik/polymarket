"""Raw multi-source WebSocket dumper — captures everything, filters nothing.

Connects to all available Polymarket data sources and writes every single
WebSocket message as a raw JSONL line.  No filtering, no normalization,
no field extraction — just {source, arrival_ts, raw}.

The goal is to understand exactly what each endpoint/topic/subscription
delivers so we can decide what to use for live trading.

Sources:
  1. RTDS activity/trades         — wss://ws-live-data.polymarket.com
  2. RTDS activity/orders_matched — same socket, different subscription
  3. Alchemy eth_subscribe (logs) — Polygon RPC, all logs from CTF exchanges
  4. CLOB WS (Market channel)     — wss://ws-subscriptions-clob.polymarket.com/ws/market

CLOB WS requires asset_ids to subscribe.  Pass them via --assets or
let the script discover them from RTDS (auto mode, ~5s warmup).

Usage:
  uv run python scripts/trade_dump.py
  uv run python scripts/trade_dump.py --out data/raw_dump.jsonl
  uv run python scripts/trade_dump.py --duration 300
  uv run python scripts/trade_dump.py --no-alchemy
  uv run python scripts/trade_dump.py --no-clob
  uv run python scripts/trade_dump.py --assets 12345,67890  # specific asset_ids

Requires PM_ALCHEMY_WS_URL in .env (unless --no-alchemy).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import websockets
from dotenv import load_dotenv

# ── Configuration ────────────────────────────────────────────────────────

RTDS_URL = "wss://ws-live-data.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

PING_INTERVAL = 5
STATS_INTERVAL = 30
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
WATCHDOG_TIMEOUT = 120
CLOB_SUB_INTERVAL = 5


# ── State ────────────────────────────────────────────────────────────────


@dataclass
class DumpState:
    start_time: float = field(default_factory=time.time)

    # Per-source message counts (every message, not just trades)
    counts: Counter[str] = field(default_factory=Counter)
    # Per-source per-"type" breakdown (discovered from the data)
    type_counts: Counter[str] = field(default_factory=Counter)  # "source:type_value"
    # Reconnects
    reconnects: Counter[str] = field(default_factory=Counter)
    # Last message time per source
    last_msg: dict[str, float] = field(default_factory=dict)

    # CLOB WS dynamic subscription
    seen_assets: set[str] = field(default_factory=set)
    subscribed_assets: set[str] = field(default_factory=set)


# ── Writer ───────────────────────────────────────────────────────────────

_write_lock = asyncio.Lock()


async def write_line(outfile: object, source: str, arrival_ts: float, raw: object) -> None:
    """Write one JSONL line: {source, arrival_ts, msg: <raw parsed JSON>}."""
    line = json.dumps({"source": source, "arrival_ts": arrival_ts, "msg": raw}, default=str)
    async with _write_lock:
        outfile.write(line + "\n")  # type: ignore[union-attr]


# ── RTDS feed ────────────────────────────────────────────────────────────


async def rtds_feed(state: DumpState, outfile: object, stop: asyncio.Event) -> None:
    """RTDS: subscribe to activity/trades AND activity/orders_matched."""
    backoff = RECONNECT_BASE
    while not stop.is_set():
        try:
            print("[RTDS] connecting...", file=sys.stderr)
            async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
                backoff = RECONNECT_BASE
                print("[RTDS] connected, subscribing to trades + orders_matched", file=sys.stderr)

                await ws.send(json.dumps({
                    "action": "subscribe",
                    "subscriptions": [
                        {"topic": "activity", "type": "trades"},
                        {"topic": "activity", "type": "orders_matched"},
                    ],
                }))

                ping_task = asyncio.create_task(_rtds_ping(ws, stop))
                watchdog_task = asyncio.create_task(
                    _watchdog("rtds", state, ws, stop),
                )
                try:
                    async for raw in ws:
                        if stop.is_set():
                            break
                        now = time.time()

                        # Skip PING/PONG keepalives — not data
                        if raw in ("PING", "PONG", "pong"):
                            continue

                        state.counts["rtds"] += 1
                        state.last_msg["rtds"] = now

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            # Dump unparseable messages too
                            await write_line(outfile, "rtds", now, {"_raw_text": raw})
                            state.type_counts["rtds:_unparseable"] += 1
                            continue

                        # Track message type for the report
                        msg_type = msg.get("type", "_no_type")
                        state.type_counts[f"rtds:{msg_type}"] += 1

                        # Collect asset_ids for CLOB WS auto-subscription
                        payload = msg.get("payload")
                        if isinstance(payload, dict):
                            asset = payload.get("asset")
                            if asset:
                                state.seen_assets.add(str(asset))

                        await write_line(outfile, "rtds", now, msg)

                finally:
                    ping_task.cancel()
                    watchdog_task.cancel()

        except websockets.ConnectionClosed as e:
            state.reconnects["rtds"] += 1
            print(f"[RTDS] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            state.reconnects["rtds"] += 1
            print(f"[RTDS] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


async def _rtds_ping(ws: websockets.WebSocketClientProtocol, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(PING_INTERVAL)
        try:
            await ws.send("PING")
        except Exception:
            return


# ── Alchemy feed ─────────────────────────────────────────────────────────


async def alchemy_feed(
    state: DumpState, ws_url: str, outfile: object, stop: asyncio.Event
) -> None:
    """Alchemy: eth_subscribe to ALL logs from both CTF exchange contracts."""
    backoff = RECONNECT_BASE
    while not stop.is_set():
        try:
            print("[Alchemy] connecting...", file=sys.stderr)
            async with websockets.connect(ws_url, ping_interval=30) as ws:
                backoff = RECONNECT_BASE
                print("[Alchemy] connected, subscribing to OrderFilled logs", file=sys.stderr)

                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        {
                            "address": [CTF_EXCHANGE, NEGRISK_EXCHANGE],
                            "topics": [[ORDER_FILLED_SIG]],
                        },
                    ],
                }))

                async for raw in ws:
                    if stop.is_set():
                        break
                    now = time.time()
                    state.counts["alchemy"] += 1
                    state.last_msg["alchemy"] = now

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        await write_line(outfile, "alchemy", now, {"_raw_text": raw})
                        state.type_counts["alchemy:_unparseable"] += 1
                        continue

                    # Classify by JSON-RPC method or by first topic sig
                    method = msg.get("method", "")
                    if method == "eth_subscription":
                        result = msg.get("params", {}).get("result", {})
                        topics = result.get("topics", [])
                        sig = topics[0][:10] + "..." if topics else "_no_topics"
                        state.type_counts[f"alchemy:{sig}"] += 1
                    else:
                        state.type_counts[f"alchemy:rpc:{method or msg.get('id', '?')}"] += 1

                    await write_line(outfile, "alchemy", now, msg)

        except websockets.ConnectionClosed as e:
            state.reconnects["alchemy"] += 1
            print(f"[Alchemy] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            state.reconnects["alchemy"] += 1
            print(f"[Alchemy] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


# ── CLOB WS feed ────────────────────────────────────────────────────────


async def clob_feed(
    state: DumpState,
    outfile: object,
    stop: asyncio.Event,
    explicit_assets: list[str] | None = None,
) -> None:
    """CLOB WS: subscribe to Market channel, dump ALL event types."""
    backoff = RECONNECT_BASE
    while not stop.is_set():
        # Need asset_ids — either explicit or wait for RTDS to discover some
        if not explicit_assets:
            while not state.seen_assets and not stop.is_set():
                await asyncio.sleep(1)
            if stop.is_set():
                break

        try:
            print("[CLOB] connecting...", file=sys.stderr)
            async with websockets.connect(CLOB_WS_URL, ping_interval=30) as ws:
                backoff = RECONNECT_BASE
                print("[CLOB] connected", file=sys.stderr)

                # Initial subscription
                assets = explicit_assets or list(state.seen_assets)
                await _clob_subscribe(ws, state, assets)

                sub_task = asyncio.create_task(
                    _clob_sub_loop(ws, state, stop, bool(explicit_assets)),
                )
                watchdog_task = asyncio.create_task(
                    _watchdog("clob", state, ws, stop),
                )
                try:
                    async for raw in ws:
                        if stop.is_set():
                            break
                        now = time.time()
                        state.counts["clob"] += 1
                        state.last_msg["clob"] = now

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            await write_line(outfile, "clob", now, {"_raw_text": raw})
                            state.type_counts["clob:_unparseable"] += 1
                            continue

                        # CLOB WS sends arrays of events
                        events = msg if isinstance(msg, list) else [msg]
                        for evt in events:
                            evt_type = evt.get("event_type", "_no_type") if isinstance(evt, dict) else "_non_dict"
                            state.type_counts[f"clob:{evt_type}"] += 1

                        await write_line(outfile, "clob", now, msg)

                finally:
                    sub_task.cancel()
                    watchdog_task.cancel()

        except websockets.ConnectionClosed as e:
            state.reconnects["clob"] += 1
            state.subscribed_assets.clear()
            print(f"[CLOB] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            state.reconnects["clob"] += 1
            state.subscribed_assets.clear()
            print(f"[CLOB] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


async def _clob_subscribe(
    ws: websockets.WebSocketClientProtocol, state: DumpState, asset_ids: list[str]
) -> None:
    if not asset_ids:
        return
    new = [a for a in asset_ids if a not in state.subscribed_assets]
    if not new:
        return
    await ws.send(json.dumps({
        "auth": {},
        "markets": [],
        "assets_ids": new,
        "type": "Market",
    }))
    state.subscribed_assets.update(new)
    print(
        f"[CLOB] subscribed +{len(new)} assets (total: {len(state.subscribed_assets)})",
        file=sys.stderr,
    )


async def _clob_sub_loop(
    ws: websockets.WebSocketClientProtocol,
    state: DumpState,
    stop: asyncio.Event,
    explicit: bool,
) -> None:
    """Auto-subscribe to newly discovered assets (skip if explicit list provided)."""
    if explicit:
        return
    while not stop.is_set():
        await asyncio.sleep(CLOB_SUB_INTERVAL)
        try:
            await _clob_subscribe(ws, state, list(state.seen_assets))
        except Exception:
            pass


# ── Shared watchdog ──────────────────────────────────────────────────────


async def _watchdog(
    name: str, state: DumpState, ws: websockets.WebSocketClientProtocol, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        await asyncio.sleep(30)
        last = state.last_msg.get(name, 0)
        if last > 0 and time.time() - last > WATCHDOG_TIMEOUT:
            print(
                f"[{name}] SILENT FREEZE — {time.time() - last:.0f}s, reconnecting",
                file=sys.stderr,
            )
            await ws.close()
            return


# ── Stats printer ────────────────────────────────────────────────────────


async def stats_loop(state: DumpState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(STATS_INTERVAL)
        elapsed = time.time() - state.start_time
        parts = []
        for src in ("rtds", "alchemy", "clob"):
            c = state.counts[src]
            if c > 0:
                parts.append(f"{src}={c}")
        print(
            f"[stats {time.strftime('%H:%M:%S')}] "
            + " ".join(parts)
            + f" | clob_subs={len(state.subscribed_assets)}"
            + f" | {elapsed:.0f}s",
            file=sys.stderr,
        )


# ── Report ───────────────────────────────────────────────────────────────


def print_report(state: DumpState) -> None:
    elapsed = time.time() - state.start_time
    r = sys.stderr

    r.write(f"\n{'=' * 76}\n")
    r.write(f"  Raw Trade Dump — Final Report\n")
    r.write(f"{'=' * 76}\n\n")
    r.write(f"  Duration: {elapsed:.0f}s ({elapsed / 60:.1f}m)\n\n")

    # Per-source totals
    r.write(f"  --- Messages per source ---\n\n")
    total = sum(state.counts.values())
    for src in ("rtds", "alchemy", "clob"):
        c = state.counts[src]
        tps = c / elapsed if elapsed > 0 else 0
        reconn = state.reconnects[src]
        r.write(f"    {src:<12} {c:>8} msgs  ({tps:>6.1f}/s)  reconnects={reconn}\n")
    r.write(f"    {'TOTAL':<12} {total:>8} msgs\n")

    # Per-source per-type breakdown
    r.write(f"\n  --- Message types (source:type -> count) ---\n\n")
    for key, count in state.type_counts.most_common():
        r.write(f"    {key:<45} {count:>8}\n")

    # CLOB subscription stats
    r.write(f"\n  CLOB assets discovered:  {len(state.seen_assets)}\n")
    r.write(f"  CLOB assets subscribed:  {len(state.subscribed_assets)}\n")

    r.write(f"\n{'=' * 76}\n\n")


# ── Main ─────────────────────────────────────────────────────────────────


async def main(
    duration: int,
    out_path: str,
    no_alchemy: bool,
    no_clob: bool,
    explicit_assets: list[str] | None,
) -> None:
    load_dotenv()

    alchemy_url = os.environ.get("PM_ALCHEMY_WS_URL", "")
    if not no_alchemy and (not alchemy_url or "YOUR_ALCHEMY_KEY" in alchemy_url):
        print(
            "WARNING: PM_ALCHEMY_WS_URL not set, disabling Alchemy. "
            "Set it in .env or use --no-alchemy.",
            file=sys.stderr,
        )
        no_alchemy = True

    state = DumpState()
    stop = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    sources = ["RTDS (trades + orders_matched)"]
    if not no_alchemy:
        sources.append("Alchemy (OrderFilled logs)")
    if not no_clob:
        mode = f"explicit: {len(explicit_assets)}" if explicit_assets else "auto-discover"
        sources.append(f"CLOB WS (all event types, {mode})")

    print(f"[trade_dump] Starting (duration={duration}s, output={out_path})", file=sys.stderr)
    for s in sources:
        print(f"  -> {s}", file=sys.stderr)
    print(f"[trade_dump] Dumping RAW messages, no filtering. Ctrl-C to stop.\n", file=sys.stderr)

    with open(out_path, "a") as f:
        tasks = [
            asyncio.create_task(rtds_feed(state, f, stop)),
            asyncio.create_task(stats_loop(state, stop)),
        ]

        if not no_alchemy:
            tasks.append(asyncio.create_task(alchemy_feed(state, alchemy_url, f, stop)))

        if not no_clob:
            tasks.append(asyncio.create_task(clob_feed(state, f, stop, explicit_assets)))

        if duration > 0:
            async def _timer() -> None:
                await asyncio.sleep(duration)
                print(f"\n[trade_dump] Duration {duration}s reached, stopping...", file=sys.stderr)
                stop.set()

            tasks.append(asyncio.create_task(_timer()))

        await stop.wait()
        await asyncio.sleep(0.5)
        for t in tasks:
            t.cancel()

        f.flush()
        os.fsync(f.fileno())

    print_report(state)
    total = sum(state.counts.values())
    print(f"[trade_dump] {total} messages written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Raw multi-source Polymarket WS dump")
    parser.add_argument("--out", default="data/raw_dump.jsonl", help="Output JSONL path")
    parser.add_argument("--duration", type=int, default=0, help="Seconds (0 = indefinite)")
    parser.add_argument("--no-alchemy", action="store_true", help="Skip Alchemy feed")
    parser.add_argument("--no-clob", action="store_true", help="Skip CLOB WS feed")
    parser.add_argument(
        "--assets",
        type=str,
        default="",
        help="Comma-separated asset_ids for CLOB WS (otherwise auto-discovered from RTDS)",
    )
    args = parser.parse_args()

    explicit = [a.strip() for a in args.assets.split(",") if a.strip()] or None
    asyncio.run(main(args.duration, args.out, args.no_alchemy, args.no_clob, explicit))
