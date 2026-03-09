"""Trailing stop delta tuning — sweep trail_delta values against historical positions.

For each position in the ledger:
1. Load all trades on that asset_id after entry
2. Replay the trailing stop at various delta values
3. Compare SL exit PnL vs hold-to-resolution PnL

Usage:
    PYTHONPATH=. uv run python research/scripts/tune_trailing_stop.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

# ── Configuration ─────────────────────────────────────────────────────────────

LEDGER_FILES = {
    "sports_yes": "research/output/ledger_sports_yes_v3_k25_n2.parquet",
    "politics_no": "research/output/ledger_politics_no_v3_k100_n2.parquet",
    "politics_yes": "research/output/ledger_politics_yes_v3_k100_n3.parquet",
}

TRADES_DIR = Path("data/research/trades")
METADATA_DIR = Path("data/research/metadata")

# Delta values to sweep (in cents)
TRAIL_DELTAS = [0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class SimResult:
    """Result of simulating a trailing stop on one position."""

    condition_id: str
    entry_price: float
    resolution: str  # "win" or "loss"
    pnl_hold: float  # PnL from holding to resolution
    exit_price: float | None  # price at SL exit (None = no trigger)
    pnl_sl: float  # PnL from SL exit (or hold PnL if no trigger)
    hwm: float  # highest price seen
    triggered: bool


# ── Core simulation ──────────────────────────────────────────────────────────


def simulate_trailing_stop(
    entry_price: float,
    trail_delta: float,
    prices: list[float],
    resolution: str,  # "win" or "loss"
    fill_size_usd: float,
) -> SimResult:
    """Simulate a trailing stop against a price series.

    Returns the result including whether it triggered and at what PnL.
    """
    hwm = entry_price
    qty_tokens = fill_size_usd / entry_price

    # PnL if we hold to resolution
    if resolution == "win":
        pnl_hold = (1.0 - entry_price) * qty_tokens
    else:
        pnl_hold = -fill_size_usd

    # Walk the price series
    for price in prices:
        if price > hwm:
            hwm = price
        stop_level = hwm - trail_delta
        if price <= stop_level:
            # Triggered — sell at this price
            pnl_sl = (price - entry_price) * qty_tokens
            return SimResult(
                condition_id="",
                entry_price=entry_price,
                resolution=resolution,
                pnl_hold=pnl_hold,
                exit_price=price,
                pnl_sl=pnl_sl,
                hwm=hwm,
                triggered=True,
            )

    # No trigger — hold to resolution
    return SimResult(
        condition_id="",
        entry_price=entry_price,
        resolution=resolution,
        pnl_hold=pnl_hold,
        exit_price=None,
        pnl_sl=pnl_hold,
        hwm=hwm,
        triggered=False,
    )


# ── Main sweep ────────────────────────────────────────────────────────────────


def load_trades_for_assets(
    asset_ids: set[str], min_ts: float, max_ts: float,
) -> dict[str, pl.DataFrame]:
    """Load trades from Parquet snapshot filtered by asset_id and time range."""
    import datetime

    min_dt = datetime.datetime.fromtimestamp(min_ts, tz=datetime.timezone.utc)
    max_dt = datetime.datetime.fromtimestamp(max_ts, tz=datetime.timezone.utc)

    # Determine which monthly files to scan
    files = sorted(TRADES_DIR.glob("trades_*.parquet"))
    # Filter to relevant months
    relevant = []
    for f in files:
        # Extract YYYYMM from filename
        month_str = f.stem.replace("trades_", "")
        try:
            file_year = int(month_str[:4])
            file_month = int(month_str[4:6])
            file_dt = datetime.datetime(file_year, file_month, 1, tzinfo=datetime.timezone.utc)
            # Include if file month could contain data in our range
            # (trades after min_ts, file is at most 1 month before max_ts)
            if file_dt.year * 100 + file_dt.month >= min_dt.year * 100 + min_dt.month - 1:
                if file_dt <= max_dt:
                    relevant.append(f)
        except (ValueError, IndexError):
            continue

    if not relevant:
        return {}

    # Scan with predicate pushdown
    lf = pl.scan_parquet(relevant)
    trades = (
        lf.filter(pl.col("asset_id").is_in(list(asset_ids)))
        .select("asset_id", "price", "timestamp")
        .sort("timestamp")
        .collect()
    )

    # Group by asset_id
    result: dict[str, pl.DataFrame] = {}
    for aid in asset_ids:
        subset = trades.filter(pl.col("asset_id") == aid)
        if len(subset) > 0:
            result[aid] = subset

    return result


def run_sweep(name: str, ledger_path: str) -> pl.DataFrame:
    """Run trailing stop sweep for one strategy ledger."""
    ledger = pl.read_parquet(ledger_path)

    # Filter to resolved positions with fills
    ledger = ledger.filter(
        (pl.col("fill_status") == "filled")
        & (pl.col("resolution").is_not_null())
        & (pl.col("resolution") != "")
        & (pl.col("asset_id").is_not_null())
        & (pl.col("asset_id") != "")
    )

    if len(ledger) == 0:
        print(f"  {name}: no resolved positions, skipping")
        return pl.DataFrame()

    # Determine win/loss from resolution field ("WON" / "LOST")
    ledger = ledger.with_columns(
        pl.when(pl.col("resolution") == "WON")
        .then(pl.lit("win"))
        .otherwise(pl.lit("loss"))
        .alias("result")
    )

    n_win = ledger.filter(pl.col("result") == "win").height
    n_loss = ledger.filter(pl.col("result") == "loss").height
    print(f"  {name}: {len(ledger)} positions ({n_win} win, {n_loss} loss)")

    # Collect unique asset_ids and time range
    asset_ids = set(ledger["asset_id"].unique().to_list())
    min_ts = ledger["signal_time"].min()
    max_ts = ledger["resolved_at"].max()

    print(f"  Loading trades for {len(asset_ids)} assets...")
    t0 = time.time()
    trades_by_asset = load_trades_for_assets(asset_ids, min_ts, max_ts)
    print(f"  Loaded trades in {time.time() - t0:.1f}s ({sum(len(v) for v in trades_by_asset.values())} total)")

    # Run simulation for each delta
    rows: list[dict] = []
    for delta in TRAIL_DELTAS:
        triggered_wins = 0
        triggered_losses = 0
        total_pnl_sl = 0.0
        total_pnl_hold = 0.0
        saved_on_losses = 0.0  # PnL improvement on losing trades
        lost_on_wins = 0.0  # PnL given up on winning trades
        n_triggered = 0

        for row in ledger.iter_rows(named=True):
            aid = row["asset_id"]
            entry_price = row["fill_price"]
            entry_time = row["signal_time"]
            resolution = row["result"]
            fill_size = row["fill_size_usd"]

            # Get prices after entry
            asset_trades = trades_by_asset.get(aid)
            if asset_trades is None or len(asset_trades) == 0:
                # No trade data — assume hold
                qty = fill_size / entry_price
                if resolution == "win":
                    pnl = (1.0 - entry_price) * qty
                else:
                    pnl = -fill_size
                total_pnl_hold += pnl
                total_pnl_sl += pnl
                continue

            # Filter to trades after entry
            ts_col = asset_trades["timestamp"]
            import datetime
            entry_dt = datetime.datetime.fromtimestamp(
                entry_time, tz=datetime.timezone.utc,
            )
            mask = ts_col > entry_dt
            post_entry = asset_trades.filter(mask)

            if len(post_entry) == 0:
                qty = fill_size / entry_price
                if resolution == "win":
                    pnl = (1.0 - entry_price) * qty
                else:
                    pnl = -fill_size
                total_pnl_hold += pnl
                total_pnl_sl += pnl
                continue

            prices = post_entry["price"].to_list()

            result = simulate_trailing_stop(
                entry_price=entry_price,
                trail_delta=delta,
                prices=prices,
                resolution=resolution,
                fill_size_usd=fill_size,
            )

            total_pnl_hold += result.pnl_hold
            total_pnl_sl += result.pnl_sl

            if result.triggered:
                n_triggered += 1
                diff = result.pnl_sl - result.pnl_hold
                if resolution == "win":
                    triggered_wins += 1
                    lost_on_wins += abs(diff) if diff < 0 else 0
                else:
                    triggered_losses += 1
                    saved_on_losses += diff if diff > 0 else 0

        improvement = total_pnl_sl - total_pnl_hold
        rows.append({
            "strategy": name,
            "trail_delta": delta,
            "pnl_hold": round(total_pnl_hold, 2),
            "pnl_sl": round(total_pnl_sl, 2),
            "improvement": round(improvement, 2),
            "improvement_pct": round(improvement / abs(total_pnl_hold) * 100, 1) if total_pnl_hold != 0 else 0,
            "n_triggered": n_triggered,
            "triggered_pct": round(n_triggered / len(ledger) * 100, 1),
            "triggered_wins": triggered_wins,
            "triggered_losses": triggered_losses,
            "saved_on_losses": round(saved_on_losses, 2),
            "lost_on_wins": round(lost_on_wins, 2),
        })

    return pl.DataFrame(rows)


def run_bucketed_sweep(name: str, ledger_path: str) -> None:
    """Run sweep bucketed by entry price to find where SL helps most."""
    ledger = pl.read_parquet(ledger_path)

    ledger = ledger.filter(
        (pl.col("fill_status") == "filled")
        & (pl.col("resolution").is_not_null())
        & (pl.col("resolution") != "")
        & (pl.col("asset_id").is_not_null())
        & (pl.col("asset_id") != "")
    )

    ledger = ledger.with_columns(
        pl.when(pl.col("resolution") == "WON")
        .then(pl.lit("win"))
        .otherwise(pl.lit("loss"))
        .alias("result")
    )

    if len(ledger) == 0:
        return

    # Entry price buckets
    BUCKETS = [(0, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 1.0)]
    DELTAS_FINE = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]

    asset_ids = set(ledger["asset_id"].unique().to_list())
    min_ts = ledger["signal_time"].min()
    max_ts = ledger["resolved_at"].max()
    trades_by_asset = load_trades_for_assets(asset_ids, min_ts, max_ts)

    import datetime

    print(f"\n{'='*90}")
    print(f"  {name} — BY ENTRY PRICE BUCKET")
    print(f"{'='*90}")

    for lo, hi in BUCKETS:
        bucket = ledger.filter(
            (pl.col("fill_price") >= lo) & (pl.col("fill_price") < hi)
        )
        if len(bucket) == 0:
            continue

        n_win = bucket.filter(pl.col("result") == "win").height
        n_loss = bucket.filter(pl.col("result") == "loss").height
        hr = n_win / len(bucket) * 100

        print(f"\n  Entry [{lo:.2f}, {hi:.2f})  n={len(bucket)}  win={n_win} loss={n_loss}  HR={hr:.0f}%")
        print(f"  {'delta':>6} {'hold':>10} {'sl':>10} {'improv':>10} {'%':>7} "
              f"{'trig':>5} {'t_win':>5} {'t_loss':>6} {'saved':>10} {'lost':>10} "
              f"{'cap_freed':>10}")

        for delta in DELTAS_FINE:
            total_pnl_hold = 0.0
            total_pnl_sl = 0.0
            n_triggered = 0
            triggered_wins = 0
            triggered_losses = 0
            saved = 0.0
            lost = 0.0
            capital_freed_days = 0.0  # sum of (hold_duration - exit_time) for triggered

            for row in bucket.iter_rows(named=True):
                aid = row["asset_id"]
                entry_price = row["fill_price"]
                entry_time = row["signal_time"]
                resolved_at = row["resolved_at"]
                resolution = row["result"]
                fill_size = row["fill_size_usd"]

                asset_trades = trades_by_asset.get(aid)
                qty = fill_size / entry_price

                if resolution == "win":
                    pnl_hold = (1.0 - entry_price) * qty
                else:
                    pnl_hold = -fill_size

                total_pnl_hold += pnl_hold

                if asset_trades is None or len(asset_trades) == 0:
                    total_pnl_sl += pnl_hold
                    continue

                entry_dt = datetime.datetime.fromtimestamp(
                    entry_time, tz=datetime.timezone.utc,
                )
                post_entry = asset_trades.filter(pl.col("timestamp") > entry_dt)

                if len(post_entry) == 0:
                    total_pnl_sl += pnl_hold
                    continue

                prices = post_entry["price"].to_list()
                timestamps = post_entry["timestamp"].to_list()

                # Simulate
                hwm = entry_price
                triggered = False
                for i, price in enumerate(prices):
                    if price > hwm:
                        hwm = price
                    if price <= hwm - delta:
                        # Triggered
                        pnl_sl = (price - entry_price) * qty
                        total_pnl_sl += pnl_sl
                        n_triggered += 1
                        diff = pnl_sl - pnl_hold

                        if resolution == "win":
                            triggered_wins += 1
                            lost += abs(diff) if diff < 0 else 0
                        else:
                            triggered_losses += 1
                            saved += diff if diff > 0 else 0

                        # Capital freed: days between exit and resolution
                        exit_ts = timestamps[i].timestamp() if hasattr(timestamps[i], 'timestamp') else float(timestamps[i])
                        freed_days = (resolved_at - exit_ts) / 86400
                        capital_freed_days += max(freed_days, 0) * fill_size / 100  # weighted by $100 position

                        triggered = True
                        break

                if not triggered:
                    total_pnl_sl += pnl_hold

            improvement = total_pnl_sl - total_pnl_hold
            imp_pct = improvement / abs(total_pnl_hold) * 100 if total_pnl_hold != 0 else 0
            print(
                f"  {delta:6.2f} {total_pnl_hold:>10.0f} {total_pnl_sl:>10.0f} "
                f"{improvement:>+10.0f} {imp_pct:>+6.1f}% "
                f"{n_triggered:>5} {triggered_wins:>5} {triggered_losses:>6} "
                f"{saved:>10.0f} {lost:>10.0f} "
                f"{capital_freed_days:>10.1f}"
            )


def main() -> None:
    print("=== Trailing Stop Delta Sweep ===\n")
    all_results: list[pl.DataFrame] = []

    for name, path in LEDGER_FILES.items():
        if not Path(path).exists():
            print(f"  {name}: ledger not found at {path}, skipping")
            continue
        print(f"\n--- {name} ---")
        df = run_sweep(name, path)
        if len(df) > 0:
            all_results.append(df)

    if not all_results:
        print("\nNo results to display.")
        return

    combined = pl.concat(all_results)

    # Display per-strategy results
    for name in combined["strategy"].unique().to_list():
        strat_df = combined.filter(pl.col("strategy") == name)
        print(f"\n{'='*80}")
        print(f"  {name}")
        print(f"{'='*80}")
        print(strat_df.select(
            "trail_delta", "pnl_hold", "pnl_sl", "improvement", "improvement_pct",
            "n_triggered", "triggered_pct", "triggered_wins", "triggered_losses",
            "saved_on_losses", "lost_on_wins",
        ))

    # Summary: best delta per strategy
    print(f"\n{'='*80}")
    print("  BEST DELTA PER STRATEGY (max improvement)")
    print(f"{'='*80}")
    for name in combined["strategy"].unique().to_list():
        strat_df = combined.filter(pl.col("strategy") == name)
        best = strat_df.sort("improvement", descending=True).head(1)
        delta = best["trail_delta"][0]
        imp = best["improvement"][0]
        imp_pct = best["improvement_pct"][0]
        pnl_hold = best["pnl_hold"][0]
        pnl_sl = best["pnl_sl"][0]
        trig_pct = best["triggered_pct"][0]
        print(
            f"  {name:20s}  delta={delta:.2f}  "
            f"hold=${pnl_hold:>8.2f}  sl=${pnl_sl:>8.2f}  "
            f"improvement=${imp:>7.2f} ({imp_pct:+.1f}%)  "
            f"triggered={trig_pct:.0f}%"
        )

    # Bucketed analysis
    print("\n\n" + "="*90)
    print("  ENTRY PRICE BUCKETED ANALYSIS")
    print("="*90)
    for name, path in LEDGER_FILES.items():
        if Path(path).exists():
            run_bucketed_sweep(name, path)


if __name__ == "__main__":
    main()
