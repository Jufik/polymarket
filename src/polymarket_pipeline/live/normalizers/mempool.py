"""Normalizer for decoded mempool fillOrder calldata dicts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from polymarket_pipeline.constants import EXCHANGE_ADDRS, USDC_SCALE
from polymarket_pipeline.models import NormalizedTrade, Side, Source


class MempoolNormalizer:
    """Normalizes decoded mempool trade dicts into NormalizedTrade.

    Input dicts are produced by the Rust PyO3 sidecar (polymarket_mempool).
    """

    def __init__(
        self,
        token_market_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._token_map = token_market_map or {}

    def normalize(self, raw: dict[str, Any]) -> NormalizedTrade | None:
        """Normalize a single decoded mempool trade.

        Args:
            raw: Dict with keys: tx_hash, maker, taker, token_id,
                 maker_amount, taker_amount, fee_rate_bps, side,
                 expiration, seen_at.

        Returns:
            NormalizedTrade or None if unknown token or taker duplicate.
        """
        token_id = raw["token_id"]

        # Must resolve condition_id via token_map
        if token_id not in self._token_map:
            return None
        condition_id = self._token_map[token_id][0]

        # Drop taker-perspective duplicates
        taker = raw["taker"].lower()
        if taker in EXCHANGE_ADDRS:
            return None

        maker = raw["maker"].lower()

        # Side from calldata: 0=BUY, 1=SELL
        is_buy = raw["side"] == 0

        if is_buy:
            # BUY: taker pays USDC (taker_amount), maker provides tokens (maker_amount)
            usdc_raw = raw["taker_amount"]
            token_raw = raw["maker_amount"]
        else:
            # SELL: maker pays USDC (maker_amount), taker provides tokens (taker_amount)
            usdc_raw = raw["maker_amount"]
            token_raw = raw["taker_amount"]

        amount_usd = Decimal(usdc_raw) / USDC_SCALE
        size = Decimal(token_raw) / USDC_SCALE
        price = (amount_usd / size).quantize(Decimal("0.0001")) if size > 0 else Decimal("0")

        # Deterministic trade_id: mempool:{sha256(tx_hash)[:16]}
        digest = sha256(raw["tx_hash"].encode()).hexdigest()[:16]
        trade_id = f"mempool:{digest}"

        timestamp = datetime.fromtimestamp(raw["seen_at"], tz=UTC)

        return NormalizedTrade(
            trade_id=trade_id,
            condition_id=condition_id,
            asset_id=token_id,
            side=Side.BUY if is_buy else Side.SELL,
            price=price,
            size=size,
            amount_usd=amount_usd,
            fee_usd=Decimal("0"),  # fee not yet charged (pending tx)
            maker=maker,
            taker=taker,
            timestamp=timestamp,
            source=Source.MEMPOOL,
            tx_hash=raw["tx_hash"],
            order_hash=None,  # not available from calldata
            block_number=None,  # not mined yet
            is_backfill=False,
            version=0,  # lowest priority: mempool(0) < off-chain(1) < on-chain(2)
        )
