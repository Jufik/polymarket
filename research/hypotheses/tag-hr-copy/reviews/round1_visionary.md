# Visionary Review: tag-hr-copy (Round 1)

## Adjacent Signals

1. **Esports: game-title sub-signal (CS2 vs Dota2 vs LoL).** The 77% HR across all Esports is almost certainly the average of heterogeneous sub-pools. CS2 has a professional tournament calendar (BLAST, ESL) with predictable scheduling and well-documented team strength signals. LoL worlds bracket outcomes are heavily covered by analysts. Dota2 has a smaller but more insular trader pool. Run an independent sweep per game title using sub-tag or question-text filtering. The strongest game title is likely carrying the aggregate; the weakest may be dragging it.

2. **Recency-weighted qualification score.** The notes flag that Esports base rates shifted 37pp over 12 months (Jan 2025: 13% YES → Jan 2026: 51% YES). A trader who was highly skilled in the 2025-H1 low-YES-bias regime may be systematically miscalibrated now that YES base rates have doubled. Replace the binary `min_trades >= 50` gate with an exponentially weighted excess HR where older trades decay with a half-life of 90 days. This would naturally downweight stale skill signals without requiring a fixed trailing window.

3. **Entry price filter for thin-edge tags (1H Crypto, Tennis).** Both tags show robust HR but modest dollar PnL ($3-5 avg). Price at entry determines asymmetric payoff: a $5.30 avg PnL at 0.70 entry has very different risk/reward than the same HR at 0.40 entry. Filter signals to entries below 0.55 (the price below which payoff asymmetry starts compensating for slippage). This is the same insight from `pitfalls/excess_hr_vs_absolute_hr.md` — positive PnL can come from entry price asymmetry independent of genuine alpha, but you can also USE that asymmetry deliberately.

4. **NCAA anti-signal: trade NO on traders whose BUY HR is below base.** BUY-only HR for NCAA qualified traders is 18.6% vs a ~30% base rate — a -11pp gap. These are traders who systematically bet YES in NCAA markets and lose. The signal inversion: when they signal BUY YES, bet NO. This requires a short-selling mechanism or synthetic NO entry, but the informational content is real. Validate the pool size first — if fewer than 10 traders drive this, it is too thin.

5. **Tennis grand-slam vs regular-tour segmentation.** Tennis shows 55.7% HR with extreme robustness (0.9pp max drop). Grand slams (Wimbledon, US Open, Roland Garros, Australian Open) have far denser betting activity, more public coverage, and potentially noisier signals. Regular ATP/WTA tour events are lower volume but may have stronger specialist edges because the information set is more private. Split the tag by event tier using question text pattern matching, and sweep separately.

## Parameter Variations

1. **Esports: test mt=20-40 range at ep=15.** The current winner is mt=50 but the sweep shows mt=40 increases universe while keeping HR > 74.6%. The unlock at mt=20-30 could double the signal count if HR stays above 70%. The key question is whether the qualifier pool dilutes below the minimum viable specialist density.

2. **Basketball: add consensus filter (n_traders >= 2).** BUY-only Basketball has avg_pnl $86 driven by whale outliers and median $0.86. The median signals are nearly break-even. A consensus filter requiring 2+ independently qualified traders on the same market before signaling would select for markets with more genuine information density, not just one whale's directional bet. This directly addresses the `pitfalls/consensus_dedup.md` concern.

3. **1H Crypto: test minimum entry price floor (e.g., price < 0.80).** At $5.30 avg PnL with 1.67h hold, the strategy is thin. If traders are buying YES at 0.85+ the payoff is capped and slippage burns the edge. Excluding high-price entries would raise the floor on per-signal PnL at the cost of some volume.

4. **Trailing window length sensitivity (3mo vs 6mo vs 12mo).** All folds use the same trailing window implicitly. With Esports base rates shifting 37pp over 12 months, a 12-month qualification window may be using stale traders. Sweep 3mo / 6mo / 9mo trailing windows across tags. Expected: 3mo window hurts Tennis and 1H (thin monthly volume), but helps Esports by excluding early-2025-regime traders who may be miscalibrated now.

5. **Mixed-tag portfolio sweep.** Currently each tag is evaluated independently. A trader who qualifies in BOTH Esports and Tennis (multi-domain specialist) may have a much higher combined HR than either tag alone. Run a joint qualification sweep: traders who meet the mt/ep threshold in two or more target tags simultaneously. The hypothesis is that cross-domain sports expertise signals genuine domain competence rather than luck.

## Cross-Hypothesis Connections

- **`pitfalls/split_position_blind_spot.md`**: The discovery uses `maker_positions_resolved_corrected`, which applies split corrections. However, split-affected YES positions have 23% HR (below 38% base rate), often from MMs misclassified as directional. The Esports 77% HR is computed on corrected positions, but if Esports has higher-than-average split activity (e.g., MMs providing liquidity via split+sell), the correction may leave residual contamination. Worth checking: what fraction of Esports qualified traders have `net_tokens < 0` on their positions?

- **`data/tag_base_rates.md`**: Esports base rate is listed as 45.8% YES overall, but the discovery notes it ranged 13-51% over 12 months. This means the "balanced" tag characterization in the knowledge base is misleading for per-period analysis. The period_base_rate_variance entry needs a tag-specific extension that captures within-tag temporal variance — Esports is the most extreme case found so far.

- **`execution/hold_time_capital.md`**: Esports hold = 2h, 1H Crypto hold = 1.67h. Per the throughput table, Esports at 2h hold gives ~12x capital recycling efficiency over politics. With 608 signals/month at 2h hold, each dollar of capital completes ~18 trades/month in Esports. This makes Esports + 1H Crypto the highest-compounding pair in the current portfolio, and they can be run in parallel without significant capital contention.

- **`pitfalls/vectorized_vs_tick.md`**: The 20-40pp degradation estimate applies here. Esports at 77% vectorized HR should validate to 37-57% tick-by-tick. The 34pp excess over base rate means even at 40pp degradation it could retain 34% HR — still above the 45.8% base rate. Tennis at 55.7% vectorized and only 27pp excess is more fragile: 40pp degradation would push it to 15.7%, well below the 28.5% base rate (tennis approximated from sports aggregate). Esports is more defensible; validate Esports first.

## Compounding Improvements

- **Capital rotation between Esports and 1H Crypto.** Esports markets are time-clustered around tournament schedules (weekends, evening slots). 1H Crypto runs uniformly around the clock. These two tags are naturally time-complementary: capital not deployed in Esports off-hours can be recycled into 1H Crypto. A portfolio strategy that allocates to 1H Crypto as a "parking lot" during Esports dead time would increase overall throughput without adding capital.

- **Early exit on market price convergence.** Qualified traders signal BUY at some entry price, say 0.55. If the market price moves to 0.90 within the first 30 minutes (strong consensus that YES wins), exit early to free the capital slot. The 2h average hold in Esports likely includes both rapid-convergence markets (close early) and slow-grind markets (wait until resolution). Splitting these with an early-exit threshold at 0.85-0.90 could reduce effective hold to 1.0-1.2h without meaningful HR impact.

- **Graduated position sizing by excess HR tier.** Signals where the qualified trader pool has median excess HR > 30pp should receive 2x position size vs signals where median excess is 15-20pp. Within Esports, this would concentrate capital on the highest-confidence signals while still capturing the breadth of the pool.

## New Hypothesis Ideas

For `research/ideas.md` backlog:

1. **esports-by-game**: Sub-tag sweep splitting Esports into CS2/Dota2/LoL by question text pattern match. Run independent qualification and parameter sweeps per game title. Expected: one or two game titles dominate the signal, one may be anti-predictive. Priority: **high** — directly derived from the strongest signal found, low implementation cost.

2. **ncaa-anti-signal**: NCAA BUY-only traders (qualified on YES) are -11pp below base rate. Test betting NO when these traders signal YES. Requires identifying the anti-pool (hr_yes < base - threshold), then taking the opposite position. Priority: **medium** — requires short-side mechanism, but the informational signal is genuine.

3. **multi-tag-specialist**: Traders who meet qualification thresholds in two or more of {Esports, Tennis, Basketball} simultaneously. Hypothesis: cross-domain specialists have harder-to-acquire information edges. Sweep the intersection pool size and HR. Priority: **medium** — small pool risk, but high potential for precision signals.

4. **esports-tournament-calendar**: Use question text to identify tournament-phase markets (group stage vs bracket vs grand final). Finals markets may have higher expert density and sharper signals. Priority: **low** — requires NLP-style pattern matching, uncertain payoff.

5. **recency-weighted-qualification**: Replace binary trailing window with exponential decay on trader HR score (half-life 90d). Test across all four GO tags. Priority: **medium** — architectural improvement, not tag-specific, benefits all copy strategies.

## Summary

The most promising next direction is **immediate tick-by-tick validation of Esports BUY-only**, followed by an **Esports per-game sub-tag sweep** while validation is running. Esports has the only signal with a vectorized excess HR large enough (34pp) to survive the expected 20-40pp tick-by-tick degradation with meaningful alpha remaining. The base rate non-stationarity (13-51% YES over 12 months) is the primary risk — the validator must use per-period trailing base rates, not the all-time 45.8%. Concurrently, the 1H Crypto signal is robust and high-frequency (1905 signals/month, 1.67h hold) and should be co-validated as a capital-recycling complement to Esports. Basketball should be deprioritized until the whale-skew issue is resolved with a consensus filter — the median PnL of $0.86 is insufficient after realistic slippage.
