"""Shared constants for the Polymarket pipeline."""

from decimal import Decimal

# CTF Exchange contract addresses (used to detect taker-focused duplicates)
EXCHANGE_ADDRS: frozenset[str] = frozenset(
    {
        "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
        "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk CTF Exchange
    }
)

# FeeModule contracts — operators route matchOrders through these proxies
FEE_MODULE_ADDRS: frozenset[str] = frozenset(
    {
        "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0",  # Polymarket: Fee Module (94%)
        "0xb768891e3130f6df18214ac804d4db76c2c37730",  # Secondary router (6%)
        "0x78769d50be1763ed1ca0d5e878d93f05aabff29e",  # Polymarket: Neg Risk Fee Module
    }
)

# USDC uses 6 decimals (1e6), NOT 1e18
USDC_SCALE = Decimal("1000000")
