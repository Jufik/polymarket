# Research vs Implementation Gap Analysis

**Date**: 2026-02-25
**Method**: Side-by-side comparison of research insights (#01-#24) against industrialized strategy code in `strategies_impl/`
**Scope**: S1 (proportional copy), S2a (will-no), S2b (crypto OTM NO), S3 (consensus copy), market size classifier

---

## Gap 1: S2a YES price band too wide

**Research** (`17_s2_rotation_accelerators.md`):
- YES 15-30% is optimal: $18.37/bet, $4.59/day PnL, 86.7-95.7% HR
- 30-35%: HR drops to 75.8%, PnL/day halves
- 35-40%: 65.7% HR, $4.27/bet (thin edge)
- 40-45%: 57.9% HR, **-$0.27/bet** (negative)

**Implementation** (`will_no/config.py`):
```python
yes_price_max: float = 0.40  # includes the 35-40% negative-edge tail
```

**Classifier impact test confirms**: strategy is -12.6% ROI across 19K signals at 15-40%.

**Fix**: Change default to `yes_price_max=0.30`. One line.

---

## Gap 2: Rotation accelerators not implemented

**Research** (`17_s2_rotation_accelerators.md`):
Three filters combined cut median lockup from 4 days to ~1 day and 2.8x PnL:
1. Volume < $5K (89.7% HR at <$1K, $19.42/bet, 88x higher PnL/day than $100K+)
2. Keyword prefer "above"/"below"/"today" (same-day resolution, 87-88% HR)
3. Avoid "reach"/"hit"/"by"/"before" (long lockup or negative edge)

**Implementation**:
- `avoid_keywords` works: `{"reach", "hit"}` default. Missing "by", "before".
- `prefer_keywords` exists in config but **never checked in strategy logic** — `_is_eligible()` and `compute_signals()` don't use it.
- `max_volume_usd` exists in config but **never enforced** — no code reads it.
- No volume filter equivalent. The classifier's `max_bucket` gate could serve as proxy but isn't wired.

**Fix**:
- Add "by", "before" to default `avoid_keywords`.
- Implement prefer_keywords filtering in `compute_signals()` (boost or require).
- Enforce `max_volume_usd` in both event-driven and vectorized paths.
- Or: wire `max_bucket="med"` as the volume proxy (classifier-based).

---

## Gap 3: Market size classifier built but never activated

**Research** (`17_s2_rotation_accelerators.md`, `03_fav_longshot_research.md`):
- Edge concentrated in less liquid markets
- <$1K volume: 89.7% HR, $19.42/bet
- >$100K volume: 73.6% HR, $1.76/bet

**Classifier impact test** (2026-02-25):
- actual=thin: -3.2% ROI (best), 72.9% HR
- actual=heavy: -25.9% ROI (worst), 54.6% HR
- `max_bucket="thin"` saves $101K (84% of baseline loss)
- Classifier accuracy on strategy signals: 79.4% exact, 99.9% within ±1 bucket

**Implementation**:
- `MarketSizeClassifier` trained, validated at 80.9% accuracy, model saved.
- `max_bucket` field exists in `WillNoConfig` and `CryptoOTMNoConfig`.
- `on_trade()` has the gating logic (lines 58-75 in will_no/strategy.py).
- **But `max_bucket` defaults to `None`** — never activated in any config.
- `MarketSizeProvider` exists but is not wired into any live runner.

**Fix**: Set `max_bucket="med"` as default in `WillNoConfig`. Wire `MarketSizeProvider` into the live feature pipeline.

---

## Gap 4: S2b crypto OTM NO — best strategy, not prioritized for live

**Research** (`21_crypto_otm_no_strategy.md`):
> "This is the best strategy we've found."
- 98.9% HR, $12.72/bet, $802/month at $1K capital
- 4-8 hour lockup (6x daily rotation)
- Zero losing months across 7 months of data
- Max drawdown: $298

**Implementation**:
- Strategy code works (`crypto_otm_no/strategy.py`).
- Backtest script uses it via `run_vectorized_strategies.py`.
- **Missing for live**:
  - No checkpoint-specific timing logic (should scan every 4h at ET boundaries)
  - No asset-level risk management (BTC 0.5% loss rate vs SOL 2.7%)
  - Not integrated into live pipeline (`live/orchestrator.py`)
  - No fast-market filter (< 24h lockup) despite research showing it adds +$222/mo

---

## Gap 5: S1 proportional copy pool selection ignores research

**Research** (`02_consistency_as_predictor.md`, `03_mvf_patterns.md`, `12_entry_price_filter.md`):
- 9-month consistent, pure_taker (MVF < 0.10), min 20 markets/month
- Median directional entry price ≤ 0.80 (removes 83% of fake-consistent traders)
- Expected: ~90-110 traders, 87.8% win rate

**Implementation**:
- `run_vectorized_strategies.py` uses **top 20 traders by distinct market count >= 50** — no consistency, no MVF, no entry price filter. This is essentially random selection.
- `consensus_copy/providers.py` does use proper filters (consistency + MVF + entry price). But `proportional_copy` has no equivalent provider — it takes `pool_traders` as a raw set from config.

**Fix**: Build a `ProportionalCopyProvider` that mirrors the consensus_copy provider's pool selection logic (9m consistency, MVF < 0.10, entry ≤ 0.80). Or share the pool between S1 and S3.

---

## Gap 6: Dual-sided execution never activated

**Research** (`20_s2_dual_sided_capacity.md`):
- At $600+, dual-sided (BUY NO + SELL YES) accesses 2 separate liquidity pools
- 1.9x PnL at same capital (6 dual slots vs 6 single slots)
- 87.5% of target markets support full dual deployment
- At $300: no benefit (capital-bound regardless)

**Implementation**:
- `WillNoConfig.dual_sided: bool = False` exists.
- `on_trade()` handles dual (lines 79-104): emits both BUY NO and SELL YES intents at half size.
- **Never activated** — all backtest configs use `dual_sided=False`.
- `compute_signals()` (vectorized path) does NOT implement dual — only emits BUY NO.

**Fix**: Enable `dual_sided=True` when capital > $500. Implement dual in vectorized path for backtesting.

---

## Gap 7: No combined equity curve

**Research** (`15_composite_strategy_next_steps.md`):
> "Combined equity curve: Simulate S1+S2+S3 running simultaneously with proper capital partitioning."

Recommended allocation ($1,500):
- S1 proportional copy: $1,000
- S2 fav-longshot NO: $300
- S3 consensus NO: $200

**Implementation**: No combined simulation exists. Each strategy runs independently in `run_vectorized_strategies.py` and `run_pnl_analysis.py`. No capital partitioning, no slot management across strategies, no combined drawdown analysis.

---

## Gap 8: No execution price validation

**Research** (`03_fav_longshot_research.md`, `19_s2_expectations.md`, `21_crypto_otm_no_strategy.md`):
All flag this as a pre-deployment validation item:
- Backtest uses median early price or first trade price
- Live execution may get worse fills (price moved, thin orderbook)
- 50% haircut applied as rough correction but never validated

**Implementation**: No script compares backtest entry prices vs achievable prices. No CLOB orderbook depth analysis. The `clob_client.py` exists but isn't used for price validation.

---

## Gap 9: Keyword avoid list incomplete

**Research** (`17_s2_rotation_accelerators.md`):

| Keyword | Action | Reason |
|---------|--------|--------|
| "reach" | Avoid | 71% HR, -$9.21/bet |
| "hit" | Avoid | 77.1% HR, -$1.17/bet |
| "by" | Avoid | 17-day lockup |
| "before" | Avoid | 40-day lockup |
| "in 2025"/"in 2026" | Avoid | 118+ day lockup |

**Implementation**: `avoid_keywords={"reach", "hit"}`. Missing "by", "before", annual patterns.

**Fix**: Expand default avoid set. Consider regex patterns for "in 20\d\d".

---

## Gap 10: S3 consensus copy has only 2 holdout windows

**Research** (`06_forward_pricing_backtest.md`):
- Top configs only validated on Dec 2025 + Jan 2026
- "Small bet counts — top configs have 20-65 bets per window, susceptible to noise"
- Jan 2026 shows dramatically better performance — may be recency bias

**Implementation**: S3 is allocated $200 in the composite plan, which is appropriate for "building validation." But no automated rolling validation exists to track whether the signal holds as new data arrives.

**Fix**: Build a monthly validation cron that re-evaluates S3 on the latest window and flags degradation.

---

## Priority Matrix

| # | Gap | Impact | Effort | Blocking Live? |
|---|-----|--------|--------|:-:|
| 1 | Narrow YES band to 15-30% | Removes negative-edge tail | 1 line | Yes |
| 2 | Wire `max_bucket="med"` | Cuts 84% of S2a loss | 1 line | Yes |
| 3 | Expand avoid keywords | Cuts multi-week lockups | 1 line | Yes |
| 9 | Add prefer_keywords logic | 3x capital rotation | Small | Yes |
| 4 | S2b crypto OTM live pipeline | Best strategy not deployed | Medium | Yes |
| 5 | Fix S1 pool selection | Currently random top-20 | Medium | Yes |
| 6 | Enable dual-sided S2 | 1.9x PnL at $600+ | Small | No |
| 7 | Combined equity curve | Validates capital plan | Medium | No |
| 8 | Execution price validation | Reality check on fills | Medium | No |
| 10 | S3 rolling validation | Tracks signal decay | Medium | No |

---

## Data References

- Classifier impact test: `scripts/assess_classifier_impact_will_no.py` (2026-02-25)
- Market size model: `models/market_size_xgb.joblib` (80.9% accuracy, Optuna-tuned)
- Research insights: `research/insights/copy/` (#01-#20), `research/insights/overpriceNo/` (#01-#03)
- Strategy implementations: `src/polymarket_pipeline/strategies_impl/`
