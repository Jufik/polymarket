"""CLI entry point for the exchange feed ingestor (standalone process)."""

from __future__ import annotations

import asyncio

import structlog
from faststream.kafka import KafkaBroker

from polymarket_pipeline.live.settings import Settings

log = structlog.get_logger()


async def _run() -> None:
    settings = Settings()
    broker = KafkaBroker(settings.redpanda_url, compression_type="lz4", linger_ms=5)
    async with broker:
        from polymarket_pipeline.live.ingestors.exchange_feed import ExchangeFeedIngestor

        symbols = [s.strip() for s in settings.exchange_feed_symbols.split(",") if s.strip()]
        exchanges = [e.strip() for e in settings.exchange_feed_exchanges.split(",") if e.strip()]

        ingestor = ExchangeFeedIngestor(
            broker=broker,
            topic=settings.exchange_feed_topic,
            status_topic="pipeline.status",
            symbols=symbols,
            exchanges=exchanges,
            bar_interval_s=settings.exchange_feed_bar_interval_s,
            flush_delay_s=settings.exchange_feed_flush_delay_s,
        )

        log.info("pm-exchange.starting", symbols=symbols, exchanges=exchanges)
        await ingestor.run()


def main() -> None:
    """Run the exchange feed ingestor as a standalone process."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
