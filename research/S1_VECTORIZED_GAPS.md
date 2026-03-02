# S1 Vectorized vs Tick-by-Tick Gap Analysis

**Date**: 2026-03-02
**Strategy**: S1 Hit-Rate Copy-Trading
**Vectorized source**: `_tmp_s1d_enriched` + `_tmp_s1d_consensus` on CH 192.168.0.148:18123
**Tick-by-tick source**: `ReplayRunner` + `S1HitRateCopyStrategy` + `S1HitRateProvider`

## Executive Summary

The vectorized backtest reports 87.9% HR and +$14,135 over 8 months. The tick-by-tick replay
reports ~77% YES HR, ~49% NO HR, and -$982/month. The gap comes from **9 distinct loopholes**
in the vectorized simulation, ranked by impact below. The single largest cause is the
**consensus definition mismatch** (GAP-9), which alone explains most of the PnL sign flip.

| # | Gap | Impact | Vectorized Assumes | Reality |
|---|-----|--------|-------------------|---------|
| 9 | Consensus definition | **CRITICAL** | Unique traders per (market, side, month) | Provider counts every trade (same trader counts N times) |
| 4 | NO direction ambiguity | **HIGH** | net_no > 0 = "correct NO bet" | SELL YES = exit signal copied as "go NO" |
| 7 | Capital constraint | **HIGH** | Unlimited concurrent positions | 50 max open, capital-constrained |
| 1 | Look-through bias | **MEDIUM** | NET position after all trades in month | Individual trade price at copy moment |
| 5 | Entry price divergence | **MEDIUM** | Volume-weighted average across all fills | Specific price of triggering trade |
| 10 | Survivorship bias | **MEDIUM** | Only resolved positions counted | 50.3% of positions unresolved (capital locked) |
| 6 | Consensus timing | **LOW** | Pre-computed for whole month | First 3 entries missed; 4th triggers |
| 8 | "Correct" definition | **NEGLIGIBLE** | PnL > 0 (includes trading profit) | Direction matches winner |
| 3 | SELL trade fraction | Subsumed by #4 | Not modeled (only net position) | 22.8% of qualified trades are SELLs |

---

## Detailed Gap Analysis

### GAP-1: Look-Through Bias (MEDIUM)

**What vectorized assumes**: Each row in `_tmp_s1d_enriched` represents a single (trader, market)
pair with a NET position computed from `trader_market_positions FINAL`. The `dir_entry_price` is
derived from the volume-weighted average YES price across ALL trades in that position. A trader
who bought YES at 0.60, sold at 0.80, bought again at 0.70 shows as one "YES position" with
a blended entry price.

**What actually happens**: Tick-by-tick sees each trade individually. The first BUY at 0.60
triggers a copy. The SELL at 0.80 may trigger a "go NO" copy (GAP-4). The re-entry at 0.70
is ignored because we already have a position in that market.

**Quantified impact**:
- 70.9% of S1 universe positions involve 2+ trades (avg 12.9 trades per position)
- 34.2% have 5+ trades
- Single-trade positions: 80.7% HR. Multi-trade (11+): 78.6% HR. Delta: -2.1pp
- Price dispersion within multi-trade positions means the tick-by-tick entry price can differ
  significantly from the vectorized's weighted average

**Proposed fix**: Instead of using the volume-weighted average entry price, use the FIRST
trade's price as `dir_entry_price`. This better approximates what a copier would pay.

```sql
-- Replace wavg_yes_price with first-trade price
argMin(price, timestamp) AS first_trade_price
```

**Estimated HR impact**: -2 to -3pp (reduces vectorized HR from 87.9% toward 85-86%)

---

### GAP-4: NO Direction Ambiguity (HIGH)

**What vectorized assumes**: A "NO position" (`net_no > 0, net_yes <= 0`) means the trader
intentionally bet on NO. The vectorized doesn't care HOW they got there -- whether by buying
NO tokens or selling YES tokens.

**What actually happens**: In tick-by-tick, the provider's `on_trade()` interprets each trade
independently:
- `BUY NO` -> copy direction = NO (correct interpretation)
- `SELL YES` -> copy direction = NO (WRONG: this is often an exit, not a new directional bet)

**Quantified impact**:
- 22.8% of all qualified trader trades are SELLs
- SELL YES = 12.3% of all trades -> interpreted as "go NO" by tick-by-tick
- SELL NO = 10.5% of all trades -> interpreted as "go YES" by tick-by-tick
- These exit signals are the primary driver of the -33pp NO HR gap (82% vectorized -> 49% tick)
- 99% of vectorized NO positions are "pure NO" (net_no > 0, net_yes = 0), so the issue is
  entirely about how the net_no was BUILT (BUY NO vs SELL YES accumulation)

**Proposed vectorized fix**: Cannot fix in vectorized (it's already correct). Must fix in
tick-by-tick:

```python
# In provider.on_trade(): ONLY count BUY trades for consensus
# SELL trades are ambiguous (exit vs new direction)
if side_str != "BUY":
    return  # Skip SELL trades entirely
```

**Proposed tick-by-tick fix**:
1. **Filter SELLs from copy signals**: Only copy BUY trades from qualified traders.
   A BUY is always a new entry or position increase. A SELL is always an exit.
2. **Track trader positions**: Before copying a SELL as "go opposite", check if the
   trader has an existing position in that direction (if so, it's an exit, skip).

**Estimated impact**: Eliminating SELL-as-signal would remove ~22% of entries but dramatically
improve NO HR from ~49% toward the vectorized's 85%.

---

### GAP-7: Capital Constraint (HIGH)

**What vectorized assumes**: Unlimited concurrent positions. In peak months (Jan 2026), the
vectorized counts ~2,816 estimated concurrent positions. There is no capital or slot limit.

**What actually happens**: Tick-by-tick is constrained by `StrategyConfig`:
- `max_open_positions = 50` (the replay uses 50)
- `capital_usd = 10,000` / `max_position_usd = 500`
- Positions that haven't resolved block slots. Politics averages 22.4d hold time.

**Quantified impact**:
- Peak concurrent estimate: ~2,816 positions (Jan 2026) vs 50 max = 56x capacity gap
- Monthly entries: 200.5/day (Jan 2026) vs throughput of ~50/6.4d = 7.8 entries/day
- Fill coverage: tick-by-tick captures ~617 fills/month vs vectorized's ~1,377 = 45% coverage
- Capital is consumed by long-dated markets (politics 22.4d, crypto 11.0d) while
  high-turnover markets (esports 0.3d, sports 1.4d) have positive EV

**Proposed vectorized fix**: Simulate capital constraints in the vectorized. Add a "slot
utilization" model that prioritizes positions by expected capital efficiency.

```sql
-- For each month, rank positions by expected PnL/dollar-day
-- Only include positions until sum(entry_price * avg_hold) <= capital_limit
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY res_month
        ORDER BY hold_days ASC, dir_entry_price ASC  -- prioritize short-hold, cheap entries
    ) AS slot_rank,
    -- Running sum of slot-days consumed
    SUM(greatest(hold_days, 1)) OVER (
        PARTITION BY res_month
        ORDER BY hold_days ASC, dir_entry_price ASC
    ) AS cum_slot_days
    FROM backtest_universe
)
SELECT * FROM ranked WHERE cum_slot_days <= 50 * 30  -- 50 slots * 30 days per month
```

**Estimated impact**: Reduces vectorized position count from ~15K to ~3-5K, better matching
tick-by-tick throughput. HR may increase slightly (short-hold positions tend to have higher HR).

---

### GAP-9: Consensus Definition Mismatch (CRITICAL)

**What vectorized assumes**: `_tmp_s1d_consensus.n_traders_same_side` counts **unique qualified
traders** who took a position on the same side of a market in a given month. Consensus >= 4
means at least 4 different skilled traders independently agree.

**What actually happens**: `S1HitRateProvider.on_trade()` increments the consensus counter
on **every trade** from any qualified trader -- regardless of whether the same trader already
incremented. A single trader making 4 trades in a market reaches "consensus 4" despite being
only 1 unique trader.

**Quantified impact** (THIS IS THE SMOKING GUN):

| Scenario | Market-sides | Avg trades | Avg unique traders | HR |
|----------|-------------|------------|-------------------|-----|
| tick_only (trades>=4, unique<4) | 11,492 | 25.8 | 1.5 | 79.7% |
| both_enter (trades>=4, unique>=4) | 1,238 | 110.3 | 5.9 | 80.0% |
| neither (trades<4, unique<4) | 9,618 | 1.7 | 1.1 | 79.0% |

- **11,492 market-sides** that tick-by-tick enters but vectorized wisely avoids
  (fake consensus from 1-2 active traders)
- tick_only market-sides have average 1.5 unique traders -- this is NOT consensus,
  it's one prolific trader's activity being misinterpreted
- The tick-by-tick universe is **10.3x larger** than the overlap -- it's entering
  massive numbers of low-quality "consensus" markets
- 72.6% of ALL market-sides have only 1 unique qualified trader (avg 9.7 trades)

**Proposed tick-by-tick fix** (MUST DO):

```python
# In S1HitRateProvider.on_trade():
# Track UNIQUE traders, not total trades
class ConsensusState:
    traders_yes: set[str]  # unique trader addresses on YES side
    traders_no: set[str]   # unique trader addresses on NO side

async def on_trade(self, trade):
    ...
    cid = trade.condition_id
    if cid not in self._consensus:
        self._consensus[cid] = {"yes_traders": set(), "no_traders": set()}
    key = "yes_traders" if dir_outcome == "YES" else "no_traders"
    self._consensus[cid][key].add(maker)  # set.add is idempotent

# In strategy: count = len(consensus[cid]["yes_traders"])
```

**Proposed vectorized fix**: None needed -- vectorized is already correct (unique traders).

**Estimated impact**: Fixing this single bug would eliminate ~90% of the false positive entries
and bring tick-by-tick results dramatically closer to vectorized.

---

### GAP-5: Entry Price Divergence (MEDIUM)

**What vectorized assumes**: `dir_entry_price` is computed from `wavg_yes_price`, which is
`yes_px_vol / volume` from `trader_market_positions` -- a volume-weighted average across all
YES-side trades. For NO positions, `dir_entry_price = 1 - wavg_yes_price`.

**What actually happens**: Tick-by-tick enters at the specific price of the trade that triggers
the copy. This could be any of the 12.9 average trades in a position. The triggering trade is
typically the 4th+ qualified trade on that market-side (after consensus builds), which may be
far from the volume-weighted average.

**Quantified impact**:
- Average vectorized entry price: 0.786
- Entry price standard deviation: 0.085
- Multi-trade positions (11+ trades) average 8.8d hold vs single-trade 5.6d hold
- The first trade's price can differ from the wavg by 5-15 cents in typical positions

**Proposed vectorized fix**: Use the first qualifying trade's price instead of the wavg.
Better yet, use the Nth trade's price where N = min_consensus:

```sql
-- In enriched table construction, instead of wavg_yes_price:
-- Use the price of the Nth qualified trader's first trade
-- This requires access to the raw trade stream (expensive)
-- Approximation: use 95th percentile of entry prices (conservative)
quantile(0.95)(avg_yes_price) OVER (PARTITION BY cid, position, res_month)
```

**Estimated impact**: -1 to -3pp on HR. The actual tick-by-tick entry is typically WORSE
than the wavg because the copier enters AFTER the information is partially priced in.

---

### GAP-10: Survivorship / Resolution Bias (MEDIUM)

**What vectorized assumes**: Only resolved positions are counted. The vectorized backtest
filters to `resolved_at IS NOT NULL`. Positions that never resolve during the evaluation
window are invisible.

**What actually happens**: 50.3% of positions from Jul 2025+ are still unresolved as of
March 2026. These are typically long-dated markets (politics, future events) that:
1. Tie up capital in tick-by-tick (blocking slots)
2. May ultimately be wrong (we don't know yet)
3. Have negative carry due to time value of money

**Quantified impact**:
- 50.3% of all positions unresolved
- This disproportionately affects politics (22.4d avg hold) and crypto (11.0d)
- The vectorized only shows the "winners" (positions that resolved favorably during the window)
- Unresolved positions are excluded from HR calculation, inflating it

**Proposed vectorized fix**: Add a "still-open penalty" to account for unresolved positions:

```sql
-- Estimate: assume unresolved positions have base-rate HR (40% YES won)
-- Weight vectorized HR by the fraction that actually resolved
adjusted_hr = vectorized_hr * resolved_fraction + base_rate * (1 - resolved_fraction)
-- Or more conservatively: count unresolved positions as losses
conservative_hr = wins / (resolved + unresolved_in_window)
```

**Estimated impact**: Reduces effective HR by 2-5pp depending on the resolution rate of
the specific markets entered.

---

### GAP-6: Consensus Timing (LOW)

**What vectorized assumes**: Consensus is pre-computed for the entire month. If 6 traders
eventually agree on YES in a market, ALL 6 of their positions count as "with consensus >= 4."

**What actually happens**: Tick-by-tick builds consensus incrementally. The first 3 traders
don't trigger entry (consensus < 4). Only the 4th triggers the entry signal. The first 3
traders' entries are missed.

**Quantified impact**:
- Among qualifying market-sides (consensus >= 4): average 10.1 unique entries
- Average capturable entries (after 4th): 6.1 = 60.5% capture rate
- 21.8% of qualifying market-sides have EXACTLY 4 entries (0 capturable after trigger)
- However, since the strategy deduplicates to 1 entry per market, the timing gap only affects
  WHICH price we enter at, not WHETHER we enter

**Proposed vectorized fix**: For each market-side, use only the entries from traders 4-N
(excluding the first 3). This is complex but more realistic.

**Estimated impact**: Low -- the main effect is on entry price, not on market selection or HR.
The 4th trade's price is typically within 2-3 cents of the average.

---

### GAP-8: "Correct" Definition (NEGLIGIBLE)

**What vectorized assumes**: `correct = (payout + net_usd) > 0` -- a PnL-based definition
that includes intermediate trading profits. A position that lost directionally but profited
from scalping is counted as "correct."

**What actually happens**: Tick-by-tick uses asset_id-based resolution: `won = asset_id in
winning_asset_ids`. This is a pure directional check.

**Quantified impact**:
- Directional HR: 87.6%
- PnL HR: 85.7%
- Delta: -1.9pp (directional HR is actually HIGHER)
- Only 2.5% of positions have PnL-correct != direction-correct
- The gap goes in the vectorized's DISFAVOR (PnL HR < directional HR)

**Proposed fix**: None needed. The definitions are 97.5% aligned and the difference
favors the tick-by-tick.

---

### GAP-3: SELL Trade Fraction (Subsumed by GAP-4)

This gap is a subset of GAP-4 (NO direction ambiguity). 22.8% of qualified trader trades are
SELLs, which tick-by-tick interprets as directional signals but are actually exits. The fix
is the same as GAP-4: only copy BUY trades.

---

## Proposed Fixes (Priority Order)

### Fix 1: Unique-Trader Consensus (Fixes GAP-9) -- CRITICAL

The single most important fix. Change `S1HitRateProvider.on_trade()` to track unique trader
addresses per (market, side) instead of incrementing a counter per trade.

**Before** (current provider):
```python
self._consensus[cid][key] += 1  # Counts EVERY trade
```

**After** (fixed):
```python
# Store set of unique traders per side
self._consensus[cid] = {"yes": set(), "no": set()}
self._consensus[cid][key].add(maker)  # Idempotent -- same trader counted once

# In get_features(), expose counts:
{cid: {"yes": len(s["yes"]), "no": len(s["no"])} for cid, s in self._consensus.items()}
```

**Expected impact**: Eliminates 90%+ of false positive entries. Tick-by-tick enters ~1,200
market-sides (matching vectorized overlap) instead of ~12,700.

### Fix 2: BUY-Only Copy Signal (Fixes GAP-4)

Only copy BUY trades from qualified traders. SELL trades are ambiguous (exit vs new direction)
and are the primary driver of the NO HR collapse.

**Change in strategy**:
```python
# In on_trade():
side_str = str(trade.side)
if side_str != "BUY":
    return None  # SELL trades are exits, not directional signals
# copy_outcome = outcome (since we only copy BUYs, outcome IS the direction)
```

**Change in provider**:
```python
# In on_trade(): only count BUY trades for consensus
if side_str != "BUY":
    return
```

**Expected impact**: Eliminates ~22% of entries (all SELL-triggered), dramatically improves
NO-side HR from ~49% toward vectorized's 85%.

### Fix 3: Category-Based Position Sizing (Fixes GAP-7 partially)

Prioritize short-hold, high-turnover categories to maximize capital efficiency within the
50-slot constraint.

**Priority queue** (by capital efficiency = PnL per dollar-day):
1. Esports: 0.3d avg hold, $1.20/trade (best)
2. Sports: 1.4d avg hold, $1.24/trade
3. Other: 9.3d avg hold, $0.57/trade
4. Crypto: 11.0d avg hold, $0.39/trade
5. Politics: 22.4d avg hold, $0.59/trade (worst capital efficiency)

**Implementation**: Add a hold-time-based priority to the strategy. When max_open_positions
is reached, only enter sports/esports markets. Hard-skip markets with close_epoch > 7 days
from entry (already implemented as `max_hold_hours` in production strategy).

### Fix 4: Vectorized Capital Simulation (Fixes GAP-7)

Add a slot utilization model to the vectorized backtest SQL to cap concurrent positions:

```sql
WITH daily_entries AS (
    SELECT *, toDate(first_trade) AS entry_day,
           entry_day + hold_days AS est_exit_day
    FROM backtest_positions
),
daily_slots AS (
    SELECT entry_day, cid, position,
           SUM(1) OVER (
               ORDER BY entry_day
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) - SUM(1) OVER (
               ORDER BY est_exit_day
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS open_slots
    FROM daily_entries
)
SELECT * FROM daily_slots WHERE open_slots <= 50
```

### Fix 5: First-Trade Entry Price (Fixes GAP-1, GAP-5)

In the enriched table, replace `wavg_yes_price` with `argMin(price, timestamp)` (first
trade's price). This better approximates the copier's entry price.

### Fix 6: Conservative Vectorized with Penalty Factors

Apply multiplicative penalties to the vectorized HR to account for unquantifiable gaps:

```
adjusted_pnl = vectorized_pnl
    * consensus_discount     (0.90 -- some fake consensus leaks through)
    * entry_price_penalty    (0.95 -- copier enters at worse price)
    * resolution_discount    (0.80 -- unresolved positions as losses)
    * capital_utilization     (varies by month)
```

---

## What the Vectorized CAN'T Capture (Inherent Limitations)

1. **Orderbook state**: Vectorized has no access to bid/ask at entry time. The actual fill
   price depends on the orderbook at the moment of copy, which can differ from the trader's
   fill price by 1-5 cents.

2. **Trade-level timing**: Vectorized sees positions aggregated over a month. It cannot
   model the exact sequence of events (trader A enters, then B, then C, then consensus
   triggers our entry, but by then the price has moved).

3. **Execution risk**: Vectorized assumes instant fills at the entry price. Real execution
   involves CLOB REST API latency (1-5s), potential price slippage, and partial fills.

4. **Market state changes**: A market can become illiquid, get delisted, or change conditions
   between the trader's entry and our copy attempt.

5. **Correlated positions**: Vectorized treats each position independently. In reality,
   multiple positions may be on the same event (multi-outcome markets), creating concentration
   risk that the 50-slot constraint amplifies.

6. **Feedback effects**: Our own trading changes the orderbook. At scale, entering $10 on
   the same side as 4+ other traders in a market with $1K-100K volume may move the price.

---

## Generalist Tick-by-Tick Replay Framework Design

### Current State

The codebase has three runners:
- **`BacktestRunner`** (`strategies/runners/backtest.py`): Basic event-driven replay with
  risk gate, no resolution. ~186 LOC.
- **`ReplayRunner`** (`strategies/runners/replay.py`): Adds asset_id resolution, provider
  hot path, inline ledger enrichment. ~336 LOC.
- **`research/harness.py`**: Convenience wrapper around BacktestRunner for compact parquet data.

The `s1_replay.py` script (~440 LOC) handles S1-specific setup: CH data loading, provider
construction, spread calibration, monthly walk-forward. Much of this is boilerplate that
would be identical for any strategy.

### Proposed Architecture

```
ReplayEngine (new)
  |
  +-- DataLoader (pluggable)
  |     +-- ClickHouseLoader (query CH, materialize to NormalizedTrade)
  |     +-- ParquetLoader (read compact parquet files)
  |     +-- CachedLoader (pre-loaded in-memory, for parameter sweeps)
  |
  +-- FeatureScheduler (new)
  |     +-- Monthly walk-forward: rotate training window, re-compute features
  |     +-- Incremental: provider.on_trade() per tick
  |     +-- Caching: snapshot feature state for reuse across parameter sweeps
  |
  +-- ReplayRunner (existing, enhanced)
  |     +-- Asset_id resolution (already done)
  |     +-- Provider hot path (already done)
  |     +-- Inline ledger enrichment (already done)
  |
  +-- AnalyticsCollector (new)
        +-- Per-month summary
        +-- Gap analysis metrics (consensus quality, direction breakdown, etc.)
        +-- Equity curve + drawdown
        +-- Export to parquet + human-readable summary
```

### API Design

```python
from research.engine import ReplayEngine, ReplayConfig, WalkForwardSchedule

engine = ReplayEngine(
    # Strategy + providers (same as live)
    strategy=S1HitRateCopyStrategy(**params),
    providers=[S1HitRateProvider(**provider_params)],

    # Config (same TOML as live)
    config=StrategyConfig(
        capital_usd=10_000,
        max_position_usd=500,
        max_open_positions=50,
    ),

    # Data source
    data=ClickHouseLoader(host="192.168.0.148", port=18123, database="polymarket"),
    # OR: data=CachedLoader(trades=preloaded_trades, resolutions=preloaded_res),

    # Walk-forward schedule
    schedule=WalkForwardSchedule(
        test_months=["2025-07-01", "2025-08-01", ..., "2026-02-01"],
        train_lookback_months=9,
    ),

    # Fill model
    fill_config=FillModelConfig(fallback_half_spread=0.01),

    # Output
    output_dir=Path("research/output/replay"),
)

# Run full walk-forward
results = await engine.run()

# Parameter sweep (re-uses cached data)
for consensus in [2, 3, 4, 5, 6]:
    engine.strategy = S1HitRateCopyStrategy(min_consensus=consensus)
    results = await engine.run(cached=True)  # re-uses loaded trades + resolutions
```

### Key Design Decisions

#### 1. Data Loading is Separate from Replay

```python
class DataLoader(Protocol):
    async def load_trades(self, month: str, filter: TradeFilter | None = None) -> list[NormalizedTrade]:
        """Load trades for a single month."""
        ...

    async def load_resolutions(self) -> tuple[dict[str, MarketResolution], dict[str, dict[str, str]]]:
        """Load resolution data + token map."""
        ...

class CachedLoader:
    """Wraps any DataLoader with in-memory caching for parameter sweeps."""

    def __init__(self, inner: DataLoader):
        self._inner = inner
        self._cache: dict[str, list[NormalizedTrade]] = {}

    async def load_trades(self, month, filter=None):
        if month not in self._cache:
            self._cache[month] = await self._inner.load_trades(month, filter)
        return self._cache[month]
```

#### 2. Feature Scheduler Handles Walk-Forward

Instead of each script implementing its own monthly loop, the engine handles the walk-forward
schedule:

```python
class WalkForwardSchedule:
    test_months: list[str]
    train_lookback_months: int = 9

    def train_window(self, test_month: str) -> tuple[str, str]:
        """Return (train_start, train_end) for a given test month."""
        end = datetime.strptime(test_month, "%Y-%m-%d")
        start = end - timedelta(days=self.train_lookback_months * 30)
        return start.strftime("%Y-%m-%d"), test_month

class FeatureScheduler:
    async def setup_month(self, month: str, backend: FeatureBackend):
        """Re-compute provider features for a new test month."""
        for provider in self.providers:
            # Set the provider's training window
            provider.set_as_of_date(month)
            await provider.compute(backend)
```

#### 3. Trade Filtering at Load Time

The biggest performance win in the current replay is filtering trades to qualified makers only
(6M -> 500K per month). This should be a first-class concept:

```python
@dataclass
class TradeFilter:
    maker_whitelist: set[str] | None = None   # Only these makers
    condition_ids: set[str] | None = None      # Only these markets
    min_timestamp: float | None = None
    max_timestamp: float | None = None
    sides: set[str] | None = None              # {"BUY"} to skip SELLs (Fix 2)
```

The `ClickHouseLoader` converts this to a CH `WHERE` clause. The `CachedLoader` applies it
as a Python filter on cached data.

#### 4. Analytics are Built-In

Every replay automatically collects gap-analysis metrics:

```python
@dataclass
class ReplayAnalytics:
    # Standard metrics
    total_fills: int
    hr_yes: float
    hr_no: float
    pnl_total: float
    sharpe: float
    max_drawdown: float

    # Gap metrics (for vectorized comparison)
    consensus_quality: dict[str, float]  # market -> unique_traders / total_trades
    sell_signal_fraction: float          # fraction of entries from SELL-as-signal
    avg_concurrent_positions: float      # actual slot utilization
    capital_utilization: float           # deployed / available
    unresolved_count: int                # positions still open at end
    rejection_reasons: dict[str, int]    # from risk gate
```

#### 5. Resolution is Always Asset-ID Based

The `MarketResolution` type from `replay.py` (with `winning_asset_ids: frozenset[str]`)
is the ONLY resolution mechanism. No string matching, no outcome comparison. This is
already correct in the codebase and should remain the standard.

### File Structure

```
research/
  engine/
    __init__.py          # ReplayEngine public API
    loader.py            # DataLoader protocol + ClickHouseLoader + CachedLoader
    scheduler.py         # WalkForwardSchedule + FeatureScheduler
    analytics.py         # ReplayAnalytics + gap metrics
    config.py            # ReplayConfig (merges StrategyConfig + engine-specific)
  scripts/
    s1_replay.py         # Simplified to ~50 lines using engine
    s1_backtest.py       # Existing vectorized (kept for comparison)
```

### Migration Path

1. **Phase 1**: Fix GAP-9 (unique-trader consensus) and GAP-4 (BUY-only) in the existing
   `S1HitRateProvider` and `S1HitRateCopyStrategy`. Re-run `s1_replay.py` to validate.

2. **Phase 2**: Extract the generic parts of `s1_replay.py` into `research/engine/`. The
   S1-specific parts become a thin wrapper.

3. **Phase 3**: Add `CachedLoader` for parameter sweeps. Run vectorized vs tick-by-tick
   comparison on the same universe to validate convergence.

4. **Phase 4**: Add gap analytics to every replay run. Monitor consensus quality, sell-signal
   fraction, and capital utilization as first-class metrics.

---

## Appendix: CH Temp Tables Used

| Table | Rows (OOS) | Key Columns | Purpose |
|-------|-----------|-------------|---------|
| `_tmp_s1d_enriched` | 16.2M | trader, cid, position, dir_entry_price, correct, hold_days, category | Enriched positions with all derived fields |
| `_tmp_s1d_consensus` | 191K | tmonth, cid, pos, n_traders_same_side | Unique-trader consensus per (market, side, month) |
| `_tmp_s1d_train_stats` | ~50K | test_month, trader, train_hr, train_positions | Walk-forward training stats |
| `_tmp_s1d_specialization` | ~50K | test_month, trader, specialization | Category specialization fraction |
| `_tmp_s1d_streaks` | ~50K | test_month, trader, last5_correct | Recent streak |
| `_tmp_gap_qual` | 6,547 | trader | Qualified traders for gap analysis |

## Appendix: Key Queries for Validation

To verify that fixes close the gap, run these queries after applying Fix 1 and Fix 2:

```sql
-- After fixing consensus to unique-trader in tick-by-tick:
-- Expected: fills/month should drop from ~617 to ~200-300
-- Expected: NO HR should rise from ~49% to ~70-80%

-- After fixing BUY-only signal:
-- Expected: NO HR should match YES HR (both ~80%+)
-- Expected: Total entries drop by ~22% (SELL fraction removed)
```
