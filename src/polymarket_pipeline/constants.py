"""Shared constants for the Polymarket pipeline."""

from decimal import Decimal

# CTF Exchange contract addresses (used to detect taker-focused duplicates)
EXCHANGE_ADDRS: frozenset[str] = frozenset(
    {
        "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
        "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
    }
)

# USDC uses 6 decimals (1e6), NOT 1e18
USDC_SCALE = Decimal("1000000")
