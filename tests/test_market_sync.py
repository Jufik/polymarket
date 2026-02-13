"""Tests for Gamma API market syncer."""

import pytest

from polymarket_pipeline.market_sync import fetch_token_market_map


@pytest.mark.integration
async def test_fetch_token_market_map() -> None:
    """Integration test — fetches real data from Gamma API."""
    token_map = await fetch_token_market_map(limit=50)

    # Should have at least some mappings
    assert len(token_map) > 50

    # Each entry should map to (condition_id, outcome)
    for asset_id, (condition_id, outcome) in token_map.items():
        assert isinstance(asset_id, str)
        assert len(asset_id) > 10  # Token IDs are long numbers
        assert condition_id.startswith("0x")
        assert outcome in ("YES", "NO")
