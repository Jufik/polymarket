"""Backward-compat shim — re-exports from pm_core."""

from pm_core.trade_id import (  # noqa: F401
    make_trade_id_chain,
    make_trade_id_pending,
    make_trade_id_ws,
    make_trade_ids_chain_batch,
)
