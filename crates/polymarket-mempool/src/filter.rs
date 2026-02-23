//! Transaction filter: only pass CTF/NegRisk Exchange trades.

use alloy_primitives::Address;
use std::collections::HashSet;
use std::sync::LazyLock;

/// CTF Exchange contract address.
const CTF_EXCHANGE: &str = "4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e";
/// NegRisk CTF Exchange contract address.
const NEGRISK_EXCHANGE: &str = "c5d563a36ae78145c45a50134d48a1215220f80a";

/// Set of exchange addresses for fast lookup.
static EXCHANGE_ADDRS: LazyLock<HashSet<Address>> = LazyLock::new(|| {
    let mut set = HashSet::new();
    set.insert(CTF_EXCHANGE.parse().unwrap());
    set.insert(NEGRISK_EXCHANGE.parse().unwrap());
    set
});

/// fillOrder(Order,Sig) function selector.
/// keccak256("fillOrder((uint256,address,address,address,uint256,uint256,uint256,uint256,uint256,uint256,uint8,uint8),(uint8,bytes32,bytes32))")
const FILL_ORDER_SELECTOR: [u8; 4] = [0xfe, 0x72, 0x9a, 0xee];
/// fillOrders(Order[],Sig[]) function selector.
const FILL_ORDERS_SELECTOR: [u8; 4] = [0xd7, 0x98, 0xb1, 0x06];

/// Check if a transaction targets a Polymarket exchange contract.
pub fn is_exchange_tx(to: &Address) -> bool {
    EXCHANGE_ADDRS.contains(to)
}

/// Check if calldata starts with a fillOrder/fillOrders selector.
pub fn is_fill_order(calldata: &[u8]) -> bool {
    if calldata.len() < 4 {
        return false;
    }
    let selector = &calldata[..4];
    selector == FILL_ORDER_SELECTOR || selector == FILL_ORDERS_SELECTOR
}
