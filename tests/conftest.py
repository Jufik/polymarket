"""Shared test fixtures."""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_broker() -> AsyncMock:
    """Shared mock broker for ingestor tests."""
    broker = AsyncMock()
    broker.publish = AsyncMock()
    return broker
