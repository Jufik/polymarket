"""RPC latency probe — compare OrderFilled delivery across Polygon RPC providers.

Connects to multiple Polygon WebSocket RPC endpoints simultaneously,
subscribes to OrderFilled logs on each, matches events by tx_hash,
and reports who delivers first.

Usage:
  uv run python scripts/rpc_latency_probe.py
  uv run python scripts/rpc_latency_probe.py --duration 600
  uv run python scripts/rpc_latency_probe.py --out data/rpc_latency.jsonl

Endpoints (configure via .env or env vars):
  PM_ALCHEMY_WS_URL       — required (Alchemy)
  PM_CHAINSTACK_WS_URL    — optional (Chainstack)

PublicNode and dRPC are hardcoded (free, no key needed).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median, quantiles

import websockets
from dotenv import load_dotenv

# ── Configuration ────────────────────────────────────────────────────────

CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE = "0xc5d563a36ae78145c45a50134d48a1215220f80a"
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

STATS_INTERVAL = 30
RECONNECT_BASE = 1.0
RECONNECT_MAX = 60.0
GC_WINDOW = 300  # discard unmatched entries older than this


# ── State ────────────────────────────────────────────────────────────────


@dataclass
class Arrival:
    """Per-provider arrival times for a single tx_hash."""
    times: dict[str, float] = field(default_factory=dict)
    created: float = field(default_factory=time.time)


@dataclass
class ProbeState:
    start_time: float = field(default_factory=time.time)
    providers: list[str] = field(default_factory=list)

    # Per-provider counters
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    reconnects: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_msg: dict[str, float] = field(default_factory=dict)

    # tx_hash -> Arrival
    arrivals: dict[str, Arrival] = field(default_factory=dict)

    def record(self, provider: str, tx_hash: str, ts: float) -> None:
        if not tx_hash:
            return
        if tx_hash not in self.arrivals:
            self.arrivals[tx_hash] = Arrival()
        # Keep first arrival per provider
        if provider not in self.arrivals[tx_hash].times:
            self.arrivals[tx_hash].times[provider] = ts

    def gc(self) -> None:
        cutoff = time.time() - GC_WINDOW
        self.arrivals = {k: v for k, v in self.arrivals.items() if v.created > cutoff}


# ── Writer ───────────────────────────────────────────────────────────────

_write_lock = asyncio.Lock()


async def write_line(outfile: object, provider: str, arrival_ts: float, raw: object) -> None:
    line = json.dumps({"provider": provider, "arrival_ts": arrival_ts, "msg": raw}, default=str)
    async with _write_lock:
        outfile.write(line + "\n")  # type: ignore[union-attr]


# ── Generic RPC feed ─────────────────────────────────────────────────────


async def rpc_feed(
    name: str,
    ws_url: str,
    state: ProbeState,
    outfile: object,
    stop: asyncio.Event,
) -> None:
    """Connect to a Polygon RPC WebSocket and subscribe to OrderFilled logs."""
    backoff = RECONNECT_BASE
    while not stop.is_set():
        try:
            print(f"[{name}] connecting to {ws_url[:60]}...", file=sys.stderr)
            async with websockets.connect(ws_url, ping_interval=30, open_timeout=15) as ws:
                backoff = RECONNECT_BASE
                print(f"[{name}] connected", file=sys.stderr)

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

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        state.errors[name] += 1
                        continue

                    # Skip subscription confirmation
                    if msg.get("method") != "eth_subscription":
                        continue

                    result = msg.get("params", {}).get("result")
                    if not result:
                        continue

                    state.counts[name] += 1
                    state.last_msg[name] = now

                    tx_hash = result.get("transactionHash", "").lower()
                    state.record(name, tx_hash, now)

                    await write_line(outfile, name, now, msg)

        except websockets.ConnectionClosed as e:
            state.reconnects[name] += 1
            print(f"[{name}] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            state.reconnects[name] += 1
            print(f"[{name}] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


# ── Stats ────────────────────────────────────────────────────────────────


async def stats_loop(state: ProbeState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(STATS_INTERVAL)
        state.gc()
        elapsed = time.time() - state.start_time
        parts = [f"{p}={state.counts[p]}" for p in state.providers]
        matched = sum(
            1 for a in state.arrivals.values() if len(a.times) >= 2
        )
        print(
            f"[stats {time.strftime('%H:%M:%S')}] "
            + " ".join(parts)
            + f" | matched={matched}"
            + f" | {elapsed:.0f}s",
            file=sys.stderr,
        )


# ── Report ───────────────────────────────────────────────────────────────


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total > 0 else "n/a"


def print_report(state: ProbeState) -> None:
    elapsed = time.time() - state.start_time
    r = sys.stderr

    r.write(f"\n{'=' * 76}\n")
    r.write(f"  RPC Latency Probe — Final Report\n")
    r.write(f"{'=' * 76}\n\n")
    r.write(f"  Duration: {elapsed:.0f}s ({elapsed / 60:.1f}m)\n\n")

    # Per-provider summary
    r.write(f"  {'Provider':<20} {'Events':>8} {'EPS':>8} {'Errors':>8} {'Reconnects':>10}\n")
    r.write(f"  {'-' * 58}\n")
    for p in state.providers:
        c = state.counts[p]
        eps = c / elapsed if elapsed > 0 else 0
        r.write(
            f"  {p:<20} {c:>8} {eps:>7.1f}/s "
            f"{state.errors[p]:>8} {state.reconnects[p]:>10}\n"
        )

    # Pairwise latency comparison
    providers = state.providers
    r.write(f"\n  --- Pairwise Latency (who delivers first?) ---\n")

    for i, pa in enumerate(providers):
        for pb in providers[i + 1 :]:
            deltas: list[float] = []
            a_first = b_first = 0

            for arrival in state.arrivals.values():
                t_a = arrival.times.get(pa)
                t_b = arrival.times.get(pb)
                if t_a is not None and t_b is not None:
                    delta = t_a - t_b  # negative = pa first
                    deltas.append(delta)
                    if delta < 0:
                        a_first += 1
                    elif delta > 0:
                        b_first += 1

            n = len(deltas)
            r.write(f"\n  {pa} vs {pb}")
            if n == 0:
                r.write(f"  — no matched events\n")
                continue

            r.write(f"  ({n} matched events)\n")
            r.write(f"    {pa} first: {a_first} ({_pct(a_first, n)})\n")
            r.write(f"    {pb} first: {b_first} ({_pct(b_first, n)})\n")

            if n >= 2:
                q = quantiles(deltas, n=4)
                r.write(
                    f"    delta ({pa} - {pb}):\n"
                    f"      mean={mean(deltas):+.4f}s  median={median(deltas):+.4f}s\n"
                    f"      p25={q[0]:+.4f}s  p75={q[2]:+.4f}s\n"
                    f"      min={min(deltas):+.4f}s  max={max(deltas):+.4f}s\n"
                )
            elif n == 1:
                r.write(f"    delta: {deltas[0]:+.4f}s\n")

    # Coverage matrix
    r.write(f"\n  --- Coverage ---\n\n")
    from collections import Counter
    combos: Counter[str] = Counter()
    for a in state.arrivals.values():
        key = "+".join(sorted(a.times.keys()))
        combos[key] += 1
    total = len(state.arrivals)
    for combo, count in combos.most_common():
        r.write(f"    {combo:<40} {count:>6} ({_pct(count, total)})\n")

    r.write(f"\n{'=' * 76}\n\n")


# ── Main ─────────────────────────────────────────────────────────────────

# Public endpoints (free, no key)
# PublicNode tested 2026-02-21: 0 events, doesn't support eth_subscribe for logs.
# Tenderly tested 2026-02-21: works but ~8s behind Alchemy (3+ blocks late).
PUBLIC_ENDPOINTS = {
    "drpc": "wss://polygon.drpc.org",
    "tenderly": "wss://polygon.gateway.tenderly.co",
}


async def main(duration: int, out_path: str) -> None:
    load_dotenv()

    # Build provider list
    endpoints: dict[str, str] = {}

    alchemy_url = os.environ.get("PM_ALCHEMY_WS_URL", "")
    if alchemy_url and "YOUR_ALCHEMY_KEY" not in alchemy_url:
        endpoints["alchemy"] = alchemy_url
    else:
        print("WARNING: PM_ALCHEMY_WS_URL not set, skipping Alchemy", file=sys.stderr)

    chainstack_url = os.environ.get("PM_CHAINSTACK_WS_URL", "")
    if chainstack_url:
        endpoints["chainstack"] = chainstack_url

    # Always include public endpoints
    endpoints.update(PUBLIC_ENDPOINTS)

    if len(endpoints) < 2:
        print("ERROR: Need at least 2 endpoints to compare. Set PM_ALCHEMY_WS_URL in .env.",
              file=sys.stderr)
        sys.exit(1)

    state = ProbeState(providers=list(endpoints.keys()))
    stop = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"[rpc_probe] Starting (duration={duration}s, output={out_path})", file=sys.stderr)
    print(f"[rpc_probe] Comparing {len(endpoints)} providers:", file=sys.stderr)
    for name, url in endpoints.items():
        print(f"  {name}: {url[:70]}{'...' if len(url) > 70 else ''}", file=sys.stderr)
    print(f"[rpc_probe] Ctrl-C to stop\n", file=sys.stderr)

    with open(out_path, "a") as f:
        tasks = [
            asyncio.create_task(rpc_feed(name, url, state, f, stop))
            for name, url in endpoints.items()
        ]
        tasks.append(asyncio.create_task(stats_loop(state, stop)))

        if duration > 0:
            async def _timer() -> None:
                await asyncio.sleep(duration)
                print(f"\n[rpc_probe] Duration {duration}s reached, stopping...", file=sys.stderr)
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
    print(f"[rpc_probe] {total} events written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Polygon RPC latency comparison probe")
    parser.add_argument("--out", default="data/rpc_latency.jsonl", help="Output JSONL path")
    parser.add_argument("--duration", type=int, default=0, help="Seconds (0 = indefinite)")
    args = parser.parse_args()

    asyncio.run(main(args.duration, args.out))
