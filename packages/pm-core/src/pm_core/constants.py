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

# UMA adapters for on-chain resolution detection
UMA_CTF_ADAPTER_V3 = "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49"
NEGRISK_UMA_ADAPTER = "0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d"

# CTF Conditional Tokens contract (splits, merges, redemptions)
CTF_TOKEN_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# NegRisk adapter (multi-outcome conversions)
NEGRISK_ADAPTER = "0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"

# CTF event signatures (verified from on-chain tx receipts)
POSITION_SPLIT_SIG = "0x2e6bb91f8cbcda0c93623c54d0403a43514fabc40084ec96b6d5379a74786298"
POSITIONS_MERGE_SIG = "0x6f13ca62553fcc2bcd2372180a43949c1e4cebba603901ede2f4e14f36b282ca"
PAYOUT_REDEMPTION_SIG = "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
POSITIONS_CONVERTED_SIG = "0xb03d19dddbc72a87e735ff0ea3b57bef133ebe44e1894284916a84044deb367e"

# USDC uses 6 decimals (1e6), NOT 1e18
USDC_SCALE = Decimal("1000000")
