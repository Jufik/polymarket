//! Polygon PoS chain specification for devp2p handshake.
//!
//! The critical elements for peer connectivity:
//! 1. Genesis hash must match exactly (peers verify during Status msg)
//! 2. ForkID (EIP-2124) must be compatible at current chain height
//! 3. Chain ID must be 137
//!
//! IMPORTANT: Bor's ForkID only includes `*Block` fields from `ChainConfig`
//! (the Go struct). Bor-specific forks (Jaipur, Delhi, Indore, etc.) live in
//! a separate `BorConfig` struct and are NOT part of the ForkID computation.
//! The ForkID forks are: Istanbul, MuirGlacier (deduped with Istanbul),
//! Berlin, London, Shanghai, Cancun, Prague — all block-based.
//! Expected ForkHash at head > Prague: 0x22d523b2, next: 0.

use alloy_genesis::Genesis;
use alloy_hardforks::ForkCondition;
use alloy_primitives::{b256, B256};
use reth_ethereum::chainspec::ChainSpec;
use reth_ethereum_forks::EthereumHardfork;
use reth_network_peers::NodeRecord;
use reth_primitives_traits::SealedHeader;
use std::sync::Arc;

/// Polygon PoS mainnet genesis block hash.
/// Source: Bor genesis (`BorMainnetGenesisHash` constant).
const POLYGON_GENESIS_HASH: B256 =
    b256!("a9c28ce2141b56c474f1dc504bee9b01eb1bd7d1a507580d5519d4437a97de1b");

/// Polygon mainnet chain ID.
pub const POLYGON_CHAIN_ID: u64 = 137;

/// Polygon head block for ForkID computation & Status message.
/// Must be BELOW the chain tip. Last ChainConfig fork is Prague at 73,440,256.
/// Using 83,230,720 (0x4F60000) — well past Prague, giving ForkHash 0x22d523b2.
pub const POLYGON_HEAD_BLOCK: u64 = 83_230_720;

/// Block hash for POLYGON_HEAD_BLOCK, used in eth Status message.
/// Without a real hash, peers see head=0x000...000 which looks invalid.
pub const POLYGON_HEAD_HASH: B256 =
    b256!("0271dd09e920b6e64f24d400866dd789b35d52a7093bf0f044787c36553e9174");

/// Build the Polygon PoS chain spec for devp2p handshake.
///
/// Key differences from Ethereum:
/// - Genesis hash is overridden to Polygon's real genesis
/// - All forks are block-based (Polygon has no Merge/TTD)
/// - Only ChainConfig forks are included (NOT BorConfig forks)
///
/// The genesis JSON provides the base Ethereum forks. We then override
/// Shanghai/Cancun/Prague from timestamp-based to block-based.
pub fn polygon_chain_spec() -> Arc<ChainSpec> {
    // Base genesis with pre-Shanghai Ethereum forks only.
    // Shanghai/Cancun are NOT included here — we add them as block-based below.
    let genesis_json = serde_json::json!({
        "config": {
            "chainId": POLYGON_CHAIN_ID,
            "homesteadBlock": 0,
            "eip150Block": 0,
            "eip155Block": 0,
            "eip158Block": 0,
            "byzantiumBlock": 0,
            "constantinopleBlock": 0,
            "petersburgBlock": 0,
            "istanbulBlock": 3_395_000,
            "muirGlacierBlock": 3_395_000,
            "berlinBlock": 14_750_000,
            "londonBlock": 23_850_000
        },
        "nonce": "0x0",
        "timestamp": "0x0",
        "gasLimit": "0x989680",
        "difficulty": "0x1",
        "alloc": {}
    });

    let genesis: Genesis =
        serde_json::from_value(genesis_json).expect("hardcoded genesis is valid");

    let mut spec: ChainSpec = genesis.into();

    // Override genesis hash with Polygon's real genesis hash.
    let header = spec.genesis_header.clone_header();
    spec.genesis_header = SealedHeader::new(header, POLYGON_GENESIS_HASH);

    // Add block-based Ethereum-equivalent forks that Polygon activates.
    // (Shanghai, Cancun, Prague are block-based on Polygon, not timestamp-based)
    spec.hardforks
        .insert(EthereumHardfork::Shanghai, ForkCondition::Block(50_523_000));
    spec.hardforks
        .insert(EthereumHardfork::Cancun, ForkCondition::Block(54_876_000));
    spec.hardforks
        .insert(EthereumHardfork::Prague, ForkCondition::Block(73_440_256));

    Arc::new(spec)
}

/// Polygon mainnet bootnodes (official, verified Feb 2026).
///
/// Source: https://docs.polygon.technology/pos/how-to/full-node/
pub fn boot_nodes() -> Vec<NodeRecord> {
    let enodes = [
        // Official Polygon PoS mainnet bootnodes (Feb 2026)
        "enode://48e6326841ce106f6b4e229a1be7e98a1d12be57e328b08cb461f6744ae4e78f5ec2340996ce9b40928a1a90137aadea13e25ca34774b52a3600d13a52c5c7bb@34.185.209.56:30303",
        "enode://8ab6905fe76aa9001adb77135250e918db888cac216870c0e95cf26650d83d31d8c2c93d54c3333e0a2196517c41651d174b743ec3e11f44e595f62b77fec7ba@34.185.162.14:30303",
        "enode://02e0b33cf60fb1f88f853c7c04830156151f4acd1c36173cd3fe1f375801fb4f5be5b3a89c98527915d37ed217752933c3faf4c820df740c9dd681294caebcf6@34.179.171.228:30303",
        "enode://079c387b65b09674825462ea63c528ca996af7b03d19b1b2ab6557347434838067db6dd7ae5e0c2e08d5ba164117f3d7faffbf3e890cb91cffbdf45a433ddfce@35.246.166.189:30303",
        "enode://191d06720948ae0119343e5798098f5b1f95a308174c4119d226da91833bc0176009bcc8bf5012e490500562d4d5b5427c307b01f3485b2e8351ac5afd946864@34.142.28.190:30303",
        "enode://30a4651b245e9a0cec674b9ecb5a06ca01553aa727e14a77d0f1ccdb9e48a975f3be631505f417aae438be545ac3b290cd3ed00bef96efd7fb0fb7f916397b3f@34.39.56.114:30303",
        "enode://b950b98b92e118551d79c7280b97ddfcdf3dacb620367ebd45e8382f8e69390df192055386221025ffd3c03912da2aadf668ae6ea7b35f391d82ef87452b3f02@34.147.169.102:30303",
        "enode://92ef18168f6c281a313d0ca76d6122b913a101352b5069af9cea6c8dd0f8b51d669601d59fdf250e972cf9a547d8a10f21ecf5b99ce8511605f328e5f66e845f@34.105.180.11:30303",
        // Secondary bootnodes
        "enode://d40ab6b340be9f78179bd1ec7aa4df346d43dc1462d85fb44c5d43f595991d2ec215d7c778a7588906cb4edf175b3df231cecce090986a739678cd3c620bf580@34.89.255.109:30303",
        "enode://13abba15caa024325f2209d3566fa77cd864281dda4f73bca4296277bfd919ac68cef4dbb508028e0310a24f6f9e23c761fa41ac735cdc87efdee76d5ff985a7@34.185.137.160:30303",
        "enode://fc5bd3856a4ce6389eef1d6bc637ce7617e6ba8013f7d722d9878cf13f1c5a5a95a9e26ccb0b38bcc330343941ce117ab50db9f61e72ba450dd528a1184d8e6a@34.89.119.250:30303",
        // Legacy bootnodes (may still be active)
        "enode://b8f1cc9c5d4403703fbf377116469667d2b1823c0daf16b7250aa576bacf399e42c3930ccfcb02c5df6879565a2b8931335565f0e8d3f8e72385ecf4a4bf160a@3.36.224.80:30303",
        "enode://8729e0c825f3d9cad382555f3e46dcff21af323e89025a0e6312df541f4a9e73abfa562d64906f5e59c51fe6f0501b3e61b07979606c56329c020ed739910759@54.194.245.5:30303",
    ];

    enodes.iter().filter_map(|e| e.parse().ok()).collect()
}
