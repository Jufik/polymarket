"""pm-panic CLI -- emergency position close."""

from __future__ import annotations

import asyncio
import sys

import structlog

log = structlog.get_logger()


def main() -> None:
    """Close all positions immediately."""
    asyncio.run(_run())


async def _run() -> None:
    from polymarket_pipeline.execution.clob_client import ClobClient
    from polymarket_pipeline.execution.panic import panic_close_all
    from polymarket_pipeline.execution.position_tracker import PositionTracker
    from polymarket_pipeline.live.settings import Settings

    settings = Settings()

    import asyncpg

    pool = await asyncpg.create_pool(dsn=settings.pg_dsn)

    tracker = PositionTracker(pool=pool)
    await tracker.initialize()

    positions = tracker.get_all_positions()
    if not positions:
        log.info("panic.no_positions")
        print("No open positions. Nothing to close.")
        await pool.close()
        return

    print(f"PANIC: Closing {len(positions)} position(s)...")
    for p in positions:
        print(f"  {p.condition_id}: {p.side} {p.size:.4f} @ {p.avg_entry:.4f}")

    async with ClobClient(
        base_url=settings.clob_api_url,
        api_key=settings.clob_api_key,
        api_secret=settings.clob_api_secret,
        api_passphrase=settings.clob_api_passphrase,
    ) as clob:
        results = await panic_close_all(clob, tracker)

    success = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    print(f"Done: {success} closed, {failed} failed")
    if failed > 0:
        sys.exit(1)

    await pool.close()
