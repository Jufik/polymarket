"""Compare dedicated single-asset WS vs pooled managed-slot data.

Opens a raw WS connection to wss://ws-subscriptions-clob.polymarket.com/ws/market
for ONE asset, logs all events. Simultaneously reads Redis for the same asset
(written by managed slots). Prints side-by-side comparison.

Usage:
    PYTHONPATH=. uv run python scripts/ws_compare.py <asset_id> [--duration 60]
"""

import argparse
import asyncio
import json
import time

import websockets

# Try importing redis for managed-slot comparison
try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


async def run(asset_id: str, duration: float):
    direct_events: list[dict] = []
    redis_snapshots: list[dict] = []

    # Start Redis poller if available
    redis_task = None
    if HAS_REDIS:
        redis_task = asyncio.create_task(
            poll_redis(asset_id, duration, redis_snapshots)
        )

    # Track running book state from direct WS
    running_book: dict[str, float] = {"bid": 0.0, "ask": 0.0}

    # Direct WS connection — single asset
    print(f"[DIRECT] Connecting to {WS_URL} for asset {asset_id[:20]}...")
    try:
        async with websockets.connect(WS_URL, ping_interval=10) as ws:
            subscribe_msg = json.dumps({
                "type": "market",
                "assets_ids": [asset_id],
                "custom_feature_enabled": True,
            })
            await ws.send(subscribe_msg)
            print(f"[DIRECT] Subscribed. Collecting for {duration}s...")

            t0 = time.time()
            while time.time() - t0 < duration:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    now = time.time()
                    msgs = json.loads(raw)
                    if not isinstance(msgs, list):
                        msgs = [msgs]
                    for msg in msgs:
                        evt_type = msg.get("event_type", "?")
                        entry = {
                            "ts": now,
                            "elapsed": round(now - t0, 3),
                            "event_type": evt_type,
                            "asset_id": msg.get("asset_id", ""),
                        }
                        if evt_type == "price_change":
                            changes = msg.get("changes", [])
                            entry["n_changes"] = len(changes)
                            # price_change is a delta — use running book for current state
                            entry["bid"] = running_book["bid"] if running_book["bid"] > 0 else None
                            entry["ask"] = running_book["ask"] if running_book["ask"] > 0 else None
                        elif evt_type == "book":
                            bids = msg.get("bids", [])
                            asks = msg.get("asks", [])
                            if bids:
                                best_b = max(float(b["price"]) for b in bids)
                                entry["bid"] = best_b
                                running_book["bid"] = best_b
                            if asks:
                                best_a = min(float(a["price"]) for a in asks)
                                entry["ask"] = best_a
                                running_book["ask"] = best_a
                            entry["n_bids"] = len(bids)
                            entry["n_asks"] = len(asks)
                        elif evt_type == "last_trade_price":
                            entry["price"] = float(msg.get("price", 0))
                        elif evt_type == "best_bid_ask":
                            bb = float(msg.get("best_bid", 0))
                            ba = float(msg.get("best_ask", 0))
                            entry["bid"] = bb
                            entry["ask"] = ba
                            if bb > 0:
                                running_book["bid"] = bb
                            if ba > 0:
                                running_book["ask"] = ba
                        elif evt_type == "tick_size_change":
                            entry["tick_size"] = msg.get("tick_size")

                        direct_events.append(entry)
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"[DIRECT] WS error: {e}")

    if redis_task:
        redis_task.cancel()
        try:
            await redis_task
        except asyncio.CancelledError:
            pass

    # Summary
    print("\n" + "=" * 80)
    print("DIRECT WS EVENTS")
    print("=" * 80)
    by_type: dict[str, int] = {}
    for e in direct_events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t:25s} {c:6d}")
    print(f"  {'TOTAL':25s} {len(direct_events):6d}")

    # Print first/last few events
    print(f"\nFirst 10 events:")
    for e in direct_events[:10]:
        _print_event(e)
    if len(direct_events) > 20:
        print(f"\n... ({len(direct_events) - 20} more) ...\n")
        print(f"Last 10 events:")
        for e in direct_events[-10:]:
            _print_event(e)

    # Redis comparison
    if redis_snapshots:
        print("\n" + "=" * 80)
        print("REDIS (MANAGED SLOT) SNAPSHOTS")
        print("=" * 80)
        print(f"  Snapshots collected: {len(redis_snapshots)}")
        for s in redis_snapshots[:5]:
            print(f"  +{s['elapsed']:6.1f}s  bid={s.get('bid','?'):>8s}  ask={s.get('ask','?'):>8s}  "
                  f"ltp={s.get('ltp','?'):>8s}  bba_bid={s.get('bba_bid','?'):>8s}  bba_ask={s.get('bba_ask','?'):>8s}")
        if len(redis_snapshots) > 5:
            print(f"  ... ({len(redis_snapshots) - 5} more)")

    # Side-by-side price comparison at 2s intervals
    if direct_events and redis_snapshots:
        print("\n" + "=" * 80)
        print("SIDE-BY-SIDE (2s intervals) — DIRECT WS vs MANAGED SLOT (BBA/LTP)")
        print(f"{'elapsed':>8s} | {'DIRECT bid':>10s} {'DIRECT ask':>10s} | {'SLOT bba_b':>10s} {'SLOT bba_a':>10s} {'SLOT ltp':>10s} | {'bid gap':>8s}")
        print("-" * 90)

        t0 = direct_events[0]["ts"]
        for interval in range(0, int(duration), 2):
            t_target = t0 + interval
            # Find nearest direct event with price info
            direct_near = _nearest(direct_events, t_target, ["book", "best_bid_ask"])
            if not direct_near:
                direct_near = _nearest(direct_events, t_target, ["price_change"])
            redis_near = _nearest_redis(redis_snapshots, interval)

            d_bid = direct_near.get("bid", "") if direct_near else ""
            d_ask = direct_near.get("ask", "") if direct_near else ""
            r_bba_bid = redis_near.get("bba_bid", "") if redis_near else ""
            r_bba_ask = redis_near.get("bba_ask", "") if redis_near else ""
            r_ltp = redis_near.get("ltp", "") if redis_near else ""

            gap = ""
            if d_bid and r_bba_bid and d_bid != "" and r_bba_bid != "?":
                try:
                    gap = f"{float(d_bid) - float(r_bba_bid):+.4f}"
                except (ValueError, TypeError):
                    pass

            def _fmt(v):
                if v == "" or v == "?":
                    return f"{'—':>10s}"
                try:
                    return f"{float(v):>10.4f}"
                except (ValueError, TypeError):
                    return f"{str(v):>10s}"

            print(f"{interval:>7d}s | {_fmt(d_bid)} {_fmt(d_ask)} | {_fmt(r_bba_bid)} {_fmt(r_bba_ask)} {_fmt(r_ltp)} | {gap:>8s}")


def _print_event(e: dict):
    parts = [f"+{e['elapsed']:7.3f}s  {e['event_type']:20s}"]
    if "bid" in e and e["bid"] is not None:
        parts.append(f"bid={e['bid']:.4f}")
    if "ask" in e and e["ask"] is not None:
        parts.append(f"ask={e['ask']:.4f}")
    if "price" in e:
        parts.append(f"price={e['price']:.4f}")
    if "n_changes" in e:
        parts.append(f"changes={e['n_changes']}")
    if "n_bids" in e:
        parts.append(f"bids={e['n_bids']} asks={e['n_asks']}")
    print(f"  {'  '.join(parts)}")


def _nearest(events, t_target, types):
    best = None
    best_dist = float("inf")
    for e in events:
        if e["event_type"] not in types:
            continue
        dist = abs(e["ts"] - t_target)
        if dist < best_dist:
            best_dist = dist
            best = e
    return best


def _nearest_redis(snapshots, elapsed_target):
    best = None
    best_dist = float("inf")
    for s in snapshots:
        dist = abs(s["elapsed"] - elapsed_target)
        if dist < best_dist:
            best_dist = dist
            best = s
    return best


async def poll_redis(asset_id: str, duration: float, snapshots: list):
    """Poll Redis every 1s for the managed-slot OB + LTP + BBA for this asset."""
    try:
        import msgpack
        r = aioredis.from_url("redis://localhost:6379/0")
        t0 = time.time()

        while time.time() - t0 < duration:
            elapsed = round(time.time() - t0, 1)
            snap: dict = {"elapsed": elapsed}

            # L2 OB from managed slot
            try:
                ob_key = f"ob:{asset_id}"
                ob_raw = await r.get(ob_key)
                if ob_raw:
                    ob = msgpack.unpackb(ob_raw, raw=False)
                    snap["bid"] = str(round(ob.get("best_bid", 0), 4))
                    snap["ask"] = str(round(ob.get("best_ask", 0), 4))
            except Exception:
                pass

            # LTP
            try:
                ltp_key = f"ltp:{asset_id}"
                ltp_raw = await r.get(ltp_key)
                if ltp_raw:
                    ltp = msgpack.unpackb(ltp_raw, raw=False)
                    snap["ltp"] = str(round(ltp.get("price", 0), 4))
            except Exception:
                pass

            # BBA tip
            try:
                tip_key = f"tip:{asset_id}"
                tip_raw = await r.get(tip_key)
                if tip_raw:
                    tip = msgpack.unpackb(tip_raw, raw=False)
                    snap["bba_bid"] = str(round(tip.get("best_bid", 0), 4))
                    snap["bba_ask"] = str(round(tip.get("best_ask", 0), 4))
            except Exception:
                pass

            snapshots.append(snap)
            await asyncio.sleep(1.0)

        await r.close()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[REDIS] Error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare direct WS vs managed-slot data")
    parser.add_argument("asset_id", help="Polymarket asset ID to monitor")
    parser.add_argument("--duration", type=float, default=60, help="Duration in seconds (default 60)")
    args = parser.parse_args()
    asyncio.run(run(args.asset_id, args.duration))
