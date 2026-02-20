#!/usr/bin/env python3
"""
Verify whether RTDS proxyWallet matches on-chain maker address.

Strategy:
  1. Connect to RTDS, collect N trades that have both proxyWallet AND transactionHash
  2. Query Goldsky Subgraph for the same transactionHash values
  3. Compare RTDS proxyWallet vs Subgraph maker for each match

Usage:
  uv run python scripts/verify_proxy_wallet.py              # collect 20 trades, then verify
  uv run python scripts/verify_proxy_wallet.py --count 50   # collect 50 trades
"""

import asyncio
import json
import argparse
import time
from datetime import datetime, timezone

import websockets
import aiohttp

# ── Config ────────────────────────────────────────────────────────────

RTDS_URL = "wss://ws-live-data.polymarket.com"
SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/"
    "project_cl6mb8i9h0003e201j6li0diw/"
    "subgraphs/orderbook-subgraph/0.0.1/gn"
)
PING_INTERVAL = 5


# ── RTDS Collection ──────────────────────────────────────────────────


async def collect_rtds_trades(count: int, timeout: int = 120) -> list[dict]:
    """Connect to RTDS, collect trades with proxyWallet + transactionHash."""
    trades: list[dict] = []
    deadline = time.time() + timeout

    print(f"[RTDS] Connecting to {RTDS_URL} ...")
    async with websockets.connect(RTDS_URL, ping_interval=None) as ws:
        print("[RTDS] Connected. Subscribing to activity/trades (global) ...")
        await ws.send(json.dumps({
            "action": "subscribe",
            "subscriptions": [{"topic": "activity", "type": "trades"}],
        }))

        ping_task = asyncio.create_task(_ping_loop(ws))
        try:
            while len(trades) < count and time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue

                if raw in ("PING", "PONG", "pong"):
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Only process trade messages
                if msg.get("type") != "trades":
                    continue

                payload = msg.get("payload", {})
                proxy_wallet = payload.get("proxyWallet")
                tx_hash = payload.get("transactionHash")

                if proxy_wallet and tx_hash:
                    trade = {
                        "proxyWallet": proxy_wallet,
                        "transactionHash": tx_hash,
                        "asset": payload.get("asset", ""),
                        "side": payload.get("side", ""),
                        "price": payload.get("price", ""),
                        "size": payload.get("size", ""),
                        "timestamp": payload.get("timestamp", ""),
                        "conditionId": payload.get("conditionId", ""),
                    }
                    trades.append(trade)
                    print(
                        f"[RTDS] Trade {len(trades)}/{count}: "
                        f"proxyWallet={proxy_wallet[:10]}... "
                        f"txHash={tx_hash[:14]}..."
                    )
        finally:
            ping_task.cancel()

    print(f"[RTDS] Collected {len(trades)} trades with proxyWallet + txHash")
    return trades


async def _ping_loop(ws):
    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            await ws.send("PING")
        except Exception:
            break


# ── Subgraph Lookup ──────────────────────────────────────────────────


async def lookup_subgraph(tx_hashes: list[str]) -> dict[str, list[dict]]:
    """Query Goldsky Subgraph for OrderFilled events by transactionHash.

    Returns: {tx_hash: [event, ...]} mapping
    """
    results: dict[str, list[dict]] = {}

    async with aiohttp.ClientSession() as session:
        for i, tx_hash in enumerate(tx_hashes):
            # Subgraph may store tx hashes with or without 0x prefix, try both
            query = """
            query($txHash: String!) {
                orderFilledEvents(
                    where: { transactionHash: $txHash }
                    first: 10
                ) {
                    id
                    maker
                    taker
                    makerAssetId
                    takerAssetId
                    makerAmountFilled
                    takerAmountFilled
                    fee
                    timestamp
                    transactionHash
                    orderHash
                }
            }
            """
            payload = {
                "query": query,
                "variables": {"txHash": tx_hash},
            }

            try:
                async with session.post(
                    SUBGRAPH_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    events = data.get("data", {}).get("orderFilledEvents", [])
                    if events:
                        results[tx_hash] = events
                        print(
                            f"[SUBGRAPH] {i+1}/{len(tx_hashes)}: "
                            f"txHash={tx_hash[:14]}... → {len(events)} event(s), "
                            f"maker={events[0]['maker'][:10]}..."
                        )
                    else:
                        print(
                            f"[SUBGRAPH] {i+1}/{len(tx_hashes)}: "
                            f"txHash={tx_hash[:14]}... → NOT FOUND (may not be indexed yet)"
                        )
            except Exception as e:
                print(f"[SUBGRAPH] {i+1}/{len(tx_hashes)}: ERROR: {e}")

            # Rate limit: ~2 req/sec to be polite
            await asyncio.sleep(0.5)

    return results


# ── Cross-Reference ──────────────────────────────────────────────────


def cross_reference(rtds_trades: list[dict], subgraph_data: dict[str, list[dict]]) -> None:
    """Compare proxyWallet (RTDS) vs maker (Subgraph) for matching transactions."""

    print("\n" + "=" * 80)
    print("  CROSS-REFERENCE: proxyWallet vs on-chain maker")
    print("=" * 80)

    matched = 0
    mismatched = 0
    not_found = 0
    proxy_is_maker = 0
    proxy_is_taker = 0
    proxy_is_neither = 0

    for trade in rtds_trades:
        tx_hash = trade["transactionHash"]
        proxy = trade["proxyWallet"].lower()

        events = subgraph_data.get(tx_hash, [])
        if not events:
            not_found += 1
            continue

        matched += 1

        # Check all events for this tx (could be multiple fills in one tx)
        found_role = None
        for event in events:
            maker = event["maker"].lower()
            taker = event["taker"].lower()

            if proxy == maker:
                found_role = "MAKER"
                proxy_is_maker += 1
                break
            elif proxy == taker:
                found_role = "TAKER"
                proxy_is_taker += 1
                break

        if found_role is None:
            proxy_is_neither += 1
            found_role = "NEITHER"
            print(
                f"  MISMATCH: tx={tx_hash[:14]}... "
                f"proxyWallet={proxy[:10]}... "
                f"maker={events[0]['maker'][:10]}... "
                f"taker={events[0]['taker'][:10]}..."
            )

    total_checked = matched
    print(f"\n  Summary:")
    print(f"    Total RTDS trades collected:  {len(rtds_trades)}")
    print(f"    Found on subgraph:            {matched}")
    print(f"    Not yet indexed:              {not_found}")
    print()
    if total_checked > 0:
        print(f"    proxyWallet == maker:         {proxy_is_maker} ({proxy_is_maker/total_checked:.1%})")
        print(f"    proxyWallet == taker:         {proxy_is_taker} ({proxy_is_taker/total_checked:.1%})")
        print(f"    proxyWallet == neither:       {proxy_is_neither} ({proxy_is_neither/total_checked:.1%})")

    print("\n" + "=" * 80)
    if total_checked == 0:
        print("  INCONCLUSIVE — no subgraph matches found.")
        print("  Subgraph may be behind. Try again with --wait 120 to delay lookup.")
    elif proxy_is_maker == total_checked:
        print("  CONFIRMED: proxyWallet == maker in 100% of cases.")
        print("  RTDS gives you the real maker address in real-time. No enrichment needed.")
    elif proxy_is_maker > proxy_is_taker:
        print(f"  MOSTLY MAKER: proxyWallet is the maker in {proxy_is_maker/total_checked:.1%} of cases.")
        print(f"  But also matches taker in {proxy_is_taker/total_checked:.1%} — RTDS may report both sides.")
    elif proxy_is_taker > proxy_is_maker:
        print(f"  MOSTLY TAKER: proxyWallet is the taker in {proxy_is_taker/total_checked:.1%} of cases.")
        print("  proxyWallet is NOT the maker — enrichment via Alchemy IS needed for copy trading.")
    else:
        print("  MIXED RESULTS — needs deeper investigation.")
    print("=" * 80)


# ── Main ─────────────────────────────────────────────────────────────


async def main(count: int, wait: int):
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  proxyWallet vs On-Chain Maker Verification                  ║
║                                                              ║
║  1. Collect {count:3d} trades from RTDS with proxyWallet + txHash  ║
║  2. Wait {wait:3d}s for subgraph indexing                          ║
║  3. Query Goldsky Subgraph for same transactions             ║
║  4. Compare proxyWallet vs maker address                     ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Step 1: Collect from RTDS
    rtds_trades = await collect_rtds_trades(count)

    if not rtds_trades:
        print("ERROR: No trades collected from RTDS. Exiting.")
        return

    # Step 2: Wait for subgraph to index
    if wait > 0:
        print(f"\n[WAIT] Waiting {wait}s for subgraph to index these transactions...")
        await asyncio.sleep(wait)

    # Step 3: Lookup on subgraph
    tx_hashes = list({t["transactionHash"] for t in rtds_trades})
    print(f"\n[SUBGRAPH] Looking up {len(tx_hashes)} unique transaction hashes...")
    subgraph_data = await lookup_subgraph(tx_hashes)

    # Step 4: Cross-reference
    cross_reference(rtds_trades, subgraph_data)

    # Dump raw data for manual inspection
    print("\n[RAW] First 3 RTDS trades:")
    for t in rtds_trades[:3]:
        print(f"  {json.dumps(t, indent=2)}")

    if subgraph_data:
        print("\n[RAW] First 3 subgraph matches:")
        for tx, events in list(subgraph_data.items())[:3]:
            print(f"  txHash: {tx}")
            for e in events:
                print(f"    maker: {e['maker']}")
                print(f"    taker: {e['taker']}")
                print(f"    makerAssetId: {e['makerAssetId']}")
                print(f"    takerAssetId: {e['takerAssetId']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify RTDS proxyWallet vs on-chain maker")
    parser.add_argument("--count", type=int, default=20, help="Number of RTDS trades to collect")
    parser.add_argument("--wait", type=int, default=90, help="Seconds to wait for subgraph indexing before lookup")
    args = parser.parse_args()

    asyncio.run(main(args.count, args.wait))
