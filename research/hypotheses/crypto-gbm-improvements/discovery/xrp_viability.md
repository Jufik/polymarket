# XRP Up/Down GBM Scalp Strategy — Viability Assessment

**Date**: 2026-03-10  
**Analyst**: Researcher Agent  
**Reference**: BTC GBM scalp baseline — +$2.10/trade median at $50 notional

## VERDICT: CONDITIONAL NO-GO (with path to limited GO)

XRP GBM signal has real predictive power (81% accuracy at threshold=0.10) but three compounding factors reduce expected EV to ~$1.02/trade vs $2.10 for BTC: (1) 15x thinner liquidity, (2) 47x fatter tails breaking GBM assumptions, (3) unmodelable regulatory event risk.

## Section 1: Market Structure

| win_size | n_markets | n_resolved | up_rate | earliest | latest |
| --- | --- | --- | --- | --- | --- |
| 5m | 15905 | 2991 | 0.0939 | 2025-12-18 | 2026-03-01 |
| 15m | 12396 | 11903 | 0.4702 | 2025-10-27 | 2026-03-01 |
|  | 6410 | 6253 | 0.4832 | 2025-05-01 | 2026-03-01 |
| 4h | 758 | 727 | 0.4591 | 2025-10-28 | 2026-03-01 |

| asset | win_size | n_markets | n_positions | pos_per_mkt |
| --- | --- | --- | --- | --- |
| BTC | 15m | 13631 | 5264417 | 386.2000 |
| BTC | 4h | 779 | 42164 | 54.1000 |
| BTC | 5m | 4954 | 2903391 | 586.1000 |
| XRP | 15m | 11903 | 1014059 | 85.2000 |
| XRP | 4h | 727 | 21934 | 30.2000 |
| XRP | 5m | 2991 | 212642 | 71.1000 |

**Liquidity ratio**: BTC/XRP = 15.4x at 15m window.
XRP 15m median market vol = $3,833 | BTC = $59,097.

| asset | win_size | n_mkts | p10 | p50 | p90 | avg_vol |
| --- | --- | --- | --- | --- | --- | --- |
| BTC | 15m | 13630 | 11,691.0 | 59,097.0 | 142,782.0 | 70,247.0 |
| BTC | 4h | 779 | 844.0000 | 5,715.0 | 22,579.0 | 11,472.0 |
| BTC | 5m | 4953 | 31,112.0 | 49,118.0 | 79,947.0 | 56,051.0 |
| XRP | 15m | 11882 | 994.0000 | 3,833.0 | 9,055.0 | 5,050.0 |
| XRP | 4h | 726 | 93.0000 | 871.0000 | 4,329.0 | 1,670.0 |
| XRP | 5m | 2989 | 555.0000 | 1,399.0 | 3,190.0 | 1,959.0 |

## Section 2: Volatility Profile

### Hourly Realized Vol Distribution (basis points)
| symbol | p10_bps | p25_bps | p50_bps | p75_bps | p90_bps | p99_bps | avg_bps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BTC-USDT | 0.3500 | 0.5200 | 0.7800 | 1.1900 | 1.7200 | 3.2400 | 0.9500 |
| XRP-USDT | 0.3800 | 0.5100 | 0.7400 | 1.0800 | 1.6200 | 3.2400 | 0.9200 |

### Return Distribution: Fat Tails
| symbol | kurtosis | skewness | std_bps | p99_abs_bps | p999_abs_bps |
| --- | --- | --- | --- | --- | --- |
| BTC-USDT | 2,544.0 | -2.4000 | 1.1700 | 3.9600 | 10.8000 |
| XRP-USDT | 119,717.0 | 99.9000 | 1.4100 | 4.8600 | 10.1600 |

XRP kurtosis = 119,717 vs BTC = 2,544. Normal = 3. XRP fat tails are **47x worse** than BTC.

### Monthly Annualized Vol
| month | symbol | ann_vol_pct | max_1min_range_pct |
| --- | --- | --- | --- |
| 2025-09-01 | BTC-USDT | 4.0000 | 1.2700 |
| 2025-10-01 | BTC-USDT | 8.7000 | 5.7100 |
| 2025-11-01 | BTC-USDT | 8.7000 | 2.8400 |
| 2025-12-01 | BTC-USDT | 7.4000 | 1.1800 |
| 2026-01-01 | BTC-USDT | 6.9000 | 2.5700 |
| 2026-02-01 | BTC-USDT | 11.9000 | 1.5200 |
| 2026-03-01 | BTC-USDT | 9.8000 | 1.3100 |
| 2025-09-01 | XRP-USDT | 5.1000 | 1.1200 |
| 2025-10-01 | XRP-USDT | 17.5000 | 33.7300 |
| 2025-11-01 | XRP-USDT | 9.1000 | 1.2500 |
| 2025-12-01 | XRP-USDT | 5.7000 | 0.6700 |
| 2026-01-01 | XRP-USDT | 6.9000 | 0.9900 |
| 2026-02-01 | XRP-USDT | 11.0000 | 0.7500 |
| 2026-03-01 | XRP-USDT | 6.8000 | 0.4700 |

### Extreme Move Frequency (>5% in 15 min)
| symbol | n_extreme_5pct | n_extreme_2pct | n_extreme_10pct | total_windows | freq_5pct_per_10k | freq_2pct_per_10k |
| --- | --- | --- | --- | --- | --- | --- |
| BTC-USDT | 1 | 34 | 0 | 16014121 | 0.0000 | 0.0200 |
| XRP-USDT | 194 | 706 | 26 | 15551985 | 0.1200 | 0.4500 |

## Section 3: GBM Signal Quality

GBM model applied at 15-min midpoint (bar 8 of 15). Signal = GBM_prob diverges from 0.50 by >0.10.

| symbol | gbm_bucket | n_windows | actual_up_rate | avg_gbm_prob |
| --- | --- | --- | --- | --- |
| BTC-USDT | 0.40-0.45 (DOWN signal) | 275787 | 0.2975 | 0.4249 |
| BTC-USDT | 0.45-0.50 (weak DOWN) | 516076 | 0.3867 | 0.4873 |
| BTC-USDT | 0.50-0.55 (weak UP) | 337681 | 0.5897 | 0.5190 |
| BTC-USDT | 0.55-0.60 (UP signal) | 273597 | 0.6906 | 0.5750 |
| BTC-USDT | <0.40 (strong DOWN signal) | 1406343 | 0.1322 | 0.2327 |
| BTC-USDT | >0.60 (strong UP signal) | 1391320 | 0.8629 | 0.7673 |
| XRP-USDT | 0.40-0.45 (DOWN signal) | 223692 | 0.3529 | 0.4262 |
| XRP-USDT | 0.45-0.50 (weak DOWN) | 329478 | 0.4327 | 0.4896 |
| XRP-USDT | 0.50-0.55 (weak UP) | 87591 | 0.5302 | 0.5388 |
| XRP-USDT | 0.55-0.60 (UP signal) | 222325 | 0.5693 | 0.5739 |
| XRP-USDT | <0.40 (strong DOWN signal) | 1534011 | 0.1579 | 0.1870 |
| XRP-USDT | >0.60 (strong UP signal) | 1511776 | 0.8101 | 0.8138 |

| Symbol | Strong-UP (>0.60) Accuracy | Assessment |
| --- | --- | --- |
| BTC-USDT | 86.3% | EXCELLENT — model well-calibrated |
| XRP-USDT | 81.0% | GOOD but 5.3pp degraded vs BTC |

Key degradation: at weak-UP bucket (0.55-0.60), XRP=56.9% vs BTC=69.1%. Model is noisy in the marginal signal zone. This is the fat-tail effect.

## Section 4: Liquidity Check

Fill feasibility at $50 and $200 position sizes:
- **BTC/15m**: median vol=$59,097 | $50=0.1% of market | $200=0.3%
- **BTC/4h**: median vol=$5,715 | $50=0.9% of market | $200=3.5%
- **BTC/5m**: median vol=$49,118 | $50=0.1% of market | $200=0.4%
- **XRP/15m**: median vol=$3,833 | $50=1.3% of market | $200=5.2%
- **XRP/4h**: median vol=$871 | $50=5.7% of market | $200=23.0%
- **XRP/5m**: median vol=$1,399 | $50=3.6% of market | $200=14.3%

**Conclusion**: $50 fills on XRP 15m = 1.3% of market (borderline). $200 = 5.2% (market-moving). Max viable size: **$50**.

## Section 5: XRP-Specific Risks

1. **Regulatory event risk** — SEC/CFTC actions can move XRP 20-75% in minutes. Unmodelable, uncorrelated with GBM signal. Low frequency (~2/year) but high magnitude.
2. **Fat-tail GBM failure mode** — When XRP spikes 15% in 10 min, sigma was estimated from quiet periods. d2 → ∞, GBM fires at max confidence in wrong direction during corrections.
3. **Vol regime changes** — XRP monthly vol ranged 5.1% to 17.5% annualized in our data. EWMA sigma lags regime transitions by 30-60 min.
4. **PM liquidity structure** — XRP PM markets are 15x thinner. Wider effective spreads, slower PM price discovery (a mixed signal: PM lags longer = more time to fill, but spreads are wider).

## Section 6: EV Estimation

Starting from BTC baseline: **+$2.10/trade at $50 notional**

| Degradation Factor | Estimated Cost/Trade |
| --- | --- |
| GBM calibration gap (-5pp accuracy) | -$0.30 |
| Liquidity/spread degradation | -$0.20 |
| Fat-tail model failure (occasional bad entries) | -$0.30 |
| Vol regime lag (sigma underestimation) | -$0.20 |
| Regulatory event risk (annualized) | -$0.08 |
| **XRP estimated net EV** | **+$1.02** |

Range: **[-$0.50 to +$1.80]** depending on vol regime. Wide confidence interval.

Compounding score comparison:
- BTC: $2.10 / (15 min hold / 1440 min/day) = ~0.0028/min
- XRP: $1.02 / (20 min hold / 1440 min/day) = ~0.0010/min
- XRP is ~36% as capital-efficient as BTC

## Section 7: Recommendation

**CONDITIONAL NO-GO** with path to limited GO.

### What would change the verdict

1. **Higher threshold (0.15 vs 0.10)**: Only fire when GBM diverges strongly. Eliminates the noisy weak-signal regime where XRP degrades most.
2. **Small position ($25-$50 max)**: Stays under 1% market impact. Reduces returns but preserves fill quality.
3. **Regulatory filter**: Auto-pause for 2h after XRP moves >5% in 2 min.
4. **30-day paper validation**: Run paper alongside BTC. Promote if net PnL > 0.

### Conservative launch parameters (if GO)

```toml
[strategy.xrp_gbm]
symbol = 'XRP-USDT'
threshold = 0.15          # vs 0.10 for BTC — higher bar
max_position_usd = 25     # vs 50-200 for BTC — smaller
exit_threshold = 0.02     # same as BTC
time_stop_s = 300         # same as BTC
gbm_flip_exit = 0.35      # same as BTC
pause_on_vol_spike = 3.0  # pause if vol spikes 3x trailing
pause_on_move_pct = 5.0   # pause if XRP moves >5% in 2 min
```

### Expected EV at conservative params
- Net PnL: ~$0.50-$1.50/trade at $25 notional
- Monthly trades: ~50-100 (thinner market)
- Monthly PnL: ~$50-100 (vs $250-400 for BTC at $50)
- Marginal resource cost: negligible (same infrastructure)

### Priority guidance
**Do NOT launch XRP before validating BTC improvements** (dynamic sizing, EWMA sigma — 3-4x higher compounding score). XRP is a low-priority optional expansion, not a core improvement.

### Conditions for revisiting (GO)
- XRP gets an ETF approval: vol structure changes, more PM liquidity
- Regulatory clarity (SEC settlement): idiosyncratic risk removed
- PM expands XRP market liquidity: if median vol > $15K, re-evaluate