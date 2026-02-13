"""Real row samples from order_filled/ Parquet files (via fastparquet).

These were captured from the actual Goldsky Sink data. The bytes values
for transaction_hash and order_hash are real.
"""

# BUY trade: maker provides USDC (maker_asset_id == "0")
SINK_ROW_BUY: dict = {
    "vid": 63022,
    "block_range": "[41062881,)",
    "id": "some-unique-id-buy",
    "transaction_hash": bytes.fromhex(
        "bbcfa118b585eace1e34171583d72320c9a75d36a32e935f063d018c1ce20213"
    ),
    "timestamp": 1680452705.0,  # 2023-04-02 16:25:05 UTC
    "order_hash": bytes.fromhex("aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666777788889999aabb"),
    "maker": "0xa4a6fcb5df72529d4a",
    "taker": "0x1e057fb222bf2fdcb8",
    "maker_asset_id": "0",
    "taker_asset_id": (
        "46434110155841033529384949983718980438706543876953886750286883506638610790525"
    ),
    "maker_amount_filled": 110_000_000.0,  # 110 USDC (6 decimals)
    "taker_amount_filled": 200_000_000.0,  # 200 tokens
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "996a321be875025713244d9377ada141",
}

# SELL trade: maker provides tokens (maker_asset_id != "0")
SINK_ROW_SELL: dict = {
    "vid": 202299,
    "block_range": "[47861179,)",
    "id": "some-unique-id-sell",
    "transaction_hash": bytes.fromhex(
        "7fe3e09d2c1dfeca72f62f3a780cb1352b066d94c5976a31f20a3a135915a1c1"
    ),
    "timestamp": 1695411604.0,  # 2023-09-22 19:40:04 UTC
    "order_hash": bytes.fromhex("1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"),
    "maker": "0x6cf31245801f2a053b",
    "taker": "0x08adb952cede72402b",
    "maker_asset_id": (
        "46434110155841033529384949983718980438706543876953886750286883506638610790525"
    ),
    "taker_asset_id": "0",
    "maker_amount_filled": 117_440_000.0,  # 117.44 tokens
    "taker_amount_filled": 91_560_000.0,  # 91.56 USDC
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "bf2929897e3cfc24ed3ff9443f5de31",
}

# Taker-focused DUPLICATE: taker is CTF Exchange contract
SINK_ROW_DUP_CTF: dict = {
    "vid": 62955,
    "block_range": "[41060688,)",
    "id": "some-unique-id-dup-ctf",
    "transaction_hash": bytes.fromhex(
        "f1eb2777da76fac15875a7997d1732928d1d7b38eb557a160bd0469a1568a36e"
    ),
    "timestamp": 1680447830.0,
    "order_hash": bytes.fromhex("ccccddddeeeeffffaaaabbbb000011112222333344445555666677778888cccc"),
    "maker": "0xa4a6fcb5df72529d4a",
    "taker": "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
    "maker_asset_id": "0",
    "taker_asset_id": "12345",
    "maker_amount_filled": 300_000_000.0,
    "taker_amount_filled": 600_000_000.0,
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "75addd6d3729fe2845af6eeb5e4a2de3",
}

# Taker-focused DUPLICATE: taker is NegRisk CTF Exchange
SINK_ROW_DUP_NEGRISK: dict = {
    "vid": 62933,
    "block_range": "[41059717,)",
    "id": "some-unique-id-dup-neg",
    "transaction_hash": bytes.fromhex(
        "d09a2fed582c55722685e81ff2ecd8019ae8e96f4a47a7f523d5c4e50cf5146b"
    ),
    "timestamp": 1680445702.0,
    "order_hash": bytes.fromhex("ddddeeeeffff000011112222333344445555666677778888999900001111aaaa"),
    "maker": "0x6c7eafee6f03867c0b",
    "taker": "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # NegRisk
    "maker_asset_id": "0",
    "taker_asset_id": "67890",
    "maker_amount_filled": 200_000_000.0,
    "taker_amount_filled": 408_160_000.0,
    "fee": 0.0,
    "_gs_chain": "matic",
    "_gs_gid": "8b8c516a640f493d627f48342dcc37ed",
}
