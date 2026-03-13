"""Backward-compat shim — re-exports from pm_ingest."""
from pm_ingest.ingestors.rtds import (  # noqa: F401
    PING_INTERVAL,
    RECONNECT_BASE,
    RECONNECT_MAX,
    RTDS_URL,
    RTDSIngestor,
)
