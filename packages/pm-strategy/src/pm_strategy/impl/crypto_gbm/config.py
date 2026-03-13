"""Immutable configuration for the crypto GBM mid-window repricing strategy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CryptoGBMConfig:
    # Entry
    threshold: float = 0.10
    base_bet_usd: float = 50.0
    sigma_lookback_min: int = 1440
    min_sigma: float = 1e-7
    max_spread: float = 0.04
    min_time_remaining_min: float = 1.5
    skip_minute_zero: bool = True
    fee_pct: float = 0.03
    min_gbm_deviation: float = 0.05
    primary_exchange: str = "BINANCE"
    primary_symbol: str = "BTC-USDT"
    # Scalp exit
    scalp_enabled: bool = True
    exit_threshold: float = 0.02
    exit_min_time_remaining_s: float = 30.0
    no_entry_within_s: float = 90.0

    def __init__(
        self,
        threshold: float = 0.10,
        base_bet_usd: float = 50.0,
        sigma_lookback_min: int = 1440,
        min_sigma: float = 1e-7,
        max_spread: float = 0.04,
        min_time_remaining_min: float = 1.5,
        skip_minute_zero: bool = True,
        fee_pct: float = 0.03,
        min_gbm_deviation: float = 0.05,
        primary_exchange: str = "BINANCE",
        primary_symbol: str = "BTC-USDT",
        scalp_enabled: bool = True,
        exit_threshold: float = 0.02,
        exit_min_time_remaining_s: float = 30.0,
        no_entry_within_s: float = 90.0,
        **_extra: object,
    ) -> None:
        object.__setattr__(self, "threshold", threshold)
        object.__setattr__(self, "base_bet_usd", base_bet_usd)
        object.__setattr__(self, "sigma_lookback_min", sigma_lookback_min)
        object.__setattr__(self, "min_sigma", min_sigma)
        object.__setattr__(self, "max_spread", max_spread)
        object.__setattr__(self, "min_time_remaining_min", min_time_remaining_min)
        object.__setattr__(self, "skip_minute_zero", skip_minute_zero)
        object.__setattr__(self, "fee_pct", fee_pct)
        object.__setattr__(self, "min_gbm_deviation", min_gbm_deviation)
        object.__setattr__(self, "primary_exchange", primary_exchange)
        object.__setattr__(self, "primary_symbol", primary_symbol)
        object.__setattr__(self, "scalp_enabled", scalp_enabled)
        object.__setattr__(self, "exit_threshold", exit_threshold)
        object.__setattr__(self, "exit_min_time_remaining_s", exit_min_time_remaining_s)
        object.__setattr__(self, "no_entry_within_s", no_entry_within_s)
