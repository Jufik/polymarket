"""$1K Deployment Analysis for the Will-Binary-NO Strategy.

Analyses:
1. Reproduce the Will binary NO walk-forward (Sharpe 2.43 from research)
2. Risk of ruin Monte Carlo (50K paths at various sizing)
3. Concurrent positions / capital lockup analysis
4. Worst losing streaks
5. "Will" question subtype PnL breakdown
6. Optimal bet sizing for $1K bankroll
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

DATA_DIR = Path("data")
OUT_DIR = Path("insights/overpriceNo")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Re-use early price logic
import sys
sys.path.insert(0, str(Path(__file__).parent))
from backtest_fav_longshot import compute_early_prices


# ---------------------------------------------------------------------------
# Data loading + Will filter
# ---------------------------------------------------------------------------

def load_will_binary_data() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load binary resolved markets filtered to 'Will' questions, with early prices."""
    print("1. Loading market data...")
    mr = pl.read_parquet(DATA_DIR / "derived/markets_resolved.parquet")
    mkt = pl.read_parquet(DATA_DIR / "metadata/markets.parquet")

    # Binary markets only (neg_risk=False)
    binary_ids = set(mkt.filter(pl.col("neg_risk") == False)["condition_id"].to_list())
    resolved_binary = mr.filter(pl.col("condition_id").is_in(list(binary_ids)))
    print(f"  Binary resolved markets: {resolved_binary.height:,}")

    # Add question text
    mkt_q = mkt.select("condition_id", "question").rename({"question": "question_text"})
    resolved_binary = resolved_binary.join(mkt_q, on="condition_id", how="left")

    # Filter to "Will" questions
    will_mask = (
        pl.col("question_text").str.to_lowercase().str.starts_with("will ")
        | pl.col("question_text").str.to_lowercase().str.contains("^will ")
    )
    resolved_will = resolved_binary.filter(will_mask)
    print(f"  'Will' binary resolved: {resolved_will.height:,}")

    # Compute early prices
    print("\n2. Computing early prices...")
    early_prices = compute_early_prices(
        DATA_DIR / "derived/market_prices.parquet",
        pct_lifetime=0.30,
        min_trades=3,
    )

    # Join
    resolved = resolved_will.join(
        early_prices.select("condition_id", "early_yes_price", "n_early_trades", "lifetime_s"),
        on="condition_id",
        how="inner",
    )
    print(f"  Will binary with early prices: {resolved.height:,}")

    return resolved, mkt


# ---------------------------------------------------------------------------
# Walk-forward specific to Will-binary
# ---------------------------------------------------------------------------

@dataclass
class Bet:
    condition_id: str
    question_text: str
    resolved_at: datetime
    entry_month: str
    early_yes_price: float
    won_no: bool
    bet_size: float
    pnl: float
    lifetime_days: float


def walk_forward_will_no(
    df: pl.DataFrame,
    entry_lo: float = 0.10,
    entry_hi: float = 0.50,
    bet_size: float = 100.0,
    test_start: str = "2025-01",
    test_end: str = "2026-02",
) -> list[Bet]:
    """Simple walk-forward: bet NO on all Will-binary in [entry_lo, entry_hi].
    No calibration needed (research showed it adds nothing)."""
    df2 = df.with_columns(
        pl.col("resolved_at").dt.strftime("%Y-%m").alias("resolve_month")
    )
    months = sorted(df2["resolve_month"].unique().drop_nulls().to_list())
    test_months = [m for m in months if m >= test_start and m < test_end]

    bets = []
    for month in test_months:
        test = df2.filter(
            (pl.col("resolve_month") == month)
            & (pl.col("early_yes_price") >= entry_lo)
            & (pl.col("early_yes_price") <= entry_hi)
        )
        for row in test.iter_rows(named=True):
            price = row["early_yes_price"]
            no_price = 1.0 - price
            won_no = not row["yes_won"]
            pnl = bet_size * price / no_price if won_no else -bet_size
            lifetime_days = row["lifetime_s"] / 86400 if row["lifetime_s"] else 0

            bets.append(Bet(
                condition_id=row["condition_id"],
                question_text=row.get("question_text", ""),
                resolved_at=row["resolved_at"],
                entry_month=month,
                early_yes_price=price,
                won_no=won_no,
                bet_size=bet_size,
                pnl=pnl,
                lifetime_days=lifetime_days,
            ))
    return bets


# ---------------------------------------------------------------------------
# Analysis 1: Monte Carlo Risk of Ruin
# ---------------------------------------------------------------------------

def risk_of_ruin(
    bets: list[Bet],
    bankroll: float = 1000.0,
    sizing_pcts: list[float] | None = None,
    n_sims: int = 50_000,
    rng_seed: int = 42,
) -> dict:
    """Monte Carlo simulation: draw bets with replacement, track bankroll paths."""
    if sizing_pcts is None:
        sizing_pcts = [0.02, 0.05, 0.10, 0.15, 0.20]

    rng = np.random.default_rng(rng_seed)

    # Normalize PnLs to per-$1 bet
    pnl_per_dollar = np.array([b.pnl / b.bet_size for b in bets])
    n_bets_per_month = len(bets) / 13  # 13 months of data
    n_bets_sim = int(n_bets_per_month * 12)  # simulate 12 months

    results = {}
    for pct in sizing_pcts:
        busted = 0
        finals = []
        max_dds = []
        min_bankrolls = []

        for _ in range(n_sims):
            capital = bankroll
            peak = bankroll
            max_dd = 0.0
            min_cap = bankroll

            # Draw n_bets_sim random bets
            indices = rng.integers(0, len(pnl_per_dollar), size=n_bets_sim)

            for idx in indices:
                bet_amount = capital * pct
                pnl = bet_amount * pnl_per_dollar[idx]
                capital += pnl

                if capital > peak:
                    peak = capital
                dd = (peak - capital) / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
                if capital < min_cap:
                    min_cap = capital

                if capital <= 0:
                    busted += 1
                    break

            finals.append(max(capital, 0))
            max_dds.append(max_dd)
            min_bankrolls.append(min_cap)

        finals_arr = np.array(finals)
        max_dds_arr = np.array(max_dds)
        min_arr = np.array(min_bankrolls)

        results[f"{pct:.0%}"] = {
            "sizing_pct": pct,
            "bust_rate": round(busted / n_sims, 4),
            "median_final": round(float(np.median(finals_arr)), 0),
            "p10_final": round(float(np.percentile(finals_arr, 10)), 0),
            "p25_final": round(float(np.percentile(finals_arr, 25)), 0),
            "p75_final": round(float(np.percentile(finals_arr, 75)), 0),
            "p90_final": round(float(np.percentile(finals_arr, 90)), 0),
            "median_max_dd": round(float(np.median(max_dds_arr)), 4),
            "p95_max_dd": round(float(np.percentile(max_dds_arr, 95)), 4),
            "median_min_bankroll": round(float(np.median(min_arr)), 0),
            "p5_min_bankroll": round(float(np.percentile(min_arr, 5)), 0),
        }

    return {"bankroll": bankroll, "n_sims": n_sims, "bets_simulated": n_bets_sim, "results": results}


# ---------------------------------------------------------------------------
# Analysis 2: Concurrent positions / capital lockup
# ---------------------------------------------------------------------------

def concurrent_positions(bets: list[Bet], bet_size: float = 100.0) -> dict:
    """Estimate concurrent open positions assuming entry at market creation,
    exit at resolution."""
    # For each bet, approximate open period: entry = resolved_at - lifetime_days,
    # close = resolved_at
    events = []
    for b in bets:
        if b.lifetime_days > 0:
            entry_dt = b.resolved_at - timedelta(days=b.lifetime_days)
            events.append(("open", entry_dt, b))
            events.append(("close", b.resolved_at, b))

    events.sort(key=lambda x: x[1])

    concurrent = 0
    max_concurrent = 0
    capital_locked = 0.0
    max_capital_locked = 0.0
    daily_concurrent = {}

    for etype, dt, bet in events:
        if etype == "open":
            concurrent += 1
            capital_locked += bet_size * (1 - bet.early_yes_price)  # NO price
        else:
            concurrent -= 1
            capital_locked -= bet_size * (1 - bet.early_yes_price)
            capital_locked = max(0, capital_locked)

        if concurrent > max_concurrent:
            max_concurrent = concurrent
        if capital_locked > max_capital_locked:
            max_capital_locked = capital_locked

        day = dt.strftime("%Y-%m-%d")
        daily_concurrent[day] = max(daily_concurrent.get(day, 0), concurrent)

    daily_vals = list(daily_concurrent.values())
    return {
        "max_concurrent_positions": max_concurrent,
        "median_concurrent": round(float(np.median(daily_vals)), 0) if daily_vals else 0,
        "p75_concurrent": round(float(np.percentile(daily_vals, 75)), 0) if daily_vals else 0,
        "p95_concurrent": round(float(np.percentile(daily_vals, 95)), 0) if daily_vals else 0,
        "max_capital_locked_at_100": round(max_capital_locked, 0),
        "avg_days_to_resolution": round(float(np.mean([b.lifetime_days for b in bets])), 1),
        "median_days_to_resolution": round(float(np.median([b.lifetime_days for b in bets])), 1),
        "p75_days_to_resolution": round(float(np.percentile([b.lifetime_days for b in bets], 75)), 1),
    }


# ---------------------------------------------------------------------------
# Analysis 3: Losing streaks
# ---------------------------------------------------------------------------

def losing_streaks(bets: list[Bet]) -> dict:
    """Analyze worst losing streaks."""
    sorted_bets = sorted(bets, key=lambda b: b.resolved_at)

    max_streak = 0
    current_streak = 0
    streaks = []

    for b in sorted_bets:
        if not b.won_no:
            current_streak += 1
        else:
            if current_streak > 0:
                streaks.append(current_streak)
            current_streak = 0
    if current_streak > 0:
        streaks.append(current_streak)

    max_streak = max(streaks) if streaks else 0
    streaks_5plus = sum(1 for s in streaks if s >= 5)
    streaks_10plus = sum(1 for s in streaks if s >= 10)
    streaks_15plus = sum(1 for s in streaks if s >= 15)

    # Worst drawdown streak (max consecutive PnL loss)
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd_pnl = 0.0
    dd_start = None
    dd_end = None
    current_dd_start = None

    for b in sorted_bets:
        cum_pnl += b.pnl
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
            current_dd_start = b.resolved_at
        dd = peak_pnl - cum_pnl
        if dd > max_dd_pnl:
            max_dd_pnl = dd
            dd_start = current_dd_start
            dd_end = b.resolved_at

    return {
        "max_losing_streak": max_streak,
        "streaks_5_plus": streaks_5plus,
        "streaks_10_plus": streaks_10plus,
        "streaks_15_plus": streaks_15plus,
        "total_streak_count": len(streaks),
        "max_pnl_drawdown": round(max_dd_pnl, 2),
        "drawdown_period": f"{dd_start} to {dd_end}" if dd_start else "N/A",
    }


# ---------------------------------------------------------------------------
# Analysis 4: Will-question subtypes
# ---------------------------------------------------------------------------

def classify_will_subtype(q: str) -> str:
    """Classify a Will question into subtypes."""
    q_lower = q.lower() if q else ""

    if any(w in q_lower for w in ["$", "price", "bitcoin", "btc", "eth", "crypto", "solana", "sol "]):
        if any(w in q_lower for w in ["above", "over", "reach", "hit", "exceed", "surpass"]):
            return "crypto_above"
        if any(w in q_lower for w in ["below", "under", "drop", "fall"]):
            return "crypto_below"
        return "crypto_other"

    if any(w in q_lower for w in ["win", "beat", "defeat", "game", "match", "championship", "nba", "nfl", "mlb", "nhl"]):
        return "sports"

    if any(w in q_lower for w in ["before", "by ", "by the end"]):
        return "deadline"

    if any(w in q_lower for w in ["trump", "biden", "elect", "vote", "president", "congress", "senate", "governor"]):
        return "politics"

    if any(w in q_lower for w in ["above", "over", "reach", "hit", "exceed"]):
        return "threshold_above"

    if any(w in q_lower for w in ["below", "under", "drop", "fall", "decline"]):
        return "threshold_below"

    return "other"


def subtype_analysis(bets: list[Bet]) -> dict:
    """PnL breakdown by Will-question subtype."""
    subtypes: dict[str, list[Bet]] = {}
    for b in bets:
        st = classify_will_subtype(b.question_text)
        subtypes.setdefault(st, []).append(b)

    results = {}
    for st, st_bets in sorted(subtypes.items(), key=lambda x: -sum(b.pnl for b in x[1])):
        wins = sum(1 for b in st_bets if b.won_no)
        total_pnl = sum(b.pnl for b in st_bets)
        results[st] = {
            "n_bets": len(st_bets),
            "n_wins": wins,
            "hit_rate": round(wins / len(st_bets), 4),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_bet": round(total_pnl / len(st_bets), 2),
            "avg_yes_price": round(float(np.mean([b.early_yes_price for b in st_bets])), 4),
        }

    return results


# ---------------------------------------------------------------------------
# Analysis 5: Optimal bet sizing for $1K
# ---------------------------------------------------------------------------

def optimal_sizing(bets: list[Bet], bankroll: float = 1000.0) -> dict:
    """Given 8K bets over 13 months, what bet sizes work for $1K?"""
    # Monthly bet counts
    monthly_counts: dict[str, int] = {}
    for b in bets:
        monthly_counts[b.entry_month] = monthly_counts.get(b.entry_month, 0) + 1

    avg_bets_per_month = np.mean(list(monthly_counts.values()))

    # How many concurrent bets (from concurrency analysis)?
    # Rough estimate: avg_bets_per_month * avg_lifetime / 30
    avg_lifetime = np.mean([b.lifetime_days for b in bets])
    est_concurrent = avg_bets_per_month * avg_lifetime / 30

    # At various sizing levels, what's the max monthly capital needed?
    sizing_options = {}
    for bet_amt in [5, 10, 15, 20, 25, 50]:
        # Peak concurrent * bet_amt * avg_no_price
        avg_no_price = 1 - np.mean([b.early_yes_price for b in bets])
        peak_capital = est_concurrent * bet_amt * avg_no_price
        monthly_bets = avg_bets_per_month
        monthly_turnover = monthly_bets * bet_amt
        # Expected monthly PnL based on $4.96/bet (from research at $100)
        pnl_per_dollar = 4.96 / 100  # ~5% return per bet
        expected_monthly_pnl = monthly_bets * bet_amt * pnl_per_dollar
        expected_monthly_pct = expected_monthly_pnl / bankroll * 100

        sizing_options[f"${bet_amt}/bet"] = {
            "bet_amount": bet_amt,
            "avg_concurrent_capital": round(est_concurrent * bet_amt * avg_no_price, 0),
            "pct_of_bankroll_per_bet": round(bet_amt / bankroll * 100, 1),
            "monthly_bets": round(avg_bets_per_month, 0),
            "expected_monthly_pnl": round(expected_monthly_pnl, 0),
            "expected_monthly_return_pct": round(expected_monthly_pct, 1),
            "expected_annual_return_pct": round(expected_monthly_pct * 12, 0),
            "fits_bankroll": peak_capital <= bankroll,
        }

    return {
        "bankroll": bankroll,
        "avg_bets_per_month": round(avg_bets_per_month, 0),
        "avg_lifetime_days": round(avg_lifetime, 1),
        "est_concurrent_positions": round(est_concurrent, 0),
        "sizing_options": sizing_options,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("$1K DEPLOYMENT ANALYSIS: Will-Binary-NO Strategy")
    print("=" * 70)

    resolved, mkt = load_will_binary_data()

    # Walk-forward
    print("\n3. Running walk-forward backtest (Will binary NO, YES 10-50%)...")
    bets = walk_forward_will_no(
        resolved,
        entry_lo=0.10,
        entry_hi=0.50,
        bet_size=100.0,
        test_start="2025-01",
        test_end="2026-02",
    )
    print(f"  Total bets: {len(bets):,}")
    total_pnl = sum(b.pnl for b in bets)
    wins = sum(1 for b in bets if b.won_no)
    print(f"  Wins: {wins:,} ({wins/len(bets):.1%})")
    print(f"  Total PnL: ${total_pnl:,.0f}")
    print(f"  Avg PnL/bet: ${total_pnl/len(bets):.2f}")

    # Monthly stats
    monthly: dict[str, dict] = {}
    for b in bets:
        m = monthly.setdefault(b.entry_month, {"bets": 0, "wins": 0, "pnl": 0.0})
        m["bets"] += 1
        m["wins"] += int(b.won_no)
        m["pnl"] += b.pnl

    print(f"\n  {'Month':<10} {'Bets':>6} {'HR':>7} {'PnL':>10} {'CumPnL':>10}")
    print("  " + "-" * 48)
    cum = 0.0
    monthly_pnls = []
    for month in sorted(monthly):
        s = monthly[month]
        hr = s["wins"] / s["bets"]
        cum += s["pnl"]
        monthly_pnls.append(s["pnl"])
        print(f"  {month:<10} {s['bets']:>6} {hr:>6.1%} ${s['pnl']:>9,.0f} ${cum:>9,.0f}")

    # Monthly Sharpe
    if len(monthly_pnls) > 1:
        m_sharpe = float(np.mean(monthly_pnls) / np.std(monthly_pnls, ddof=1) * np.sqrt(12))
    else:
        m_sharpe = 0.0
    print(f"\n  Monthly Sharpe: {m_sharpe:.2f}")

    # --- Analysis 1: Risk of Ruin ---
    print("\n" + "=" * 70)
    print("ANALYSIS 1: Risk of Ruin Monte Carlo ($1K bankroll, 50K simulations)")
    print("=" * 70)
    ror = risk_of_ruin(bets, bankroll=1000.0, n_sims=50_000)
    print(f"  Simulating {ror['bets_simulated']} bets (12 months worth)")
    print(f"\n  {'Sizing':<10} {'Bust':>7} {'Med$':>8} {'P10$':>8} {'P90$':>8} {'MedDD':>8} {'P95DD':>8}")
    print("  " + "-" * 62)
    for name, r in ror["results"].items():
        print(
            f"  {name:<10} {r['bust_rate']:>6.1%} ${r['median_final']:>7,.0f}"
            f" ${r['p10_final']:>7,.0f} ${r['p90_final']:>7,.0f}"
            f" {r['median_max_dd']:>7.1%} {r['p95_max_dd']:>7.1%}"
        )

    # --- Analysis 2: Concurrent Positions ---
    print("\n" + "=" * 70)
    print("ANALYSIS 2: Concurrent Positions & Capital Lockup")
    print("=" * 70)
    conc = concurrent_positions(bets, bet_size=100.0)
    for k, v in conc.items():
        print(f"  {k}: {v}")

    # --- Analysis 3: Losing Streaks ---
    print("\n" + "=" * 70)
    print("ANALYSIS 3: Losing Streaks")
    print("=" * 70)
    streaks = losing_streaks(bets)
    for k, v in streaks.items():
        print(f"  {k}: {v}")

    # --- Analysis 4: Will Subtypes ---
    print("\n" + "=" * 70)
    print("ANALYSIS 4: Will Question Subtypes")
    print("=" * 70)
    subtypes = subtype_analysis(bets)
    print(f"\n  {'Subtype':<20} {'Bets':>6} {'HR':>7} {'PnL':>10} {'$/bet':>8} {'AvgYES':>8}")
    print("  " + "-" * 65)
    for st, s in subtypes.items():
        print(
            f"  {st:<20} {s['n_bets']:>6} {s['hit_rate']:>6.1%}"
            f" ${s['total_pnl']:>9,.0f} ${s['avg_pnl_per_bet']:>7.2f}"
            f" {s['avg_yes_price']:>7.1%}"
        )

    # --- Analysis 5: Sizing Options ---
    print("\n" + "=" * 70)
    print("ANALYSIS 5: Bet Sizing for $1K Bankroll")
    print("=" * 70)
    sizing = optimal_sizing(bets, bankroll=1000.0)
    print(f"  Avg bets/month: {sizing['avg_bets_per_month']}")
    print(f"  Avg lifetime: {sizing['avg_lifetime_days']} days")
    print(f"  Est. concurrent positions: {sizing['est_concurrent_positions']}")
    print(f"\n  {'Size':<12} {'Cap%':>6} {'Conc$':>8} {'Mo PnL':>8} {'Mo%':>6} {'Yr%':>6} {'Fits?':>6}")
    print("  " + "-" * 60)
    for name, s in sizing["sizing_options"].items():
        print(
            f"  {name:<12} {s['pct_of_bankroll_per_bet']:>5.1f}%"
            f" ${s['avg_concurrent_capital']:>7,.0f}"
            f" ${s['expected_monthly_pnl']:>7,.0f}"
            f" {s['expected_monthly_return_pct']:>5.1f}%"
            f" {s['expected_annual_return_pct']:>5.0f}%"
            f" {'YES' if s['fits_bankroll'] else 'NO':>5}"
        )

    # --- Save all results ---
    output = {
        "strategy": "Will-Binary-NO, YES 10-50%",
        "backtest_period": "2025-01 to 2026-01",
        "n_bets": len(bets),
        "hit_rate": round(wins / len(bets), 4),
        "total_pnl": round(total_pnl, 2),
        "monthly_sharpe": round(m_sharpe, 2),
        "risk_of_ruin": ror,
        "concurrent_positions": conc,
        "losing_streaks": streaks,
        "subtypes": subtypes,
        "sizing_options": sizing,
    }

    json_path = OUT_DIR / "04_deployment_analysis.json"
    json_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n\nSaved to {json_path}")


if __name__ == "__main__":
    main()
