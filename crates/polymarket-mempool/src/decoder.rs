//! Decode fillOrder/fillOrders calldata into structured trade data.

use alloy::sol;
use alloy::sol_types::SolCall;
use pyo3::types::{PyDict, PyDictMethods};
use pyo3::{Bound, PyResult, Python};
use std::time::{SystemTime, UNIX_EPOCH};

sol! {
    #[derive(Debug)]
    struct Order {
        uint256 salt;
        address maker;
        address signer;
        address taker;
        uint256 tokenId;
        uint256 makerAmount;
        uint256 takerAmount;
        uint256 expiration;
        uint256 nonce;
        uint256 feeRateBps;
        uint8 side;          // 0 = BUY, 1 = SELL
        uint8 signatureType;
    }

    #[derive(Debug)]
    struct Sig {
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    #[derive(Debug)]
    function fillOrder(Order order, Sig sig);

    #[derive(Debug)]
    function fillOrders(Order[] orders, Sig[] sigs);
}

/// Decode a single Order into a Python dict.
fn order_to_dict<'py>(
    py: Python<'py>,
    order: &Order,
    tx_hash: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();

    dict.set_item("tx_hash", tx_hash)?;
    dict.set_item("maker", format!("0x{:x}", order.maker))?;
    dict.set_item("taker", format!("0x{:x}", order.taker))?;
    dict.set_item("token_id", order.tokenId.to_string())?;
    dict.set_item("maker_amount", order.makerAmount.to::<u128>())?;
    dict.set_item("taker_amount", order.takerAmount.to::<u128>())?;
    dict.set_item("fee_rate_bps", order.feeRateBps.to::<u64>())?;
    dict.set_item("side", order.side)?;
    dict.set_item("expiration", order.expiration.to::<u64>())?;
    dict.set_item("seen_at", now)?;

    Ok(dict)
}

/// Decode calldata and return a vec of Python dicts (one per order).
pub fn decode_calldata<'py>(
    py: Python<'py>,
    calldata: &[u8],
    tx_hash: &str,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    if calldata.len() < 4 {
        return Ok(vec![]);
    }

    // Try fillOrder (single order)
    if let Ok(call) = fillOrderCall::abi_decode(&calldata[4..]) {
        let dict = order_to_dict(py, &call.order, tx_hash)?;
        return Ok(vec![dict]);
    }

    // Try fillOrders (batch)
    if let Ok(call) = fillOrdersCall::abi_decode(&calldata[4..]) {
        let mut results = Vec::with_capacity(call.orders.len());
        for order in &call.orders {
            results.push(order_to_dict(py, order, tx_hash)?);
        }
        return Ok(results);
    }

    // Neither decoded — unknown function
    Ok(vec![])
}
