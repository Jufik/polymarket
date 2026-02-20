# Five Strategies to Research from the NO Edge Analysis

Source: `insights/overpriceNo/01_no_edge_analysis.md`

---

## 1. Favorite-Longshot Arbitrage (Binary Markets Only)

**The edge**: The market systematically overprices longshots. When early YES price is 15-45%, actual YES rate is 5-9pp lower. This is free NO edge on binary markets.

**Implementation**:
- Universe: binary markets only (neg_risk=False), volume > $10K
- Entry signal: YES token trades in the 0.15-0.45 range during the first 30% of market lifetime
- Action: buy NO at 1 - YES_price
- Exit: hold to resolution
- Sizing: fixed $100/bet, or proportional to calibration edge (bigger when implied YES is 20-30% where edge is 8pp)

**What to backtest**:
- Walk-forward: for each month, compute the calibration curve on all prior resolved markets, then bet NO on new markets where early YES price falls in the edge zone
- Compare: raw NO rate in the edge zone vs breakeven NO rate given the entry price
- Track: PnL per bet = won ? $bet * YES_price / (1-YES_price) - fee : -$bet - fee
- Metric: Sharpe on daily resolved PnL, hit rate vs implied probability

**Data needed**: `market_prices.parquet` for early price detection, `markets_resolved.parquet` for outcomes, `markets.parquet` for neg_risk filter

**Key risk**: The calibration edge may be driven by stale/illiquid prices that you can't actually execute at. Need to verify the price persists for at least 60s (check price timeseries volatility in the first 30% of lifetime).

**Estimated signal count**: ~60K binary markets in the 15-45% YES range. Even with strict filters, thousands of bets per year.

---

## 2. Earnings Contrarian (Buy YES on Earnings Markets)

**The edge**: Markets tagged "Earnings" or "Earnings Calls" resolve YES 74% and 59% of the time respectively. Companies systematically beat market expectations. This is well-documented in financial literature (the "earnings surprise" bias).

**Implementation**:
- Universe: markets with tags containing "Earnings", volume > $5K
- Entry signal: buy YES when the market opens or when YES price < 0.70
- Action: buy YES
- Exit: hold to resolution (typically next trading day after earnings call)
- Sizing: equal-weight, cap at $500/bet, diversify across 10+ earnings markets per quarter

**What to backtest**:
- Walk-forward by quarter: use prior quarters' YES rate as the prior, bet YES on new earnings markets
- Critical test: is the 74% rate stable over time? Check Q-by-Q for earnings specifically
- Check entry price: if YES is already priced at 0.74, there's no edge — need to verify typical YES price at market creation is below 0.70
- Compare PnL to: buying SPY on each earnings date (systematic earnings premium exists in equities too)

**Data needed**: `markets.parquet` (tags column), `markets_resolved.parquet`, `market_prices.parquet` for entry price at creation

**Key risk**: Small universe (546 Earnings markets total). May not have enough for statistical significance per quarter. Also, the 74% could be inflated by specific mega-cap companies (AAPL, MSFT) that always beat — check if it generalizes.

**Estimated signal count**: ~150/year based on current data growth

---

## 3. Aggressive Threshold Fader (Bet NO on "Will Drop Below" / "Will Reach")

**The edge**: Questions with "below" have 93.5% NO rate. "Will reach" has 74.3% NO rate. "Will drop" has 77.9% NO rate. These are threshold questions where the threshold is set too aggressively relative to actual price dynamics.

**Implementation**:
- Universe: binary markets where question matches ("below" OR "drop below" OR "reach" OR "hit $X"), volume > $5K
- Entry signal: buy NO when market opens or when NO price < 0.85 (i.e., YES > 0.15)
- Special case for "above"/"over": SKIP — these have near-fair 46% YES rates
- Sizing: scale with confidence. "Below" questions (93% NO) get full size. "Reach" questions (74% NO) get half size.

**What to backtest**:
- Walk-forward: parse question text for pattern, filter to qualifying markets, bet NO
- Measure: PnL net of capital lockup cost (NO at 0.85 ties up $85 to win $15)
- Compare to: simply buying NO on ALL "Will" questions (78% NO rate) — is the threshold-specific filter better?
- Check: annual return on capital, not just hit rate. High NO price means low return per dollar even with high hit rate

**Data needed**: `markets.parquet` (question column), `markets_resolved.parquet`

**Key risk**: Capital efficiency. Buying NO at $0.90 to win $0.10 means 11% return on capital per correct bet, minus losses on 7-10% wrong bets. Need to check if the risk/reward is actually +EV after capital lockup.

**Estimated signal count**: ~3,500/year ("below" + "reach" + "drop" questions)

---

## 4. Category-Conditional NO Overlay (Boost Existing Consensus Copy)

**The edge**: The consensus copy backtester already runs NO-only with pure_taker filter. The calibration analysis shows the NO edge varies dramatically by market type. Overlaying category-specific priors should improve signal quality.

**Implementation**:
- Start with: existing consensus copy signal (NO-only, pure_taker, 7+ traders, 70% agreement)
- Add filter: only take bets where the market's category/tags have a historical NO rate > 55%
- Weight by: calibration edge. If the early YES price implies +8pp NO edge (from the favorite-longshot curve), increase bet size. If the curve shows 0pp or negative edge, skip.
- Exclude: neg_risk markets entirely (no excess NO edge beyond structural 1/N)

**What to backtest**:
- Re-run the existing sweep with an additional dimension: `neg_risk_filter=[True/False]` and `calibration_overlay=[True/False]`
- Measure: does filtering to binary-only improve Sharpe? Does the calibration overlay improve PnL/bet?
- Cross-validate: the calibration curve must be computed only on prior data for each window (no lookahead)

**Data needed**: existing backtester infrastructure + `markets.parquet` for neg_risk flag + calibration curve computation

**Key risk**: Reduces already-small bet counts. The consensus copy signal generates 20-65 bets per monthly window. Adding a binary-only filter + calibration overlay may cut this to 5-15 bets — too few for statistical power.

**Estimated signal count**: ~30-50% of existing consensus copy bets survive filtering

---

## 5. Temporal Regime Detector (Adaptive NO Sizing)

**The edge**: The NO rate varies dramatically over time: 50.5% in 2023, 73% in 2024 Q1-Q3, 60.3% in 2026 Q1. A fixed strategy misses this. An adaptive strategy that sizes NO bets based on the recent NO rate should capture regime shifts.

**Implementation**:
- Trailing indicator: compute the rolling 90-day NO resolution rate across all binary markets
- Regime classification:
  - **Strong NO regime** (NO rate > 65%): full NO sizing, expand to lower-conviction signals
  - **Neutral regime** (NO rate 50-65%): standard sizing, only highest-conviction NO signals
  - **YES regime** (NO rate < 50%): reduce or pause NO strategies, consider YES signals (earnings, high-YES categories)
- Apply to: ALL other NO strategies (1-4 above). The regime detector is a sizing overlay, not a standalone strategy.
- Signal update: weekly, based on markets that resolved in the trailing 90 days

**What to backtest**:
- Walk-forward: at each month, compute the trailing 90-day NO rate, classify regime, adjust sizing multiplier (e.g., 1.5x in strong NO, 1.0x in neutral, 0.5x in YES regime)
- Measure: does the regime overlay improve Sharpe vs fixed sizing?
- Critical check: is the trailing NO rate predictive of the NEXT month's NO rate? (autocorrelation analysis)
- Compare to: fixed sizing with the unconditional 62% base rate

**Data needed**: `markets_resolved.parquet` with resolved_at timestamps for rolling computation

**Key risk**: Regime changes may be abrupt (e.g., a wave of new neg_risk sports markets suddenly shifts the base rate). The 90-day trailing window may lag. Also, the regime may be driven by market mix (more neg_risk markets = higher NO rate) rather than a genuine shift in binary market calibration. Must compute the regime on binary markets only.

**Estimated alpha**: 10-20% Sharpe improvement from adaptive sizing vs fixed, based on the 2024→2026 regime shift magnitude.

---

## Priority Ranking

| # | Strategy | Edge Size | Data Ready? | Complexity | Bet Count | Priority |
|---|----------|-----------|:-----------:|:----------:|:---------:|:--------:|
| 1 | Favorite-Longshot Arb | 6-9pp | Yes | Low | High (~5K/yr) | **Research first** |
| 4 | Category NO Overlay | Additive | Yes | Low | Medium | **Quick win** |
| 3 | Threshold Fader | 10-30pp | Yes | Low | Medium (~3.5K/yr) | **High edge, check capital efficiency** |
| 5 | Regime Detector | ~10-20% Sharpe lift | Yes | Medium | Overlay | **Low effort, high upside** |
| 2 | Earnings YES | ~15pp | Yes | Low | Low (~150/yr) | **Small universe, validate first** |

**Strategy 1** (Favorite-Longshot) should be researched first because it has the largest universe, a well-documented theoretical basis, and all data is already available. Strategy 4 (Category Overlay) is a quick modification to the existing backtester. Strategy 3 needs a capital efficiency check before committing.
