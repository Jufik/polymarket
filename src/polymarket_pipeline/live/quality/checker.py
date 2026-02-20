"""Health check implementations for the data quality gate."""

from __future__ import annotations

import time
from typing import Any

import structlog

from polymarket_pipeline.live.quality.state import CheckResult, ReadinessState
from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()


class QualityChecker:
    """Runs health checks and manages pipeline readiness state."""

    def __init__(self, settings: Settings, clickhouse: Any) -> None:
        self._settings = settings
        self._ch = clickhouse
        self._state = ReadinessState()
        self._heartbeats: dict[str, float] = {}

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

    def record_heartbeat(self, source: str, ts: float) -> None:
        """Record a heartbeat from an ingestor source."""
        self._heartbeats[source] = ts

    def check_source_liveness(self) -> CheckResult:
        """Check that all ingestor sources are reporting heartbeats."""
        now = time.time()
        timeout = self._settings.source_liveness_timeout_s

        required = ["rtds", "alchemy"]
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
                "SELECT count() as cnt FROM trades_raw "
                "WHERE timestamp > now() - INTERVAL 1 HOUR"
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
                return CheckResult(
                    ok=False,
                    reason=f"Volume {ratio:.1%} of average (< {self._settings.volume_drop_red_pct:.0%})",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Query error: {e}")

    def check_metadata_freshness(self) -> CheckResult:
        """Check that token_map has coverage for recent trades."""
        # Placeholder -- will be implemented when metadata sync is integrated
        return CheckResult(ok=True)

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
                return CheckResult(
                    ok=False,
                    reason=f"Enrichment ratio {ratio:.1%} < {self._settings.enrichment_ratio_min:.0%}",
                )
            return CheckResult(ok=True)
        except Exception as e:
            return CheckResult(ok=False, reason=f"Query error: {e}")

    def check_resolved_completeness(self) -> CheckResult:
        """Check that closed markets have trades in ClickHouse."""
        # Placeholder -- will be implemented when PostgreSQL metadata is integrated
        return CheckResult(ok=True)

    def run_all_checks(self) -> dict[str, CheckResult]:
        """Run all health checks and update readiness state."""
        results = {
            "source_liveness": self.check_source_liveness(),
            "volume_reconciliation": self.check_volume_reconciliation(),
            "metadata_freshness": self.check_metadata_freshness(),
            "dedup_sanity": self.check_dedup_sanity(),
            "resolved_completeness": self.check_resolved_completeness(),
        }
        self._state.update(results)
        log.info(
            "quality.check_complete",
            state=self._state.current,
            failures=self._state.failures,
        )
        return results
