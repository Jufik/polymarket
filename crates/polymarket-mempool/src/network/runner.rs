//! Polygon devp2p network runner.
//!
//! Connects to Polygon peers, receives pending transaction gossip,
//! filters for CTF Exchange txs, decodes calldata, and sends
//! structured trade dicts through a channel to Python.

use crate::chain_spec::{boot_nodes, polygon_chain_spec, POLYGON_HEAD_BLOCK, POLYGON_HEAD_HASH};
use crate::filter::{is_exchange_tx, is_fill_order};

use alloy_consensus::Transaction;
use alloy_primitives::U256;
use reth_discv4::Discv4Config;
use reth_dns_discovery::DnsDiscoveryConfig;
use reth_network::{
    config::rng_secret_key, eth_requests::EthRequestHandler, transactions::NetworkTransactionEvent,
    EthNetworkPrimitives, NetworkConfig, NetworkEvent, NetworkEventListenerProvider,
    NetworkManager,
};
use reth_network_api::events::PeerEvent;
use reth_network_api::{NetworkInfo, Peers, PeersInfo, PeerRequest};
use reth_eth_wire_types::GetPooledTransactions;
use reth_network_types::PeersConfig;
use reth_storage_api::noop::NoopProvider;
use tokio_stream::StreamExt;

use std::collections::HashSet;
use std::net::{Ipv4Addr, SocketAddr};
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

/// Pre-flight: verify TCP connectivity to at least one bootnode.
/// If all checks fail, outbound connections are likely blocked (firewall/NAT).
async fn tcp_preflight(boot: &[reth_network_peers::NodeRecord]) {
    for node in boot.iter().take(3) {
        let addr = SocketAddr::new(node.address, node.tcp_port);
        match tokio::time::timeout(Duration::from_secs(5), tokio::net::TcpStream::connect(&addr))
            .await
        {
            Ok(Ok(_)) => {
                tracing::info!(%addr, "Pre-flight TCP connectivity OK");
                return;
            }
            Ok(Err(e)) => {
                tracing::warn!(%addr, error = %e, "Pre-flight TCP failed");
            }
            Err(_) => {
                tracing::warn!(%addr, "Pre-flight TCP timeout (5s)");
            }
        }
    }
    tracing::error!(
        "All pre-flight TCP checks FAILED — outbound connections may be blocked by firewall"
    );
}

/// Run the network manager and send decoded trades through the channel.
pub async fn run_network(
    listen_port: u16,
    tx: mpsc::Sender<serde_json::Value>,
) -> eyre::Result<()> {
    let local_addr = SocketAddr::new(Ipv4Addr::UNSPECIFIED.into(), listen_port);
    let chain_spec = polygon_chain_spec();

    let boot = boot_nodes();
    let fork_id = chain_spec.fork_id(&alloy_eip2124::Head {
        number: POLYGON_HEAD_BLOCK,
        ..Default::default()
    });
    tracing::info!(
        bootnodes = boot.len(),
        chain_id = %chain_spec.chain().id(),
        genesis_hash = %chain_spec.genesis_hash(),
        ?fork_id,
        fork_hash = %format!("{:#x}", u32::from_be_bytes(fork_id.hash.0)),
        fork_next = fork_id.next,
        "Chain spec loaded"
    );

    // Verify we can reach bootnodes at the TCP level before starting the full network.
    tcp_preflight(&boot).await;

    let mut discv4 = Discv4Config::builder();
    discv4.add_boot_nodes(boot);

    // DNS discovery: Polygon's official ENR tree contains hundreds of
    // pre-validated peers and is the fastest way to find nodes.
    // Source: https://forum.polygon.technology/t/introducing-our-new-dns-discovery-for-polygon-pos/19871
    let polygon_dns: reth_dns_discovery::tree::LinkEntry = "enrtree://AKUEZKN7PSKVNR65FZDHECMKOJQSGPARGTPPBI7WS2VUL4EGR6XPC@pos.polygon-peers.io"
        .parse()
        .expect("valid Polygon DNS discovery URL");
    let dns_config = DnsDiscoveryConfig {
        bootstrap_dns_networks: Some(HashSet::from([polygon_dns])),
        ..Default::default()
    };

    // Higher peer limits for mempool coverage (default is 100 out + 30 in).
    let peers_config = PeersConfig::default()
        .with_max_outbound(200)
        .with_max_inbound(100);

    let head = alloy_eip2124::Head {
        number: POLYGON_HEAD_BLOCK,
        hash: POLYGON_HEAD_HASH,
        // Bor uses difficulty 1 per block, so TD ≈ block number
        total_difficulty: U256::from(POLYGON_HEAD_BLOCK),
        ..Default::default()
    };

    let config = NetworkConfig::<_, EthNetworkPrimitives>::builder(rng_secret_key())
        .listener_addr(local_addr)
        .dns_discovery(dns_config)
        .peer_config(peers_config)
        .set_head(head)
        .discovery(discv4)
        .build_with_noop_provider(chain_spec.clone());

    // Create an unbounded channel to tap into transaction events.
    // NetworkManager forwards raw tx gossip through this channel.
    let (tx_event_tx, mut tx_event_rx) =
        tokio::sync::mpsc::unbounded_channel::<NetworkTransactionEvent<EthNetworkPrimitives>>();

    // Create eth request handler so peers get responses to GetBlockHeaders etc.
    // Without this, incoming requests are silently dropped (oneshot sender dropped)
    // and peers disconnect with SubprotocolSpecific after the Status exchange.
    let (eth_req_tx, eth_req_rx) = tokio::sync::mpsc::channel(256);
    let noop_client = NoopProvider::eth(chain_spec);

    let network = NetworkManager::new(config).await?;
    let handle = network.handle().clone();
    let peers = network.peers_handle();
    let eth_handler = EthRequestHandler::new(noop_client, peers, eth_req_rx);

    let network = network
        .with_eth_request_handler(eth_req_tx)
        .with_transactions(tx_event_tx);
    let mut events = handle.event_listener();

    let mut eth_jh = tokio::task::spawn(eth_handler);
    let mut net_jh = tokio::task::spawn(network);

    // Explicitly mark network as active so PeersManager dials outbound peers.
    // (Default is Active, but call explicitly to be safe after spawning.)
    handle.set_network_active();

    // Register all bootnodes as **trusted** peers via the handle.
    // Trusted peers can't be removed by ENR fork-ID checks and are
    // prioritised by fill_outbound_slots(), guaranteeing dial attempts.
    let boot = boot_nodes();
    for node in &boot {
        handle.add_trusted_peer(
            node.id,
            SocketAddr::new(node.address, node.tcp_port),
        );
    }
    tracing::info!(trusted_bootnodes = boot.len(), "Registered bootnodes as trusted peers");

    // Log our enode URL and listening address for diagnostics
    let local_record = handle.local_node_record();
    let local_addr = handle.local_addr();
    tracing::info!(
        %local_addr,
        enode = %local_record,
        "Listening for Polygon peers"
    );

    // Log the actual Status message fields that will be sent to peers.
    // This is critical for diagnosing handshake failures — if genesis hash
    // or head hash don't match what Polygon Bor expects, peers will reject us.
    match handle.network_status().await {
        Ok(net_status) => {
            tracing::info!(
                protocol_version = net_status.protocol_version,
                genesis = %net_status.eth_protocol_info.genesis,
                head = %net_status.eth_protocol_info.head,
                network_id = net_status.eth_protocol_info.network,
                "Status message fields (sent to peers during eth handshake)"
            );
        }
        Err(e) => {
            tracing::warn!(error = %e, "Failed to get network status");
        }
    }

    let mut peers_active: usize = 0;
    // Aggressive diagnostics initially: 10s for first 2 minutes, then 30s
    let mut diag_timer = interval(Duration::from_secs(10));
    diag_timer.tick().await; // skip first immediate tick
    let mut peers_ever_seen: usize = 0;
    let mut sessions_attempted: usize = 0;
    let mut sessions_closed: usize = 0;
    let mut diag_count: usize = 0;
    let mut erigon_peers: HashSet<reth_network_peers::PeerId> = HashSet::new();

    loop {
        tokio::select! {
            // Catch panics / early exits from spawned tasks
            result = &mut net_jh => {
                tracing::error!("Network manager task exited: {:?}", result);
                break;
            }
            result = &mut eth_jh => {
                tracing::error!("Eth request handler task exited: {:?}", result);
                break;
            }

            // Diagnostic timer: 10s initially, slows to 30s after 2 min
            _ = diag_timer.tick() => {
                diag_count += 1;
                // Slow down after 12 ticks (2 minutes at 10s)
                if diag_count == 12 {
                    diag_timer = interval(Duration::from_secs(30));
                }

                let connected = handle.num_connected_peers();
                tracing::info!(
                    connected_peers = connected,
                    peers_discovered = peers_ever_seen,
                    sessions_attempted = sessions_attempted,
                    sessions_closed = sessions_closed,
                    peers_active = peers_active,
                    erigon_count = erigon_peers.len(),
                    is_syncing = handle.is_syncing(),
                    "Network diagnostics"
                );

                // If we've discovered peers but have zero sessions,
                // force re-add bootnodes via add_peer to trigger dials.
                if sessions_attempted == 0 && peers_ever_seen > 10 {
                    let boot = boot_nodes();
                    for node in &boot {
                        handle.add_peer(
                            node.id,
                            SocketAddr::new(node.address, node.tcp_port),
                        );
                    }
                    tracing::warn!(
                        boot_count = boot.len(),
                        "No sessions yet — force re-added bootnodes via add_peer"
                    );
                }
            }

            // Branch 1: Network events (peer connect/disconnect)
            Some(evt) = events.next() => {
                match evt {
                    NetworkEvent::ActivePeerSession { info, .. } => {
                        peers_active += 1;
                        let client = info.client_version.to_string();
                        let is_erigon = client.starts_with("erigon/");

                        if is_erigon {
                            erigon_peers.insert(info.peer_id);
                            handle.add_trusted_peer(info.peer_id, info.remote_addr);
                            tracing::warn!(
                                peers = peers_active,
                                erigon_count = erigon_peers.len(),
                                client = %client,
                                peer = %info.peer_id,
                                addr = %info.remote_addr,
                                "ERIGON peer connected — promoted to trusted"
                            );
                        } else {
                            tracing::info!(
                                peers = peers_active,
                                client = %client,
                                peer = %info.peer_id,
                                "Peer session active"
                            );
                        }
                    }
                    NetworkEvent::Peer(PeerEvent::SessionClosed { peer_id, reason }) => {
                        peers_active = peers_active.saturating_sub(1);
                        sessions_closed += 1;
                        erigon_peers.remove(&peer_id);
                        tracing::info!(
                            peers = peers_active,
                            peer = %peer_id,
                            reason = ?reason,
                            "Peer session closed"
                        );
                    }
                    NetworkEvent::Peer(PeerEvent::SessionEstablished(info)) => {
                        sessions_attempted += 1;
                        tracing::info!(
                            peer = %info.peer_id,
                            client = %info.client_version,
                            remote_addr = %info.remote_addr,
                            "Peer session established (awaiting active)"
                        );
                    }
                    NetworkEvent::Peer(PeerEvent::PeerAdded(peer_id)) => {
                        peers_ever_seen += 1;
                        // Log first 5 at info, rest at debug to reduce noise
                        if peers_ever_seen <= 5 {
                            tracing::info!(
                                peer = %peer_id,
                                total_discovered = peers_ever_seen,
                                "Peer discovered"
                            );
                        } else {
                            tracing::debug!(
                                peer = %peer_id,
                                total_discovered = peers_ever_seen,
                                "Peer discovered"
                            );
                        }
                    }
                    NetworkEvent::Peer(PeerEvent::PeerRemoved(peer_id)) => {
                        tracing::debug!(peer = %peer_id, "Peer removed from table");
                    }
                }
                // Send peer count update
                let mut status = serde_json::Map::new();
                status.insert("_peers_active".to_string(), peers_active.into());
                let _ = tx.try_send(serde_json::Value::Object(status));
            }

            // Branch 2: Transaction events (incoming pending txs)
            Some(tx_evt) = tx_event_rx.recv() => {
                match tx_evt {
                    NetworkTransactionEvent::IncomingTransactions { peer_id, msg } => {
                        let tx_count = msg.0.len();
                        tracing::info!(
                            peer = %peer_id,
                            tx_count,
                            "Received IncomingTransactions"
                        );
                        for pooled_tx in msg.0.iter() {
                            // Extract destination address
                            let to_addr = match pooled_tx.to() {
                                Some(addr) => addr,
                                None => continue, // contract creation, skip
                            };

                            if !is_exchange_tx(&to_addr) {
                                continue;
                            }

                            let input = pooled_tx.input();
                            if !is_fill_order(input) {
                                continue;
                            }

                            let tx_hash = format!("0x{:x}", pooled_tx.tx_hash());

                            match decode_calldata_to_json(input, &tx_hash) {
                                Ok(trades) => {
                                    for trade in trades {
                                        let _ = tx.try_send(trade);
                                    }
                                }
                                Err(e) => {
                                    tracing::debug!(
                                        %tx_hash,
                                        error = %e,
                                        "Failed to decode calldata"
                                    );
                                }
                            }
                        }
                    }
                    NetworkTransactionEvent::IncomingPooledTransactionHashes { peer_id, msg } => {
                        let hashes: Vec<alloy_primitives::B256> = msg.hashes().clone();
                        let hash_count = hashes.len();
                        tracing::info!(
                            peer = %peer_id,
                            hash_count,
                            "Received tx hash announcements — fetching full txs"
                        );

                        if hashes.is_empty() {
                            continue;
                        }

                        let (resp_tx, resp_rx) = tokio::sync::oneshot::channel();
                        handle.send_request(
                            peer_id,
                            PeerRequest::GetPooledTransactions {
                                request: GetPooledTransactions(hashes),
                                response: resp_tx,
                            },
                        );

                        let tx_out = tx.clone();
                        tokio::spawn(async move {
                            match resp_rx.await {
                                Ok(Ok(pooled_txs)) => {
                                    let fetched = pooled_txs.0.len();
                                    tracing::info!(
                                        peer = %peer_id,
                                        fetched,
                                        requested = hash_count,
                                        "GetPooledTransactions response"
                                    );
                                    for pooled_tx in pooled_txs.0.iter() {
                                        let to_addr = match pooled_tx.to() {
                                            Some(addr) => addr,
                                            None => continue,
                                        };
                                        if !is_exchange_tx(&to_addr) {
                                            continue;
                                        }
                                        let input = pooled_tx.input();
                                        if !is_fill_order(input) {
                                            continue;
                                        }
                                        let tx_hash = format!("0x{:x}", pooled_tx.tx_hash());
                                        match decode_calldata_to_json(input, &tx_hash) {
                                            Ok(trades) => {
                                                for trade in trades {
                                                    let _ = tx_out.try_send(trade);
                                                }
                                            }
                                            Err(e) => {
                                                tracing::debug!(
                                                    %tx_hash,
                                                    error = %e,
                                                    "Failed to decode calldata (fetched)"
                                                );
                                            }
                                        }
                                    }
                                }
                                Ok(Err(req_err)) => {
                                    tracing::debug!(
                                        peer = %peer_id,
                                        error = ?req_err,
                                        "GetPooledTransactions request failed"
                                    );
                                }
                                Err(_) => {
                                    tracing::debug!(
                                        peer = %peer_id,
                                        "GetPooledTransactions channel closed"
                                    );
                                }
                            }
                        });
                    }
                    _ => {
                        tracing::debug!("Received other tx event variant");
                    }
                }
            }

            else => break,
        }
    }

    Ok(())
}

/// Decode calldata into serde_json::Value (GIL-free).
fn decode_calldata_to_json(
    calldata: &[u8],
    tx_hash: &str,
) -> Result<Vec<serde_json::Value>, String> {
    use crate::decoder::{fillOrderCall, fillOrdersCall, Order};
    use alloy::sol_types::SolCall;
    use std::time::{SystemTime, UNIX_EPOCH};

    if calldata.len() < 4 {
        return Ok(vec![]);
    }

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();

    let order_to_json = |order: &Order| -> serde_json::Value {
        serde_json::json!({
            "tx_hash": tx_hash,
            "maker": format!("0x{:x}", order.maker),
            "taker": format!("0x{:x}", order.taker),
            "token_id": order.tokenId.to_string(),
            "maker_amount": order.makerAmount.to::<u128>(),
            "taker_amount": order.takerAmount.to::<u128>(),
            "fee_rate_bps": order.feeRateBps.to::<u64>(),
            "side": order.side,
            "expiration": order.expiration.to::<u64>(),
            "seen_at": now,
        })
    };

    // Try fillOrder (single)
    if let Ok(call) = fillOrderCall::abi_decode(&calldata[4..]) {
        return Ok(vec![order_to_json(&call.order)]);
    }

    // Try fillOrders (batch)
    if let Ok(call) = fillOrdersCall::abi_decode(&calldata[4..]) {
        return Ok(call.orders.iter().map(order_to_json).collect());
    }

    Err("Unknown function selector".to_string())
}
