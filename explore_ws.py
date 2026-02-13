"""
Polymarket WebSocket Explorer v2

Connects to WebSocket endpoints and dumps raw messages to understand schemas.

Usage:
    uv run --with websockets --with httpx explore_ws.py [--duration 30]
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone

import websockets


# --- Config ---

# Seahawks vs Patriots (high volume)
MARKET_TOKENS = [
    "46434110155841033529384949983718980438706543876953886750286883506638610790525",
    "57625936606489185661652559589880983710918172021553907271126623944716577292773",
]

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_WS_URL = "wss://ws-live-data.polymarket.com"

DURATION = int(sys.argv[sys.argv.index("--duration") + 1]) if "--duration" in sys.argv else 30

msg_log: dict[str, list] = {"market_ws": [], "rtds_global": []}


def ts_now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:12]


# --- WebSocket Connections ---

async def market_ws(duration: float) -> None:
    """TAP 1: Market WebSocket."""
    deadline = time.monotonic() + duration
    try:
        async with websockets.connect(MARKET_WS_URL) as ws:
            sub = json.dumps({"type": "market", "assets_ids": MARKET_TOKENS})
            await ws.send(sub)
            print(f"[{ts_now()}] market_ws: subscribed")

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"_raw": raw}
                    msg_log["market_ws"].append(data)
                    print(f"[{ts_now()}] market_ws: got msg (type={type(data).__name__})")
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"[{ts_now()}] market_ws ERROR: {e}")


async def rtds_global(duration: float) -> None:
    """TAP 3: RTDS Activity — global trades."""
    deadline = time.monotonic() + duration
    count = 0
    try:
        async with websockets.connect(RTDS_WS_URL) as ws:
            sub = json.dumps({
                "action": "subscribe",
                "subscriptions": [{"topic": "activity", "type": "trades"}],
            })
            await ws.send(sub)
            print(f"[{ts_now()}] rtds_global: subscribed")

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    if raw == "PING":
                        await ws.send("PONG")
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"_raw": raw}
                    msg_log["rtds_global"].append(data)
                    count += 1
                    if count <= 3 or count % 50 == 0:
                        print(f"[{ts_now()}] rtds_global: msg #{count}")
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"[{ts_now()}] rtds_global ERROR: {e}")
    print(f"[{ts_now()}] rtds_global: total {count} messages")


# --- Main ---

async def main() -> None:
    print(f"Connecting for {DURATION}s...\n")

    await asyncio.gather(
        market_ws(DURATION),
        rtds_global(DURATION),
    )

    # --- Analysis ---
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    # Market WS
    print(f"\n### MARKET WS: {len(msg_log['market_ws'])} messages ###\n")
    for i, msg in enumerate(msg_log["market_ws"][:3]):
        print(f"Message {i}:")
        print(json.dumps(msg, indent=2, default=str)[:3000])
        print()

    # RTDS
    print(f"\n### RTDS GLOBAL: {len(msg_log['rtds_global'])} messages ###\n")

    # Show first 3 full messages
    for i, msg in enumerate(msg_log["rtds_global"][:3]):
        print(f"Message {i}:")
        print(json.dumps(msg, indent=2, default=str)[:3000])
        print()

    # Extract all unique keys from RTDS payload
    if msg_log["rtds_global"]:
        all_payload_keys: set[str] = set()
        all_top_keys: set[str] = set()
        for msg in msg_log["rtds_global"]:
            if isinstance(msg, dict):
                all_top_keys.update(msg.keys())
                payload = msg.get("payload", {})
                if isinstance(payload, dict):
                    all_payload_keys.update(payload.keys())

        print(f"RTDS top-level keys: {sorted(all_top_keys)}")
        print(f"RTDS payload keys:   {sorted(all_payload_keys)}")

        # Check which payload fields are always present vs optional
        if len(msg_log["rtds_global"]) > 5:
            sample = msg_log["rtds_global"][:100]
            field_counts: dict[str, int] = {}
            for msg in sample:
                if isinstance(msg, dict):
                    payload = msg.get("payload", {})
                    if isinstance(payload, dict):
                        for k in payload:
                            field_counts[k] = field_counts.get(k, 0) + 1
            total = len(sample)
            print(f"\nField presence in first {total} RTDS messages:")
            for k, v in sorted(field_counts.items(), key=lambda x: -x[1]):
                pct = v / total * 100
                print(f"  {k:30s}  {v:>4d}/{total}  ({pct:.0f}%)")

            # Show range of values for key fields
            print("\nSample values for key fields:")
            for field in ["price", "size", "side", "maker_address", "taker_address",
                          "transaction_hash", "match_time", "owner", "type",
                          "conditionId", "asset"]:
                vals = set()
                for msg in sample:
                    if isinstance(msg, dict):
                        payload = msg.get("payload", {})
                        if isinstance(payload, dict) and field in payload:
                            v = payload[field]
                            vals.add(str(v)[:60])
                if vals:
                    print(f"  {field}: {list(vals)[:5]}")

    # Dump full capture
    with open("explore_ws_capture.json", "w") as f:
        json.dump(msg_log, f, indent=2, default=str)
    print("\nFull capture saved to explore_ws_capture.json")


if __name__ == "__main__":
    asyncio.run(main())
