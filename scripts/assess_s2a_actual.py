"""Assess S2a Will-NO strategy: actual implementation vs exploration claims.

Runs the REAL WillNoStrategy.compute_signals() against ClickHouse data,
joins with resolution outcomes, and computes true PnL.  Compares to the
exploration script methodology to identify discrepancies.

Key gaps being tested:
  1. Signal count — does the strategy produce ~2,533 signals?
  2. Entry price — strategy uses trade.price (first qualifying trade),
     exploration uses argMin(price, timestamp) from ALL trades.
  3. Volume filter — strategy uses event_volume (Gamma API) if present,
     exploration uses sum(price*size) from trades.  Completely different.
  4. Keyword matching — both use str.contains (substring). Should match.
  5. PnL — are the numbers consistent?

Usage:
    uv run python scripts/assess_s2a_actual.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

import httpx
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_pipeline.strategies_impl.will_no.config import (
    DEFAULT_PRICE_BANDS,
    WillNoConfig,
)
from polymarket_pipeline.strategies_impl.will_no.strategy import WillNoStrategy
from polymarket_pipeline.strategies.runners.vectorized import VectorizedRunner

CH_HOST = "192.168.0.148"
CH_PORT = 18123
CH_DB = "polymarket"
N_MONTHS = 18.0


def _fmt(t0: float) -> str:
    e = time.time() - t0
    return f"{e:.1f}s" if e < 60 else f"{e / 60:.1f}min"


async def query_ch(
    client: httpx.AsyncClient, query: str, *, label: str = "query"
) -> pl.DataFrame:
    t0 = time.time()
    print(f"  [{label}] …", end="", flush=True)
    async with client.stream(
        "POST", "/", content=f"{query} FORMAT CSVWithNamesAndTypes",
        params={"database": CH_DB},
        headers={"Content-Type": "text/plain"},
        timeout=600.0,
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode()[:500]
            raise RuntimeError(body)
        chunks: list[bytes] = []
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data.strip():
        print(f" empty ({_fmt(t0)})")
        return pl.DataFrame()
    df = pl.read_csv(
        io.BytesIO(data), has_header=True, skip_rows_after_header=1,
        null_values=["\\N", ""],
        infer_schema_length=10000,
    )
    print(f" {len(df):,} rows ({_fmt(t0)})")
    return df


def print_band_breakdown(
    df: pl.DataFrame, bands: tuple, price_col: str = "entry_price"
) -> None:
    """Print per-band breakdown."""
    n_bands = len(bands)
    print(f"\n  {'Band':<10} {'Sigs':>6} {'HR':>7} {'ROI(2%)':>8} {'$/bet':>7} "
          f"{'Med LD':>7} {'Mult':>5}")
    print(f"  {'-' * 10} {'-' * 6} {'-' * 7} {'-' * 8} {'-' * 7} "
          f"{'-' * 7} {'-' * 5}")

    for i, (lo, hi, mult) in enumerate(bands):
        if i < n_bands - 1:
            subset = df.filter(
                (pl.col(price_col) >= lo) & (pl.col(price_col) < hi)
            )
        else:
            subset = df.filter(
                (pl.col(price_col) >= lo) & (pl.col(price_col) <= hi)
            )
        ns = len(subset)
        if ns < 3:
            continue
        ws = subset.filter(pl.col("won")).height
        hr = ws / ns
        pnl = subset["pnl"].sum()
        bet = subset["size_usd"].sum()
        roi = pnl / bet * 100 if bet > 0 else 0
        avg_pnl = pnl / ns

        ld_str = "    —"
        if "lockup_days" in subset.columns:
            ld = subset.filter(pl.col("lockup_days").is_not_null())["lockup_days"]
            if len(ld) > 0:
                med_ld = ld.median()
                ld_str = f"{med_ld:>5.1f}d" if med_ld is not None else "    —"

        marker = "+" if roi > 0 else "-"
        print(f" {marker} {lo:.0%}-{hi:.0%}     {ns:>6,} {hr:>6.1%} {roi:>+7.1f}% "
              f"${avg_pnl:>+5.2f} {ld_str}  {mult:.2f}")


async def main() -> None:
    t_start = time.time()
    cfg = WillNoConfig()  # production defaults
    print("=" * 100)
    print("  S2a Will-NO: ACTUAL IMPLEMENTATION vs EXPLORATION ASSESSMENT")
    print("=" * 100)
    print(f"\n  Production config:")
    print(f"    price_bands:      {len(cfg.price_bands)} bands, "
          f"{cfg.price_bands[0][0]:.0%}-{cfg.price_bands[-1][1]:.0%}")
    print(f"    prefer_keywords:  {cfg.prefer_keywords}")
    print(f"    avoid_keywords:   {cfg.avoid_keywords}")
    print(f"    max_volume_usd:   {cfg.max_volume_usd}")
    print(f"    max_bucket:       {cfg.max_bucket}")
    print(f"    question_pattern: {cfg.question_pattern}")
    print(f"    base_bet_usd:     {cfg.base_bet_usd}")
    print(f"    fee_pct:          {cfg.fee_pct}")
    print(f"    dual_sided:       {cfg.dual_sided}")

    client = httpx.AsyncClient(
        base_url=f"http://{CH_HOST}:{CH_PORT}", timeout=600.0
    )

    # ══════════════════════════════════════════════════════════════════
    # PHASE 1: Data Loading (all via ClickHouse subqueries)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 1: Data Loading")
    print(f"{'=' * 100}")

    # All markets
    markets_df = await query_ch(
        client,
        """SELECT condition_id, toString(event_id) AS event_id, question,
                  category, neg_risk
           FROM markets""",
        label="markets",
    )
    n_will = markets_df.filter(
        pl.col("question").str.contains(r"(?i)^Will\b")
    ).height
    print(f"  Will markets in metadata: {n_will:,}")

    # First trade per condition_id — ALL Will markets, aggregated server-side.
    # This is what the strategy reduces to after unique(keep="first").
    first_trades = await query_ch(
        client,
        """WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
           SELECT condition_id,
                  argMin(CAST(price AS Float64), toUnixTimestamp(timestamp)) AS first_price,
                  min(toUnixTimestamp64Milli(timestamp)) / 1000.0 AS first_published_at
           FROM (SELECT * FROM polymarket.trades_raw FINAL) t
           WHERE condition_id IN (SELECT condition_id FROM wm)
           GROUP BY condition_id""",
        label="first-trades",
    )
    for c in ["first_price", "first_published_at"]:
        first_trades = first_trades.with_columns(pl.col(c).cast(pl.Float64))
    print(f"  Markets with trades: {len(first_trades):,}")

    # Trade-level volume (exploration's volume metric)
    trade_volume = await query_ch(
        client,
        """WITH wm AS (SELECT condition_id FROM markets WHERE question LIKE 'Will %%')
           SELECT condition_id,
                  sum(CAST(price AS Float64) * CAST(size AS Float64)) AS trade_volume
           FROM (SELECT * FROM polymarket.trades_raw FINAL) t
           WHERE condition_id IN (SELECT condition_id FROM wm)
           GROUP BY condition_id""",
        label="trade-volume",
    )
    trade_volume = trade_volume.with_columns(pl.col("trade_volume").cast(pl.Float64))

    # Gamma API event_volume (what the strategy uses)
    events_df = await query_ch(
        client,
        """SELECT toString(id) AS event_id,
                  volume AS event_volume,
                  dateDiff('second', toDateTime64('1970-01-01', 3), end_date) AS end_ts
           FROM events""",
        label="events",
    )
    for c in ["event_volume", "end_ts"]:
        events_df = events_df.with_columns(pl.col(c).cast(pl.Float64))

    # Lockup
    lockup_df = await query_ch(
        client,
        """WITH
            wm AS (SELECT condition_id, toString(event_id) AS event_id
                   FROM markets WHERE question LIKE 'Will %%'),
            ft AS (SELECT condition_id, min(toUnixTimestamp(timestamp)) AS first_ts
                   FROM (SELECT * FROM polymarket.trades_raw FINAL) t
                   WHERE condition_id IN (SELECT condition_id FROM wm)
                   GROUP BY condition_id)
           SELECT w.condition_id AS condition_id,
                  (CAST(toUnixTimestamp(e.end_date) AS Float64) - ft.first_ts) / 86400.0
                      AS lockup_days
           FROM wm w
           JOIN ft ON ft.condition_id = w.condition_id
           JOIN events e ON toString(e.id) = w.event_id
           WHERE e.end_date IS NOT NULL""",
        label="lockup",
    )
    lockup_df = lockup_df.with_columns(pl.col("lockup_days").cast(pl.Float64))

    await client.aclose()

    # Resolution from parquet
    resolution = pl.read_parquet("data/metadata/markets.parquet")
    resolution = resolution.select("condition_id", "winner_outcome", "resolution_value")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 2: Run ACTUAL strategy (compute_signals)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 2: Actual WillNoStrategy.compute_signals()")
    print(f"{'=' * 100}")

    # Build trades DataFrame that matches compute_signals() expectations:
    # columns: condition_id, price, published_at, [maker, side optional]
    # The strategy joins trades with markets, filters, sorts by published_at,
    # then unique(subset=["condition_id"], keep="first").
    # Using first_trades (one row per condition_id) is equivalent since
    # the strategy picks the first trade anyway.
    trades_for_strat = first_trades.select(
        pl.col("condition_id"),
        pl.col("first_price").alias("price"),
        pl.col("first_published_at").alias("published_at"),
    )

    # Markets with event_volume (for volume filter)
    markets_with_vol = markets_df.join(
        events_df.select("event_id", "event_volume"),
        on="event_id", how="left",
    )

    # --- Test A: WITHOUT event_volume (volume filter not applied) ---
    print(f"\n  --- Test A: compute_signals WITHOUT event_volume ---")
    print(f"      (no volume filter will apply)")

    strat_a = WillNoStrategy(config=cfg)
    runner_a = VectorizedRunner(strat_a)
    result_a = runner_a.run(trades_for_strat.lazy(), markets_df.lazy())
    signals_a = result_a.signals
    print(f"  Raw signals: {result_a.total_signals:,}")

    # --- Test B: WITH Gamma event_volume (volume_column="event_volume") ---
    print(f"\n  --- Test B: compute_signals WITH Gamma event_volume ---")
    print(f"      (volume_column='event_volume', filter <= {cfg.max_volume_usd})")

    cfg_b = WillNoConfig(volume_column="event_volume")
    strat_b = WillNoStrategy(config=cfg_b)
    runner_b = VectorizedRunner(strat_b)
    result_b = runner_b.run(trades_for_strat.lazy(), markets_with_vol.lazy())
    signals_b = result_b.signals
    print(f"  Raw signals: {result_b.total_signals:,}")

    # --- Test C: WITH trade-level volume as market_volume (default column) ---
    # This matches the exploration methodology and the strategy's default
    print(f"\n  --- Test C: compute_signals with TRADE VOLUME as market_volume ---")
    print(f"      (default volume_column='market_volume', filter <= {cfg.max_volume_usd})")

    markets_with_tv = markets_df.join(
        trade_volume.rename({"trade_volume": "market_volume"}),
        on="condition_id", how="left",
    )
    strat_c = WillNoStrategy(config=cfg)  # default volume_column="market_volume"
    runner_c = VectorizedRunner(strat_c)
    result_c = runner_c.run(trades_for_strat.lazy(), markets_with_tv.lazy())
    signals_c = result_c.signals
    print(f"  Raw signals: {result_c.total_signals:,}")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 3: Compute PnL for each test
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 3: PnL Assessment (resolved markets, 2% fee)")
    print(f"{'=' * 100}")

    def compute_and_report(
        signals: pl.DataFrame, label: str, *, show_bands: bool = True
    ) -> pl.DataFrame | None:
        if signals.is_empty():
            print(f"\n  {label}: NO SIGNALS")
            return None

        # Join with resolution
        df = signals.join(resolution, on="condition_id", how="inner")
        df = df.filter(pl.col("resolution_value") == 1)
        if df.is_empty():
            print(f"\n  {label}: no resolved signals")
            return None

        df = df.with_columns(
            (pl.col("winner_outcome") == "No").alias("won"),
        )
        df = df.with_columns(
            pl.when(pl.col("won"))
            .then(
                pl.col("size_usd") * pl.col("entry_price")
                / (1.0 - pl.col("entry_price"))
                - pl.col("size_usd") * 0.02
            )
            .otherwise(-pl.col("size_usd"))
            .alias("pnl"),
        )

        # Add lockup
        df = df.join(lockup_df, on="condition_id", how="left")

        n = len(df)
        wins = df.filter(pl.col("won")).height
        hr = wins / n
        total_pnl = df["pnl"].sum()
        total_bet = df["size_usd"].sum()
        roi = total_pnl / total_bet * 100 if total_bet > 0 else 0
        avg_bet = total_bet / n
        sigs_mo = n / N_MONTHS
        monthly_pnl = total_pnl / N_MONTHS

        ld = df.filter(pl.col("lockup_days").is_not_null())["lockup_days"]
        med_ld = ld.median() if len(ld) > 0 else None
        capital = None
        mo_roi_cap = None
        if med_ld and med_ld > 0:
            concurrent = sigs_mo * med_ld / 30.0
            capital = concurrent * avg_bet
            mo_roi_cap = monthly_pnl / capital if capital > 0 else 0

        print(f"\n  {label}:")
        print(f"    Resolved signals:   {n:,}")
        print(f"    Hit Rate:           {hr:.1%} ({wins}/{n})")
        print(f"    ROI (2% fee):       {roi:+.1f}%")
        print(f"    Total PnL:          ${total_pnl:+,.0f}")
        print(f"    Avg bet size:       ${avg_bet:.1f}")
        print(f"    Sigs/month:         {sigs_mo:.1f}")
        print(f"    Monthly PnL:        ${monthly_pnl:+,.0f}")
        if med_ld is not None:
            print(f"    Median lockup:      {med_ld:.1f}d")
        if capital is not None:
            print(f"    Est. capital:       ${capital:,.0f}")
        if mo_roi_cap is not None:
            print(f"    Monthly ROI/cap:    {mo_roi_cap:.0%}")

        if show_bands:
            print_band_breakdown(df, cfg.price_bands)

        return df

    pnl_a = compute_and_report(signals_a, "A: No volume filter")
    pnl_b = compute_and_report(signals_b, "B: Gamma event_volume filter")
    pnl_c = compute_and_report(signals_c, "C: Trade volume filter (exploration match)")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 4: Exploration methodology (independent comparison)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 4: Exploration Script Methodology (comparison)")
    print(f"{'=' * 100}")

    # Build exploration base table
    explore_base = markets_df.filter(
        pl.col("question").str.contains(r"(?i)^Will\b")
    )
    explore_base = explore_base.join(first_trades, on="condition_id", how="inner")
    explore_base = explore_base.join(trade_volume, on="condition_id", how="left")
    explore_base = explore_base.join(lockup_df, on="condition_id", how="left")
    explore_base = explore_base.join(resolution, on="condition_id", how="left")

    q_lower = pl.col("question").str.to_lowercase()
    n_bands = len(cfg.price_bands)

    # 1. Price band filter
    band_cond = pl.lit(False)
    for i, (lo, hi, _) in enumerate(cfg.price_bands):
        if i < n_bands - 1:
            band_cond = band_cond | (
                (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
            )
        else:
            band_cond = band_cond | (
                (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
            )
    explore_filt = explore_base.filter(band_cond)

    # 2. Prefer keywords
    if cfg.prefer_keywords:
        prefer_cond = pl.lit(False)
        for kw in cfg.prefer_keywords:
            prefer_cond = prefer_cond | q_lower.str.contains(kw.lower())
        explore_filt = explore_filt.filter(prefer_cond)

    # 3. Avoid keywords
    for kw in cfg.avoid_keywords:
        explore_filt = explore_filt.filter(~q_lower.str.contains(kw.lower()))

    # Show counts at each stage
    n_after_band = explore_base.filter(band_cond).height
    n_after_kw = explore_filt.height
    print(f"\n  Exploration filter funnel (all markets, not just resolved):")
    print(f"    Will markets with trades:     {len(explore_base):,}")
    print(f"    After price band filter:      {n_after_band:,}")
    print(f"    After keyword filters:        {n_after_kw:,}")

    # Without volume filter
    explore_no_vol = explore_filt.filter(pl.col("resolution_value") == 1)
    explore_no_vol = explore_no_vol.with_columns(
        (pl.col("winner_outcome") == "No").alias("won"),
    )
    size_expr = pl.lit(0.0)
    for i, (lo, hi, mult) in enumerate(cfg.price_bands):
        if i < n_bands - 1:
            c = (pl.col("first_price") >= lo) & (pl.col("first_price") < hi)
        else:
            c = (pl.col("first_price") >= lo) & (pl.col("first_price") <= hi)
        size_expr = pl.when(c).then(pl.lit(cfg.base_bet_usd * mult)).otherwise(size_expr)
    explore_no_vol = explore_no_vol.with_columns(size_expr.alias("size_usd"))
    explore_no_vol = explore_no_vol.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
            - pl.col("size_usd") * 0.02
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )

    # With trade volume filter
    explore_tv = explore_filt.filter(pl.col("trade_volume") <= cfg.max_volume_usd)
    n_after_trade_vol = explore_tv.height
    print(f"    After trade_vol <= $2K:        {n_after_trade_vol:,}")

    explore_tv_res = explore_tv.filter(pl.col("resolution_value") == 1)
    explore_tv_res = explore_tv_res.with_columns(
        (pl.col("winner_outcome") == "No").alias("won"),
    )
    explore_tv_res = explore_tv_res.with_columns(size_expr.alias("size_usd"))
    explore_tv_res = explore_tv_res.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
            - pl.col("size_usd") * 0.02
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )

    # With event volume filter
    explore_with_ev = explore_filt.join(
        events_df.select("event_id", "event_volume"), on="event_id", how="left"
    )
    explore_ev = explore_with_ev.filter(pl.col("event_volume") <= cfg.max_volume_usd)
    n_after_ev_vol = explore_ev.height
    print(f"    After event_vol <= $2K:        {n_after_ev_vol:,}")

    explore_ev_res = explore_ev.filter(pl.col("resolution_value") == 1)
    explore_ev_res = explore_ev_res.with_columns(
        (pl.col("winner_outcome") == "No").alias("won"),
    )
    explore_ev_res = explore_ev_res.with_columns(size_expr.alias("size_usd"))
    explore_ev_res = explore_ev_res.with_columns(
        pl.when(pl.col("won"))
        .then(
            pl.col("size_usd") * pl.col("first_price") / (1.0 - pl.col("first_price"))
            - pl.col("size_usd") * 0.02
        )
        .otherwise(-pl.col("size_usd"))
        .alias("pnl"),
    )

    def _summary(label: str, df: pl.DataFrame) -> None:
        n = len(df)
        if n == 0:
            print(f"    {label}: no data")
            return
        wins = df.filter(pl.col("won")).height
        hr = wins / n
        total_pnl = df["pnl"].sum()
        total_bet = df["size_usd"].sum()
        roi = total_pnl / total_bet * 100 if total_bet > 0 else 0
        monthly_pnl = total_pnl / N_MONTHS
        print(f"    {label}: {n:,} sigs, {hr:.1%} HR, {roi:+.1f}% ROI, "
              f"${monthly_pnl:+,.0f}/mo")

    print(f"\n  Exploration results (resolved, 2% fee):")
    _summary("No volume filter  ", explore_no_vol)
    _summary("Trade vol <= $2K  ", explore_tv_res)
    _summary("Event vol <= $2K  ", explore_ev_res)

    # ══════════════════════════════════════════════════════════════════
    # PHASE 5: Entry Price Comparison
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 5: Entry Price Comparison (strategy vs exploration)")
    print(f"{'=' * 100}")

    if not signals_a.is_empty() and not explore_no_vol.is_empty():
        strat_prices = signals_a.select(
            pl.col("condition_id"),
            pl.col("entry_price").alias("strat_entry"),
        )
        explore_prices = explore_no_vol.select(
            pl.col("condition_id"),
            pl.col("first_price").alias("explore_entry"),
        )
        price_comp = strat_prices.join(explore_prices, on="condition_id", how="inner")
        price_comp = price_comp.with_columns(
            (pl.col("strat_entry") - pl.col("explore_entry")).abs().alias("price_diff")
        )
        n_matched = len(price_comp)
        n_exact = price_comp.filter(pl.col("price_diff") < 1e-6).height
        n_close = price_comp.filter(pl.col("price_diff") < 0.01).height
        max_diff = price_comp["price_diff"].max() if n_matched > 0 else 0
        mean_diff = price_comp["price_diff"].mean() if n_matched > 0 else 0

        strat_cids = set(signals_a["condition_id"].to_list())
        explore_cids = set(explore_no_vol["condition_id"].to_list())

        print(f"\n  Markets in strategy (no vol):     {len(strat_cids):,}")
        print(f"  Markets in exploration (no vol):   {len(explore_cids):,}")
        print(f"  In both:                          {n_matched:,}")
        print(f"  Strategy only:                    {len(strat_cids - explore_cids):,}")
        print(f"  Exploration only:                 {len(explore_cids - strat_cids):,}")

        print(f"\n  Entry price comparison ({n_matched:,} common markets):")
        print(f"    Exact match (<1e-6):  {n_exact:,} ({n_exact/n_matched*100:.1f}%)")
        print(f"    Close match (<0.01):  {n_close:,} ({n_close/n_matched*100:.1f}%)")
        print(f"    Max difference:       {max_diff:.6f}")
        print(f"    Mean difference:      {mean_diff:.6f}")

        if n_exact < n_matched:
            mismatches = price_comp.filter(pl.col("price_diff") >= 1e-6).sort(
                "price_diff", descending=True
            ).head(10)
            print(f"\n    Top 10 mismatches:")
            for row in mismatches.iter_rows(named=True):
                print(f"      {row['condition_id'][:20]}… strat={row['strat_entry']:.6f} "
                      f"explore={row['explore_entry']:.6f} diff={row['price_diff']:.6f}")

        # Investigate strategy-only and exploration-only markets
        if strat_cids - explore_cids:
            strat_only = signals_a.filter(
                pl.col("condition_id").is_in(list(strat_cids - explore_cids))
            )
            print(f"\n  Strategy-only markets ({len(strat_only):,}):")
            ep = strat_only["entry_price"]
            print(f"    entry_price: min={ep.min():.3f} med={ep.median():.3f} max={ep.max():.3f}")

            # Check if these are in the exploration base at all
            explore_all_cids = set(explore_base["condition_id"].to_list())
            not_in_base = strat_cids - explore_cids - explore_all_cids
            in_base_but_filtered = (strat_cids - explore_cids) & explore_all_cids
            print(f"    Not in exploration base at all:  {len(not_in_base):,}")
            print(f"    In base but filtered out:        {len(in_base_but_filtered):,}")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 6: Volume Filter Deep Dive
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 6: Volume Filter Discrepancy Analysis")
    print(f"{'=' * 100}")

    vol_compare = trade_volume.join(
        markets_df.join(events_df.select("event_id", "event_volume"),
                        on="event_id", how="left")
        .select("condition_id", "event_volume"),
        on="condition_id", how="inner",
    ).filter(
        pl.col("event_volume").is_not_null() & pl.col("trade_volume").is_not_null()
    ).with_columns([
        pl.col("trade_volume").cast(pl.Float64),
        pl.col("event_volume").cast(pl.Float64),
    ])

    if not vol_compare.is_empty():
        trade_pass = vol_compare.filter(pl.col("trade_volume") <= 2000).height
        event_pass = vol_compare.filter(pl.col("event_volume") <= 2000).height
        both_pass = vol_compare.filter(
            (pl.col("trade_volume") <= 2000) & (pl.col("event_volume") <= 2000)
        ).height
        trade_only = vol_compare.filter(
            (pl.col("trade_volume") <= 2000) & (pl.col("event_volume") > 2000)
        ).height
        event_only = vol_compare.filter(
            (pl.col("trade_volume") > 2000) & (pl.col("event_volume") <= 2000)
        ).height

        corr = vol_compare.select(
            pl.corr("trade_volume", "event_volume").alias("corr")
        )["corr"][0]

        print(f"\n  Markets with both volume measures: {len(vol_compare):,}")
        print(f"    trade_vol <= $2K:              {trade_pass:,}")
        print(f"    event_vol <= $2K:              {event_pass:,}")
        print(f"    Both <= $2K:                   {both_pass:,}")
        print(f"    trade_vol only (<$2K):         {trade_only:,} "
              f"(in exploration, not strategy)")
        print(f"    event_vol only (<$2K):         {event_only:,} "
              f"(in strategy, not exploration)")
        print(f"    Correlation:                   {corr:.3f}")

        # Distribution of ratios
        vol_compare = vol_compare.with_columns(
            (pl.col("event_volume") / pl.col("trade_volume").clip(lower_bound=0.01))
            .alias("ev_tv_ratio")
        )
        ratio = vol_compare["ev_tv_ratio"]
        print(f"\n  event_volume / trade_volume ratio:")
        print(f"    min={ratio.min():.2f}  p25={ratio.quantile(0.25):.2f}  "
              f"median={ratio.median():.2f}  p75={ratio.quantile(0.75):.2f}  "
              f"max={ratio.max():.1f}")

        # For Will+kw markets specifically
        will_kw_cids = set(explore_filt["condition_id"].to_list())
        vol_will_kw = vol_compare.filter(
            pl.col("condition_id").is_in(list(will_kw_cids))
        )
        if not vol_will_kw.is_empty():
            tv_pass_wk = vol_will_kw.filter(pl.col("trade_volume") <= 2000).height
            ev_pass_wk = vol_will_kw.filter(pl.col("event_volume") <= 2000).height
            both_wk = vol_will_kw.filter(
                (pl.col("trade_volume") <= 2000) & (pl.col("event_volume") <= 2000)
            ).height
            print(f"\n  For Will+keyword markets ({len(vol_will_kw):,}):")
            print(f"    trade_vol <= $2K:              {tv_pass_wk:,}")
            print(f"    event_vol <= $2K:              {ev_pass_wk:,}")
            print(f"    Both <= $2K:                   {both_wk:,}")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 7: Fee Sensitivity (from actual strategy, event_vol filter)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 7: Fee Sensitivity (actual strategy)")
    print(f"{'=' * 100}")

    # Use Test C (trade_volume) for apples-to-apples with exploration
    test_signals = signals_c if not signals_c.is_empty() else signals_b
    test_label = "C (trade vol)" if not signals_c.is_empty() else "B (event vol)"
    for fee_label, fee_val in [("0%", 0.0), ("1%", 0.01), ("2%", 0.02), ("3%", 0.03)]:
        df = test_signals.join(resolution, on="condition_id", how="inner")
        df = df.filter(pl.col("resolution_value") == 1)
        df = df.with_columns((pl.col("winner_outcome") == "No").alias("won"))
        df = df.with_columns(
            pl.when(pl.col("won"))
            .then(
                pl.col("size_usd") * pl.col("entry_price")
                / (1.0 - pl.col("entry_price"))
                - pl.col("size_usd") * fee_val
            )
            .otherwise(-pl.col("size_usd"))
            .alias("pnl"),
        )
        n = len(df)
        if n == 0:
            continue
        wins = df.filter(pl.col("won")).height
        total_pnl = df["pnl"].sum()
        total_bet = df["size_usd"].sum()
        roi = total_pnl / total_bet * 100
        print(f"  Test {test_label} @ {fee_label} fee: "
              f"{n:,} sigs, {wins/n:.1%} HR, {roi:+.1f}% ROI, "
              f"${total_pnl/N_MONTHS:+,.0f}/mo")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 8: Monthly Time Series
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  PHASE 8: Monthly PnL Time Series (actual strategy, trade vol filter)")
    print(f"{'=' * 100}")

    if pnl_c is not None and len(pnl_c) > 0:
        best_pnl = pnl_c
    elif pnl_b is not None and len(pnl_b) > 0:
        best_pnl = pnl_b
    elif pnl_a is not None and len(pnl_a) > 0:
        best_pnl = pnl_a
    else:
        best_pnl = None

    if best_pnl is not None:
        best_pnl = best_pnl.with_columns(
            pl.from_epoch(pl.col("signal_time").cast(pl.Int64), time_unit="s")
            .alias("signal_date")
        )
        best_pnl = best_pnl.with_columns(
            pl.col("signal_date").dt.strftime("%Y-%m").alias("month"),
        )

        months = sorted(best_pnl["month"].unique().to_list())
        print(f"\n  {'Month':<10} {'Sigs':>5} {'HR':>7} {'ROI':>8} {'PnL':>9} {'Cum PnL':>9}")
        print(f"  {'-'*10} {'-'*5} {'-'*7} {'-'*8} {'-'*9} {'-'*9}")

        cum_pnl = 0.0
        win_months = 0
        for m in months:
            md = best_pnl.filter(pl.col("month") == m)
            ns = len(md)
            if ns == 0:
                continue
            ws = md.filter(pl.col("won")).height
            hr = ws / ns
            pnl_m = md["pnl"].sum()
            bet_m = md["size_usd"].sum()
            roi_m = pnl_m / bet_m * 100 if bet_m > 0 else 0
            cum_pnl += pnl_m
            if pnl_m > 0:
                win_months += 1
            marker = "+" if pnl_m > 0 else "-"
            print(f" {marker} {m:<10} {ns:>5} {hr:>6.1%} {roi_m:>+7.1f}% "
                  f"${pnl_m:>+8,.0f} ${cum_pnl:>+8,.0f}")

        print(f"\n  {win_months}/{len(months)} months profitable "
              f"({win_months/len(months)*100:.0f}%)")

        # Rolling 3-month stability
        win_w = 0
        total_w = 0
        for i in range(len(months) - 2):
            wm = months[i:i + 3]
            w = best_pnl.filter(pl.col("month").is_in(wm))
            if len(w) < 5:
                continue
            total_w += 1
            if w["pnl"].sum() > 0:
                win_w += 1
        if total_w > 0:
            print(f"  {win_w}/{total_w} rolling 3-month windows profitable "
                  f"({win_w/total_w*100:.0f}%)")

    # ══════════════════════════════════════════════════════════════════
    # PHASE 9: Verdict
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 100}")
    print("  VERDICT: Exploration Claims vs Actual Implementation")
    print(f"{'=' * 100}")

    print(f"""
  CLAIMED (exploration scripts, trade-level volume < $2K):
    ~2,533 signals (resolved), 80.4% HR, +38.1% ROI, $2,024/mo PnL

  ACTUAL STRATEGY RESULTS (2% fee):""")

    for label, sigs, pnl_df in [
        ("A: No volume filter", signals_a, pnl_a),
        ("B: Gamma event_vol", signals_b, pnl_b),
        ("C: Trade vol (=explore)", signals_c, pnl_c),
    ]:
        if pnl_df is not None and len(pnl_df) > 0:
            n = len(pnl_df)
            wins = pnl_df.filter(pl.col("won")).height
            total_pnl = pnl_df["pnl"].sum()
            total_bet = pnl_df["size_usd"].sum()
            roi = total_pnl / total_bet * 100
            mo_pnl = total_pnl / N_MONTHS
            print(f"    {label}: {n:,} sigs, {wins/n:.1%} HR, "
                  f"{roi:+.1f}% ROI, ${mo_pnl:+,.0f}/mo")
        else:
            print(f"    {label}: no data")

    print(f"""
  GAPS IDENTIFIED:
    1. VOLUME FILTER MISMATCH: Strategy uses Gamma event_volume (if present).
       Exploration uses trade-level sum(price*size). These are different numbers.
       In production, the effective filter depends on which volume column is
       available in the data pipeline.

    2. EVENT_VOLUME MAY BE NULL: Many markets have no event_volume. The strategy
       passes these through (null <= $2K is false in Polars, so they'd be
       filtered OUT). This may reduce signal count vs exploration.

    3. MAX_BUCKET FILTER: The event-driven path uses max_bucket=med (from
       MarketSizeProvider). The vectorized path does NOT have this filter.
       This creates a gap between event-driven and vectorized behavior.

    4. ENTRY PRICE: Strategy uses the first qualifying trade's price.
       Exploration uses argMin(price, timestamp) = price of earliest trade.
       These should match for most markets but may differ when the earliest
       trade doesn't qualify (e.g., price outside band) and a later trade does.
  """)

    print(f"  Assessment complete in {_fmt(t_start)}")


if __name__ == "__main__":
    asyncio.run(main())
