"""Mempool latency probe — measure P2P mempool advantage over RPC + WebSocket sources.

Runs three feeds simultaneously and matches trades by tx_hash:

  1. **Mempool** (Rust PyO3 sidecar) — pending tx gossip from Polygon devp2p P2P
  2. **RPC** — on-chain OrderFilled logs via eth_subscribe (free public endpoint)
  3. **CLOB WS** (optional) — Polymarket's off-chain match notification

Reports four deltas per matched trade:

  Delta M-R: mempool_arrival - rpc_arrival         (negative = mempool first)
  Delta M-C: mempool_arrival - clob_arrival        (negative = mempool first)
  Delta C-R: clob_arrival    - rpc_arrival         (always negative, CLOB is pre-chain)
  Delta M-B: mempool_arrival - rpc_block_ts        (mempool vs on-chain timestamp)

Expected result: Mempool arrives ~2-4s before RPC (pre-block), roughly same
time as CLOB WS but WITH maker+taker identity from decoded calldata.

Usage:
  uv run python scripts/mempool_latency_probe.py
  uv run python scripts/mempool_latency_probe.py --duration 600
  uv run python scripts/mempool_latency_probe.py --no-clob          # skip CLOB WS
  uv run python scripts/mempool_latency_probe.py --ws-url wss://your-rpc.com

Requires:
  - polymarket-mempool Rust crate built (maturin develop --release)
  - Port 30304 outbound for P2P (or custom --port)
  - Uses free public Polygon WS by default (no API key needed)
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

CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
EXCHANGE_ADDRS = frozenset({CTF_EXCHANGE, NEGRISK_EXCHANGE})

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Free public Polygon WS endpoints that support eth_subscribe
DEFAULT_WS_ENDPOINTS = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon-mainnet.public.blastapi.io",
    "wss://polygon.drpc.org",
]

STATS_INTERVAL = 30
MATCH_WINDOW = 300

# ── Data structures ──────────────────────────────────────────────────────


@dataclass
class MempoolEvent:
    tx_hash: str
    arrival: float
    maker: str
    taker: str
    token_id: str
    maker_amount: int
    taker_amount: int
    seen_at: float  # Rust-side timestamp


@dataclass
class AlchemyEvent:
    tx_hash: str
    arrival: float
    block_ts: float
    block_number: int
    maker: str | None
    taker: str | None


@dataclass
class ClobEvent:
    tx_hash: str
    arrival: float
    asset_id: str
    price: str
    size: str
    side: str
    clob_ts: float  # CLOB match timestamp (ms)


@dataclass
class MatchedTrade:
    tx_hash: str
    # Sources (None if that source didn't see it)
    mempool_arrival: float | None
    mempool_seen_at: float | None
    alchemy_arrival: float | None
    alchemy_block_ts: float | None
    clob_arrival: float | None
    clob_ts: float | None
    # Identity from mempool
    maker: str | None
    taker: str | None
    # Deltas (None if source missing)
    delta_m_a: float | None  # mempool - alchemy arrival
    delta_m_c: float | None  # mempool - clob arrival
    delta_c_a: float | None  # clob - alchemy arrival
    delta_m_b: float | None  # mempool arrival - alchemy block_ts


@dataclass
class ProbeState:
    mempool_events: dict[str, MempoolEvent] = field(default_factory=dict)
    alchemy_events: dict[str, AlchemyEvent] = field(default_factory=dict)
    clob_events: dict[str, ClobEvent] = field(default_factory=dict)
    matched: list[MatchedTrade] = field(default_factory=list)

    # Counters
    mempool_count: int = 0
    mempool_peers: int = 0
    alchemy_count: int = 0
    clob_count: int = 0

    def try_match(self, tx_hash: str) -> None:
        """Match when we have at least mempool + alchemy."""
        mem = self.mempool_events.get(tx_hash)
        alc = self.alchemy_events.get(tx_hash)

        if mem is None or alc is None:
            return

        clob = self.clob_events.pop(tx_hash, None)
        self.mempool_events.pop(tx_hash, None)
        self.alchemy_events.pop(tx_hash, None)

        delta_m_a = mem.arrival - alc.arrival
        delta_m_b = mem.arrival - alc.block_ts
        delta_m_c = (mem.arrival - clob.arrival) if clob else None
        delta_c_a = (clob.arrival - alc.arrival) if clob else None

        self.matched.append(
            MatchedTrade(
                tx_hash=tx_hash,
                mempool_arrival=mem.arrival,
                mempool_seen_at=mem.seen_at,
                alchemy_arrival=alc.arrival,
                alchemy_block_ts=alc.block_ts,
                clob_arrival=clob.arrival if clob else None,
                clob_ts=clob.clob_ts if clob else None,
                maker=mem.maker,
                taker=mem.taker,
                delta_m_a=delta_m_a,
                delta_m_c=delta_m_c,
                delta_c_a=delta_c_a,
                delta_m_b=delta_m_b,
            )
        )

    def gc_stale(self) -> None:
        cutoff = time.time() - MATCH_WINDOW
        self.mempool_events = {
            k: v for k, v in self.mempool_events.items() if v.arrival > cutoff
        }
        self.alchemy_events = {
            k: v for k, v in self.alchemy_events.items() if v.arrival > cutoff
        }
        self.clob_events = {
            k: v for k, v in self.clob_events.items() if v.arrival > cutoff
        }


# ── Mempool feed (Rust sidecar) ─────────────────────────────────────────


async def mempool_feed(
    state: ProbeState,
    listen_port: int,
    stop: asyncio.Event,
) -> None:
    """Run the Rust mempool monitor and record trade arrivals."""
    try:
        from polymarket_mempool import MempoolMonitor
    except ImportError:
        print(
            "[Mempool] ERROR: polymarket_mempool not installed. "
            "Run: cd crates/polymarket-mempool && maturin develop --release",
            file=sys.stderr,
        )
        return

    log_file = os.environ.get("MEMPOOL_LOG_FILE", "data/mempool_rust.log")
    print(f"[Mempool] Starting on port {listen_port}, logs -> {log_file}", file=sys.stderr)
    monitor = MempoolMonitor(listen_port=listen_port, log_file=log_file)

    async for raw in monitor.stream():
        if stop.is_set():
            break

        now = time.time()

        # Peer status update
        if "_peers_active" in raw:
            state.mempool_peers = raw["_peers_active"]
            continue

        tx_hash = raw.get("tx_hash", "").lower()
        if not tx_hash:
            continue

        # Don't overwrite (keep first arrival)
        if tx_hash in state.mempool_events:
            continue

        state.mempool_count += 1
        state.mempool_events[tx_hash] = MempoolEvent(
            tx_hash=tx_hash,
            arrival=now,
            maker=raw.get("maker", ""),
            taker=raw.get("taker", ""),
            token_id=raw.get("token_id", ""),
            maker_amount=raw.get("maker_amount", 0),
            taker_amount=raw.get("taker_amount", 0),
            seen_at=raw.get("seen_at", now),
        )

        state.try_match(tx_hash)


# ── Alchemy feed ─────────────────────────────────────────────────────────


async def rpc_feed(
    state: ProbeState,
    ws_urls: list[str],
    stop: asyncio.Event,
) -> None:
    """Connect to a Polygon WS RPC, subscribe to OrderFilled logs."""
    url_idx = 0
    backoff = 1.0
    while not stop.is_set():
        ws_url = ws_urls[url_idx % len(ws_urls)]
        try:
            print(f"[RPC] connecting to {ws_url}...", file=sys.stderr)
            async with websockets.connect(ws_url, ping_interval=30) as ws:
                backoff = 1.0
                print(f"[RPC] connected", file=sys.stderr)

                await ws.send(
                    json.dumps(
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
                )

                async for raw in ws:
                    if stop.is_set():
                        break
                    _handle_rpc_msg(state, raw)

        except websockets.ConnectionClosed as e:
            print(f"[RPC] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[RPC] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        url_idx += 1  # try next endpoint on reconnect
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _handle_rpc_msg(state: ProbeState, raw: str) -> None:
    now = time.time()
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

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

    tx_hash = result.get("transactionHash", "").lower()
    if not tx_hash or tx_hash in state.alchemy_events:
        return

    state.alchemy_count += 1

    block_ts_hex = result.get("blockTimestamp")
    block_ts = int(block_ts_hex, 16) if block_ts_hex else now
    maker = "0x" + topics[2][-40:]
    block_number = int(result.get("blockNumber", "0x0"), 16)

    state.alchemy_events[tx_hash] = AlchemyEvent(
        tx_hash=tx_hash,
        arrival=now,
        block_ts=block_ts,
        block_number=block_number,
        maker=maker,
        taker=taker,
    )

    state.try_match(tx_hash)


# ── CLOB WS feed (optional) ─────────────────────────────────────────────


async def clob_feed(
    state: ProbeState,
    stop: asyncio.Event,
) -> None:
    """Connect to CLOB WS and record last_trade_price arrivals."""
    backoff = 1.0
    while not stop.is_set():
        try:
            print("[CLOB] connecting...", file=sys.stderr)
            async with websockets.connect(CLOB_WS_URL, ping_interval=30) as ws:
                backoff = 1.0
                print("[CLOB] connected", file=sys.stderr)

                # Subscribe to all markets (wildcard not supported —
                # we subscribe to a dummy and rely on last_trade_price
                # being broadcast for all active markets)
                await ws.send(
                    json.dumps(
                        {
                            "type": "market",
                            "assets_ids": [],
                            "auth": {},
                            "markets": [],
                        }
                    )
                )

                async for raw in ws:
                    if stop.is_set():
                        break
                    _handle_clob_msg(state, raw)

        except websockets.ConnectionClosed as e:
            print(f"[CLOB] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[CLOB] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _handle_clob_msg(state: ProbeState, raw: str) -> None:
    now = time.time()
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    # Handle array messages (book snapshots)
    if isinstance(msg, list):
        return

    if msg.get("event_type") != "last_trade_price":
        return

    tx_hash = (msg.get("transaction_hash") or "").lower()
    if not tx_hash or tx_hash in state.clob_events:
        return

    state.clob_count += 1
    clob_ts_raw = msg.get("timestamp", "0")
    clob_ts = (
        float(clob_ts_raw) / 1000 if float(clob_ts_raw) > 1e12 else float(clob_ts_raw)
    )

    state.clob_events[tx_hash] = ClobEvent(
        tx_hash=tx_hash,
        arrival=now,
        asset_id=msg.get("asset_id", ""),
        price=msg.get("price", ""),
        size=msg.get("size", ""),
        side=msg.get("side", ""),
        clob_ts=clob_ts,
    )

    state.try_match(tx_hash)


# ── Stats + report ───────────────────────────────────────────────────────


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total > 0 else "n/a"


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


def print_report(state: ProbeState) -> None:
    n = len(state.matched)
    r = sys.stderr

    r.write(f"\n{'=' * 76}\n")
    r.write("  Mempool Latency Probe — Final Report\n")
    r.write(f"{'=' * 76}\n\n")

    r.write(f"  Mempool trades received:    {state.mempool_count}\n")
    r.write(f"  Mempool peers active:       {state.mempool_peers}\n")
    r.write(f"  RPC events received:        {state.alchemy_count}\n")
    r.write(f"  CLOB WS trades received:    {state.clob_count}\n")
    r.write(f"  Matched (mempool+rpc):      {n}\n")
    r.write(f"  Pending mempool:            {len(state.mempool_events)}\n")
    r.write(f"  Pending rpc:                {len(state.alchemy_events)}\n")

    if n == 0:
        r.write(
            "\n  No matched trades — need P2P peers + RPC running simultaneously.\n"
        )
        r.write(f"{'=' * 76}\n\n")
        return

    # Delta M-R: mempool vs RPC arrival
    deltas_m_a = [m.delta_m_a for m in state.matched if m.delta_m_a is not None]
    mempool_first = sum(1 for d in deltas_m_a if d < 0)
    rpc_first = sum(1 for d in deltas_m_a if d > 0)

    r.write("\n  --- Delta M-R: mempool_arrival - rpc_arrival ---\n")
    r.write("  (negative = mempool delivered FIRST — pre-block advantage)\n")
    r.write(
        f"  Mempool first: {mempool_first} ({_pct(mempool_first, len(deltas_m_a))})\n"
    )
    r.write(f"  RPC first:     {rpc_first} ({_pct(rpc_first, len(deltas_m_a))})\n")
    r.write(_stats_line("Delta M-R", deltas_m_a) + "\n")

    # Delta M-B: mempool arrival vs block timestamp
    deltas_m_b = [m.delta_m_b for m in state.matched if m.delta_m_b is not None]
    r.write("\n  --- Delta M-B: mempool_arrival - block_timestamp ---\n")
    r.write("  (negative = mempool saw tx BEFORE block was produced)\n")
    r.write(_stats_line("Delta M-B", deltas_m_b) + "\n")

    # Delta M-C: mempool vs CLOB (if available)
    deltas_m_c = [m.delta_m_c for m in state.matched if m.delta_m_c is not None]
    if deltas_m_c:
        mempool_before_clob = sum(1 for d in deltas_m_c if d < 0)
        clob_before_mempool = sum(1 for d in deltas_m_c if d > 0)
        r.write("\n  --- Delta M-C: mempool_arrival - clob_arrival ---\n")
        r.write("  (negative = mempool before CLOB, positive = CLOB before mempool)\n")
        r.write(
            f"  Mempool first: {mempool_before_clob} "
            f"({_pct(mempool_before_clob, len(deltas_m_c))})\n"
        )
        r.write(
            f"  CLOB first:    {clob_before_mempool} "
            f"({_pct(clob_before_mempool, len(deltas_m_c))})\n"
        )
        r.write(_stats_line("Delta M-C", deltas_m_c) + "\n")

    # Delta C-R: CLOB vs RPC (reference, should match known ~3.7s)
    deltas_c_a = [m.delta_c_a for m in state.matched if m.delta_c_a is not None]
    if deltas_c_a:
        r.write("\n  --- Delta C-R: clob_arrival - rpc_arrival ---\n")
        r.write("  (reference: expected ~-3.7s from prior measurements)\n")
        r.write(_stats_line("Delta C-R", deltas_c_a) + "\n")

    # Key insight
    if deltas_m_a:
        med = median(deltas_m_a)
        r.write("\n  --- KEY INSIGHT ---\n")
        if med < 0:
            r.write(
                f"  Mempool delivers identity {abs(med):.1f}s BEFORE RPC (median).\n"
                f"  This is the fastest source with maker+taker addresses.\n"
            )
        else:
            r.write(
                f"  Mempool delivers {med:.1f}s AFTER RPC (median).\n"
                f"  P2P gossip may be slow — check peer count and connectivity.\n"
            )

    r.write(f"\n{'=' * 76}\n\n")


def dump_results(state: ProbeState, out_path: str) -> None:
    results = {
        "summary": {
            "matched_count": len(state.matched),
            "mempool_count": state.mempool_count,
            "mempool_peers": state.mempool_peers,
            "alchemy_count": state.alchemy_count,
            "clob_count": state.clob_count,
        },
        "matched_trades": [
            {
                "tx_hash": m.tx_hash,
                "mempool_arrival": m.mempool_arrival,
                "mempool_seen_at": m.mempool_seen_at,
                "alchemy_arrival": m.alchemy_arrival,
                "alchemy_block_ts": m.alchemy_block_ts,
                "clob_arrival": m.clob_arrival,
                "clob_ts": m.clob_ts,
                "maker": m.maker,
                "taker": m.taker,
                "delta_m_a": round(m.delta_m_a, 4) if m.delta_m_a else None,
                "delta_m_c": round(m.delta_m_c, 4) if m.delta_m_c else None,
                "delta_c_a": round(m.delta_c_a, 4) if m.delta_c_a else None,
                "delta_m_b": round(m.delta_m_b, 4) if m.delta_m_b else None,
            }
            for m in state.matched
        ],
    }

    Path(out_path).write_text(json.dumps(results, indent=2))
    print(f"\nResults written to {out_path}", file=sys.stderr)


async def stats_loop(state: ProbeState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(STATS_INTERVAL)
        state.gc_stale()
        n = len(state.matched)
        print(
            f"[stats {time.strftime('%H:%M:%S')}] "
            f"mempool={state.mempool_count} peers={state.mempool_peers} "
            f"rpc={state.alchemy_count} clob={state.clob_count} "
            f"matched={n}",
            file=sys.stderr,
        )


# ── Main ─────────────────────────────────────────────────────────────────


async def main(
    duration: int, out_path: str, port: int, skip_clob: bool, ws_url: str | None
) -> None:
    load_dotenv()

    # Build list of WS endpoints: user-provided first, then free public fallbacks
    ws_urls: list[str] = []
    if ws_url:
        ws_urls.append(ws_url)
    env_url = os.environ.get("PM_ALCHEMY_WS_URL", "")
    if env_url and "YOUR_ALCHEMY_KEY" not in env_url and env_url not in ws_urls:
        ws_urls.append(env_url)
    ws_urls.extend(u for u in DEFAULT_WS_ENDPOINTS if u not in ws_urls)

    state = ProbeState()
    stop = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    print(
        f"[probe] Mempool latency probe (duration={duration}s, port={port})",
        file=sys.stderr,
    )
    print(
        f"[probe] RPC endpoints: {ws_urls[0]} (+{len(ws_urls)-1} fallbacks)",
        file=sys.stderr,
    )
    print(
        f"[probe] Sources: mempool + rpc{'' if skip_clob else ' + clob_ws'}",
        file=sys.stderr,
    )
    print(f"[probe] Output: {out_path}", file=sys.stderr)
    print(f"[probe] Ctrl-C to stop\n", file=sys.stderr)

    tasks = [
        asyncio.create_task(mempool_feed(state, port, stop)),
        asyncio.create_task(rpc_feed(state, ws_urls, stop)),
        asyncio.create_task(stats_loop(state, stop)),
    ]

    if not skip_clob:
        tasks.append(asyncio.create_task(clob_feed(state, stop)))

    if duration > 0:

        async def _timer() -> None:
            await asyncio.sleep(duration)
            print(
                f"\n[probe] Duration {duration}s reached, stopping...", file=sys.stderr
            )
            stop.set()

        tasks.append(asyncio.create_task(_timer()))

    await stop.wait()
    await asyncio.sleep(1)
    for t in tasks:
        t.cancel()

    print_report(state)
    dump_results(state, out_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mempool vs RPC/WS latency benchmark")
    parser.add_argument(
        "--duration", type=int, default=600, help="Run duration (0=indefinite)"
    )
    parser.add_argument(
        "--out", default="data/mempool_latency.json", help="Output JSON path"
    )
    parser.add_argument("--port", type=int, default=30304, help="P2P listen port")
    parser.add_argument("--no-clob", action="store_true", help="Skip CLOB WS feed")
    parser.add_argument(
        "--ws-url", default=None, help="Custom WS RPC URL (default: free public)"
    )
    args = parser.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args.duration, args.out, args.port, args.no_clob, args.ws_url))
