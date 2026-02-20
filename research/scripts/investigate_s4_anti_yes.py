"""Investigate S4: Anti-Consensus YES → Bet NO.

When >=N skilled traders consensus YES on a market, bet NO.
Key question: Is the 18.5% YES HR statistically below the per-window base rate?

Tests:
1. Per-window base-rate control (is anti-signal real or just NO base rate?)
2. Walk-forward monthly holdout (same rigor as S1)
3. Bet count and capacity
4. PnL simulation at $100/bet
5. Comparison to S3 (consensus NO signal)

Usage:
    PYTHONPATH=. uv run python scripts/investigate_s4_anti_yes.py
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

from strategies.consistency_copy.backtester.runner import (
    _compute_trader_median_entry,
    _load_data,
    _precompute_mvf_subsets,
    get_consistent_traders,
)

# Pool config
CONSISTENCY_MONTHS = 9
MIN_MARKETS = 20
MAX_MEDIAN_ENTRY = 0.90
MVF_BAND = "pure_taker"
TRAIN_ANCHOR = datetime(2023, 1, 1)

WINDOWS = [
    ("2025-01", datetime(2025, 1, 1), datetime(2025, 2, 1)),
    ("2025-02", datetime(2025, 2, 1), datetime(2025, 3, 1)),
    ("2025-03", datetime(2025, 3, 1), datetime(2025, 4, 1)),
    ("2025-04", datetime(2025, 4, 1), datetime(2025, 5, 1)),
    ("2025-05", datetime(2025, 5, 1), datetime(2025, 6, 1)),
    ("2025-06", datetime(2025, 6, 1), datetime(2025, 7, 1)),
    ("2025-07", datetime(2025, 7, 1), datetime(2025, 8, 1)),
    ("2025-08", datetime(2025, 8, 1), datetime(2025, 9, 1)),
    ("2025-09", datetime(2025, 9, 1), datetime(2025, 10, 1)),
    ("2025-10", datetime(2025, 10, 1), datetime(2025, 11, 1)),
    ("2025-11", datetime(2025, 11, 1), datetime(2025, 12, 1)),
    ("2025-12", datetime(2025, 12, 1), datetime(2026, 1, 1)),
    ("2026-01", datetime(2026, 1, 1), datetime(2026, 2, 1)),
]

# Sweep parameters
MIN_TRADERS_OPTIONS = [3, 5, 7]
AGREEMENT_OPTIONS = [0.60, 0.80, 1.00]
FEE_RATE = 0.02
BET_SIZE = 100.0


def build_signal_table(
    df_pnl: pl.DataFrame,
    pool: set[str],
    h_start: datetime,
    h_end: datetime,
) -> pl.DataFrame:
    """Build per-market consensus signal from pool traders' positions.

    Returns DataFrame: condition_id, n_yes, n_no, n_total, yes_fraction, yes_won
    """
    # Pool positions that ENTERED before holdout start (training signal)
    # but market RESOLVES in holdout window
    positions = df_pnl.filter(
        (pl.col("resolved_at") >= h_start)
        & (pl.col("resolved_at") < h_end)
        & pl.col("trader").is_in(list(pool))
        & (pl.col("first_trade") < h_start)  # signal from training period
    )

    if positions.height == 0:
        return pl.DataFrame(schema={
            "condition_id": pl.Utf8,
            "n_yes": pl.UInt32,
            "n_no": pl.UInt32,
            "n_total": pl.UInt32,
            "yes_fraction": pl.Float64,
            "yes_won": pl.Boolean,
        })

    # Per-market consensus
    signals = positions.group_by("condition_id").agg(
        (pl.col("net_yes_tokens") > 0).sum().alias("n_yes"),
        (pl.col("net_yes_tokens") <= 0).sum().alias("n_no"),
        pl.col("net_yes_tokens").len().alias("n_total"),
        pl.col("yes_won").first().alias("yes_won"),
    ).with_columns(
        (pl.col("n_yes") / pl.col("n_total")).alias("yes_fraction"),
    )

    return signals


def evaluate_anti_yes(
    signals: pl.DataFrame,
    min_traders: int,
    min_agreement: float,
) -> dict:
    """Evaluate: when consensus is YES, bet NO."""
    # Filter to markets with enough traders and YES consensus
    eligible = signals.filter(
        (pl.col("n_total") >= min_traders)
        & (pl.col("yes_fraction") >= min_agreement)
    )

    n_bets = eligible.height
    if n_bets == 0:
        return {"n_bets": 0, "hr": 0, "pnl": 0, "yes_hr": 0}

    # We bet NO. We win when yes_won is False.
    no_wins = int((~eligible["yes_won"]).sum())
    hr = no_wins / n_bets

    # PnL: for each bet, if NO wins → profit, if YES wins → loss
    # Simplified: $100 bet on NO at market fair value (no price data here, use base)
    # Approximate: if NO wins, profit = $100 * (1 - fee) - entry cost; if YES wins, lose $100
    # For now, just track hit rate. PnL requires entry price.

    return {
        "n_bets": n_bets,
        "no_wins": no_wins,
        "hr_no": hr,
        "hr_yes": 1 - hr,
    }


def evaluate_consensus_no(
    signals: pl.DataFrame,
    min_traders: int,
    min_agreement: float,
) -> dict:
    """Evaluate: when consensus is NO, bet NO (standard S3)."""
    no_fraction = 1.0 - signals["yes_fraction"]
    eligible = signals.filter(
        (pl.col("n_total") >= min_traders)
    ).with_columns(
        (1.0 - pl.col("yes_fraction")).alias("no_fraction")
    ).filter(
        pl.col("no_fraction") >= min_agreement
    )

    n_bets = eligible.height
    if n_bets == 0:
        return {"n_bets": 0, "hr": 0}

    no_wins = int((~eligible["yes_won"]).sum())
    hr = no_wins / n_bets
    return {"n_bets": n_bets, "no_wins": no_wins, "hr_no": hr}


def main():
    print("Loading data...")
    df_pnl, mvf_df, _markets, _price_ts = _load_data()
    mvf_subsets = _precompute_mvf_subsets(mvf_df)
    med_entry_df = _compute_trader_median_entry(df_pnl)

    # =====================================================================
    # 1. PER-WINDOW BASE RATE CONTROL
    # =====================================================================
    print(f"\n{'=' * 80}")
    print("  1. PER-WINDOW BASE RATE vs ANTI-YES SIGNAL (min_traders=5, agree>=60%)")
    print(f"{'=' * 80}\n")

    header = (
        f"{'Window':<10} {'Pool':>5} {'Base NO%':>9} "
        f"{'S4 Bets':>8} {'S4 NO HR':>9} {'S4 Edge':>8} "
        f"{'S3 Bets':>8} {'S3 NO HR':>9} {'S3 Edge':>8}"
    )
    print(header)
    print("-" * len(header))

    all_s4_rows = []
    all_s3_rows = []

    for month_name, h_start, h_end in WINDOWS:
        t_end = h_start
        skilled = get_consistent_traders(df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS)
        eligible = set(
            med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
        )
        pool = skilled & mvf_subsets[MVF_BAND] & eligible

        if len(pool) < 5:
            print(f"{month_name:<10} {len(pool):>5} {'---':>9} {'---':>8} {'---':>9} {'---':>8} {'---':>8} {'---':>9} {'---':>8}")
            continue

        # Base rate for this window
        window_markets = df_pnl.filter(
            (pl.col("resolved_at") >= h_start) & (pl.col("resolved_at") < h_end)
        ).select("condition_id", "yes_won").unique()
        if window_markets.height == 0:
            continue
        base_no_rate = float((~window_markets["yes_won"]).mean())

        # Build signal table
        signals = build_signal_table(df_pnl, pool, h_start, h_end)

        # S4: Anti-consensus YES → bet NO
        s4 = evaluate_anti_yes(signals, min_traders=5, min_agreement=0.60)
        # S3: Consensus NO → bet NO
        s3 = evaluate_consensus_no(signals, min_traders=5, min_agreement=0.60)

        s4_hr = s4["hr_no"] if s4["n_bets"] > 0 else 0
        s4_edge = (s4_hr - base_no_rate) * 100 if s4["n_bets"] > 0 else 0
        s3_hr = s3["hr_no"] if s3["n_bets"] > 0 else 0
        s3_edge = (s3_hr - base_no_rate) * 100 if s3["n_bets"] > 0 else 0

        all_s4_rows.append({"month": month_name, "n_bets": s4["n_bets"], "hr": s4_hr, "edge": s4_edge, "base": base_no_rate})
        all_s3_rows.append({"month": month_name, "n_bets": s3["n_bets"], "hr": s3_hr, "edge": s3_edge, "base": base_no_rate})

        s4_bets_s = str(s4["n_bets"]) if s4["n_bets"] > 0 else "---"
        s4_hr_s = f"{s4_hr*100:.1f}%" if s4["n_bets"] > 0 else "---"
        s4_edge_s = f"{s4_edge:+.1f}pp" if s4["n_bets"] > 0 else "---"
        s3_bets_s = str(s3["n_bets"]) if s3["n_bets"] > 0 else "---"
        s3_hr_s = f"{s3_hr*100:.1f}%" if s3["n_bets"] > 0 else "---"
        s3_edge_s = f"{s3_edge:+.1f}pp" if s3["n_bets"] > 0 else "---"

        print(
            f"{month_name:<10} {len(pool):>5} {base_no_rate*100:>8.1f}% "
            f"{s4_bets_s:>8} {s4_hr_s:>9} {s4_edge_s:>8} "
            f"{s3_bets_s:>8} {s3_hr_s:>9} {s3_edge_s:>8}"
        )

    # Summary
    s4_with_bets = [r for r in all_s4_rows if r["n_bets"] > 0]
    s3_with_bets = [r for r in all_s3_rows if r["n_bets"] > 0]

    if s4_with_bets:
        total_bets = sum(r["n_bets"] for r in s4_with_bets)
        weighted_hr = sum(r["hr"] * r["n_bets"] for r in s4_with_bets) / total_bets
        weighted_base = sum(r["base"] * r["n_bets"] for r in s4_with_bets) / total_bets
        avg_edge = sum(r["edge"] for r in s4_with_bets) / len(s4_with_bets)
        n_positive_edge = sum(1 for r in s4_with_bets if r["edge"] > 0)
        print()
        print(f"  S4 summary: {len(s4_with_bets)} windows with bets, {total_bets} total bets")
        print(f"  Weighted HR: {weighted_hr*100:.1f}% vs weighted base: {weighted_base*100:.1f}%")
        print(f"  Avg edge: {avg_edge:+.1f}pp above base NO rate")
        print(f"  Windows with positive edge: {n_positive_edge}/{len(s4_with_bets)}")

    if s3_with_bets:
        total_bets = sum(r["n_bets"] for r in s3_with_bets)
        weighted_hr = sum(r["hr"] * r["n_bets"] for r in s3_with_bets) / total_bets
        weighted_base = sum(r["base"] * r["n_bets"] for r in s3_with_bets) / total_bets
        avg_edge = sum(r["edge"] for r in s3_with_bets) / len(s3_with_bets)
        n_positive_edge = sum(1 for r in s3_with_bets if r["edge"] > 0)
        print()
        print(f"  S3 summary: {len(s3_with_bets)} windows with bets, {total_bets} total bets")
        print(f"  Weighted HR: {weighted_hr*100:.1f}% vs weighted base: {weighted_base*100:.1f}%")
        print(f"  Avg edge: {avg_edge:+.1f}pp above base NO rate")
        print(f"  Windows with positive edge: {n_positive_edge}/{len(s3_with_bets)}")

    # =====================================================================
    # 2. PARAMETER SWEEP: min_traders x agreement
    # =====================================================================
    print(f"\n{'=' * 80}")
    print("  2. S4 PARAMETER SWEEP: min_traders x agreement (all windows pooled)")
    print(f"{'=' * 80}\n")

    header2 = f"{'MinT':>5} {'Agree':>6} {'Bets':>7} {'NO HR':>7} {'Base':>7} {'Edge':>8} {'Win Mo':>7} {'PnL*':>8}"
    print(header2)
    print("-" * len(header2))

    for min_t in MIN_TRADERS_OPTIONS:
        for agree in AGREEMENT_OPTIONS:
            month_results = []
            total_bets = 0
            total_no_wins = 0

            for month_name, h_start, h_end in WINDOWS:
                t_end = h_start
                skilled = get_consistent_traders(df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS)
                eligible_set = set(
                    med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
                )
                pool = skilled & mvf_subsets[MVF_BAND] & eligible_set

                if len(pool) < 5:
                    continue

                signals = build_signal_table(df_pnl, pool, h_start, h_end)
                s4 = evaluate_anti_yes(signals, min_traders=min_t, min_agreement=agree)

                if s4["n_bets"] > 0:
                    # Simple PnL estimate: win → +$100*(1-fee), lose → -$100
                    pnl = s4["no_wins"] * BET_SIZE * (1 - FEE_RATE) - (s4["n_bets"] - s4["no_wins"]) * BET_SIZE
                    month_results.append({"pnl": pnl, "bets": s4["n_bets"], "hr": s4["hr_no"]})
                    total_bets += s4["n_bets"]
                    total_no_wins += s4["no_wins"]

            if total_bets == 0:
                print(f"{min_t:>5} {agree*100:>5.0f}% {'0':>7} {'---':>7} {'---':>7} {'---':>8} {'---':>7} {'---':>8}")
                continue

            overall_hr = total_no_wins / total_bets
            # Approximate base rate (overall)
            overall_base = 0.619  # from insight #06
            edge = (overall_hr - overall_base) * 100
            total_pnl = sum(r["pnl"] for r in month_results)
            win_months = sum(1 for r in month_results if r["pnl"] > 0)
            n_months = len(month_results)

            print(
                f"{min_t:>5} {agree*100:>5.0f}% {total_bets:>7} {overall_hr*100:>6.1f}% "
                f"{overall_base*100:>6.1f}% {edge:>+7.1f}pp {win_months:>3}/{n_months:<3} "
                f"${total_pnl:>7,.0f}"
            )

    # =====================================================================
    # 3. SIGNAL OVERLAP: Do S3 and S4 cover different markets?
    # =====================================================================
    print(f"\n{'=' * 80}")
    print("  3. MARKET OVERLAP: S3 (consensus NO) vs S4 (anti-consensus YES)")
    print(f"{'=' * 80}\n")

    for month_name, h_start, h_end in WINDOWS[-4:]:  # Last 4 windows
        t_end = h_start
        skilled = get_consistent_traders(df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS)
        eligible_set = set(
            med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
        )
        pool = skilled & mvf_subsets[MVF_BAND] & eligible_set

        if len(pool) < 5:
            continue

        signals = build_signal_table(df_pnl, pool, h_start, h_end)
        signals = signals.filter(pl.col("n_total") >= 5)

        s4_markets = set(
            signals.filter(pl.col("yes_fraction") >= 0.60)["condition_id"].to_list()
        )
        s3_markets = set(
            signals.filter((1.0 - pl.col("yes_fraction")) >= 0.60)["condition_id"].to_list()
        )
        neither = set(signals["condition_id"].to_list()) - s4_markets - s3_markets
        overlap = s4_markets & s3_markets  # should be empty (can't be both >60%)

        print(f"  {month_name}: S4={len(s4_markets)} mkts, S3={len(s3_markets)} mkts, "
              f"overlap={len(overlap)}, neither={len(neither)}, total={signals.height}")

    # =====================================================================
    # 4. ENTRY PRICE ANALYSIS: What NO price do we get on S4 markets?
    # =====================================================================
    print(f"\n{'=' * 80}")
    print("  4. ENTRY PRICE ON S4 MARKETS (what NO price can we get?)")
    print(f"{'=' * 80}\n")

    # Use last window with good data
    t_end = datetime(2026, 1, 1)
    h_start, h_end = datetime(2026, 1, 1), datetime(2026, 2, 1)
    skilled = get_consistent_traders(df_pnl, TRAIN_ANCHOR, t_end, CONSISTENCY_MONTHS, MIN_MARKETS)
    eligible_set = set(
        med_entry_df.filter(pl.col("median_entry") <= MAX_MEDIAN_ENTRY)["trader"].to_list()
    )
    pool = skilled & mvf_subsets[MVF_BAND] & eligible_set
    signals = build_signal_table(df_pnl, pool, h_start, h_end)

    # S4 markets: consensus YES
    s4_mkts = signals.filter(
        (pl.col("n_total") >= 5) & (pl.col("yes_fraction") >= 0.60)
    )

    if s4_mkts.height > 0:
        # Get entry prices from pool traders' YES positions on these markets
        s4_ids = set(s4_mkts["condition_id"].to_list())
        pool_yes_positions = df_pnl.filter(
            (pl.col("resolved_at") >= h_start)
            & (pl.col("resolved_at") < h_end)
            & pl.col("condition_id").is_in(list(s4_ids))
            & pl.col("trader").is_in(list(pool))
            & (pl.col("net_yes_tokens") > 0)
        )

        if pool_yes_positions.height > 0:
            yes_prices = pool_yes_positions["wavg_yes_entry_price"]
            # If we bet NO, our entry price = 1 - YES_price (we buy NO at this level)
            no_prices = 1.0 - yes_prices
            print(f"  S4 markets in Jan 2026: {s4_mkts.height}")
            print(f"  Pool YES entry prices (these are what the YES consensus bought at):")
            print(f"    Median YES price: {float(yes_prices.median()):.3f}")
            print(f"    Mean YES price:   {float(yes_prices.mean()):.3f}")
            print(f"    P25-P75:          {float(yes_prices.quantile(0.25)):.3f} - {float(yes_prices.quantile(0.75)):.3f}")
            print()
            print(f"  Implied NO entry prices (1 - YES price):")
            print(f"    Median NO price:  {float(no_prices.median()):.3f}")
            print(f"    Mean NO price:    {float(no_prices.mean()):.3f}")
            print(f"    P25-P75:          {float(no_prices.quantile(0.25)):.3f} - {float(no_prices.quantile(0.75)):.3f}")

            # What's the actual NO win rate on these specific markets?
            outcomes = s4_mkts.select("condition_id", "yes_won")
            no_wr = float((~outcomes["yes_won"]).mean())
            yes_wr = float(outcomes["yes_won"].mean())
            print()
            print(f"  Actual outcomes: YES won {yes_wr*100:.1f}%, NO won {no_wr*100:.1f}%")

            # Expected PnL per bet
            # Buy NO at median price, win → payout (1-fee), lose → lose bet
            med_no = float(no_prices.median())
            ev_win = (1.0 - med_no) / med_no  # return if NO wins
            ev = no_wr * (1 - FEE_RATE) * BET_SIZE * ev_win - yes_wr * BET_SIZE
            print(f"  At median NO price ({med_no:.3f}):")
            print(f"    If NO wins: +${BET_SIZE * ev_win * (1-FEE_RATE):.0f}")
            print(f"    If YES wins: -${BET_SIZE:.0f}")
            print(f"    E[PnL] per $100 bet: ${ev:.1f}")


if __name__ == "__main__":
    main()
