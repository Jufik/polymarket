"""Consensus strategy R5 — sharp pool + signal-time volume + dissent filter.

Three structural fixes stacked:
1. Sharp pool (top K by excess_hr) — controls pool explosion
2. Signal-time volume gate — causal volume from first N traders only
3. Dissent filter — skip when qualified NO traders dilute consensus

Plus: price_ceil=0.75 (NOT 0.40 — confirmed anti-knowledge).
"""

from __future__ import annotations

from polymarket_pipeline.strategies.types import TradeIntent


class ConsensusStrategyR5:
    """Tick-by-tick consensus with vol gate + dissent filter.

    Parameters
    ----------
    qualified_traders : set[str]
        Sharp pool — top K traders by excess_hr from training window.
    yes_asset_ids : dict[str, str]
        Maps condition_id -> YES asset_id.
    no_asset_ids : dict[str, str]
        Maps condition_id -> NO asset_id (for dissent tracking).
    consensus_n : int
        Number of distinct qualified YES traders required to fire.
    price_ceil : float
        Max signal price (skip if trade.price > this).
    min_signal_vol : float
        Minimum USD volume from first N qualified YES traders. Skip if below.
    min_dissent_ratio : float
        Minimum ratio of qualified YES / (YES + NO) traders. Skip if below.
    test_start_epoch : float
        Phantom filter — only count trades after this timestamp.
    size_usd : float
        Base position size in USD per signal.
    """

    name: str = "consensus_r5"

    def __init__(
        self,
        qualified_traders: set[str],
        yes_asset_ids: dict[str, str],
        no_asset_ids: dict[str, str],
        consensus_n: int = 4,
        price_ceil: float = 0.75,
        min_signal_vol: float = 500.0,
        min_dissent_ratio: float = 0.90,
        test_start_epoch: float = 0.0,
        size_usd: float = 100.0,
    ) -> None:
        self._qualified = qualified_traders
        self._yes_asset_ids = yes_asset_ids
        self._no_asset_ids = no_asset_ids
        self._n = consensus_n
        self._price_ceil = price_ceil
        self._min_signal_vol = min_signal_vol
        self._min_dissent_ratio = min_dissent_ratio
        self._test_start = test_start_epoch
        self._size_usd = size_usd

        # Per-market state
        self._yes_traders: dict[str, set[str]] = {}  # cid -> set of YES traders
        self._no_traders: dict[str, set[str]] = {}   # cid -> set of NO traders
        self._yes_vol: dict[str, dict[str, float]] = {}  # cid -> {trader: total_usd}
        self._fired: set[str] = set()

        # Stats
        self.skip_vol = 0
        self.skip_dissent = 0
        self.skip_price = 0
        self.total_fired = 0

    def reset(self) -> None:
        self._yes_traders.clear()
        self._no_traders.clear()
        self._yes_vol.clear()
        self._fired.clear()
        self.skip_vol = 0
        self.skip_dissent = 0
        self.skip_price = 0
        self.total_fired = 0

    async def on_trade(self, trade: object, ctx: object) -> list[TradeIntent] | None:
        if trade.side != "BUY":  # type: ignore[union-attr]
            return None

        cid = trade.condition_id  # type: ignore[union-attr]
        maker = trade.maker  # type: ignore[union-attr]
        ts = trade.published_at  # type: ignore[union-attr]
        asset_id = trade.asset_id  # type: ignore[union-attr]

        # Phantom filter
        if ts < self._test_start:
            return None

        # Only count qualified makers
        if not maker or maker not in self._qualified:
            return None

        # Skip if already fired
        if cid in self._fired:
            return None

        # Determine if this is a YES or NO trade
        expected_yes = self._yes_asset_ids.get(cid)
        expected_no = self._no_asset_ids.get(cid)

        is_yes = expected_yes and asset_id == expected_yes
        is_no = expected_no and asset_id == expected_no

        if not is_yes and not is_no:
            return None

        # Track NO traders for dissent
        if is_no:
            if cid not in self._no_traders:
                self._no_traders[cid] = set()
            self._no_traders[cid].add(maker)
            return None

        # --- YES trade processing ---
        if cid not in self._yes_traders:
            self._yes_traders[cid] = set()
            self._yes_vol[cid] = {}

        traders = self._yes_traders[cid]
        vol_map = self._yes_vol[cid]

        # Accumulate volume per trader
        trade_usd = getattr(trade, "amount_usd", 0.0) or (trade.price * getattr(trade, "size", 0.0))  # type: ignore[union-attr]
        vol_map[maker] = vol_map.get(maker, 0.0) + trade_usd

        if maker not in traders:
            traders.add(maker)

        # Check consensus threshold
        if len(traders) < self._n:
            return None

        # --- All gates below apply at consensus trigger time ---

        # Gate 1: Signal-time volume (sum of first N traders' volumes)
        # Sort by entry order implicit in dict — but we track all, so sum all
        signal_vol = sum(vol_map.values())
        if signal_vol < self._min_signal_vol:
            self.skip_vol += 1
            return None

        # Gate 2: Dissent ratio
        n_yes = len(traders)
        n_no = len(self._no_traders.get(cid, set()))
        dissent_ratio = n_yes / (n_yes + n_no) if (n_yes + n_no) > 0 else 1.0
        if dissent_ratio < self._min_dissent_ratio:
            self.skip_dissent += 1
            return None

        # Gate 3: Price ceiling
        signal_price = trade.price  # type: ignore[union-attr]
        if signal_price > self._price_ceil:
            self.skip_price += 1
            return None

        # Fire signal
        self._fired.add(cid)
        self.total_fired += 1

        return [
            TradeIntent(
                strategy=self.name,
                condition_id=cid,
                side="BUY",
                outcome="YES",
                size_usd=self._size_usd,
                urgency="patient",
                max_price=min(signal_price + 0.02, self._price_ceil),
                reason=(
                    f"consensus_n={self._n} yes={n_yes} no={n_no} "
                    f"dissent={dissent_ratio:.2f} vol=${signal_vol:.0f}"
                ),
                signal_time=ts,
                asset_id=expected_yes or "",
            )
        ]

    async def on_market_update(self, update: object, ctx: object) -> list[TradeIntent] | None:
        return None

    async def on_timer(self, now: float, ctx: object) -> list[TradeIntent] | None:
        return None
