"""
Phase 0: GBM Calibration & Alpha Discovery for Polymarket 5-Minute Crypto Markets
===================================================================================
Tests whether BTC price dynamics (Binance 1m candles) exhibit exploitable structure
relative to GBM assumptions used in Polymarket 5-min up/down market pricing.

Run: PYTHONPATH=. uv run python research/hypotheses/crypto-scalp/scripts/phase0_gbm_calibration.py
"""

import math
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats
from scipy.special import ndtr  # fast normal CDF

warnings.filterwarnings("ignore")

BASE = Path("research/hypotheses/crypto-scalp")
KLINES_PATH = BASE / "data/klines/BTCUSDT_1m_30d.parquet"

TRADING_MINS_PER_YEAR = 365 * 24 * 60
TAKER_FEE = 0.03  # 3% round-trip
MIN_ACCURACY_THRESHOLD = 0.53  # profitable after fees


# ─── Utility ──────────────────────────────────────────────────────────────────

def sep(title: str = "", width: int = 72) -> None:
    if title:
        pad = max(0, (width - len(title) - 2) // 2)
        print("=" * pad + f" {title} " + "=" * (width - pad - len(title) - 2))
    else:
        print("=" * width)


def subsep(title: str = "", width: int = 72) -> None:
    if title:
        print(f"\n--- {title} ---")
    else:
        print("-" * width)


def norm_cdf(x: float | np.ndarray) -> float | np.ndarray:
    """Normal CDF Φ(x)."""
    return ndtr(x)


def gbm_p_up(s_t: float, s_0: float, sigma: float, t_remaining: float) -> float:
    """
    GBM probability that S(T) >= S(0) given current S(t).
    Ignores drift (negligible at 5-min scale).
    P(Up | S_t) = Φ(ln(S_t/S_0) / (sigma * sqrt(T - t)))
    """
    if sigma <= 0 or t_remaining <= 0:
        return 0.5
    d2 = math.log(s_t / s_0) / (sigma * math.sqrt(t_remaining))
    return float(norm_cdf(d2))


# ─── Load Data ────────────────────────────────────────────────────────────────

def load_klines() -> pl.DataFrame:
    df = pl.read_parquet(KLINES_PATH)
    # Convert unix ms to datetime
    df = df.with_columns(
        pl.from_epoch("open_time", time_unit="ms").alias("dt"),
    ).sort("open_time")
    return df


# ─── Section 1: Base Rate Analysis ───────────────────────────────────────────

def section1_base_rates(df: pl.DataFrame) -> dict:
    sep("1. BASE RATE ANALYSIS")

    results = {}

    for window_min in [5, 15]:
        print(f"\n{window_min}-minute windows:")

        # Assign window ID to each row (non-overlapping)
        df_w = df.with_columns(
            (pl.col("open_time") // (window_min * 60 * 1000)).alias("window_id")
        )

        # For each window, take first and last candle
        windows = (
            df_w.group_by("window_id")
            .agg(
                pl.col("open").first().alias("open_price"),
                pl.col("close").last().alias("close_price"),
                pl.col("open_time").first().alias("start_ms"),
                pl.col("open_time").last().alias("end_ms"),
                pl.len().alias("n_candles"),
                pl.col("volume").sum().alias("total_volume"),
                pl.col("taker_buy_vol").sum().alias("taker_buy_total"),
                pl.col("quote_volume").sum().alias("total_quote_vol"),
            )
            .filter(pl.col("n_candles") == window_min)  # complete windows only
            .with_columns(
                (pl.col("close_price") >= pl.col("open_price")).alias("is_up"),
                (pl.col("close_price") / pl.col("open_price") - 1).alias("return_pct"),
            )
            .sort("start_ms")
        )

        n_total = len(windows)
        n_up = windows["is_up"].sum()
        n_down = n_total - n_up
        up_pct = n_up / n_total * 100
        down_pct = n_down / n_total * 100

        print(f"  Total complete windows : {n_total:,}")
        print(f"  Up windows             : {n_up:,} ({up_pct:.2f}%)")
        print(f"  Down windows           : {n_down:,} ({down_pct:.2f}%)")
        print(f"  Exact tie (flat close) : {(windows['return_pct'] == 0).sum():,}")

        ret = windows["return_pct"].to_numpy()
        print(f"  Avg return             : {ret.mean()*100:.4f}%")
        print(f"  Std return             : {ret.std()*100:.4f}%")
        print(f"  Max up                 : {ret.max()*100:.3f}%")
        print(f"  Max down               : {ret.min()*100:.3f}%")

        results[f"{window_min}m"] = {
            "windows": windows,
            "n_total": n_total,
            "n_up": n_up,
            "up_pct": up_pct / 100,
            "down_pct": down_pct / 100,
        }

    return results


# ─── Section 2: GBM Calibration ──────────────────────────────────────────────

def section2_gbm_calibration(df: pl.DataFrame, base_rates: dict) -> dict:
    sep("2. GBM CALIBRATION")
    print("\nGBM model: P(Up) = Φ(ln(S_t/S₀) / (σ√(T-t)))")
    print("σ = realized vol from rolling 30-min window of 1m returns (annualized)")
    print()

    # Build 5-min windows with per-minute prices
    windows_5m = base_rates["5m"]["windows"]

    # For GBM calibration we need per-minute price data inside each 5-min window
    df2 = df.with_columns(
        (pl.col("open_time") // (5 * 60 * 1000)).alias("window_id"),
        pl.col("open_time").alias("ts_ms"),
    )

    # Compute realized vol: rolling 30m window of 1m log returns (annualized)
    df2 = df2.with_columns(
        (pl.col("close") / pl.col("close").shift(1)).log().alias("log_ret_1m")
    ).with_columns(
        pl.col("log_ret_1m")
        .rolling_std(window_size=30, min_samples=10)
        .alias("sigma_1m_raw")
    ).with_columns(
        # Annualize: sigma_annual = sigma_1m * sqrt(minutes per year)
        (pl.col("sigma_1m_raw") * math.sqrt(TRADING_MINS_PER_YEAR)).alias("sigma_annual")
    )

    # For each candle at position t within its 5-min window:
    df3 = df2.with_columns(
        (
            pl.col("open_time") - (pl.col("window_id") * 5 * 60 * 1000)
        ).alias("ms_into_window"),
        # position within window (0=first candle, 4=last)
        (
            (pl.col("open_time") - (pl.col("window_id") * 5 * 60 * 1000))
            // (60 * 1000)
        ).alias("t_in_window"),
    )

    # Get open_price for each window (from first candle)
    window_opens = (
        df3.filter(pl.col("t_in_window") == 0)
        .select(["window_id", pl.col("close").alias("s0")])
    )

    df4 = df3.join(window_opens, on="window_id", how="inner")

    # Get resolution outcome for each window
    window_outcomes = (
        windows_5m
        .select(["window_id", "is_up"])
    )
    df4 = df4.join(window_outcomes, on="window_id", how="inner")

    calibration_by_minute = {}
    print(f"{'Minute':>8} | {'n':>6} | {'GBM Bucket':^12} | {'Actual Up%':^12} | {'Bucket_center':^14}")
    print("-" * 65)

    for t in [1, 2, 3, 4]:
        t_remaining_years = (5 - t) / TRADING_MINS_PER_YEAR  # T-t in years

        # Filter to candles at position t within window
        df_t = df4.filter(pl.col("t_in_window") == t).filter(pl.col("sigma_annual") > 0)

        if len(df_t) == 0:
            continue

        closes = df_t["close"].to_numpy()
        s0s = df_t["s0"].to_numpy()
        sigmas = df_t["sigma_annual"].to_numpy()
        is_up = df_t["is_up"].to_numpy()

        # Compute GBM P(Up) for each observation
        log_ratios = np.log(closes / s0s)
        d2s = log_ratios / (sigmas * math.sqrt(t_remaining_years))
        p_ups = norm_cdf(d2s)

        # Bucket into deciles
        decile_edges = np.linspace(0, 1, 11)
        buckets = np.digitize(p_ups, decile_edges) - 1
        buckets = np.clip(buckets, 0, 9)

        rows = []
        for b in range(10):
            mask = buckets == b
            n_b = mask.sum()
            if n_b < 10:
                continue
            actual_up = is_up[mask].mean()
            predicted_center = (decile_edges[b] + decile_edges[b + 1]) / 2
            rows.append((b, n_b, predicted_center, actual_up))

        calibration_by_minute[t] = rows

        if rows:
            print(f"\n  t={t} min (T-t = {5-t} min remaining):")
            print(f"  {'Bucket':>10} | {'n':>6} | {'Pred P(Up)':>12} | {'Actual Up%':>12} | {'Bias':>8}")
            print("  " + "-" * 52)
            for b, n_b, pred, actual in rows:
                bias = actual - pred
                print(f"  [{pred-0.05:.0%}-{pred+0.05:.0%}] | {n_b:>6,} | {pred:>12.3f} | {actual:>12.3f} | {bias:>+8.3f}")

    return {
        "calibration_by_minute": calibration_by_minute,
        "df4": df4,
    }


# ─── Section 3: Feature Predictiveness ────────────────────────────────────────

def section3_feature_predictiveness(df: pl.DataFrame, base_rates: dict) -> dict:
    sep("3. FEATURE PREDICTIVENESS")

    windows_5m = base_rates["5m"]["windows"]
    n_total = base_rates["5m"]["n_total"]
    base_rate_up = base_rates["5m"]["up_pct"]

    print(f"\nBase rate: {base_rate_up:.3%} Up  |  Profitable threshold: >{MIN_ACCURACY_THRESHOLD:.0%}")
    print(f"Window count: {n_total:,}")

    # Build enriched feature table for 5-min windows
    df2 = df.with_columns(
        (pl.col("open_time") // (5 * 60 * 1000)).alias("window_id"),
        pl.col("close").log().diff().alias("log_ret_1m"),
    )

    # Compute prior 5m and 15m returns: sum of log returns in prior window
    # prior_5m: previous 5 candles' log returns summed
    df2 = df2.with_columns(
        pl.col("log_ret_1m").rolling_sum(window_size=5, min_samples=5).alias("mom_5m_raw"),
        pl.col("log_ret_1m").rolling_sum(window_size=15, min_samples=15).alias("mom_15m_raw"),
        pl.col("volume").rolling_mean(window_size=5 * 6, min_samples=10).alias("avg_vol_5m"),
        pl.col("taker_buy_vol").rolling_sum(window_size=5, min_samples=5).alias("taker_buy_5m"),
        pl.col("volume").rolling_sum(window_size=5, min_samples=5).alias("total_vol_5m"),
        pl.col("log_ret_1m").rolling_std(window_size=30, min_samples=10).alias("sigma_1m"),
    )

    # Get last candle of each window for features
    # Features are computed BEFORE window opens (use prior data)
    # We use the state at the START of the window (= close of prior window end)
    # So: get candle at t=-1 relative to each window start
    window_starts = (
        df2.with_columns(
            (pl.col("open_time") - (pl.col("window_id") * 5 * 60 * 1000)).alias("ms_into_window")
        )
        .filter(pl.col("ms_into_window") == 0)  # first candle of each window
        .select([
            "window_id", "open_time",
            pl.col("mom_5m_raw").alias("mom_5m"),
            pl.col("mom_15m_raw").alias("mom_15m"),
            pl.col("avg_vol_5m"),
            pl.col("taker_buy_5m"),
            pl.col("total_vol_5m"),
            pl.col("sigma_1m"),
        ])
    )

    # Add taker buy ratio
    window_starts = window_starts.with_columns(
        (pl.col("taker_buy_5m") / (pl.col("total_vol_5m") + 1e-9)).alias("taker_buy_ratio"),
        (pl.col("total_vol_5m") / (pl.col("avg_vol_5m") * 5 + 1e-9)).alias("vol_ratio"),
    )

    # Add hour of day
    window_starts = window_starts.with_columns(
        pl.from_epoch("open_time", time_unit="ms").alias("dt")
    ).with_columns(
        pl.col("dt").dt.hour().alias("hour")
    )

    # Join with outcomes
    feat_df = window_starts.join(windows_5m.select(["window_id", "is_up"]), on="window_id", how="inner")
    feat_df = feat_df.drop_nulls()

    print(f"\nFeature dataset rows: {len(feat_df):,}")

    feature_results = {}
    features_tested = [
        ("mom_5m", "Momentum (prior 5m return)", 4),
        ("mom_15m", "Momentum (prior 15m return)", 4),
        ("vol_ratio", "Volume ratio (curr/avg)", 4),
        ("taker_buy_ratio", "Taker buy ratio", 4),
        ("sigma_1m", "Volatility (realized 30m σ)", 4),
    ]

    flagged = []

    for feat_col, feat_name, n_quantiles in features_tested:
        subsep(feat_name)
        vals = feat_df[feat_col].to_numpy()
        is_up = feat_df["is_up"].to_numpy().astype(bool)

        # Compute quantiles
        quantile_edges = np.quantile(vals, np.linspace(0, 1, n_quantiles + 1))
        quantile_edges[0] -= 1e-10  # include minimum
        quantile_edges[-1] += 1e-10

        print(f"  {'Quantile':>10} | {'Range':>24} | {'n':>6} | {'Up%':>8} | {'vs Base':>8} | {'Sig':>5}")
        print("  " + "-" * 72)

        chi2_observed = []
        chi2_expected = []
        q_data = []

        for q in range(n_quantiles):
            mask = (vals > quantile_edges[q]) & (vals <= quantile_edges[q + 1])
            n_q = mask.sum()
            if n_q == 0:
                continue
            up_rate = is_up[mask].mean()
            vs_base = up_rate - base_rate_up
            label = f"Q{q+1}/{n_quantiles}"
            r_lo = quantile_edges[q]
            r_hi = quantile_edges[q + 1]
            range_str = f"[{r_lo:+.4f}, {r_hi:+.4f}]"
            star = "*" if abs(up_rate - base_rate_up) > 0.03 else ""
            print(f"  {label:>10} | {range_str:>24} | {n_q:>6,} | {up_rate:>8.3%} | {vs_base:>+8.3%} | {star:>5}")

            chi2_observed.append(is_up[mask].sum())
            chi2_expected.append(n_q * base_rate_up)
            q_data.append((label, n_q, up_rate, vs_base))

        # Chi-squared test
        if len(chi2_observed) > 1:
            # Proper chi-sq: observed vs expected for Up/Down across quantiles
            obs_matrix = np.array([[r.sum() for r in [is_up[
                (vals > quantile_edges[q]) & (vals <= quantile_edges[q + 1])
            ] for q in range(n_quantiles)]]]).T

            # Build contingency table: [Up counts, Down counts] per quantile
            contingency = np.zeros((n_quantiles, 2))
            for q in range(n_quantiles):
                mask = (vals > quantile_edges[q]) & (vals <= quantile_edges[q + 1])
                contingency[q, 0] = is_up[mask].sum()
                contingency[q, 1] = (~is_up[mask]).sum()

            chi2_stat, p_val, dof, _ = stats.chi2_contingency(contingency)
            sig_str = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else "ns"
            print(f"\n  Chi-squared: χ²={chi2_stat:.2f}, p={p_val:.4f} [{sig_str}]")

            max_accuracy = max(r[2] for r in q_data)
            if max_accuracy > MIN_ACCURACY_THRESHOLD:
                print(f"  *** FLAG: Max accuracy {max_accuracy:.3%} > {MIN_ACCURACY_THRESHOLD:.0%} threshold ***")
                flagged.append((feat_name, max_accuracy))

            feature_results[feat_col] = {
                "quantiles": q_data,
                "chi2": chi2_stat,
                "p_val": p_val,
                "max_accuracy": max_accuracy,
                "significant": p_val < 0.05,
            }

    # Hour-of-day analysis
    subsep("Hour of day (UTC)")
    is_up = feat_df["is_up"].to_numpy().astype(bool)
    hours = feat_df["hour"].to_numpy()

    print(f"  {'Hour':>8} | {'n':>6} | {'Up%':>8} | {'vs Base':>8}")
    print("  " + "-" * 40)

    hour_data = []
    for h in range(24):
        mask = hours == h
        n_h = mask.sum()
        if n_h < 30:
            continue
        up_rate = is_up[mask].mean()
        vs_base = up_rate - base_rate_up
        hour_data.append((h, n_h, up_rate, vs_base))
        star = "*" if abs(vs_base) > 0.05 else ""
        print(f"  {h:>8}h | {n_h:>6,} | {up_rate:>8.3%} | {vs_base:>+8.3%} {star}")

    feature_results["hour"] = hour_data
    feature_results["_feat_df"] = feat_df
    feature_results["_flagged"] = flagged

    return feature_results


# ─── Section 4: Conditional Edge Analysis ────────────────────────────────────

def section4_conditional_edge(feat_results: dict, base_rates: dict) -> dict:
    sep("4. CONDITIONAL EDGE ANALYSIS")

    feat_df = feat_results["_feat_df"]
    base_rate_up = base_rates["5m"]["up_pct"]

    is_up = feat_df["is_up"].to_numpy().astype(bool)
    mom_5m = feat_df["mom_5m"].to_numpy()
    taker_ratio = feat_df["taker_buy_ratio"].to_numpy()
    vol_ratio = feat_df["vol_ratio"].to_numpy()
    mom_15m = feat_df["mom_15m"].to_numpy()

    print("\nCombined conditions — testing high-conviction signals:")
    print(f"  Base rate: {base_rate_up:.3%}")
    print(f"  Profitable threshold: >{MIN_ACCURACY_THRESHOLD:.0%}")
    print()

    # Define thresholds using quantiles
    mom5_q75 = np.quantile(mom_5m, 0.75)
    mom5_q25 = np.quantile(mom_5m, 0.25)
    mom15_q75 = np.quantile(mom_15m, 0.75)
    mom15_q25 = np.quantile(mom_15m, 0.25)
    taker_q75 = np.quantile(taker_ratio, 0.75)
    taker_q25 = np.quantile(taker_ratio, 0.25)
    vol_q75 = np.quantile(vol_ratio, 0.75)

    conditions = [
        ("Strong UP momentum (top Q mom_5m)", mom_5m > mom5_q75, "UP"),
        ("Strong DN momentum (bot Q mom_5m)", mom_5m < mom5_q25, "DN"),
        ("Strong UP momentum (top Q mom_15m)", mom_15m > mom15_q75, "UP"),
        ("Strong DN momentum (bot Q mom_15m)", mom_15m < mom15_q25, "DN"),
        ("High taker buy ratio (top Q)", taker_ratio > taker_q75, "UP"),
        ("Low taker buy ratio (bot Q)", taker_ratio < taker_q25, "DN"),
        ("High vol + UP momentum", (vol_ratio > vol_q75) & (mom_5m > mom5_q75), "UP"),
        ("High vol + DN momentum", (vol_ratio > vol_q75) & (mom_5m < mom5_q25), "DN"),
        ("High taker + UP momentum", (taker_ratio > taker_q75) & (mom_5m > mom5_q75), "UP"),
        ("Low taker + DN momentum", (taker_ratio < taker_q25) & (mom_5m < mom5_q25), "DN"),
        ("High taker + UP 15m", (taker_ratio > taker_q75) & (mom_15m > mom15_q75), "UP"),
        ("Low taker + DN 15m", (taker_ratio < taker_q25) & (mom_15m < mom15_q25), "DN"),
        ("High taker + high vol + UP mom", (taker_ratio > taker_q75) & (vol_ratio > vol_q75) & (mom_5m > mom5_q75), "UP"),
    ]

    print(f"  {'Condition':>44} | {'n':>6} | {'Hit%':>8} | {'vs Base':>8} | {'E[PnL/trade]':>14}")
    print("  " + "-" * 92)

    edge_results = []
    for cond_name, mask, direction in conditions:
        n = mask.sum()
        if n < 20:
            continue
        if direction == "UP":
            hit_rate = is_up[mask].mean()
        else:  # DN: we bet DOWN, so hit = actually Down
            hit_rate = (~is_up[mask]).mean()

        vs_base = hit_rate - base_rate_up

        # Expected PnL: buy at ~0.50+fee, win at 1.0
        # If hit_rate × 1.0 - (0.50 + 0.015) > 0 → profitable
        # More precisely: entry price for market signal ≈ 0.50 + spread/2
        # EV per $1 contract = hit_rate × 1.0 + (1-hit_rate) × 0.0 - entry_price
        # Assume entry_price = 0.50 (ignore fee for directional accuracy)
        entry_price = 0.50 + TAKER_FEE / 2  # rough estimate
        e_pnl = hit_rate * 1.0 - entry_price  # per $1 contract

        star = "***" if hit_rate > MIN_ACCURACY_THRESHOLD else ""
        print(f"  {cond_name:>44} | {n:>6,} | {hit_rate:>8.3%} | {vs_base:>+8.3%} | {e_pnl:>+14.4f} {star}")

        edge_results.append({
            "condition": cond_name,
            "direction": direction,
            "n": n,
            "hit_rate": hit_rate,
            "vs_base": vs_base,
            "e_pnl": e_pnl,
        })

    return {"edge_results": edge_results}


# ─── Section 5: GBM vs Reality at Mid-Window ─────────────────────────────────

def section5_gbm_vs_reality(df: pl.DataFrame, base_rates: dict) -> dict:
    sep("5. GBM vs REALITY AT MID-WINDOW (t=2.5 min)")
    print("\nKey test: does BTC momentum at t=2.5 min into a 5-min window")
    print("follow GBM, or is there systematic under/over-pricing?")
    print()
    print("S₀ = price at window open")
    print("S_t = price at t=2 min (close of 2nd candle)")
    print("T-t = 3 min remaining")
    print("GBM P(Up) = Φ(ln(S_t/S₀) / (σ√3/SQRT(MINS_PER_YEAR)))")
    print()

    windows_5m = base_rates["5m"]["windows"]

    df2 = df.with_columns(
        (pl.col("open_time") // (5 * 60 * 1000)).alias("window_id"),
        pl.col("close").log().diff().alias("log_ret_1m"),
    ).with_columns(
        pl.col("log_ret_1m").rolling_std(window_size=30, min_samples=10).alias("sigma_1m"),
    )

    df2 = df2.with_columns(
        (
            (pl.col("open_time") - (pl.col("window_id") * 5 * 60 * 1000))
            // (60 * 1000)
        ).alias("t_in_window"),
    )

    # Get S₀ (price at window open = t=0)
    s0_df = (
        df2.filter(pl.col("t_in_window") == 0)
        .select(["window_id", pl.col("close").alias("s0")])
    )

    # Get S_t at t=2 (close of 2nd candle) with sigma
    st_df = (
        df2.filter(pl.col("t_in_window") == 2)
        .select(["window_id", pl.col("close").alias("st"), pl.col("sigma_1m")])
    )

    mid_df = s0_df.join(st_df, on="window_id", how="inner")
    mid_df = mid_df.join(windows_5m.select(["window_id", "is_up"]), on="window_id", how="inner")
    mid_df = mid_df.drop_nulls()

    # Compute return at t=2 (% change from S₀)
    mid_df = mid_df.with_columns(
        ((pl.col("st") / pl.col("s0")) - 1).alias("ret_at_t2"),
    )

    # Compute GBM P(Up)
    t_remaining_years = 3 / TRADING_MINS_PER_YEAR
    st_arr = mid_df["st"].to_numpy()
    s0_arr = mid_df["s0"].to_numpy()
    sigma_arr = mid_df["sigma_1m"].to_numpy() * math.sqrt(TRADING_MINS_PER_YEAR)
    is_up = mid_df["is_up"].to_numpy().astype(bool)
    ret_at_t2 = mid_df["ret_at_t2"].to_numpy()

    # GBM predictions
    log_ratio = np.log(st_arr / s0_arr)
    safe_sigma = np.where(sigma_arr > 0, sigma_arr, 1e-9)
    d2 = log_ratio / (safe_sigma * math.sqrt(t_remaining_years))
    gbm_p = norm_cdf(d2)

    # Define return buckets
    ret_buckets = [
        ("< -1.0%", ret_at_t2 < -0.010),
        ("-1.0% to -0.5%", (ret_at_t2 >= -0.010) & (ret_at_t2 < -0.005)),
        ("-0.5% to -0.2%", (ret_at_t2 >= -0.005) & (ret_at_t2 < -0.002)),
        ("-0.2% to -0.1%", (ret_at_t2 >= -0.002) & (ret_at_t2 < -0.001)),
        ("-0.1% to 0%", (ret_at_t2 >= -0.001) & (ret_at_t2 < 0)),
        ("0% to +0.1%", (ret_at_t2 >= 0) & (ret_at_t2 < 0.001)),
        ("+0.1% to +0.2%", (ret_at_t2 >= 0.001) & (ret_at_t2 < 0.002)),
        ("+0.2% to +0.5%", (ret_at_t2 >= 0.002) & (ret_at_t2 < 0.005)),
        ("+0.5% to +1.0%", (ret_at_t2 >= 0.005) & (ret_at_t2 < 0.010)),
        ("> +1.0%", ret_at_t2 >= 0.010),
    ]

    print(f"  {'Return bucket':>24} | {'n':>6} | {'GBM P(Up)':>10} | {'Actual Up%':>10} | {'Diff':>8} | {'Interpretation':>25}")
    print("  " + "-" * 100)

    bucket_results = []
    for label, mask in ret_buckets:
        n_b = mask.sum()
        if n_b < 15:
            print(f"  {label:>24} | {n_b:>6,} | {'(skip)':>10} | {'(skip)':>10} | {'':>8}")
            continue
        gbm_mean = gbm_p[mask].mean()
        actual_up = is_up[mask].mean()
        diff = actual_up - gbm_mean

        if abs(diff) > 0.05:
            interp = "OVERPRICED" if diff < 0 else "UNDERPRICED"
        else:
            interp = "well-calibrated"

        exploit_str = ""
        if diff > 0.05:
            exploit_str = "(momentum persists)"
        elif diff < -0.05:
            exploit_str = "(mean-reversion)"

        print(f"  {label:>24} | {n_b:>6,} | {gbm_mean:>10.3f} | {actual_up:>10.3f} | {diff:>+8.3f} | {interp:>25} {exploit_str}")

        bucket_results.append({
            "label": label,
            "n": n_b,
            "gbm_p": gbm_mean,
            "actual_p": actual_up,
            "diff": diff,
        })

    # Statistical test: is the overall GBM calibrated?
    # Test correlation between GBM P(Up) and actual Up outcome
    corr, corr_p = stats.pearsonr(gbm_p, is_up.astype(float))
    print(f"\n  GBM-outcome correlation: r={corr:.4f}, p={corr_p:.4f}")

    # Brier score (lower = better calibrated)
    brier_gbm = np.mean((gbm_p - is_up.astype(float)) ** 2)
    # Naive forecast: always predict base rate
    base_up = is_up.mean()
    brier_naive = np.mean((base_up - is_up.astype(float)) ** 2)
    print(f"  Brier score (GBM): {brier_gbm:.5f}")
    print(f"  Brier score (naive base rate): {brier_naive:.5f}")
    print(f"  Brier skill score (vs naive): {1 - brier_gbm/brier_naive:.4f}")

    # t-test: is GBM P(Up) a better predictor than 0.5?
    slope, intercept, r_val, p_val, se = stats.linregress(gbm_p, is_up.astype(float))
    print(f"\n  OLS: actual_up = {slope:.4f} × GBM_P(Up) + {intercept:.4f}")
    print(f"  R² = {r_val**2:.6f}, p = {p_val:.4f}")
    print(f"\n  Interpretation: slope≈1, intercept≈0 → GBM well-calibrated")
    print(f"  slope<1 → GBM over-weights current price move (mean-reversion favored)")
    print(f"  slope>1 → GBM under-weights move (momentum favored)")

    return {
        "bucket_results": bucket_results,
        "gbm_p": gbm_p,
        "actual_up": is_up,
        "brier_gbm": brier_gbm,
        "brier_naive": brier_naive,
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_val ** 2,
    }


# ─── Section 6: Summary & Alpha Estimate ──────────────────────────────────────

def section6_summary(
    base_rates: dict,
    calibration: dict,
    feat_results: dict,
    cond_edge: dict,
    gbm_reality: dict,
) -> None:
    sep("6. SUMMARY & ALPHA ESTIMATE")

    base_rate_up = base_rates["5m"]["up_pct"]
    n_5m_windows = base_rates["5m"]["n_total"]
    n_windows_per_day = n_5m_windows / 30.0  # 30 days of data

    print(f"\nBase rate analysis:")
    print(f"  5m Up rate: {base_rate_up:.3%}  |  Down rate: {1-base_rate_up:.3%}")
    print(f"  15m Up rate: {base_rates['15m']['up_pct']:.3%}")
    print(f"  ~{n_windows_per_day:.0f} 5-min windows per day")
    print()

    # Assess GBM calibration quality
    slope = gbm_reality["slope"]
    r2 = gbm_reality["r_squared"]
    bss = 1 - gbm_reality["brier_gbm"] / gbm_reality["brier_naive"]
    print(f"GBM calibration:")
    print(f"  Regression slope: {slope:.4f} (ideal=1.0)")
    print(f"  R²: {r2:.6f}")
    print(f"  Brier skill score: {bss:.4f}")
    if abs(slope - 1.0) < 0.1:
        print("  => GBM is WELL-CALIBRATED (slope near 1)")
    elif slope < 0.9:
        print("  => GBM OVER-WEIGHTS price moves → mean-reversion regime")
        print("     (Actual outcomes less extreme than GBM predicts)")
    else:
        print("  => GBM UNDER-WEIGHTS price moves → momentum regime")
        print("     (Actual outcomes more extreme than GBM predicts)")
    print()

    # Find best conditional edge
    edge_results = cond_edge["edge_results"]
    if edge_results:
        best = max(edge_results, key=lambda x: x["hit_rate"])
        profitable = [r for r in edge_results if r["hit_rate"] > MIN_ACCURACY_THRESHOLD]

        print(f"Best conditional signal:")
        print(f"  Condition: {best['condition']}")
        print(f"  Hit rate:  {best['hit_rate']:.3%}")
        print(f"  vs base:   {best['vs_base']:+.3%}")
        print(f"  n signals: {best['n']}")
        print()

        if profitable:
            print(f"Conditions exceeding {MIN_ACCURACY_THRESHOLD:.0%} threshold (> fees):")
            for r in sorted(profitable, key=lambda x: -x["hit_rate"]):
                print(f"  [{r['direction']}] {r['condition']}: {r['hit_rate']:.3%} (n={r['n']:,})")
        else:
            print(f"No conditions found exceeding {MIN_ACCURACY_THRESHOLD:.0%} accuracy threshold")
    print()

    # Estimate alpha
    flagged = feat_results.get("_flagged", [])
    best_acc = max((r["hit_rate"] for r in edge_results), default=base_rate_up)
    trades_per_day_signal = n_windows_per_day * 0.25  # assume top quartile filter = 25% of windows

    # Theoretical edge
    # Market is priced around 50c for a 50/50 outcome
    # If true probability is P, edge = P - 0.50 per $1 contract
    # Net of taker fee: edge_net = P - 0.50 - 0.015
    edge_gross = best_acc - 0.50
    edge_net = edge_gross - TAKER_FEE / 2

    print(f"Alpha estimate (UPPER BOUND):")
    print(f"  Best feature accuracy:   {best_acc:.3%}")
    print(f"  Gross edge/trade:        {edge_gross:+.3%} cents per $1 contract")
    print(f"  Taker fee cost:          -{TAKER_FEE/2:.1%}")
    print(f"  Net edge/trade:          {edge_net:+.3%}")
    print(f"  5-min windows/day:       ~{n_windows_per_day:.0f}")
    print(f"  Signal filter rate:      ~25% (top quartile signals)")
    print(f"  Qualifying trades/day:   ~{trades_per_day_signal:.0f}")
    print()

    if edge_net > 0:
        # Estimate daily P&L (for $1 per trade sizing)
        daily_pnl_per_dollar = edge_net * trades_per_day_signal
        print(f"  Daily P&L ($1/trade):    ${daily_pnl_per_dollar:.4f}")
        print(f"  For $100/trade sizing:   ${daily_pnl_per_dollar * 100:.2f}/day")
        print(f"  For $1000/trade sizing:  ${daily_pnl_per_dollar * 1000:.2f}/day")
        print()
    else:
        print(f"  Net edge is NEGATIVE — no profitable alpha at this accuracy level")
        print()

    # Overall verdict
    print("=" * 72)
    has_feature_signal = len(flagged) > 0
    has_profitable_condition = len([r for r in edge_results if r["hit_rate"] > MIN_ACCURACY_THRESHOLD]) > 0
    gbm_well_calibrated = abs(slope - 1.0) < 0.15

    print(f"\nVERDICT:")
    print(f"  Feature predictiveness:  {'YES' if has_feature_signal else 'NO'} (>53% accuracy?)")
    print(f"  Profitable conditions:   {'YES' if has_profitable_condition else 'NO'}")
    print(f"  GBM well-calibrated:     {'YES' if gbm_well_calibrated else 'NO'}")
    print()

    if has_profitable_condition:
        print("  => PROMISING: Some conditions exceed profitability threshold.")
        print("     Proceed to tick-by-tick validation on Polymarket order flow.")
        print("     Key caveat: Binance data ≠ Chainlink oracle; basis risk exists.")
        verdict = "promising"
    elif has_feature_signal:
        print("  => MARGINAL: Features are predictive but not enough to clear fees.")
        print("     May be worth testing with tighter signal conditions or lower fees.")
        verdict = "marginal"
    else:
        print("  => NO SIGNAL: Features do not predict 5-min direction above base rate.")
        print("     This hypothesis is unlikely to be profitable.")
        verdict = "no_signal"

    print()
    print("  Key risks:")
    print("  1. Binance candles ≠ Chainlink oracle (basis/lag risk)")
    print("  2. Signal decay: features may be market-regime dependent")
    print("  3. Execution: can you actually get fills at the assumed price?")
    print("  4. Look-ahead: features are from prior window, not current")
    print("  5. 30-day sample: limited for identifying rare regimes")
    print("  6. Polymarket liquidity: 5-min crypto markets may have wide spreads")
    print()
    print("  Spawned ideas:")
    print("  - Test ETH and SOL (data available)")
    print("  - Test Chainlink oracle returns directly (source of truth)")
    print("  - Test larger window: 15-min markets vs 5-min")
    print("  - Test volatility regime conditioning (high-vol vs low-vol)")
    print("  - Orderbook pressure at Polymarket as confirmation signal")

    return verdict


# ─── Plot (optional) ──────────────────────────────────────────────────────────

def save_plots(base_rates: dict, gbm_reality: dict, feat_results: dict) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        out_dir = BASE / "data"
        out_dir.mkdir(parents=True, exist_ok=True)

        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle("Phase 0: BTC 5-min Crypto Scalp Analysis", fontsize=14)

        # Plot 1: Return distribution of 5-min windows
        ax1 = axes[0, 0]
        windows = base_rates["5m"]["windows"]
        returns = windows["return_pct"].to_numpy() * 100
        ax1.hist(returns, bins=80, color="steelblue", alpha=0.7, edgecolor="white", linewidth=0.3)
        ax1.axvline(0, color="red", linestyle="--", linewidth=1.5, label=f"0 (base rate up={base_rates['5m']['up_pct']:.2%})")
        ax1.set_xlabel("5-min return (%)")
        ax1.set_ylabel("Count")
        ax1.set_title("Distribution of 5-min window returns")
        ax1.legend(fontsize=9)

        # Plot 2: GBM vs Actual at t=2
        ax2 = axes[0, 1]
        bucket_results = gbm_reality["bucket_results"]
        if bucket_results:
            labels = [r["label"] for r in bucket_results]
            gbm_ps = [r["gbm_p"] for r in bucket_results]
            actual_ps = [r["actual_p"] for r in bucket_results]
            x = range(len(labels))
            ax2.plot(x, gbm_ps, "b-o", label="GBM P(Up)", markersize=5)
            ax2.plot(x, actual_ps, "r-o", label="Actual Up%", markersize=5)
            ax2.axhline(0.5, color="gray", linestyle=":", alpha=0.7)
            ax2.set_xticks(list(x))
            ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
            ax2.set_ylabel("P(Up)")
            ax2.set_title("GBM vs Actual at t=2.5 min")
            ax2.legend()

        # Plot 3: Momentum quantiles
        ax3 = axes[1, 0]
        if "mom_5m" in feat_results and "quantiles" in feat_results["mom_5m"]:
            q_data = feat_results["mom_5m"]["quantiles"]
            labels_q = [r[0] for r in q_data]
            up_rates = [r[2] * 100 for r in q_data]
            colors = ["green" if u > base_rates["5m"]["up_pct"] * 100 else "red" for u in up_rates]
            ax3.bar(labels_q, up_rates, color=colors, alpha=0.7)
            ax3.axhline(base_rates["5m"]["up_pct"] * 100, color="blue", linestyle="--", label=f"Base rate {base_rates['5m']['up_pct']:.2%}")
            ax3.axhline(53, color="black", linestyle=":", label="53% threshold")
            ax3.set_ylabel("Up% per quantile")
            ax3.set_title("Momentum (5m) quantiles vs Up rate")
            ax3.legend(fontsize=9)

        # Plot 4: Hour of day
        ax4 = axes[1, 1]
        hour_data = feat_results.get("hour", [])
        if hour_data:
            hours = [r[0] for r in hour_data]
            up_rates_h = [r[2] * 100 for r in hour_data]
            ax4.bar(hours, up_rates_h, color="steelblue", alpha=0.7)
            ax4.axhline(base_rates["5m"]["up_pct"] * 100, color="red", linestyle="--", label=f"Base {base_rates['5m']['up_pct']:.2%}")
            ax4.set_xlabel("Hour (UTC)")
            ax4.set_ylabel("Up%")
            ax4.set_title("Up rate by hour of day (UTC)")
            ax4.legend(fontsize=9)

        plt.tight_layout()
        out_path = out_dir / "phase0_analysis.png"
        plt.savefig(out_path, dpi=120, bbox_inches="tight")
        print(f"\nFigure saved to: {out_path}")
        plt.close()

    except Exception as e:
        print(f"\n(Plot skipped: {e})")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    sep("PHASE 0: GBM CALIBRATION & ALPHA DISCOVERY")
    print(f"Hypothesis: Polymarket 5-min BTC Up/Down crypto scalp")
    print(f"Data:       BTCUSDT 1m Binance klines, 30 days")
    print(f"Run time:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Note:       All results are UPPER BOUNDS")
    sep()

    df = load_klines()
    print(f"\nLoaded {len(df):,} 1m candles")
    t_min = df["open_time"].min() / 1000
    t_max = df["open_time"].max() / 1000
    print(f"Date range: {datetime.fromtimestamp(t_min)} to {datetime.fromtimestamp(t_max)}")

    # Run all sections
    base_rates = section1_base_rates(df)
    calibration = section2_gbm_calibration(df, base_rates)
    feat_results = section3_feature_predictiveness(df, base_rates)
    cond_edge = section4_conditional_edge(feat_results, base_rates)
    gbm_reality = section5_gbm_vs_reality(df, base_rates)
    verdict = section6_summary(base_rates, calibration, feat_results, cond_edge, gbm_reality)

    # Save plots
    save_plots(base_rates, gbm_reality, feat_results)

    sep()
    print(f"\nFinal verdict: {verdict.upper()}")
    sep()


if __name__ == "__main__":
    main()
