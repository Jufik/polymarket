"""Backward-compat shim — re-exports from pm_ingest."""
from pm_ingest.ingestors.rpc import (  # noqa: F401
    _DEDUP_TTL_S,
    _PUBLISH_WORKERS,
    _QUEUE_MAXSIZE,
    _STALE_TIMEOUT,
    CTF_EXCHANGE,
    NEGRISK_EXCHANGE,
    QUESTION_RESOLVED_SIG,
    RECONNECT_BASE,
    RECONNECT_MAX,
    RPCIngestor,
)
