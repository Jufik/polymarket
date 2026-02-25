"""Validate achievable execution prices against backtest assumptions.

Compares the entry prices assumed in vectorized backtests against actual
CLOB orderbook depth for the same markets.

Usage:
    uv run python scripts/validate_execution_prices.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import polars as pl
import structlog

logger = structlog.get_logger(__name__)


async def fetch_orderbook(
    client: httpx.AsyncClient,
    condition_id: str,
    *,
    asset_id: str | None = None,
) -> dict | None:
    """Fetch orderbook from CLOB API for a given market."""
    url = "https://clob.polymarket.com/book"
    params = {"token_id": asset_id or condition_id}
    try:
        resp = await client.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("orderbook.fetch_failed", condition_id=condition_id, error=str(e))
        return None


def compute_achievable_price(
    book: dict,
    side: str,
    size_usd: float,
) -> float | None:
    """Walk the orderbook to compute volume-weighted achievable price."""
    if side == "BUY":
        levels = book.get("asks", [])
    else:
        levels = book.get("bids", [])

    if not levels:
        return None

    remaining = size_usd
    total_cost = 0.0

    for level in levels:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        level_value = price * size

        if level_value >= remaining:
            total_cost += remaining
            remaining = 0
            break
        else:
            total_cost += level_value
            remaining -= level_value

    if remaining > 0:
        return None  # insufficient liquidity

    return total_cost / size_usd


async def main() -> None:
    # Load recent signals from a backtest run
    signals_path = Path("data/backtest_signals.parquet")
    if not signals_path.exists():
        print(f"No signals file at {signals_path}.")
        print("Run a backtest first and save signals to this path.")
        return

    signals = pl.read_parquet(signals_path)

    # Sample up to 50 signals
    sample = signals.sample(min(50, len(signals)), seed=42)

    results = []
    async with httpx.AsyncClient() as client:
        for row in sample.iter_rows(named=True):
            cid = row["condition_id"]
            side = row.get("side", "BUY")
            size = row.get("size_usd", 50.0)
            bt_price = row.get("entry_price", None)

            book = await fetch_orderbook(client, cid)
            if book is None:
                continue

            achievable = compute_achievable_price(book, side, size)

            results.append({
                "condition_id": cid,
                "backtest_price": bt_price,
                "achievable_price": achievable,
                "side": side,
                "size_usd": size,
                "slippage": (achievable - bt_price) if (achievable and bt_price) else None,
            })

            # Rate limit
            await asyncio.sleep(0.2)

    if not results:
        print("No results collected.")
        return

    df = pl.DataFrame(results)
    print(df.describe())

    # Summary stats
    valid = df.filter(pl.col("slippage").is_not_null())
    if not valid.is_empty():
        print(f"\nSlippage stats ({len(valid)} markets):")
        print(f"  Median: {valid['slippage'].median():.4f}")
        print(f"  Mean:   {valid['slippage'].mean():.4f}")
        print(f"  P95:    {valid['slippage'].quantile(0.95):.4f}")

    # Save
    out = Path("data/execution_price_validation.parquet")
    df.write_parquet(out)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
