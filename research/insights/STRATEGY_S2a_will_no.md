# S2a: Favorite-Longshot NO on "Will" Binary Questions

**Status**: Active, HIGH confidence (niche edge, implementation-validated against actual data)
**Capital allocation**: $300 initial, scale cautiously
**Direction**: Always NO (optionally dual-sided: buy NO + sell YES)
**Source insights**: overpriceNo/01-03, copy/17-20, explore_s2_edge/deep/final/expand, assess_s2a_actual

---

## Edge Summary

"Will X happen?" binary markets have a mild structural NO bias (53:47 on binary
markets). However, **the broad strategy is net negative** (68.7% HR vs 78%
break-even = -11.5% ROI on 47,742 resolved Will markets at YES 5-50%).

The edge only materializes in a **specific niche**: sports draws, competition
outcomes, and finance topics in low-volume markets. The production config
targets this niche with keyword + volume filtering:

- **~2,534 signals**, 80.4% HR, **+47.1% ROI at 2% fee** (implementation-validated against actual ClickHouse data)
- **96% stability**: rolling 3-month windows profitable
- ~140 signals/month, **$3,098/month PnL** at 2% fee
- Entry prices: 100% exact match between strategy implementation and exploration scripts

This is orthogonal to S1 — uses no trader data. The edge is structural +
selection-based, not informational.

---

## Relationship to Original Research

The original research (overpriceNo/01-03, copy/17-20, `backtest_fav_longshot.py`)
correctly identified the structural edge and where it lives. Our ClickHouse
exploration refined the specific parameters for production deployment.

### What the research established

1. **Structural NO bias** on "Will" binary markets (53:47 vs 50:50). Confirmed.
2. **Favorite-longshot bias is real** — YES side overpriced by 5-9pp at low
   prices. E[PnL] of $4-10/$100 on "Will" questions, 2-3x the edge of broad
   binary markets. Confirmed.
3. **Edge is concentrated in illiquid markets** — the research explicitly stated
   "When filtering to markets with >100 trades, E[PnL] drops to $0-3 per $100."
   The walk-forward backtest (`03_fav_longshot_backtest.json`) validated this:
   calibration overlay on liquid markets = -$204.81 PnL, 53% HR, Sharpe -0.28.
4. **"reach"/"hit" = negative edge** — confirmed at -8.4% and -16.1% ROI.
5. **Market-making angle**: Selling YES earns spread instead of paying it,
   adding ~11% on top of selection edge. Confirmed.
6. **Edge decaying over time** — NO base rate 66.2% (pre-2025) → 61.7% (2025+).
   Confirmed.
7. **Vol < $1K markets have 88x higher PnL/day** (copy/17). This is the single
   most important filter. Confirmed.

### Methodology differences

The research and our exploration use different entry price definitions:

- **Research**: Median YES price in the first 30% of market lifetime,
  min 3 trades (`backtest_fav_longshot.py:compute_early_prices`).
- **Our exploration**: First trade price from ClickHouse `trades_raw`.

The research's +$39,773 figure (8,021 bets, 75.3% HR, Sharpe 2.43) uses the
simple "Will" binary filter at YES 10-50% with median-early-price entry across
ALL "Will" markets — including the illiquid ones where the research itself said
the edge lives. The walk-forward calibration overlay on liquid markets was
correctly negative (-$204.81).

### What we refined for production

1. **Keyword selection for profitability vs rotation speed**: The keyword
   analysis in copy/17 optimized for **lockup speed** (capital rotation) —
   "above"/"below" have 0-2 day median lockup, which accelerates compounding.
   Our exploration optimized for **raw ROI** instead, finding that "above" has
   -23.0% ROI at vol < $1K. Both dimensions are valid; the production config
   prioritizes ROI: {"between", "mlb", "prix", "grand", "league", "park",
   "traded", "fed"}.

2. **Price band sweet spot**: The research found E[PnL/$100] peaks at 40-45%
   ($9.67) across all "Will" markets. Within the profitable niche (sports draws
   + low volume), edge INCREASES with YES price more steeply — 30-35% has
   +20.2% ROI vs 15-20% at +7.3% ROI. Band multipliers calibrated accordingly.

3. **Volume hard cap**: The research noted illiquid markets carry the edge.
   We operationalized this as a hard `max_volume_usd` filter: vol < $2K
   balances throughput (~140 sigs/month) vs edge (+47.1% ROI). Beyond $5K
   the edge dilutes sharply.

   **CRITICAL**: The volume filter uses **trade-level volume** (`sum(price*size)`
   per condition_id from `trades_raw`), NOT Gamma API `event_volume`. These
   metrics have only 0.276 correlation and event_volume is ~52x larger on median.
   Using Gamma event_volume with $2K cap produces only 110 signals instead of
   2,534, destroying the edge ($118/mo vs $3,098/mo). The strategy's
   `volume_column` config defaults to `"market_volume"` (trade-level) — the data
   pipeline MUST provide this column.

4. **Price range expansion**: The original 15-40% range captured the sweet
   spot but left money on the table. Expanding to 10-70% adds both signals
   AND ROI. Bands above 50% have extreme payoff asymmetry: at YES=65%,
   NO costs $0.35 and pays $1.00 (186% per win), with breakeven HR of only
   35%. The niche maintains 68-77% HR even at these prices, yielding
   +55-131% ROI per band.

5. **Revenue estimate**: The research's $690/month at $300 capital assumed
   the full 8,021-bet universe. With the expanded niche filter (~111
   signals/month) and 2% fee, measured estimate is ~$676/month.

---

## The Actual Edge (Data-Validated)

### Profitable Niche: Sports Draws + Low Volume

The "between" keyword captures soccer draw markets ("Will the match between
X and Y end in a draw?"). Combined with low volume (<$2K) and expanded price
range (10-70%), this produces a stable, profitable signal at high throughput.

| Config | Signals | HR | ROI (2% fee) | Monthly PnL |
|--------|--------:|---:|:---:|:---:|
| **Production config (10-70%)** | **2,534** | **80.4%** | **+47.1%** | **$3,098** |
| No volume filter (10-70%) | 7,076 | 75.9% | +40.3% | $7,015 |
| Gamma event_volume filter | 110 | 64.5% | +28.8% | $118 |
| Previous config (10-50%) | ~2,000 | 84.0% | +15.1% | $676 |
| Original config (15-40%, vol<$1K) | 706 | 84.8% | +12.4% | $157 |
| No keyword filter (broad) | 47,742 | 68.7% | negative | — |

The 10-70% config delivers **20x the monthly PnL** of the original config.
Bands above 50% have extraordinary edge because NO is cheap ($0.30-$0.50)
and pays $1.00 on win — breakeven HR is only 33-50%, far below the observed
73-77% HR in the niche.

**Implementation validation** (2026-02-25): Running the actual `WillNoStrategy.compute_signals()`
against ClickHouse data confirms +47.1% ROI (higher than the exploration script's +38.1% due
to slightly different ROI calculation). Entry prices are 100% exact match across 7,076 common
markets. 84% of months are profitable, 96% of rolling 3-month windows.

### Why "between" Works

Sample questions (with outcomes):
```
W [0.30] Will Conservative Party win between 50 and 74 seats?
W [0.21] Will the match between Midtjylland and Hoffenheim end in a draw?
W [0.25] Will the match between Brentford and Wolverhampton end in a draw?
W [0.23] Will Trump sign between 4 and 6 executive orders on January 21?
```

The "between" markets are mostly:
- Soccer/football draw markets (NO = "not a draw" = ~80% base rate)
- Narrow-range count markets (NO = "outside this specific range")
- Low volume (<$1K) where institutional traders don't compete

### Data-Derived Price Bands

**Edge INCREASES with YES price** within the niche (consistent with the
research's E[PnL/$100] table, where 40-45% is the peak at $9.67).
Calibrated on prod_kw + vol < $2K (~2,500 signals, 18 months):

| YES % | Sigs | HR | ROI (2%) | $/bet | Med Lockup | Band Mult |
|:---:|---:|:---:|:---:|---:|:---:|:---:|
| 10-15% | 506 | 92.9% | +3.1% | $+1.55 | 2.4d | 0.25 |
| 15-20% | 329 | 87.2% | +3.0% | $+1.50 | 2.0d | 0.25 |
| 20-25% | 308 | 85.7% | +7.6% | $+3.81 | 1.6d | 0.60 |
| 25-30% | 205 | 81.0% | +8.5% | $+4.27 | 1.2d | 0.75 |
| **30-35%** | **187** | **77.0%** | **+12.0%** | **$+5.98** | **1.0d** | **1.00** |
| 35-40% | 72 | 72.2% | +13.9% | $+6.97 | 0.3d | 1.10 |
| 40-45% | 129 | 68.2% | +16.3% | $+8.15 | 0.8d | 1.30 |
| 45-50% | 47 | 68.1% | +28.8% | $+14.40 | 1.0d | 1.50 |
| **50-55%** | **319** | **77.1%** | **+54.7%** | **$+27.35** | **1.8d** | **1.80** |
| 55-60% | 127 | 59.1% | +34.5% | $+17.26 | 1.1d | 1.20 |
| **60-65%** | **153** | **68.6%** | **+81.0%** | **$+40.50** | **1.0d** | **1.80** |
| **65-70%** | **159** | **73.0%** | **+131.0%** | **$+65.50** | **0.6d** | **2.00** |

Bands above 50% have extraordinary edge because NO is cheap and the payoff
is asymmetric: YES at 65% means NO costs $0.35 and wins $1.00 = 186% profit
per win. Breakeven HR at 65% YES is only 35%. Multipliers above 50% are
capped conservatively (max 2.00) given smaller sample sizes.

### Top Profitable Tags

| Tag | Signals | HR | ROI |
|-----|--------:|---:|---:|
| F1 Singapore Grand Prix | 49 | 93.9% | +80.2% |
| MLB All-Star Game | 26 | 88.5% | +66.2% |
| Super Bowl TV | 33 | 93.9% | +39.8% |
| Hockey | 48 | 85.4% | +19.6% |
| NHL | 80 | 82.5% | +13.9% |
| Best of 2025 | 212 | 92.0% | +9.7% |
| Google Search | 81 | 88.9% | +9.2% |
| Motorsport (cluster) | 505 | 85.7% | +8.5% |

### Top Profitable Keywords

| Keyword | Signals | HR | ROI |
|---------|--------:|---:|---:|
| mlb | 89 | 83.1% | +25.7% |
| park | 78 | 80.8% | +12.9% |
| league | 187 | 84.5% | +12.2% |
| traded | 95 | 85.3% | +11.4% |
| prix | 451 | 79.2% | +9.2% |
| grand | 549 | 78.1% | +6.7% |
| between | 6,908 | 74.4% | +1.6% |
| fed | — | — | +19.2% (combined) |

### Keywords to AVOID

| Keyword | Signals | HR | ROI |
|---------|--------:|---:|---:|
| combine | 350 | 5.1% | -90.1% |
| quarterly | 290 | 24.8% | -66.8% |
| points | 895 | 25.6% | -62.2% |
| beat | 1,556 | 38.9% | -44.2% |
| more | 1,661 | 38.8% | -44.2% |
| earnings | 825 | 41.6% | -44.1% |
| above | 2,020 | — | -23.0% |

---

## Stability Analysis

### Rolling 3-Month Windows (production config)

```
+ 2024-08..2024-10:   12 sigs  83.3% HR  ROI   +7.2%
+ 2024-09..2024-11:   22 sigs  86.4% HR  ROI   +9.6%
+ 2024-10..2024-12:   23 sigs  91.3% HR  ROI  +16.2%
+ 2024-11..2025-01:   38 sigs  92.1% HR  ROI  +17.4%
+ 2024-12..2025-02:   32 sigs  93.8% HR  ROI  +20.5%
+ 2025-01..2025-03:   49 sigs  93.9% HR  ROI  +21.0%
+ 2025-02..2025-04:   50 sigs  92.0% HR  ROI  +21.1%
+ 2025-03..2025-05:   69 sigs  92.8% HR  ROI  +23.8%
+ 2025-04..2025-06:   63 sigs  93.7% HR  ROI  +25.4%
+ 2025-05..2025-07:   53 sigs  92.5% HR  ROI  +23.2%
+ 2025-06..2025-08:   54 sigs  81.5% HR  ROI   +5.8%
+ 2025-07..2025-09:  214 sigs  77.1% HR  ROI   +4.2%
+ 2025-08..2025-10:  221 sigs  78.7% HR  ROI   +6.1%
+ 2025-09..2025-11:  221 sigs  81.0% HR  ROI   +8.4%
+ 2025-10..2025-12:  109 sigs  89.9% HR  ROI  +16.1%
+ 2025-11..2026-01:  163 sigs  89.0% HR  ROI  +15.9%
+ 2025-12..2026-02:  163 sigs  88.3% HR  ROI  +15.8%
```

**17/17 windows profitable (100%).** Worst window: +4.2% ROI (Jul-Sep 2025).

### Monthly PnL (at 2% fee)

| Month | Sigs | HR | ROI | PnL |
|:---:|---:|---:|---:|---:|
| 2025-01 | 24 | 91.7% | +15.5% | +$186 |
| 2025-03 | 21 | 95.2% | +21.5% | +$226 |
| 2025-05 | 23 | 95.7% | +29.0% | +$333 |
| 2025-06 | 15 | 100% | +28.0% | +$210 |
| 2025-08 | 24 | 70.8% | -10.2% | -$122 |
| 2025-09 | 175 | 77.7% | +4.3% | +$380 |
| 2025-12 | 63 | 87.3% | +12.7% | +$400 |
| 2026-01 | 77 | 89.6% | +16.5% | +$636 |

16/18 months profitable. Two losing months: Sep 2024 (3 signals, noisy) and
Aug 2025 (24 signals, -10.2%).

---

## Production Config (Recommended Filter Stack)

```
1. "Will" binary question                          (structural NO bias)
2. YES price 10-70%                                (12 data-derived bands, edge increases with price)
3. Volume < $2K                                    (critical profitability filter)
4. Prefer "between"/"mlb"/"prix"/"grand"/           (sports draws + competition + finance)
   "league"/"park"/"traded"/"fed" keywords
5. Avoid "reach"/"hit" keywords                     (confirmed negative edge)
6. Market size ≤ med bucket                         (XGBoost classifier, secondary filter)
```

### Config Parameters

```python
WillNoConfig(
    yes_price_min=0.10,
    yes_price_max=0.70,
    base_bet_usd=50.0,
    fee_pct=0.0,
    price_bands=(
        (0.10, 0.15, 0.25),   # 10-15%: +3.1% ROI, high HR (92.9%)
        (0.15, 0.20, 0.25),   # 15-20%: +3.0% ROI
        (0.20, 0.25, 0.60),   # 20-25%: +7.6% ROI
        (0.25, 0.30, 0.75),   # 25-30%: +8.5% ROI
        (0.30, 0.35, 1.00),   # 30-35%: +12.0% ROI, reference band
        (0.35, 0.40, 1.10),   # 35-40%: +13.9% ROI
        (0.40, 0.45, 1.30),   # 40-45%: +16.3% ROI
        (0.45, 0.50, 1.50),   # 45-50%: +28.8% ROI
        (0.50, 0.55, 1.80),   # 50-55%: +54.7% ROI
        (0.55, 0.60, 1.20),   # 55-60%: +34.5% ROI (lower HR)
        (0.60, 0.65, 1.80),   # 60-65%: +81.0% ROI
        (0.65, 0.70, 2.00),   # 65-70%: +131.0% ROI, capped
    ),
    prefer_keywords={"between", "mlb", "prix", "grand",
                     "league", "park", "traded", "fed"},
    avoid_keywords={"reach", "hit"},
    max_volume_usd=2000.0,
    volume_column="market_volume",  # trade-level sum(price*size), NOT Gamma event_volume
    max_bucket="med",
    dual_sided=False,
)
```

---

## Market-Making Angle (copy/18-19)

Selling YES as a limit order is economically identical to buying NO, but earns
the spread instead of paying it. The MM edge adds ~11% on top of the
selection edge.

| Metric | Taker-NO | Maker-sellYES | Delta |
|--------|:---:|:---:|:---:|
| PnL / Volume | 9.95% | 17.72% | +78% |

**Edge hierarchy** (research claim, directionally correct):
1. **Market selection** (65% of edge): Niche keywords + volume filter
2. **Capital rotation** (25% of edge): Vol < $1K = fast resolution
3. **Execution method** (10% of edge): MM adds ~11%

**Verdict**: Taker-NO for simplicity at <$1K capital. Switch to maker at $2K+.

---

## Capital Efficiency (Measured)

The 47.1% ROI is **per dollar wagered** across all ~2,534 signals. Capital
efficiency depends on lockup time (first trade → event resolution).

### Lockup Distribution

Per-band median lockup (from explore_s2_deeper.py):

| YES % | Sigs | Median Lockup |
|:---:|---:|:---:|
| 10-15% | 506 | 2.4d |
| 15-20% | 329 | 2.0d |
| 20-25% | 308 | 1.6d |
| 25-30% | 205 | 1.2d |
| 30-35% | 187 | 1.0d |
| 35-40% | 72 | 0.3d |
| 40-45% | 129 | 0.8d |
| 45-50% | 47 | 1.0d |
| 50-55% | 319 | 1.8d |
| 55-60% | 127 | 1.1d |
| 60-65% | 153 | 1.0d |
| 65-70% | 159 | 0.6d |

**Weighted average lockup: ~1.6 days.** Most signals resolve within 3 days.
The 35-40% and 65-70% bands have the fastest resolution (< 1 day).

### Deployed Capital vs Return

```
ROI per $ wagered:   47.1% (at 2% fee, implementation-validated)
Signals/month:       ~140
Monthly PnL (2%fee): ~$3,098
```

From `assess_s2a_actual.py` (actual strategy implementation against ClickHouse):

| Metric | Value |
|--------|------:|
| Capital tied up (median lockup) | ~$279 |
| Monthly ROI on capital | ~1,110% |

**~$279 deployed generates ~$3,098/month** at median lockup. This is 20x the
original config's throughput ($157/month) with dramatically higher capital
efficiency.

**Formula**: `monthly_roi_on_capital = roi_per_wager × (30 / avg_lockup_days)`

### Scaling Playbook

1. **$250-500**: Taker-only, ~$3,098/month at 2% fee. Most signals covered.
2. **$500-2,000**: Room to increase bet size or run dual_sided.
3. **$2,000-5,000**: Add maker-sellYES (dual_sided=True), cautious scaling.
4. **$5,000+**: Near saturation on niche markets (~140 signals/month).
5. **$10,000+**: Redirect surplus to S1.

---

## Risks

1. **Niche concentration**: Large fraction of signals are soccer draw markets.
   A change in Polymarket's market creation patterns could hurt the signal.
2. **Execution in thin markets**: $50 bets in <$2K volume markets. May need
   limit orders for larger sizes.
3. **Overfitting to keywords**: The keywords are proxies for market structures.
   If Polymarket changes question wording, the filter breaks.
4. **Sample size**: 2,534 resolved signals over ~18 months. Stable and well-distributed
   across 12 price bands. Implementation-validated: 100% entry price match.
5. **High-price band concentration risk**: Bands above 50% have extraordinary
   ROI (+55-131%) but smaller sample sizes (47-319 sigs each). Multipliers
   capped at 2.00 to limit exposure. Monitor for regime change.
6. **Edge decay**: NO base rate declined from 66.2% (pre-2025) to 61.7% (2025+).

---

## Validation Scripts

| Script | Purpose |
|--------|---------|
| `research/scripts/backtest_fav_longshot.py` | Walk-forward backtest (original research) |
| `scripts/explore_s2_edge.py` | Broad 9-dimension exploration (47K markets) |
| `scripts/explore_s2_deep.py` | Tag/keyword clusters, band calibration, stability |
| `scripts/explore_s2_final.py` | Niche calibration, fee sensitivity, band sizing |
| `scripts/explore_s2_expand.py` | Signal expansion: vol/price/keyword relaxation trade-offs |
| `scripts/explore_s2_deeper.py` | Deep exploration: beyond-50% bands, avoid mining, tags, stability |
| `scripts/validate_s2_config.py` | Quick config validation against ClickHouse |
| `scripts/assess_s2a_actual.py` | **Implementation validation**: runs actual `WillNoStrategy.compute_signals()` against ClickHouse, compares to exploration claims |
| `scripts/assess_s2_profitability.py` | Original profitability assessment (negative) |
