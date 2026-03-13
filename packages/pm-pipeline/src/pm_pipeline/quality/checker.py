"""Health check implementations for the data quality gate."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from pm_pipeline.quality.state import CheckResult, ReadinessState
from pm_pipeline.settings import Settings

log = structlog.get_logger()


class QualityChecker:
    """Runs health checks and manages pipeline readiness state."""

    def __init__(self, settings: Settings, clickhouse: Any, pg_pool: Any = None) -> None:
        self._settings = settings
        self._ch = clickhouse
        self._pg_pool = pg_pool
        self._state = ReadinessState(degraded_grace_s=settings.degraded_grace_s)
        self._heartbeats: dict[str, float] = {}
        self._last_quality_message: dict[str, Any] | None = None

    @property
    def state(self) -> ReadinessState:
        return self._state

    @property
    def heartbeats(self) -> dict[str, float]:
        """Current heartbeat timestamps per source."""
        return dict(self._heartbeats)

    @property
    def clickhouse(self) -> Any:
        """ClickHouse sink instance."""
        return self._ch

    @property
    def liveness_timeout_s(self) -> int:
        """Source liveness timeout in seconds."""
        return self._settings.source_liveness_timeout_s

    @property
    def last_quality_message(self) -> dict[str, Any] | None:
        """Last serialized quality state message (for publishing)."""
        return self._last_quality_message

    def to_quality_message(self) -> dict[str, Any]:
        """Serialize current quality state for the pipeline.quality topic."""
        return {
            "event": "quality_state",
            "state": self._state.current.value,
            "failures": self._state.failures,
            "degraded_since": self._state.degraded_since,
            "time_until_red": self._state.time_until_red,
            "heartbeats": dict(self._heartbeats),
            "ts": time.time(),
        }

    def record_heartbeat(self, source: str, ts: float) -> None:
        """Record a heartbeat from an ingestor source."""
        self._heartbeats[source] = ts

    def check_source_liveness(self) -> CheckResult:
        """Check that all ingestor sources are reporting heartbeats."""
        now = time.time()
        timeout = self._settings.source_liveness_timeout_s

        required = ["rtds", "alchemy"]
        # Mempool is optional — only check if we've ever seen a heartbeat
        if "mempool" in self._heartbeats:
            required.append("mempool")
        stale = []
        for src in required:
            last = self._heartbeats.get(src)
            if last is None:
                stale.append(f"{src} (no heartbeat)")
            elif now - last > timeout:
                stale.append(f"{src} ({now - last:.0f}s ago)")

        if stale:
            return CheckResult(ok=False, reason=f"Stale sources: {', '.join(stale)}")
        return CheckResult(ok=True)

    def check_volume_reconciliation(self) -> CheckResult:
        """Check current hour volume against trailing 24h average."""
        try:
            result = self._ch.query(
                "SELECT count() as cnt FROM trades_raw WHERE timestamp > now() - INTERVAL 1 HOUR"
            )
            current = result[0]["cnt"] if result else 0

            result_24h = self._ch.query(
                "SELECT count() / 24 as avg_hourly FROM trades_raw "
                "WHERE timestamp > now() - INTERVAL 24 HOUR"
            )
            avg_hourly = result_24h[0]["avg_hourly"] if result_24h else 0

            if avg_hourly == 0:
                return CheckResult(ok=True, reason="No 24h baseline yet")

            ratio = current / avg_hourly
            if ratio < self._settings.volume_drop_red_pct:
                threshold = self._settings.volume_drop_red_pct
                return CheckResult(
                    ok=False,
                    reason=f"Volume {ratio:.1%} of average (< {threshold:.0%})",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Query error: {e}")

    async def check_metadata_freshness(self) -> CheckResult:
        """Check that token_market_map was synced recently.

        Verifies the metadata sync pipeline (Gamma API -> PostgreSQL) ran within
        the configured threshold.  If no PG pool is available, returns ok (degraded
        monitoring is better than false alarms during bootstrap).
        """
        if self._pg_pool is None:
            return CheckResult(ok=True, reason="no PG pool — skipping metadata check")
        try:
            count = await self._pg_pool.fetchval(
                "SELECT count(*) FROM token_market_map "
                "WHERE updated_at > NOW() - INTERVAL '2 hours'"
            )
            if count == 0:
                return CheckResult(ok=False, reason="token_market_map has no entries updated in 2h")
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Metadata query error: {e}")

    def check_dedup_sanity(self) -> CheckResult:
        """Check version=2/version=1 enrichment ratio."""
        try:
            result = self._ch.query(
                "SELECT "
                "  countIf(_version = 2) as v2, "
                "  countIf(_version = 1) as v1 "
                "FROM trades_raw "
                "WHERE timestamp > now() - INTERVAL 1 HOUR"
            )
            row = result[0] if result else {"v1": 0, "v2": 0}
            total = row["v1"] + row["v2"]
            if total == 0:
                return CheckResult(ok=True, reason="No recent trades")

            ratio = row["v2"] / total
            if ratio < self._settings.enrichment_ratio_min:
                min_ratio = self._settings.enrichment_ratio_min
                return CheckResult(
                    ok=False,
                    reason=f"Enrichment ratio {ratio:.1%} < {min_ratio:.0%}",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Query error: {e}")

    async def check_resolved_completeness(self) -> CheckResult:
        """Check that resolved markets in PG have trade coverage in ClickHouse.

        Returns ok=True if no PG pool is available (graceful degradation).
        """
        if self._pg_pool is None:
            return CheckResult(ok=True, reason="no PG pool — skipping resolved check")
        try:
            pg_count = await self._pg_pool.fetchval(
                "SELECT count(*) FROM markets WHERE status = 'resolved'"
            )
            if pg_count == 0:
                return CheckResult(ok=True, reason="No resolved markets in PG")
            ch_result = self._ch.query(
                "SELECT uniqExact(condition_id) AS cnt FROM trades_raw FINAL "
                "WHERE condition_id IN ("
                "  SELECT condition_id FROM markets WHERE status = 'resolved'"
                ")"
            )
            ch_count = ch_result[0]["cnt"] if ch_result else 0
            ratio = ch_count / pg_count
            if ratio < 0.90:
                return CheckResult(
                    ok=False,
                    reason=f"Resolved coverage {ratio:.0%} ({ch_count}/{pg_count})",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Resolved query error: {e}")

    async def run_all_checks(self) -> dict[str, CheckResult]:
        """Run all health checks and update readiness state."""
        source_liveness = self.check_source_liveness()

        # I/O checks run concurrently
        (
            volume_reconciliation,
            dedup_sanity,
            metadata_freshness,
            resolved_completeness,
        ) = await asyncio.gather(
            asyncio.to_thread(self.check_volume_reconciliation),
            asyncio.to_thread(self.check_dedup_sanity),
            self.check_metadata_freshness(),
            self.check_resolved_completeness(),
        )

        results = {
            "source_liveness": source_liveness,
            "volume_reconciliation": volume_reconciliation,
            "metadata_freshness": metadata_freshness,
            "dedup_sanity": dedup_sanity,
            "resolved_completeness": resolved_completeness,
        }
        self._state.update(results)
        self._last_quality_message = self.to_quality_message()
        log.info(
            "quality.check_complete",
            state=self._state.current,
            failures=self._state.failures,
        )
        return results
