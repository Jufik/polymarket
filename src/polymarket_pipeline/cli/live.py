"""CLI entry point for the live sync pipeline."""

from __future__ import annotations


def main() -> None:
    """Run the live pipeline with monitoring dashboard.

    Uses uvicorn to serve the ASGI app (FastStream + dashboard routes).
    """
    import uvicorn

    from polymarket_pipeline.live.app import asgi_app, settings

    uvicorn.run(
        asgi_app,
        host="0.0.0.0",
        port=settings.dashboard_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
