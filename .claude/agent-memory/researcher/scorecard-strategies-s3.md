# Strategy 3: Elite Copy Key Findings (2026-03-07)

## Elite Pool Construction (DuckDB, maker_positions table)

- Table in DuckDB is `maker_positions` NOT `maker_positions_resolved_corrected`
- 517 elite traders qualify (5-gate funnel from 458K total):
  - n_markets >= 20, avg_conviction >= 0.90 (non-MM)
  - excess_hr >= 0.15 (15pp above tag base rate)
  - n_months_active >= 6, n_windows >= 3
  - stability_score >= 4.0
- Sports: 312 traders, Politics: 207 traders, Crypto: 28 traders

## CRITICAL: Hold=0 Sports Bias

**63% of elite signals resolve same-day** (hold_days=0). These are sports event markets where elite traders enter AFTER the outcome is clear but before official settlement.

- hold=0d: HR=68.2% (FAKE — not copyable in live trading)
- hold=1d: HR=47.2% (real)
- hold=2d: HR=32.4% (real, below base rate?)
- hold>=1d filter is MANDATORY before reporting any sports copy signal

Always filter: `AND hold_days >= 1` for Sports; or equivalently check market close time within 24h.

## Elite N>=2, hold>=1d (Valid Signal)

- 1,819 signals in test period
- HR=51.4% overall; Sports=52.8% (+20pp excess), Politics=45.3% (+28pp excess)
- Crypto: 6 signals only — too thin
- Compounding score: Sports CS~1.6, Politics CS~7.8

## Elite Market Selector (Strongest Finding)

- Elite participation → 47.7% YES win rate vs 25.0% baseline (+22.7pp)
- 17,832 markets selected out of 151,217 total test markets
- This works even for N=1 entry by any elite trader
- Pivot C (elite as market selector + secondary entry rule) may be more robust

## Entry Price Filter Pitfall

**NEVER apply price ceiling filter to elite copy signals.**
- HR by price: <10%=0.3%, 40-50%=25.7%, 60-70%=78.4%, 80%+=96.6%
- Monotonically increasing — this is just resolution anchor bias (markets near certainty)
- Ceiling filter preferentially keeps low-price = low-HR markets = inverted signal
- Price filter to use if anything: FLOOR (e.g., entry_price > 0.10 to exclude deep underdogs)

## DuckDB INT32 Overflow in epoch_ms

`epoch_ms(...) / (86400000 * 60)` overflows INT32.
Fix: use literal BIGINT constant `/ 5184000000` (= 86400000 * 60 as BIGINT).
Do NOT use `CAST(... * ... AS BIGINT)` — DuckDB evaluates multiplication before casting.

## Top-Decile HR Inflation

top-decile HR=79.8% in test is dominated by same hold=0 sports bias.
Market-level aggregation does NOT fix this — the bias is in entry timing, not counting unit.
Always filter hold>=1d before comparing pools.
