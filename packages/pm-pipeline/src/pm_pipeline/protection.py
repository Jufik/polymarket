"""Auto-protect: close all positions when pipeline enters RED state."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from pm_pipeline.quality.state import PipelineState

if TYPE_CHECKING:
    from pm_pipeline.quality.checker import QualityChecker
    from pm_pipeline.settings import Settings

log = structlog.get_logger()

_lock = asyncio.Lock()


async def auto_protect(checker: QualityChecker, settings: Settings) -> None:
    """Automatically close all positions when pipeline state is RED.

    Uses module-level lock to prevent re-entrancy.
    """
    async with _lock:
        # Skip if already closing/stopped
        if checker.state.current in (PipelineState.CLOSING, PipelineState.SAFE_STOP):
            log.info("auto_protect.already_closing", state=checker.state.current.value)
            return

        checker.state.set_closing()
        log.warning("auto_protect.triggered", state="CLOSING")

        try:
            import asyncpg

            from polymarket_pipeline.execution.clob_client import ClobClient
            from polymarket_pipeline.execution.panic import panic_close_all
            from polymarket_pipeline.execution.position_tracker import PositionTracker

            async with ClobClient(
                base_url=settings.clob_api_url,
                api_key=settings.clob_api_key,
                api_secret=settings.clob_api_secret,
                api_passphrase=settings.clob_api_passphrase,
            ) as clob:
                pool = await asyncpg.create_pool(dsn=settings.pg_dsn)
                if pool is None:
                    log.error("auto_protect.pool_creation_failed")
                    return
                tracker = PositionTracker(pool=pool)
                await tracker.initialize()
                results = await panic_close_all(clob, tracker)
                await pool.close()

            success = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)
            log.warning("auto_protect.complete", closed=success, failed=failed)

            if failed == 0:
                checker.state.set_safe_stop()
                log.warning("auto_protect.safe_stop")
            else:
                log.error("auto_protect.some_failures", failed=failed)
        except Exception:
            log.exception("auto_protect.error")
