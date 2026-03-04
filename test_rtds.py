import asyncio, json, time
import websockets

async def test():
    print("Connecting to RTDS...")
    ws = await asyncio.wait_for(
        websockets.connect("wss://ws-live-data.polymarket.com", ping_interval=None),
        timeout=10,
    )
    print("Connected OK")
    sub = json.dumps(
        {"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}]}
    )
    await ws.send(sub)
    print("Subscribed, waiting for messages...")
    start = time.time()
    count = 0
    trades = 0
    while time.time() - start < 300:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=30)
            count += 1
            if isinstance(msg, bytes):
                print(f"  [{time.time()-start:.1f}s] msg#{count} BYTES len={len(msg)}: {msg[:100]}")
                continue
            if msg in ("PING", "PONG", "pong"):
                print(f"  [{time.time()-start:.1f}s] msg#{count} heartbeat: {msg}")
                continue
            if not msg or not msg.strip():
                print(f"  [{time.time()-start:.1f}s] msg#{count} EMPTY: repr={repr(msg[:50])}")
                continue
            try:
                data = json.loads(msg)
                msg_type = data.get("type", "?")
                if msg_type == "trades":
                    trades += 1
                    payload = data.get("payload", {})
                    print(
                        f"  [{time.time()-start:.1f}s] msg#{count} TRADE:"
                        f" asset={str(payload.get('asset_id','?'))[:16]}..."
                        f" size={payload.get('size','?')}"
                        f" price={payload.get('price','?')}"
                    )
                else:
                    print(f"  [{time.time()-start:.1f}s] msg#{count} type={msg_type} keys={list(data.keys())[:5]}")
            except json.JSONDecodeError:
                print(f"  [{time.time()-start:.1f}s] msg#{count} NOT-JSON: {repr(msg[:120])}")
        except asyncio.TimeoutError:
            print("  No message in 30s")
            break
    print(f"\nTotal messages: {count}, trades: {trades}")
    await ws.close()

asyncio.run(test())
