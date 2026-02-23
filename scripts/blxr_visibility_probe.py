"""bloXroute visibility probe — can we see Polymarket txs in the mempool?

STATUS (2026-02-23): MEMPOOL PATH IS DEAD FOR POLYMARKET.
  Empirically proven: 9,997 on-chain OrderFilled events, 0 pending txs seen.
  Operators submit via private relay (Flashbots/MEV or direct-to-validator).
  This applies to ALL mempool sources: P2P, bloXroute, Alchemy, free RPC.

  The probe below can still be used to re-verify if operators change behavior,
  or to test bloXroute for non-Polymarket use cases.

  Key finding: Operators don't call CTF/NegRisk Exchange directly.
  They call router contracts (0xe3f18a... 94%, 0xb76889... 6%) via method
  0x2287e350, which internally calls the exchange. These router txs never
  appear in the public pending pool.

Original purpose:
  Answers the critical question BEFORE committing $300/mo:
  Do Polymarket operator settlement txs appear in bloXroute's newTxs stream
  BEFORE they land on-chain?

Runs two feeds simultaneously:
  1. **bloXroute newTxs** (free tier, tx_hash only) — pending tx stream via BDN
  2. **RPC logs** (free public endpoint) — on-chain OrderFilled confirmations

Three possible outcomes:
  A) bloXroute sees txs BEFORE on-chain  → mempool works, $300/mo is worth it
  B) bloXroute sees txs AFTER on-chain   → BDN has the txs but no speed advantage
  C) bloXroute sees 0 Polymarket txs     → operators submit privately, mempool is dead

Usage:
  # Set your bloXroute auth header (free Introductory tier)
  export BLXR_AUTH_HEADER="your-auth-header-here"

  # Run for 10 minutes (default)
  uv run python scripts/blxr_visibility_probe.py

  # Run for 30 minutes
  uv run python scripts/blxr_visibility_probe.py --duration 1800

  # Custom RPC endpoint
  uv run python scripts/blxr_visibility_probe.py --ws-url wss://your-rpc.com

Requires:
  - bloXroute free account (https://portal.bloxroute.com/register)
  - BLXR_AUTH_HEADER env var or --auth flag
  - No paid subscription needed (free tier gives tx_hash)
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

CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEGRISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
# Operator router contracts (discovered 2026-02-23: operators don't call exchange directly)
ROUTER_PRIMARY = "0xe3f18aCc55091E2c48d883fc8c8413319d4AB7b0"
ROUTER_SECONDARY = "0xB768891E3130F6Df18214AC804D4Db76c2c37730"
# Lowercase for matching
CTF_EXCHANGE_LOWER = CTF_EXCHANGE.lower()
NEGRISK_EXCHANGE_LOWER = NEGRISK_EXCHANGE.lower()
ROUTER_PRIMARY_LOWER = ROUTER_PRIMARY.lower()
ROUTER_SECONDARY_LOWER = ROUTER_SECONDARY.lower()
ALL_TARGET_ADDRS = frozenset({
    CTF_EXCHANGE_LOWER, NEGRISK_EXCHANGE_LOWER,
    ROUTER_PRIMARY_LOWER, ROUTER_SECONDARY_LOWER,
})
ORDER_FILLED_SIG = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

BLXR_WS_URL = "wss://api.blxrbdn.com/ws"

DEFAULT_WS_ENDPOINTS = [
    "wss://polygon-bor-rpc.publicnode.com",
    "wss://polygon-mainnet.public.blastapi.io",
    "wss://polygon.drpc.org",
]

STATS_INTERVAL = 30
MATCH_WINDOW = 600  # 10 min window to match blxr → on-chain

# ── Data structures ──────────────────────────────────────────────────────


@dataclass
class BlxrEvent:
    tx_hash: str
    arrival: float


@dataclass
class RpcEvent:
    tx_hash: str
    arrival: float
    block_ts: float
    block_number: int


@dataclass
class MatchedTx:
    tx_hash: str
    blxr_arrival: float
    rpc_arrival: float
    block_ts: float
    block_number: int
    delta_blxr_rpc: float  # blxr - rpc arrival (negative = blxr first)
    delta_blxr_block: float  # blxr arrival - block timestamp (negative = before block)


@dataclass
class ProbeState:
    blxr_events: dict[str, BlxrEvent] = field(default_factory=dict)
    rpc_events: dict[str, RpcEvent] = field(default_factory=dict)
    matched: list[MatchedTx] = field(default_factory=list)

    # Counters
    blxr_count: int = 0  # total txs from bloXroute (all Polygon txs matching filter)
    blxr_all_count: int = 0  # total raw messages from bloXroute (before filter)
    rpc_count: int = 0  # OrderFilled on-chain events
    blxr_connected: bool = False
    rpc_connected: bool = False

    def try_match(self, tx_hash: str) -> None:
        """Match when we have both blxr + rpc for same tx_hash."""
        blxr = self.blxr_events.get(tx_hash)
        rpc = self.rpc_events.get(tx_hash)

        if blxr is None or rpc is None:
            return

        self.blxr_events.pop(tx_hash, None)
        self.rpc_events.pop(tx_hash, None)

        self.matched.append(
            MatchedTx(
                tx_hash=tx_hash,
                blxr_arrival=blxr.arrival,
                rpc_arrival=rpc.arrival,
                block_ts=rpc.block_ts,
                block_number=rpc.block_number,
                delta_blxr_rpc=blxr.arrival - rpc.arrival,
                delta_blxr_block=blxr.arrival - rpc.block_ts,
            )
        )

        delta = blxr.arrival - rpc.arrival
        tag = "BEFORE" if delta < 0 else "AFTER"
        print(
            f"[MATCH] {tx_hash[:18]}... bloXroute was {abs(delta):.3f}s {tag} on-chain",
            file=sys.stderr,
        )

    def gc_stale(self) -> None:
        cutoff = time.time() - MATCH_WINDOW
        self.blxr_events = {
            k: v for k, v in self.blxr_events.items() if v.arrival > cutoff
        }
        self.rpc_events = {
            k: v for k, v in self.rpc_events.items() if v.arrival > cutoff
        }


# ── bloXroute feed ───────────────────────────────────────────────────────


async def blxr_feed(
    state: ProbeState,
    auth_header: str,
    stop: asyncio.Event,
) -> None:
    """Connect to bloXroute Cloud API, subscribe to newTxs for Polymarket contracts."""
    backoff = 1.0
    while not stop.is_set():
        try:
            print(f"[BLXR] connecting to {BLXR_WS_URL}...", file=sys.stderr)
            async with websockets.connect(
                BLXR_WS_URL,
                additional_headers={"Authorization": auth_header},
                ping_interval=30,
            ) as ws:
                state.blxr_connected = True
                backoff = 1.0
                print("[BLXR] connected", file=sys.stderr)

                # Subscribe to newTxs filtered to Polymarket contracts.
                # Free tier: only tx_hash available.
                # Filter: {to} matches exchange OR operator router contracts.
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "subscribe",
                    "params": [
                        "newTxs",
                        {
                            "blockchain_network": "Polygon-Mainnet",
                            "include": ["tx_hash"],
                            "filters": (
                                f"{{to}} == '{CTF_EXCHANGE}'"
                                f" OR {{to}} == '{NEGRISK_EXCHANGE}'"
                                f" OR {{to}} == '{ROUTER_PRIMARY}'"
                                f" OR {{to}} == '{ROUTER_SECONDARY}'"
                            ),
                        },
                    ],
                }
                await ws.send(json.dumps(subscribe_msg))
                print(
                    f"[BLXR] subscribed to newTxs (Polygon, "
                    f"filter: CTF+NegRisk exchange)",
                    file=sys.stderr,
                )

                # Read subscription confirmation
                resp = await ws.recv()
                resp_data = json.loads(resp)
                if "error" in resp_data:
                    print(
                        f"[BLXR] ERROR: {resp_data['error']}",
                        file=sys.stderr,
                    )
                    # If Polygon-Mainnet is not supported, try without blockchain_network
                    # and without filter (listen to ALL txs, match client-side)
                    print(
                        "[BLXR] Retrying without blockchain_network filter...",
                        file=sys.stderr,
                    )
                    subscribe_msg_fallback = {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "subscribe",
                        "params": [
                            "newTxs",
                            {"include": ["tx_hash"]},
                        ],
                    }
                    await ws.send(json.dumps(subscribe_msg_fallback))
                    resp2 = await ws.recv()
                    resp2_data = json.loads(resp2)
                    if "error" in resp2_data:
                        print(
                            f"[BLXR] FATAL: {resp2_data['error']}",
                            file=sys.stderr,
                        )
                        return
                    print(
                        "[BLXR] subscribed to newTxs (all networks, no filter)",
                        file=sys.stderr,
                    )
                else:
                    sub_id = resp_data.get("result")
                    print(f"[BLXR] subscription ID: {sub_id}", file=sys.stderr)

                async for raw in ws:
                    if stop.is_set():
                        break
                    _handle_blxr_msg(state, raw)

        except websockets.ConnectionClosed as e:
            state.blxr_connected = False
            print(f"[BLXR] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            state.blxr_connected = False
            print(f"[BLXR] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


def _handle_blxr_msg(state: ProbeState, raw: str) -> None:
    now = time.time()
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    # bloXroute sends notifications as:
    # {"jsonrpc":"2.0","method":"subscribe","params":{"subscription":"...","result":{...}}}
    if msg.get("method") != "subscribe":
        return

    result = msg.get("params", {}).get("result")
    if not result:
        return

    state.blxr_all_count += 1

    tx_hash = result.get("txHash", "").lower()
    if not tx_hash:
        return

    # If no server-side filter was applied, we see ALL txs.
    # We can't filter client-side on free tier (no tx_contents.to).
    # Just record everything — matching against RPC will filter for Polymarket.

    if tx_hash in state.blxr_events:
        return

    state.blxr_count += 1
    state.blxr_events[tx_hash] = BlxrEvent(tx_hash=tx_hash, arrival=now)

    state.try_match(tx_hash)


# ── RPC feed (on-chain OrderFilled logs) ─────────────────────────────────


async def rpc_feed(
    state: ProbeState,
    ws_urls: list[str],
    stop: asyncio.Event,
) -> None:
    """Connect to Polygon WS RPC, subscribe to OrderFilled logs."""
    url_idx = 0
    backoff = 1.0
    while not stop.is_set():
        ws_url = ws_urls[url_idx % len(ws_urls)]
        try:
            print(f"[RPC] connecting to {ws_url}...", file=sys.stderr)
            async with websockets.connect(ws_url, ping_interval=30) as ws:
                state.rpc_connected = True
                backoff = 1.0
                print("[RPC] connected", file=sys.stderr)

                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "eth_subscribe",
                            "params": [
                                "logs",
                                {
                                    "address": [
                                        CTF_EXCHANGE_LOWER,
                                        NEGRISK_EXCHANGE_LOWER,
                                    ]
                                },
                            ],
                        }
                    )
                )

                async for raw in ws:
                    if stop.is_set():
                        break
                    _handle_rpc_msg(state, raw)

        except websockets.ConnectionClosed as e:
            state.rpc_connected = False
            print(f"[RPC] disconnected: {e}", file=sys.stderr)
        except Exception as e:
            state.rpc_connected = False
            print(f"[RPC] error: {e}", file=sys.stderr)

        if stop.is_set():
            break
        url_idx += 1
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

    # Drop taker-perspective duplicate events
    taker = "0x" + topics[3][-40:]
    if taker.lower() in {CTF_EXCHANGE_LOWER, NEGRISK_EXCHANGE_LOWER}:
        return

    tx_hash = result.get("transactionHash", "").lower()
    if not tx_hash or tx_hash in state.rpc_events:
        return

    state.rpc_count += 1

    block_ts_hex = result.get("blockTimestamp")
    block_ts = int(block_ts_hex, 16) if block_ts_hex else now
    block_number = int(result.get("blockNumber", "0x0"), 16)

    state.rpc_events[tx_hash] = RpcEvent(
        tx_hash=tx_hash,
        arrival=now,
        block_ts=block_ts,
        block_number=block_number,
    )

    state.try_match(tx_hash)


# ── Stats + Report ───────────────────────────────────────────────────────


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


async def stats_loop(state: ProbeState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(STATS_INTERVAL)
        state.gc_stale()
        n = len(state.matched)
        blxr_pending = len(state.blxr_events)
        rpc_pending = len(state.rpc_events)
        print(
            f"[stats {time.strftime('%H:%M:%S')}] "
            f"blxr={'ON' if state.blxr_connected else 'OFF'} "
            f"blxr_txs={state.blxr_count} "
            f"blxr_raw={state.blxr_all_count} "
            f"rpc={'ON' if state.rpc_connected else 'OFF'} "
            f"rpc_events={state.rpc_count} "
            f"matched={n} "
            f"pending_blxr={blxr_pending} pending_rpc={rpc_pending}",
            file=sys.stderr,
        )


def print_report(state: ProbeState) -> None:
    n = len(state.matched)
    r = sys.stderr

    r.write(f"\n{'=' * 76}\n")
    r.write("  bloXroute Visibility Probe — Final Report\n")
    r.write(f"{'=' * 76}\n\n")

    r.write(f"  bloXroute txs (filtered):   {state.blxr_count}\n")
    r.write(f"  bloXroute raw messages:      {state.blxr_all_count}\n")
    r.write(f"  RPC OrderFilled events:      {state.rpc_count}\n")
    r.write(f"  Matched (blxr + rpc):        {n}\n")
    r.write(f"  Pending bloXroute:           {len(state.blxr_events)}\n")
    r.write(f"  Pending RPC:                 {len(state.rpc_events)}\n")

    if state.blxr_count == 0 and state.blxr_all_count == 0:
        r.write(
            "\n  bloXroute received 0 messages.\n"
            "  Possible causes:\n"
            "    - Invalid auth header (check BLXR_AUTH_HEADER)\n"
            "    - Polygon-Mainnet not supported on your tier\n"
            "    - Network/firewall issue\n"
        )
        r.write(f"{'=' * 76}\n\n")
        return

    if state.blxr_count == 0 and state.blxr_all_count > 0:
        r.write(
            f"\n  bloXroute received {state.blxr_all_count} raw msgs but 0 matched filter.\n"
            "  The server-side filter may not support Polygon, or no Polymarket txs\n"
            "  were submitted during the probe window.\n"
        )

    if n == 0 and state.rpc_count > 0 and state.blxr_all_count > 0:
        r.write(
            "\n  --- VERDICT: C — bloXroute does NOT see Polymarket txs ---\n"
            "  On-chain OrderFilled events appeared but bloXroute never saw them\n"
            "  as pending. Operators likely submit directly to validators.\n"
            "  The mempool path is DEAD for Polymarket.\n"
        )
        r.write(f"{'=' * 76}\n\n")
        return

    if n == 0:
        r.write(
            "\n  No matched trades. Need both feeds running simultaneously\n"
            "  with Polymarket activity.\n"
        )
        r.write(f"{'=' * 76}\n\n")
        return

    # We have matches — compute stats
    deltas_rpc = [m.delta_blxr_rpc for m in state.matched]
    deltas_block = [m.delta_blxr_block for m in state.matched]
    blxr_first = sum(1 for d in deltas_rpc if d < 0)
    rpc_first = sum(1 for d in deltas_rpc if d > 0)
    before_block = sum(1 for d in deltas_block if d < 0)

    r.write("\n  --- Delta B-R: blxr_arrival - rpc_arrival ---\n")
    r.write("  (negative = bloXroute delivered FIRST)\n")
    r.write(f"  bloXroute first: {blxr_first} ({_pct(blxr_first, n)})\n")
    r.write(f"  RPC first:       {rpc_first} ({_pct(rpc_first, n)})\n")
    r.write(_stats_line("Delta B-R", deltas_rpc) + "\n")

    r.write("\n  --- Delta B-Block: blxr_arrival - block_timestamp ---\n")
    r.write("  (negative = bloXroute saw tx BEFORE block was produced)\n")
    r.write(f"  Before block: {before_block} ({_pct(before_block, n)})\n")
    r.write(_stats_line("Delta B-Block", deltas_block) + "\n")

    # Verdict
    r.write("\n  --- VERDICT ---\n")
    if blxr_first > n * 0.5 and median(deltas_rpc) < -0.5:
        r.write(
            f"  A — bloXroute sees Polymarket txs {abs(median(deltas_rpc)):.1f}s "
            f"BEFORE on-chain (median).\n"
            f"  The $300/mo Professional tier is worth investigating.\n"
            f"  With tx_contents.input you can decode matchOrders calldata.\n"
        )
    elif blxr_first > 0:
        r.write(
            f"  B — bloXroute sees some txs but advantage is marginal "
            f"(median {median(deltas_rpc):+.1f}s).\n"
            f"  Might not justify $300/mo for the small speed gain.\n"
        )
    else:
        r.write(
            f"  B — bloXroute sees txs but AFTER on-chain "
            f"(median {median(deltas_rpc):+.1f}s).\n"
            f"  BDN has the data but no speed advantage.\n"
        )

    r.write(f"\n{'=' * 76}\n\n")

    # Print individual matches for inspection
    if n <= 50:
        r.write("  Individual matches:\n")
        for m in state.matched:
            tag = "BEFORE" if m.delta_blxr_rpc < 0 else "AFTER"
            r.write(
                f"    {m.tx_hash[:18]}... blxr {tag} rpc by "
                f"{abs(m.delta_blxr_rpc):.3f}s  "
                f"(block {m.block_number})\n"
            )
        r.write("\n")


def dump_results(state: ProbeState, out_path: str) -> None:
    results = {
        "summary": {
            "matched_count": len(state.matched),
            "blxr_count": state.blxr_count,
            "blxr_all_count": state.blxr_all_count,
            "rpc_count": state.rpc_count,
        },
        "matched_trades": [
            {
                "tx_hash": m.tx_hash,
                "blxr_arrival": m.blxr_arrival,
                "rpc_arrival": m.rpc_arrival,
                "block_ts": m.block_ts,
                "block_number": m.block_number,
                "delta_blxr_rpc": round(m.delta_blxr_rpc, 4),
                "delta_blxr_block": round(m.delta_blxr_block, 4),
            }
            for m in state.matched
        ],
    }

    Path(out_path).write_text(json.dumps(results, indent=2))
    print(f"\nResults written to {out_path}", file=sys.stderr)


# ── Main ─────────────────────────────────────────────────────────────────


async def main(
    duration: int,
    out_path: str,
    auth_header: str,
    ws_url: str | None,
) -> None:
    load_dotenv()

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
        f"[probe] bloXroute visibility probe (duration={duration}s)",
        file=sys.stderr,
    )
    print(
        f"[probe] bloXroute: {BLXR_WS_URL} (Polygon-Mainnet)",
        file=sys.stderr,
    )
    print(
        f"[probe] RPC: {ws_urls[0]} (+{len(ws_urls)-1} fallbacks)",
        file=sys.stderr,
    )
    print(f"[probe] Output: {out_path}", file=sys.stderr)
    print(f"[probe] Ctrl-C to stop\n", file=sys.stderr)

    tasks = [
        asyncio.create_task(blxr_feed(state, auth_header, stop)),
        asyncio.create_task(rpc_feed(state, ws_urls, stop)),
        asyncio.create_task(stats_loop(state, stop)),
    ]

    if duration > 0:

        async def _timer() -> None:
            await asyncio.sleep(duration)
            print(
                f"\n[probe] Duration {duration}s reached, stopping...",
                file=sys.stderr,
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

    parser = argparse.ArgumentParser(
        description="bloXroute visibility probe for Polymarket txs"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Run duration in seconds (0=indefinite, default=600)",
    )
    parser.add_argument(
        "--out",
        default="data/blxr_visibility.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--auth",
        default=None,
        help="bloXroute auth header (or set BLXR_AUTH_HEADER env var)",
    )
    parser.add_argument(
        "--ws-url",
        default=None,
        help="Custom WS RPC URL for on-chain feed",
    )
    args = parser.parse_args()

    auth = args.auth or os.environ.get("BLXR_AUTH_HEADER", "")
    if not auth:
        print(
            "ERROR: bloXroute auth header required.\n"
            "  Set BLXR_AUTH_HEADER env var or use --auth flag.\n"
            "  Register free at https://portal.bloxroute.com/register",
            file=sys.stderr,
        )
        sys.exit(1)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args.duration, args.out, auth, args.ws_url))
