"""TOML-driven backtest configuration — parser, window generator, sweep bridge.

Loads a ``sweep_config.toml`` file and produces:

* A sequence of :class:`WindowDef` objects (anchored-expanding or future strategies).
* A :class:`SweepConfig` ready for :func:`strategies.consistency_copy.backtester.sweep.run_sweep`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from strategies.consistency_copy.backtester.sweep import SweepConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_months(dt: datetime, months: int) -> datetime:
    """Add *months* calendar months to *dt*, pinning day to 1."""
    total = dt.year * 12 + (dt.month - 1) + months
    y, m = divmod(total, 12)
    return datetime(y, m + 1, 1)


def _quarter_label(dt: datetime) -> str:
    """Return ``'YYYYQq'`` for *dt* (quarter derived from month)."""
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}Q{q}"


# ---------------------------------------------------------------------------
# WindowDef
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowDef:
    """A single train/holdout window produced by :meth:`BacktestConfig.generate_windows`."""

    name: str
    train_start: datetime
    train_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    is_test: bool


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------


@dataclass
class BacktestConfig:
    """Parsed representation of a ``sweep_config.toml`` file."""

    # [windows]
    window_strategy: str
    train_anchor: datetime
    holdout_months: int
    step_months: int
    first_holdout: datetime
    last_holdout: datetime
    test_after: datetime

    # [pool]
    consistency_months: list[int]
    min_markets: list[int]
    mvf_bands: list[str]

    # [pricing]
    execution_delays: list[float]
    max_price_delay_s: float

    # [sweep]
    min_traders: list[int]
    agreement_pct: list[float]
    directions: list[str]
    price_bands: list[tuple[float, float]]
    sizing_strategies: list[str]
    min_bets: int
    base_bet: float
    fee_pct: float

    # [metrics]
    base_rate_adjustment: bool

    # [ranking]
    top_n: int
    min_windows: int

    # -- generated (cached) ---------------------------------------------------
    _windows: list[WindowDef] = field(default_factory=list, init=False, repr=False)

    # ------------------------------------------------------------------
    # Window generation
    # ------------------------------------------------------------------

    def generate_windows(self) -> list[WindowDef]:
        """Build the sequence of train/holdout windows.

        Only ``"anchored_expanding"`` is currently supported:

        * ``train_start`` is pinned to :pyattr:`train_anchor` for every window.
        * ``train_end`` equals ``holdout_start`` (no gap).
        * Holdout slides forward by :pyattr:`step_months` from
          :pyattr:`first_holdout` up to and including :pyattr:`last_holdout`.
        * ``is_test = True`` when ``holdout_start >= test_after``.
        """
        if self.window_strategy != "anchored_expanding":
            msg = f"Unknown window_strategy: {self.window_strategy!r}"
            raise ValueError(msg)

        windows: list[WindowDef] = []
        dev_idx = 0
        cursor = self.first_holdout

        while cursor <= self.last_holdout:
            holdout_start = cursor
            holdout_end = _add_months(cursor, self.holdout_months)
            train_start = self.train_anchor
            train_end = holdout_start  # no gap
            is_test = holdout_start >= self.test_after

            qlabel = _quarter_label(holdout_start)
            if is_test:
                name = f"test_{qlabel}"
            else:
                name = f"dev_{dev_idx:02d}_{qlabel}"
                dev_idx += 1

            windows.append(
                WindowDef(
                    name=name,
                    train_start=train_start,
                    train_end=train_end,
                    holdout_start=holdout_start,
                    holdout_end=holdout_end,
                    is_test=is_test,
                )
            )

            cursor = _add_months(cursor, self.step_months)

        self._windows = windows
        return windows

    # ------------------------------------------------------------------
    # Sweep bridge
    # ------------------------------------------------------------------

    def to_sweep_config(self) -> SweepConfig:
        """Convert the ``[sweep]`` section into a :class:`SweepConfig`."""
        return SweepConfig(
            min_traders_values=self.min_traders,
            agreement_pct_values=self.agreement_pct,
            direction_values=self.directions,
            entry_price_bands=self.price_bands,
            sizing_strategies=self.sizing_strategies,
            min_bets=self.min_bets,
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: Path) -> BacktestConfig:
    """Parse a TOML file into a :class:`BacktestConfig`.

    TOML date literals are ``datetime.date`` objects; we convert to
    ``datetime`` via ``fromisoformat(str(...))``.
    """
    raw = tomllib.loads(path.read_text())

    win = raw["windows"]
    pool = raw["pool"]
    pricing = raw["pricing"]
    sweep = raw["sweep"]
    metrics = raw["metrics"]
    ranking = raw["ranking"]

    return BacktestConfig(
        # [windows]
        window_strategy=win["strategy"],
        train_anchor=datetime.fromisoformat(str(win["train_anchor"])),
        holdout_months=int(win["holdout_months"]),
        step_months=int(win["step_months"]),
        first_holdout=datetime.fromisoformat(str(win["first_holdout"])),
        last_holdout=datetime.fromisoformat(str(win["last_holdout"])),
        test_after=datetime.fromisoformat(str(win["test_after"])),
        # [pool]
        consistency_months=list(pool["consistency_months"]),
        min_markets=list(pool["min_markets"]),
        mvf_bands=list(pool["mvf_bands"]),
        # [pricing]
        execution_delays=[float(d) for d in pricing["execution_delays"]],
        max_price_delay_s=float(pricing["max_price_delay_s"]),
        # [sweep]
        min_traders=list(sweep["min_traders"]),
        agreement_pct=[float(a) for a in sweep["agreement_pct"]],
        directions=list(sweep["directions"]),
        price_bands=[tuple(b) for b in sweep["price_bands"]],
        sizing_strategies=list(sweep["sizing_strategies"]),
        min_bets=int(sweep["min_bets"]),
        base_bet=float(sweep["base_bet"]),
        fee_pct=float(sweep["fee_pct"]),
        # [metrics]
        base_rate_adjustment=bool(metrics["base_rate_adjustment"]),
        # [ranking]
        top_n=int(ranking["top_n"]),
        min_windows=int(ranking["min_windows"]),
    )
