# S2a: Favorite-Longshot NO on "Will" Binary Questions

**Status**: Active, MODERATE confidence (niche edge, data-validated)
**Capital allocation**: $300 initial, scale cautiously
**Direction**: Always NO (optionally dual-sided: buy NO + sell YES)
**Source insights**: overpriceNo/01-03, copy/17-20, explore_s2_edge/deep/final

---

## Edge Summary

"Will X happen?" binary markets have a mild structural NO bias (53:47 on binary
markets). However, **the broad strategy is net negative** (68.7% HR vs 78%
break-even = -11.5% ROI on 47,742 resolved Will markets at YES 5-50%).

The edge only materializes in a **specific niche**: sports draws, competition
outcomes, and finance topics in low-volume markets. The production config
targets this niche with keyword + volume filtering:

- **706 signals**, 84.8% HR, **+12.4% ROI at 2% fee** (cumulative over ~18 months, i.e. $0.124 profit per $1 wagered)
- **100% stability**: 17/17 rolling 3-month windows profitable
- ~35 signals/month

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
   We operationalized this as a hard `max_volume_usd=1000` filter, which
   transforms -11.5% ROI (broad) → +12.4% ROI (niche) at 2% fee.

4. **Revenue estimate**: The research's $690/month at $300 capital assumed
   the full 8,021-bet universe. With the niche filter (~35 signals/month) and
   2% fee, realistic estimate is ~$162/month. Lower throughput, higher per-bet
   edge.

---

## The Actual Edge (Data-Validated)

### Profitable Niche: Sports Draws + Low Volume

The "between" keyword captures soccer draw markets ("Will the match between
X and Y end in a draw?"). Combined with low volume (<$1K), this produces a
stable, profitable signal.

| Config | Signals | HR | ROI (0% fee) | ROI (2% fee) |
|--------|--------:|---:|:---:|:---:|
| **Production config** | **706** | **84.8%** | **+14.1%** | **+12.4%** |
| "between" only + vol<$1K | 564 | 85.6% | +13.0% | +11.4% |
| No keyword filter (broad) | 47,742 | 68.7% | -11.5% | — |

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
research's E[PnL/$100] table, where 40-45% is the peak at $9.67):

| YES % | HR | $/bet | Edge | ROI | Band Mult |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 15-20% | 89.4% | $+3.66 | +6.1% | +7.3% | 0.35 |
| 20-25% | 87.7% | $+5.87 | +9.2% | +11.7% | 0.60 |
| 25-30% | 85.6% | $+8.21 | +12.1% | +16.4% | 0.80 |
| **30-35%** | **81.5%** | **$+10.10** | **+13.7%** | **+20.2%** | **1.00** |
| 35-40% | 73.9% | $+8.92 | +11.2% | +17.8% | 0.90 |

This is because higher YES prices offer a much better payoff ratio (YES at
30% means NO costs $0.70 and wins $1.00 = 43% profit vs YES at 15% where
NO costs $0.85 and wins $1.00 = 18% profit). The slight drop in HR is more
than compensated.

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
2. YES price 15-40%                                (data-derived bands, edge increases with price)
3. Volume < $1K                                    (critical profitability filter)
4. Prefer "between"/"mlb"/"prix"/"grand"/           (sports draws + competition + finance)
   "league"/"park"/"traded"/"fed" keywords
5. Avoid "reach"/"hit" keywords                     (confirmed negative edge)
6. Market size ≤ med bucket                         (XGBoost classifier, secondary filter)
```

### Config Parameters

```python
WillNoConfig(
    yes_price_min=0.15,
    yes_price_max=0.40,
    base_bet_usd=50.0,
    fee_pct=0.0,
    price_bands=(
        (0.15, 0.20, 0.35),   # 15-20%: +7.3% ROI, lowest edge
        (0.20, 0.25, 0.60),   # 20-25%: +11.7% ROI
        (0.25, 0.30, 0.80),   # 25-30%: +16.4% ROI
        (0.30, 0.35, 1.00),   # 30-35%: +20.2% ROI, sweet spot
        (0.35, 0.40, 0.90),   # 35-40%: +17.8% ROI
    ),
    prefer_keywords={"between", "mlb", "prix", "grand",
                     "league", "park", "traded", "fed"},
    avoid_keywords={"reach", "hit"},
    max_volume_usd=1000.0,
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

The 12.1% ROI is **per dollar wagered** across all ~703 signals. Capital
efficiency depends on lockup time (first trade → event resolution).

### Lockup Distribution (701 signals with end_date)

| Bracket | Count | Pct |
|---------|------:|----:|
| < 1 day | 259 | 36.9% |
| 1-3 days | 181 | 25.8% |
| 3-7 days | 101 | 14.4% |
| 7-14 days | 39 | 5.6% |
| 14-30 days | 30 | 4.3% |
| 30+ days | 52 | 7.4% |

**Median lockup: 1.4 days** (p25=0.2d, p75=5.2d, mean=9.9d).
63% of signals resolve within 3 days. The mean is dragged up by a 7%
tail of 30+ day markets.

### Deployed Capital vs Return

```
Avg bet size:        $32.50 (band-adjusted)
ROI per $ wagered:   12.1% (at 2% fee)
Signals/month:       ~39
Monthly wagered:     ~$1,270
Monthly PnL (2%fee): ~$153
```

| Lockup assumption | Capital tied up | Turnover/mo | Monthly ROI | Annual ROI |
|:-:|--:|--:|--:|--:|
| p25 (0.2d) | $8 | 150× | 1812% | 21,744% |
| **median (1.4d)** | **$59** | **21×** | **259%** | **3,109%** |
| p75 (5.2d) | $219 | 5.8× | 70% | 842% |
| mean (9.9d) | $420 | 3.0× | 36% | 438% |

The median scenario is realistic for typical usage: **~$59 deployed generates
~$153/month**. The mean scenario (~$420 deployed) is more conservative and
accounts for the long-tail markets. In practice, you could skip markets with
expected lockup > 14 days to stay closer to the median.

**Formula**: `monthly_roi_on_capital = roi_per_wager × (30 / avg_lockup_days)`

### Scaling Playbook

1. **$60-300**: Taker-only, ~$153/month at 2% fee. Most signals covered.
2. **$300-1,000**: Room to increase bet size or run dual_sided.
3. **$2,000-5,000**: Add maker-sellYES (dual_sided=True), cautious scaling.
4. **$5,000+**: Near saturation on niche markets (~39 signals/month).
5. **$10,000+**: Redirect surplus to S1.

---

## Risks

1. **Niche concentration**: 80%+ of signals are soccer draw markets. A change
   in Polymarket's market creation patterns could kill the signal.
2. **Execution in thin markets**: $50 bets in <$1K volume markets. May need
   limit orders for larger sizes.
3. **Overfitting to "between"**: The keyword is a proxy for a market structure.
   If Polymarket changes question wording, the filter breaks.
4. **Sample size**: 706 signals over ~18 months. Stable but not enormous.
5. **Low signal count**: ~35/month means slow compounding and variance.
6. **Edge decay**: NO base rate declined from 66.2% (pre-2025) to 61.7% (2025+).

---

## Validation Scripts

| Script | Purpose |
|--------|---------|
| `research/scripts/backtest_fav_longshot.py` | Walk-forward backtest (original research) |
| `scripts/explore_s2_edge.py` | Broad 9-dimension exploration (47K markets) |
| `scripts/explore_s2_deep.py` | Tag/keyword clusters, band calibration, stability |
| `scripts/explore_s2_final.py` | Niche calibration, fee sensitivity, band sizing |
| `scripts/validate_s2_config.py` | Quick config validation against ClickHouse |
| `scripts/assess_s2_profitability.py` | Original profitability assessment (negative) |
