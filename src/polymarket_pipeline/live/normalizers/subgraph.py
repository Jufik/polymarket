"""Normalizer for Goldsky Subgraph orderFilledEvent responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source
from polymarket_pipeline.trade_id import make_trade_id_chain

log = structlog.get_logger()


class SubgraphNormalizer:
    """Normalizes Goldsky Subgraph orderFilledEvent JSON into NormalizedTrade."""

    def __init__(self, token_market_map: dict[str, tuple[str, str]]) -> None:
        self._token_map = token_market_map
        self._unknown_assets: set[str] = set()

    def normalize(self, event: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single subgraph orderFilledEvent.

        Args:
            event: Raw GraphQL response dict with camelCase keys
                   (makerAssetId, takerAssetId, makerAmountFilled, etc.).

        Returns:
            NormalizedTrade or None if this is a taker-perspective duplicate.
        """
        taker = event["taker"]
        if taker.lower() in EXCHANGE_ADDRS:
            return None

        maker_asset_id = event["makerAssetId"]
        taker_asset_id = event["takerAssetId"]

        # BUY: maker provides USDC (makerAssetId=0), taker gets tokens
        is_buy = maker_asset_id == "0"
        if is_buy:
            asset_id = taker_asset_id
            usdc_raw = int(event["makerAmountFilled"])
            token_amount = int(event["takerAmountFilled"])
        else:
            asset_id = maker_asset_id
            usdc_raw = int(event["takerAmountFilled"])
            token_amount = int(event["makerAmountFilled"])

        amount_usd = Decimal(usdc_raw) / USDC_SCALE
        fee_usd = Decimal(int(event["fee"])) / USDC_SCALE
        size = Decimal(token_amount) / USDC_SCALE
        price = (amount_usd / size).quantize(Decimal("0.0001")) if size > 0 else Decimal("0")

        mapping = self._token_map.get(asset_id)
        if mapping is None:
            if asset_id not in self._unknown_assets:
                self._unknown_assets.add(asset_id)
                log.warning("subgraph.unknown_asset_id", asset_id=asset_id)
            condition_id = asset_id  # fallback: use asset_id as condition_id
        else:
            condition_id, _ = mapping

        tx_hash = event["transactionHash"]
        order_hash = event.get("orderHash", "")

        return NormalizedTrade(
            trade_id=make_trade_id_chain(tx_hash=tx_hash, order_hash=order_hash),
            condition_id=condition_id,
            asset_id=asset_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=fee_usd,
            maker=event["maker"],
            taker=taker,
            timestamp=datetime.fromtimestamp(int(event["timestamp"]), tz=UTC),
            source=Source.GOLDSKY_SUBGRAPH,
            tx_hash=tx_hash,
            order_hash=order_hash,
            block_number=None,
            is_backfill=False,
            version=2,
        )
