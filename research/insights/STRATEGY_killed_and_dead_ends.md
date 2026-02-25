# Killed Strategies and Dead Ends

Strategies investigated and conclusively rejected. Preserved to avoid re-researching.

---

## S4: Anti-Consensus YES -> Bet NO (KILLED — copy/16)

**Hypothesis**: When >=N skilled traders agree YES, bet NO (monetize the 18.5% YES anti-predictiveness).

**Why it was killed**: The signal is statistically real (+34pp edge, 96.2% NO HR) but has **zero practical capacity**.

| Min Traders | Agreement | Total Bets (13mo) | Bets/Month |
|:-----------:|:---------:|:-----------------:|:----------:|
| 3 | 60% | 26 | ~2 |
| 5 | 60% | 3 | ~0.2 |
| 7 | any | 0 | — |

The operational pool (53-77 traders) is too small for YES consensus to form. Even at min_traders=3, only 26 bets across 6 windows. YES consensus forms on ultra-cheap YES tokens (6.3c), so NO costs 93.7c — profit per correct bet is only $6.50. Total edge: **$58 over 13 months**.

**Root cause**: The pool is structurally NO-biased. Consensus YES requires multiple independently NO-leaning traders to all agree YES — a 3-sigma event.

**The original "18.5% YES HR" from insight #06** used wider pools (up to 4,934 traders), looser consistency (6-month), and lower min_markets (10). At operational pool parameters, there's no capacity.

---

## S5: Informed Market-Making on Consistency Signals (KILLED — insight #20)

**Hypothesis**: Place NO limit orders inside the spread on markets where 5+ pure_takers agree NO. Earn spread + directional edge.

**Why it was killed**: The signal is fundamentally unreliable. Across 12 dev windows:

| Best Config | Taker $/bet | Maker $/bet (2c spread) | Avg HR |
|-------------|:---:|:---:|:---:|
| t5, 70% agree | -$4.22 | +$1.83 | 49.1% |

The 2c spread converts a losing taker strategy into a marginal +$1.83/bet — less than $50/month. HR swings from 21% to 78% across windows.

**Why it fundamentally fails**:
1. No directional edge to capture (taker signal averages -$4.22/bet)
2. Spread is 1-2c on a 60-90c NO token — tiny relative to $100 loss when wrong
3. Execution method cannot fix a bad signal
4. Adverse selection concentrates fills on losing trades
5. High agreement (90%+) is anti-predictive

**Contrast with S2a MM**: S2a's maker-sellYES works because the 82% structural NO HR is robust. The consensus signal's 49% HR is too weak for MM to matter.

---

## Up/Down Markets — No Exploitable Edge (insight #22)

Tested 7 strategies across 32,707 markets (BTC, ETH, SOL, XRP + equities), 15-min/1-hour/daily:

| Strategy | Result |
|----------|--------|
| Serial autocorrelation (streak) | Statistically significant mean reversion on 15-min, but **market prices it in** (median next-YES = 48.2c after Up, 51.5c after Down). -$10.19/bet at actual prices. |
| Time-of-day bias | Persistent in-sample (6.8pp gap at 00:00 UTC), **collapses out-of-sample** (all negative $/bet). |
| Momentum following | Negative at every threshold (YES ≤ 5c: 94.4% HR but **-$0.54/bet**). Market perfectly calibrated. |
| Contrarian reversal | Appears profitable but **edge decaying to zero** (Jun: $44.97/bet → Feb 2026: -$1.22/bet). Extreme variance: 80% loss rate, top 10% of winners generate 243-394% of total PnL. |
| Cross-asset correlation | 80% same-direction within hour, but **no lead-lag** and correlation is fully priced in. |
| Market-making straddle | Adverse selection on one-sided fills (11.4%) overwhelms spread capture. **-$1.98/market** at every width tested. |
| Volume/price extremity | Low-volume Down bias (62 markets, impractical sample). |

**Why Up/Down is efficient but Above/Below isn't**:
- Up/Down: ~50c symmetric pricing, fast price discovery, no volatility premium
- Above/Below OTM: 5-25c asymmetric, lottery ticket premium, less liquid

**The only cross-window edge**: 2-streak reversal (covered separately in mean reversion strategy).

---

## Strategy Ideas Not Yet Tested (from Stratregfinement.md)

### Taker Trajectory (catch rising stars early)
- Top takers improve over time: -$290K early → +$275K later
- Signal: positive PnL slope + last 3 months profitable + MVF < 0.10 + 6+ months history
- **Status**: Unvalidated. Theoretical. Regression-to-mean risk.

### Market Alert + Feature Model (no direction prediction)
- Use skilled trader entry as market-level attention signal
- Combine with market features for direction (logistic regression)
- **Status**: Unvalidated. High complexity. Lookahead risk.

### Earnings YES Buyer
- Markets tagged "Earnings" resolve YES 74% (companies beat expectations)
- **Status**: Small universe (~150/year). Needs validation that YES isn't already priced at 74%.

### Temporal Regime Detector
- Rolling 90-day NO rate as sizing overlay
- NO rate declined from 66.2% (pre-2025) to 61.7% (2025+)
- **Status**: Estimated 10-20% Sharpe improvement. Low effort, not yet tested.
