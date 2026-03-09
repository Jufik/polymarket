"""Mid-window GBM repricing edge analysis.

Core question: Does Polymarket price lag behind exchange-derived fair value
DURING a 5m/15m window? If BTC moves up 0.1% in the first 2 minutes,
does PM still price P(Up) at 0.50 when GBM says it should be 0.65?

Approach:
  1. Get all resolved BTC up/down markets with time ranges from ClickHouse
  2. Parse window start/end times from question text
  3. Get PM minute-level P(Up) VWAP from CH trades during each window
  4. Get Binance 1m candles during each window
  5. Compute GBM P(Up) at each minute: Φ(d₂) where d₂ = ln(S_t/S₀)/(σ√(T-t))
  6. Compare PM price vs GBM fair value to quantify lag

Usage:
  PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/midwindow_edge.py
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import structlog
from scipy.stats import norm

log = structlog.get_logger()

CH_URL = "http://localhost:18123/?database=polymarket"
DATA_DIR = Path("research/hypotheses/crypto-scalp/data")
KLINES_DIR = DATA_DIR / "klines"
OUTPUT_DIR = DATA_DIR / "midwindow"

ET = ZoneInfo("America/New_York")


# ── Phase 1: Extract market metadata ─────────────────────────────────────────


def _fetch_ch(query: str) -> str:
    """Execute ClickHouse query and return TSV result."""
    import urllib.request

    req = urllib.request.Request(CH_URL, data=query.encode())
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode()


def fetch_markets() -> pl.DataFrame:
    """Get all resolved BTC up/down markets with time ranges from CH."""
    query = """
    SELECT
        condition_id,
        question,
        winner_outcome,
        token_yes,
        token_no,
        created_at,
        closed_at
    FROM markets
    WHERE (lower(question) LIKE '%bitcoin%' OR lower(question) LIKE '%btc%')
      AND lower(question) LIKE '%up or down%'
      AND (question LIKE '%AM-%' OR question LIKE '%PM-%')
      AND winner_outcome <> ''
    FORMAT TSVWithNames
    """
    raw = _fetch_ch(query)
    lines = raw.strip().split("\n")
    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return pl.DataFrame(
        {header[i]: [r[i] for r in rows] for i in range(len(header))}
    )


# ── Phase 2: Parse window times ─────────────────────────────────────────────


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(AM|PM)")
_DATE_RE = re.compile(r"(\w+)\s+(\d{1,2}),?\s*(\d{1,2}):(\d{2})(AM|PM)\s*-\s*(\d{1,2}):(\d{2})(AM|PM)")
_DATE_SIMPLE_RE = re.compile(r"-\s+(\w+)\s+(\d{1,2}),")

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def _parse_time_12h(h: int, m: int, ampm: str) -> tuple[int, int]:
    """Convert 12h time to 24h (hour, minute)."""
    if ampm == "AM":
        hour = 0 if h == 12 else h
    else:
        hour = h if h == 12 else h + 12
    return hour, m


def parse_window(question: str) -> tuple[datetime, datetime, int] | None:
    """Parse question to extract window_start_utc, window_end_utc, duration_min.

    Example: "Bitcoin Up or Down - December 1, 10:00AM-10:15AM ET"
    Returns (2025-12-01T15:00Z, 2025-12-01T15:15Z, 15)
    """
    # Extract date
    date_match = _DATE_SIMPLE_RE.search(question)
    if not date_match:
        return None

    month_name = date_match.group(1)
    day = int(date_match.group(2))
    month = MONTHS.get(month_name)
    if month is None:
        return None

    # Determine year from context (data spans Sep 2025 - Mar 2026)
    if month >= 9:
        year = 2025
    else:
        year = 2026

    # Extract time range
    times = _TIME_RE.findall(question)
    if len(times) < 2:
        return None

    h1, m1, ap1 = int(times[0][0]), int(times[0][1]), times[0][2]
    h2, m2, ap2 = int(times[1][0]), int(times[1][1]), times[1][2]

    hour1, min1 = _parse_time_12h(h1, m1, ap1)
    hour2, min2 = _parse_time_12h(h2, m2, ap2)

    try:
        start_et = datetime(year, month, day, hour1, min1, tzinfo=ET)
        end_et = datetime(year, month, day, hour2, min2, tzinfo=ET)

        # Handle midnight crossing (e.g. 11:45PM-12:00AM)
        if end_et <= start_et:
            end_et += timedelta(days=1)

        start_utc = start_et.astimezone(timezone.utc)
        end_utc = end_et.astimezone(timezone.utc)
        duration_min = int((end_utc - start_utc).total_seconds() / 60)

        return start_utc, end_utc, duration_min
    except (ValueError, OverflowError):
        return None


# ── Phase 3: Fetch PM trades (minute-level aggregates) ───────────────────────


def fetch_pm_trades_agg(condition_ids: list[str]) -> pl.DataFrame:
    """Fetch minute-level P(Up) VWAP from CH for all condition_ids.

    Returns: condition_id, minute_utc, p_up_vwap, volume, trades
    """
    # Build condition_id list for IN clause
    ids_str = ",".join(f"'{cid}'" for cid in condition_ids)

    query = f"""
    SELECT
        t.condition_id,
        toStartOfMinute(t.timestamp) as minute,
        -- Compute P(Up) from YES and NO token trades
        sum(
            CASE
                WHEN t.asset_id = m.token_yes THEN t.price * t.size
                WHEN t.asset_id = m.token_no THEN (1 - t.price) * t.size
                ELSE 0
            END
        ) / nullIf(sum(t.size), 0) as p_up_vwap,
        sum(t.size) as volume,
        count(*) as trades
    FROM trades_raw t
    JOIN markets m ON t.condition_id = m.condition_id
    WHERE t.condition_id IN ({ids_str})
    GROUP BY t.condition_id, minute
    HAVING volume > 0
    ORDER BY t.condition_id, minute
    FORMAT TSVWithNames
    """
    raw = _fetch_ch(query)
    lines = raw.strip().split("\n")
    if len(lines) <= 1:
        return pl.DataFrame(
            schema={
                "condition_id": pl.Utf8,
                "minute": pl.Utf8,
                "p_up_vwap": pl.Float64,
                "volume": pl.Float64,
                "trades": pl.Int64,
            }
        )

    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:] if line.strip()]
    return pl.DataFrame(
        {header[i]: [r[i] if i < len(r) else "" for r in rows] for i in range(len(header))}
    ).with_columns(
        pl.col("p_up_vwap").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
        pl.col("trades").cast(pl.Int64),
    )


# ── Phase 4: Load Binance klines ─────────────────────────────────────────────


def load_binance_klines() -> pl.DataFrame:
    """Load Binance BTCUSDT 1m klines and index by minute."""
    path = KLINES_DIR / "BTCUSDT_1m_180d.parquet"
    if not path.exists():
        log.error("klines_missing", path=str(path))
        sys.exit(1)

    df = pl.read_parquet(path)
    # Add UTC datetime column from open_time (ms)
    df = df.with_columns(
        (pl.col("open_time") * 1000).cast(pl.Datetime("us", "UTC")).alias("minute_utc"),
    )
    return df


# ── Phase 5: GBM fair value computation ──────────────────────────────────────


def compute_gbm_p_up(
    s_start: float,
    s_current: float,
    sigma: float,
    time_remaining_min: float,
    total_duration_min: float,
) -> float:
    """Compute GBM P(Up) = Φ(d₂).

    P(Up) = P(S_T > S₀) given S_t observed at time t.

    d₂ = ln(S_t / S₀) / (σ √(T - t))

    At t=0: d₂ = 0, P(Up) = 0.50
    At t→T: d₂ → ±∞, P(Up) → 0 or 1
    """
    if time_remaining_min <= 0:
        return 1.0 if s_current > s_start else 0.0

    log_return = np.log(s_current / s_start)
    # Convert sigma from per-minute to match time units
    tau = time_remaining_min  # in minutes
    vol = sigma * np.sqrt(tau)

    if vol < 1e-12:
        return 1.0 if log_return > 0 else 0.0

    d2 = log_return / vol
    return float(norm.cdf(d2))


def estimate_sigma(klines: pl.DataFrame, lookback_hours: int = 24) -> pl.DataFrame:
    """Estimate realized volatility (per-minute σ) using rolling window.

    Returns klines with added 'sigma_1m' column.
    """
    # 1-minute log returns
    klines = klines.with_columns(
        (pl.col("close") / pl.col("close").shift(1)).log().alias("log_ret")
    )
    # Rolling std of log returns (lookback window in minutes)
    window = lookback_hours * 60
    klines = klines.with_columns(
        pl.col("log_ret").rolling_std(window_size=window, min_periods=60).alias("sigma_1m")
    )
    return klines


# ── Phase 6: Main analysis ───────────────────────────────────────────────────


def run_analysis() -> None:
    """Run the full mid-window GBM repricing analysis."""

    # Step 1: Get market metadata
    log.info("fetching_markets")
    markets = fetch_markets()
    log.info("markets_fetched", count=len(markets))

    # Step 2: Parse windows
    parsed = []
    for row in markets.iter_rows(named=True):
        result = parse_window(row["question"])
        if result is None:
            continue
        start_utc, end_utc, duration_min = result
        # Only keep 5m and 15m windows
        if duration_min not in (5, 15):
            continue
        parsed.append({
            "condition_id": row["condition_id"],
            "question": row["question"],
            "winner_outcome": row["winner_outcome"],
            "token_yes": row["token_yes"],
            "token_no": row["token_no"],
            "window_start": start_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "window_end": end_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_min": duration_min,
            "went_up": 1 if row["winner_outcome"] == "Up" else 0,
        })

    windows = pl.DataFrame(parsed)
    log.info(
        "windows_parsed",
        total=len(windows),
        five_min=len(windows.filter(pl.col("duration_min") == 5)),
        fifteen_min=len(windows.filter(pl.col("duration_min") == 15)),
        up_pct=f"{windows['went_up'].mean() * 100:.1f}%",
    )

    if len(windows) == 0:
        log.error("no_windows_parsed")
        return

    # Step 3: Load Binance klines
    log.info("loading_klines")
    klines = load_binance_klines()
    klines = estimate_sigma(klines)
    log.info("klines_loaded", rows=len(klines))

    # Create minute → candle lookup
    kline_map: dict[str, dict] = {}
    for row in klines.iter_rows(named=True):
        minute_str = row["minute_utc"].strftime("%Y-%m-%d %H:%M:%S")
        kline_map[minute_str] = row

    # Step 4: Fetch PM trades (batch by chunks to avoid CH query size limits)
    log.info("fetching_pm_trades")
    all_condition_ids = windows["condition_id"].to_list()
    chunk_size = 500
    pm_chunks = []
    for i in range(0, len(all_condition_ids), chunk_size):
        chunk = all_condition_ids[i : i + chunk_size]
        log.info("fetching_chunk", i=i, size=len(chunk))
        pm_chunk = fetch_pm_trades_agg(chunk)
        pm_chunks.append(pm_chunk)

    pm_trades = pl.concat(pm_chunks) if pm_chunks else pl.DataFrame()
    log.info("pm_trades_fetched", rows=len(pm_trades))

    # Step 5: For each market, compute GBM P(Up) and PM P(Up) at each minute
    results = []
    no_kline_count = 0

    for win in windows.iter_rows(named=True):
        cid = win["condition_id"]
        start_str = win["window_start"]
        end_str = win["window_end"]
        duration = win["duration_min"]

        # Get Binance price at window start
        start_candle = kline_map.get(start_str)
        if start_candle is None:
            no_kline_count += 1
            continue

        s_start = start_candle["open"]
        sigma_1m = start_candle["sigma_1m"]
        if sigma_1m is None or np.isnan(sigma_1m) or sigma_1m < 1e-12:
            no_kline_count += 1
            continue

        # Get PM trades for this market during window
        market_trades = pm_trades.filter(
            (pl.col("condition_id") == cid)
            & (pl.col("minute") >= start_str)
            & (pl.col("minute") < end_str)
        )

        # For each minute in the window
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        for m in range(duration):
            minute_dt = start_dt + timedelta(minutes=m)
            minute_str = minute_dt.strftime("%Y-%m-%d %H:%M:%S")
            time_elapsed = m
            time_remaining = duration - m

            # Binance price at this minute
            candle = kline_map.get(minute_str)
            if candle is None:
                continue
            s_current = candle["close"]  # use close as best estimate of price at end of minute

            # GBM P(Up)
            gbm_p_up = compute_gbm_p_up(s_start, s_current, sigma_1m, time_remaining, duration)

            # PM P(Up) at this minute
            pm_row = market_trades.filter(pl.col("minute") == minute_str)
            pm_p_up = None
            pm_volume = 0.0
            pm_ntrades = 0
            if len(pm_row) > 0:
                pm_p_up = pm_row["p_up_vwap"][0]
                pm_volume = pm_row["volume"][0]
                pm_ntrades = pm_row["trades"][0]

            results.append({
                "condition_id": cid,
                "duration_min": duration,
                "minute_in_window": m,
                "pct_elapsed": m / duration,
                "went_up": win["went_up"],
                "btc_price": s_current,
                "btc_return": np.log(s_current / s_start),
                "sigma_1m": sigma_1m,
                "gbm_p_up": gbm_p_up,
                "pm_p_up": pm_p_up,
                "pm_volume": pm_volume,
                "pm_trades": pm_ntrades,
                "lag": (pm_p_up - gbm_p_up) if pm_p_up is not None else None,
            })

    log.info("analysis_complete", results=len(results), no_kline=no_kline_count)

    if not results:
        log.error("no_results")
        return

    df = pl.DataFrame(results)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUTPUT_DIR / "midwindow_analysis.parquet", compression="zstd")

    # ── Summary statistics ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("MID-WINDOW GBM REPRICING ANALYSIS")
    print("=" * 80)

    # Overall stats
    has_pm = df.filter(pl.col("pm_p_up").is_not_null())
    print(f"\nTotal observations: {len(df):,}")
    print(f"  With PM trades: {len(has_pm):,} ({len(has_pm)/len(df)*100:.1f}%)")
    print(f"  Markets analyzed: {df['condition_id'].n_unique():,}")
    print(f"  No kline data: {no_kline_count:,} markets skipped")

    for dur in [5, 15]:
        dur_df = has_pm.filter(pl.col("duration_min") == dur)
        if len(dur_df) == 0:
            continue

        print(f"\n{'─' * 60}")
        print(f"{dur}-MINUTE WINDOWS ({dur_df['condition_id'].n_unique():,} markets)")
        print(f"{'─' * 60}")

        # By minute in window
        for m in range(dur):
            minute_data = dur_df.filter(pl.col("minute_in_window") == m)
            if len(minute_data) == 0:
                continue

            lag = minute_data["lag"]
            abs_lag = lag.abs()
            gbm = minute_data["gbm_p_up"]
            pm = minute_data["pm_p_up"]

            print(f"\n  Minute {m} ({len(minute_data):,} obs):")
            print(f"    GBM P(Up) mean:  {gbm.mean():.4f}  std: {gbm.std():.4f}")
            print(f"    PM  P(Up) mean:  {pm.mean():.4f}  std: {pm.std():.4f}")
            print(f"    Lag (PM - GBM):  mean={lag.mean():.4f}  std={lag.std():.4f}")
            print(f"    |Lag|:           mean={abs_lag.mean():.4f}  median={abs_lag.median():.4f}")

            # Does the lag predict the winner?
            # If PM is too high vs GBM → sell Up (buy Down)
            # If PM is too low vs GBM → buy Up
            correct_direction = minute_data.with_columns(
                ((pl.col("gbm_p_up") > 0.5) == (pl.col("went_up") == 1)).alias("gbm_correct"),
                ((pl.col("pm_p_up") > 0.5) == (pl.col("went_up") == 1)).alias("pm_correct"),
            )
            gbm_acc = correct_direction["gbm_correct"].mean()
            pm_acc = correct_direction["pm_correct"].mean()
            print(f"    GBM accuracy:    {gbm_acc:.4f}")
            print(f"    PM accuracy:     {pm_acc:.4f}")

    # ── Edge analysis: can we profit from the lag? ───────────────────────────
    print(f"\n{'=' * 80}")
    print("EDGE ANALYSIS: Trading the GBM-PM lag")
    print(f"{'=' * 80}")

    for dur in [5, 15]:
        dur_df = has_pm.filter(pl.col("duration_min") == dur)
        if len(dur_df) == 0:
            continue

        print(f"\n{dur}-MINUTE WINDOWS:")

        for m in range(1, dur):  # skip minute 0 (no info yet)
            minute_data = dur_df.filter(pl.col("minute_in_window") == m)
            if len(minute_data) == 0:
                continue

            # Strategy: if GBM > PM + threshold → buy Up; if GBM < PM - threshold → buy Down
            for threshold in [0.0, 0.02, 0.05, 0.10, 0.15]:
                buy_up = minute_data.filter(
                    pl.col("gbm_p_up") > pl.col("pm_p_up") + threshold
                )
                buy_down = minute_data.filter(
                    pl.col("gbm_p_up") < pl.col("pm_p_up") - threshold
                )

                up_wins = buy_up.filter(pl.col("went_up") == 1)
                down_wins = buy_down.filter(pl.col("went_up") == 0)

                total_trades = len(buy_up) + len(buy_down)
                if total_trades == 0:
                    continue

                total_wins = len(up_wins) + len(down_wins)
                accuracy = total_wins / total_trades

                # Compute expected PnL per trade (binary option: pay pm_price, receive 1 if correct)
                # Buy Up: pay pm_p_up, receive 1 if went_up
                # Buy Down: pay (1 - pm_p_up), receive 1 if went_down
                up_pnl = 0.0
                if len(buy_up) > 0:
                    # Profit: 1 * (1 - fee) - pm_price if correct, -pm_price if wrong
                    up_correct = buy_up.filter(pl.col("went_up") == 1)
                    up_wrong = buy_up.filter(pl.col("went_up") == 0)
                    up_pnl = (
                        (up_correct["pm_p_up"] * 0).sum()  # placeholder - use (1-fee) - price
                        + len(up_correct) * 0.97  # receive $0.97 (3% fee)
                        - up_correct["pm_p_up"].sum()  # paid pm_price for winners
                        - up_wrong["pm_p_up"].sum()  # paid pm_price for losers
                    )

                down_pnl = 0.0
                if len(buy_down) > 0:
                    down_correct = buy_down.filter(pl.col("went_up") == 0)
                    down_wrong = buy_down.filter(pl.col("went_up") == 1)
                    down_pnl = (
                        len(down_correct) * 0.97
                        - (1 - down_correct["pm_p_up"]).sum()  # paid (1-pm) for Down winners
                        - (1 - down_wrong["pm_p_up"]).sum()  # paid (1-pm) for Down losers
                    )

                total_pnl = up_pnl + down_pnl
                avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

                if total_trades >= 10:
                    print(
                        f"  Min {m}, thresh={threshold:.2f}: "
                        f"trades={total_trades:,} acc={accuracy:.3f} "
                        f"PnL/trade=${avg_pnl:.4f} "
                        f"total_PnL=${total_pnl:.2f} "
                        f"(↑{len(buy_up)} ↓{len(buy_down)})"
                    )

    # ── GBM accuracy by confidence bucket ────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("GBM ACCURACY BY CONFIDENCE")
    print(f"{'=' * 80}")

    for dur in [5, 15]:
        dur_df = has_pm.filter(pl.col("duration_min") == dur)
        if len(dur_df) == 0:
            continue

        print(f"\n{dur}-MINUTE WINDOWS:")

        # Only look at minutes 1+ (after we have some price movement)
        mid = dur_df.filter(pl.col("minute_in_window") >= 1)

        for lo, hi in [(0.5, 0.55), (0.55, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.0)]:
            # GBM says Up
            up_bucket = mid.filter(
                (pl.col("gbm_p_up") >= lo) & (pl.col("gbm_p_up") < hi)
            )
            if len(up_bucket) > 0:
                acc = up_bucket["went_up"].mean()
                print(f"  GBM P(Up) [{lo:.2f}, {hi:.2f}): n={len(up_bucket):,} actual_up={acc:.3f}")

            # GBM says Down (mirror)
            down_bucket = mid.filter(
                (pl.col("gbm_p_up") > (1 - hi)) & (pl.col("gbm_p_up") <= (1 - lo))
            )
            if len(down_bucket) > 0:
                acc = 1 - down_bucket["went_up"].mean()
                print(f"  GBM P(Dn) [{lo:.2f}, {hi:.2f}): n={len(down_bucket):,} actual_dn={acc:.3f}")

    # Save summary
    summary = {
        "total_markets": len(windows),
        "total_observations": len(df),
        "with_pm_trades": len(has_pm),
        "no_kline_skipped": no_kline_count,
    }
    log.info("summary", **summary)


if __name__ == "__main__":
    run_analysis()
