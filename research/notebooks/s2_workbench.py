"""S2 Hit-Rate Copy — Interactive Research Workbench.

Run with: uv run marimo edit research/notebooks/s2_workbench.py
"""
import marimo

__generated_with = "0.10.0"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _(mo):
    mo.md("# S2 Hit-Rate Copy — Research Workbench")
    return ()


@app.cell
def _(mo):
    # ── Parameter Controls ──
    min_positions = mo.ui.slider(10, 100, value=30, step=10, label="Min positions")
    min_excess_hr = mo.ui.slider(0.01, 0.30, value=0.10, step=0.01, label="Min excess HR")
    seed_threshold = mo.ui.dropdown(["1", "2"], value="1", label="Seed threshold")
    scale_threshold = mo.ui.dropdown(["2", "3", "4", "5"], value="4", label="Scale threshold")
    seed_pct = mo.ui.dropdown(["0.25", "0.50"], value="0.25", label="Seed %")
    direction = mo.ui.dropdown(["YES", "NO", "BOTH"], value="BOTH", label="Direction")
    seed_timeout = mo.ui.dropdown(["None", "24", "72", "168"], value="None", label="Seed timeout (h)")
    position_size = mo.ui.slider(25, 250, value=100, step=25, label="Position size ($)")
    max_open = mo.ui.slider(5, 50, value=20, step=5, label="Max open positions")
    capital = mo.ui.slider(500, 5000, value=1000, step=500, label="Capital ($)")
    # New improvement controls
    max_entry_price = mo.ui.slider(0.50, 1.0, value=0.85, step=0.05, label="Max entry price (pool)")
    max_signal_price = mo.ui.slider(0.50, 1.0, value=0.85, step=0.05, label="Max signal price")
    use_bayesian_hr = mo.ui.checkbox(value=True, label="Bayesian HR")
    exclude_cats = mo.ui.multiselect(
        options=["Sports", "Weather", "Gambling"],
        value=["Sports", "Weather"],
        label="Exclude categories",
    )
    yes_weight = mo.ui.slider(0.5, 2.0, value=1.0, step=0.25, label="YES weight")
    no_weight = mo.ui.slider(0.5, 2.0, value=1.0, step=0.25, label="NO weight")
    max_consensus_window = mo.ui.dropdown(
        ["None", "24", "48", "72", "168"], value="None", label="Consensus window (h)"
    )
    max_hold = mo.ui.dropdown(
        ["None", "72", "168", "336"], value="None", label="Max hold (h)"
    )

    mo.hstack([
        mo.vstack([min_positions, min_excess_hr, direction, max_entry_price]),
        mo.vstack([seed_threshold, scale_threshold, seed_pct, max_signal_price]),
        mo.vstack([seed_timeout, position_size, max_open, capital]),
        mo.vstack([use_bayesian_hr, yes_weight, no_weight, max_consensus_window, max_hold]),
        mo.vstack([exclude_cats]),
    ])
    return (
        min_positions, min_excess_hr, seed_threshold, scale_threshold,
        seed_pct, direction, seed_timeout, position_size, max_open, capital,
        max_entry_price, max_signal_price, use_bayesian_hr, exclude_cats,
        yes_weight, no_weight, max_consensus_window, max_hold,
    )


@app.cell
def _(mo):
    # ── Period Selection ──
    periods = mo.ui.multiselect(
        options=["2025-01", "2025-04", "2025-07", "2025-10", "2026-01"],
        value=["2025-07"],
        label="Replay periods",
    )
    periods
    return (periods,)


@app.cell
def _(
    mo, min_positions, min_excess_hr, seed_threshold, scale_threshold,
    seed_pct, direction, seed_timeout, position_size, max_open, capital,
    max_entry_price, max_signal_price, use_bayesian_hr, exclude_cats,
    yes_weight, no_weight, max_consensus_window, max_hold,
    periods,
):
    run_btn = mo.ui.run_button(label="Run Replay")
    run_btn
    return (run_btn,)


@app.cell
def _(
    run_btn, mo, min_positions, min_excess_hr, seed_threshold, scale_threshold,
    seed_pct, direction, seed_timeout, position_size, max_open, capital,
    max_entry_price, max_signal_price, use_bayesian_hr, exclude_cats,
    yes_weight, no_weight, max_consensus_window, max_hold,
    periods,
):
    mo.stop(not run_btn.value, mo.md("*Click 'Run Replay' to start.*"))

    import asyncio
    import time

    from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
    from research.strategies.s2_replay import run_s2_replay
    from research.strategies.s2_data import load_period_trades, invert_token_map
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.types import ExecutionMode

    t0 = time.time()

    cfg = S2Config(
        min_positions=min_positions.value,
        min_excess_hr=min_excess_hr.value,
        seed_threshold=int(seed_threshold.value),
        scale_threshold=int(scale_threshold.value),
        seed_pct=float(seed_pct.value),
        seed_timeout_hours=None if seed_timeout.value == "None" else float(seed_timeout.value),
        direction=direction.value,
        position_size_usd=position_size.value,
        max_entry_price=max_entry_price.value,
        max_signal_price=max_signal_price.value,
        use_bayesian_hr=use_bayesian_hr.value,
        exclude_tags=tuple(exclude_cats.value),
        yes_weight=yes_weight.value,
        no_weight=no_weight.value,
        max_consensus_window_hours=None if max_consensus_window.value == "None" else float(max_consensus_window.value),
        max_hold_hours=None if max_hold.value == "None" else float(max_hold.value),
    )

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=capital.value,
        max_position_usd=position_size.value,
        max_open_positions=max_open.value,
        cooldown_s=0,
    )

    # Load qualified traders from CH
    from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend

    backend = ClickHouseBackend(host="192.168.0.148", port=18123, database="polymarket")
    sql = S2HitRateCopy.qualified_traders_query(
        min_positions=cfg.min_positions,
        min_excess_hr=cfg.min_excess_hr,
        recency_months=cfg.recency_months,
        direction=cfg.direction,
        max_entry_price=cfg.max_entry_price,
        exclude_tags=cfg.exclude_tags,
        use_bayesian_hr=cfg.use_bayesian_hr,
    )
    pool_df = asyncio.run(backend._execute(sql))

    qualified_traders = set(pool_df["trader"].to_list()) if len(pool_df) > 0 else set()

    mo.md(f"**Pool size:** {len(qualified_traders)} traders ({time.time() - t0:.1f}s)")

    # Load trades for selected periods (pre-filtered by qualified makers)
    all_results = {}
    for period in periods.value:
        strat = S2HitRateCopy(cfg)
        strat.set_qualified_traders(qualified_traders)

        trades, resolutions, token_map = asyncio.run(
            load_period_trades(period, qualified_traders, backend)
        )
        # Set token_map for direction-aware sizing
        strat.set_token_map(invert_token_map(token_map))

        if not trades:
            all_results[period] = None
            continue

        result, summary = asyncio.run(run_s2_replay(
            strategy=strat,
            trades=trades,
            config=strat_config,
            resolutions=resolutions,
            token_map=token_map,
        ))
        all_results[period] = summary

    elapsed = time.time() - t0

    return (all_results, elapsed, qualified_traders, pool_df)


@app.cell
def _(mo, all_results, elapsed):
    import polars as pl

    rows = []
    for period, summary in all_results.items():
        if summary is None:
            continue
        # Compute excess HR (approximate — need direction info)
        base_hr = 0.381  # YES base rate; adjust per direction
        excess = summary.hit_rate - base_hr
        hold_days = summary.avg_hold_duration_s / 86400 if summary.avg_hold_duration_s > 0 else 0
        compound = (
            excess * summary.avg_edge / max(hold_days, 0.1)
            if summary.avg_edge > 0
            else 0
        )
        rows.append({
            "period": period,
            "fills": summary.total_fills,
            "wins": summary.win_count,
            "losses": summary.loss_count,
            "hit_rate": f"{summary.hit_rate:.1%}",
            "excess_hr": f"{excess:+.1%}",
            "sharpe": f"{summary.sharpe:.2f}",
            "pnl_net": f"${summary.total_pnl_net:,.2f}",
            "avg_edge": f"${summary.avg_edge:,.4f}",
            "max_dd": f"${summary.max_drawdown:,.2f}",
            "hold_days": f"{hold_days:.1f}",
            "compounding": f"{compound:.3f}",
        })

    if rows:
        df = pl.DataFrame(rows)
        mo.md(f"### Results ({elapsed:.1f}s)")
        mo.ui.table(df)
    else:
        mo.md("*No results — check period selection and qualified pool.*")

    return ()


@app.cell
def _(mo):
    mo.md("---\n## Auto-Sweep Mode")
    return ()


@app.cell
def _(mo):
    sweep_excess_hr = mo.ui.multiselect(
        options=["0.05", "0.10", "0.15", "0.20"],
        value=["0.05", "0.10", "0.15"],
        label="Sweep: min_excess_hr",
    )
    sweep_scale = mo.ui.multiselect(
        options=["3", "4", "5"],
        value=["3", "4"],
        label="Sweep: scale_threshold",
    )
    sweep_direction = mo.ui.multiselect(
        options=["YES", "NO", "BOTH"],
        value=["YES", "BOTH"],
        label="Sweep: direction",
    )
    sweep_periods = mo.ui.multiselect(
        options=["2025-01", "2025-04", "2025-07", "2025-10", "2026-01"],
        value=["2025-04", "2025-07", "2025-10"],
        label="Sweep periods",
    )

    grid_size = len(sweep_excess_hr.value) * len(sweep_scale.value) * len(sweep_direction.value)
    total_runs = grid_size * len(sweep_periods.value)

    mo.hstack([
        mo.vstack([sweep_excess_hr, sweep_scale]),
        mo.vstack([sweep_direction, sweep_periods]),
    ])
    mo.md(f"**Grid:** {grid_size} configs x {len(sweep_periods.value)} periods = {total_runs} replays")

    return (sweep_excess_hr, sweep_scale, sweep_direction, sweep_periods)


@app.cell
def _(mo):
    sweep_btn = mo.ui.run_button(label="Run Sweep")
    sweep_btn
    return (sweep_btn,)


@app.cell
def _(
    sweep_btn, mo, sweep_excess_hr, sweep_scale, sweep_direction, sweep_periods,
    min_positions, seed_threshold, seed_pct, seed_timeout, position_size, max_open, capital,
):
    mo.stop(not sweep_btn.value, mo.md("*Click 'Run Sweep' to start.*"))

    import asyncio
    import time

    from research.strategies.s2_hitrate_copy import S2Config, S2HitRateCopy
    from research.strategies.s2_replay import run_s2_replay
    from research.strategies.s2_data import load_period_trades
    from research.strategies.s2_sweep import (
        build_sweep_grid,
        aggregate_period_summaries,
        sweep_results_to_polars,
    )
    from polymarket_pipeline.strategies.config import StrategyConfig
    from polymarket_pipeline.strategies.features.backend_clickhouse import ClickHouseBackend
    from polymarket_pipeline.strategies.types import ExecutionMode

    t0 = time.time()
    backend = ClickHouseBackend(host="192.168.0.148", port=18123, database="polymarket")

    # Build sweep grid
    grid = build_sweep_grid(
        sweep_params={
            "min_excess_hr": [float(x) for x in sweep_excess_hr.value],
            "scale_threshold": [int(x) for x in sweep_scale.value],
            "direction": list(sweep_direction.value),
        },
        fixed_params={
            "min_positions": min_positions.value,
            "seed_threshold": int(seed_threshold.value),
            "seed_pct": float(seed_pct.value),
            "seed_timeout_hours": None if seed_timeout.value == "None" else float(seed_timeout.value),
            "position_size_usd": position_size.value,
            "recency_months": 6,
        },
    )

    strat_config = StrategyConfig(
        enabled=True,
        mode=ExecutionMode.REPLAY,
        capital_usd=capital.value,
        max_position_usd=position_size.value,
        max_open_positions=max_open.value,
        cooldown_s=0,
    )

    all_sweep_results = []
    for params in grid:
        cfg = S2Config(**params)
        # Get pool for this config
        sql = S2HitRateCopy.qualified_traders_query(
            min_positions=cfg.min_positions,
            min_excess_hr=cfg.min_excess_hr,
            recency_months=cfg.recency_months,
            direction=cfg.direction,
        )
        pool_df = asyncio.run(backend._execute(sql))
        qualified = set(pool_df["trader"].to_list()) if len(pool_df) > 0 else set()

        period_summaries = []
        for period in sweep_periods.value:
            strat = S2HitRateCopy(cfg)
            strat.set_qualified_traders(qualified)

            trades, resolutions, token_map = asyncio.run(
                load_period_trades(period, qualified, backend)
            )

            if not trades:
                period_summaries.append(None)
                continue

            _, summary = asyncio.run(run_s2_replay(
                strategy=strat, trades=trades, config=strat_config,
                resolutions=resolutions, token_map=token_map,
            ))
            period_summaries.append(summary)

        sweep_result = aggregate_period_summaries(params, period_summaries)
        all_sweep_results.append(sweep_result)

    sweep_df = sweep_results_to_polars(all_sweep_results)
    elapsed_sweep = time.time() - t0

    mo.md(f"### Sweep Results ({elapsed_sweep:.1f}s, {len(grid)} configs x {len(sweep_periods.value)} periods)")
    mo.ui.table(sweep_df)

    return (all_sweep_results, sweep_df)


if __name__ == "__main__":
    app.run()
