"""SharedMemoryContext: reads orderbooks from SharedMemory, everything else in-memory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pm_strategy.context.memory import InMemoryContext
from pm_strategy.types import OrderbookSnapshot

if TYPE_CHECKING:
    from pm_core.protocols import BookReader
    from pm_core.token_map import TokenMap


class SharedMemoryContext(InMemoryContext):
    """StrategyContext backed by SharedMemory for orderbooks.

    Reads orderbook snapshots from a SharedMemory-backed BookReader
    for sub-microsecond reads on the hot path. Falls back to the
    in-memory dict if the BookReader returns None.
    """

    def __init__(self, book_reader: BookReader, token_map: TokenMap) -> None:
        super().__init__()
        self._book = book_reader
        self._token_map = token_map

    async def get_orderbook_by_asset(self, asset_id: str) -> OrderbookSnapshot | None:  # type: ignore[override]
        raw = self._book.get(asset_id)
        if raw is None:
            return super().get_orderbook_by_asset(asset_id)
        return OrderbookSnapshot(**raw)

    async def get_orderbook(self, condition_id: str) -> OrderbookSnapshot | None:
        yes_asset = self._token_map.lookup_yes_asset(condition_id)
        if yes_asset is not None:
            ob = await self.get_orderbook_by_asset(yes_asset)
            if ob is not None:
                return ob
        return await super().get_orderbook(condition_id)
