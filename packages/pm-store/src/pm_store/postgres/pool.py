"""asyncpg pool lifecycle management."""

from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped]


class PGPool:
    """Wrapper around asyncpg connection pool with lifecycle management.

    Usage::

        pool = PGPool(dsn="postgresql://...")
        await pool.connect()
        async with pool.acquire() as conn:
            await conn.fetch("SELECT 1")
        await pool.close()

    Or as async context manager::

        async with PGPool(dsn) as pool:
            ...
    """

    def __init__(
        self,
        dsn: str = "",
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Create the connection pool."""
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=self._min_size, max_size=self._max_size
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> PGPool:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def acquire(self) -> Any:
        """Acquire a connection from the pool. Use as async context manager."""
        if not self._pool:
            raise RuntimeError("PGPool not connected — call connect() first")
        return self._pool.acquire()

    @property
    def pool(self) -> asyncpg.Pool | None:
        """Direct access to the underlying asyncpg pool."""
        return self._pool

    @property
    def connected(self) -> bool:
        """Whether the pool is connected."""
        return self._pool is not None
