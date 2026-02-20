"""Goldsky Sink Parquet normalizer.

Reads rows from fastparquet DataFrames and produces NormalizedTrade instances.

IMPORTANT: Only fastparquet can read these files. pyarrow fails on DECIMAL(100,18)
and DuckDB casts to lossy DOUBLE. See docs/plans/ for empirical evidence.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain


class GoldskySinkNormalizer:
    """Normalizes Goldsky Sink Parquet rows into NormalizedTrade."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        """Args:
        token_market_map: asset_id -> (condition_id, outcome).
        """
        self._token_map = token_market_map

    def normalize(self, raw: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single Parquet row. Returns None for taker-focused duplicates."""
        # 1. Drop taker-focused duplicates
        if raw["taker"].lower() in EXCHANGE_ADDRS:
            return None

        # 2. Determine side and extract amounts
        is_buy = str(raw["maker_asset_id"]) == "0"
        usdc_raw = raw["maker_amount_filled"] if is_buy else raw["taker_amount_filled"]
        token_raw = raw["taker_amount_filled"] if is_buy else raw["maker_amount_filled"]
        token_asset_id = str(raw["taker_asset_id"] if is_buy else raw["maker_asset_id"])

        # 3. Scale amounts (USDC uses 6 decimals)
        usdc = Decimal(str(usdc_raw)) / USDC_SCALE
        tokens = Decimal(str(token_raw)) / USDC_SCALE
        price = (usdc / tokens).quantize(Decimal("0.0001")) if tokens else Decimal(0)
        fee = Decimal(str(raw["fee"])) / USDC_SCALE

        # 4. Convert byte fields to hex strings
        tx_hash = "0x" + raw["transaction_hash"].hex()
        order_hash = "0x" + raw["order_hash"].hex()

        # 5. Generate trade_id
        trade_id = make_trade_id_chain(tx_hash=tx_hash, order_hash=order_hash)

        # 6. Map token -> market
        condition_id, _ = self._token_map.get(token_asset_id, ("unknown", "unknown"))

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=condition_id,
            asset_id=token_asset_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=tokens,
            amount_usd=usdc,
            fee_usd=fee,
            maker=raw["maker"],
            taker=raw["taker"],
            timestamp=datetime.fromtimestamp(raw["timestamp"], tz=UTC),
            source=Source.GOLDSKY_SINK,
            tx_hash=tx_hash,
            order_hash=order_hash,
            block_number=None,
            is_backfill=True,
            version=2,
        )
