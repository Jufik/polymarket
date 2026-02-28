"""Backward-compatibility shim — AlchemyIngestor is now RPCIngestor."""

from polymarket_pipeline.live.ingestors.rpc import RPCIngestor as AlchemyIngestor

__all__ = ["AlchemyIngestor"]
