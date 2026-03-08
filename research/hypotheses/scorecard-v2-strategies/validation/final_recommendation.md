# Final Deployment Recommendation — Tick-Validated

**Date**: 2026-03-07
**Pipeline**: Vectorized discovery → Tick-by-tick validation → Skeptic review → This document
**All numbers below are TICK-VALIDATED (SyncReplayRunner), not vectorized upper bounds**

---

## Strategy Rankings (Post-Tick, Post-Skeptic)

| Rank | Strategy | Tick Excess HR | Fills | Sharpe | PnL | Verdict |
|------|----------|---------------|-------|--------|-----|---------|
| 1 | **Sports YES Composite K=25 N=3** | **+39.8pp** | 612 | 11.94 | +$117K | PROMOTE to paper_dev |
| 2 | **Politics YES Composite K=100 N=5** | **+51.6pp** (raw), **+41pp** (price≤0.80) | 262 (125 filtered) | - | - | PROMOTE after fixes |
| 3 | **Crypto YES HR-only K=50 N=2** | **+30.9pp** (genuine only) | 98 genuine | - | +$57K | RERUN with max_price=0.65 |

---

## Strategy 1: Sports YES Composite — PROMOTE

**The strongest tick-validated signal.**

| Metric | Value |
|--------|-------|
| Fills | 612 |
| Hit Rate | 73.0% |
| Excess HR (vs 33.2% YES base) | +39.8pp |
| Sharpe | 11.94 |
| Max Drawdown | $1,450 |
| Profit Factor | 8.08 |
| Avg Hold | 3.4h |
| Degradation from vectorized | -7pp (47→40) |

**Why it works**: The N=3 consensus gate naturally filters in-play contamination (only 1.8% of fills <1h). The YES-only filter removes structural NO bias (-3.7pp excess). Composite ranking ensures walk-forward stability.

**Skeptic flags to address before paper**:
- Remove `min_hold_hours` parameter (never enforced, misleading)
- Confirm capital enforcement in harness (SyncReplayRunner check_risk_gate)
- The -7pp degradation is below expected 20-40pp — investigate if residual in-play leaks exist

**Paper config**:
```toml
[strategy]
name = "sports_yes_composite"
capital_usd = 1000
max_position_usd = 50
max_open_positions = 10
cooldown_s = 60
```

---

## Strategy 2: Politics YES Composite — PROMOTE AFTER FIXES

| Metric | All YES | YES price≤0.80 |
|--------|---------|---------------|
| Fills | 262 | 125 |
| Hit Rate | 70.6% | 60.0% |
| Excess HR (vs 19% YES base) | +51.6pp | +41pp |

**Critical fix**: Drop NO direction entirely. Tick validation revealed NO excess = -5.0pp (vectorized claimed +14.5pp — a methodology artifact).

**Skeptic flags**:
- Add max_price=0.80 filter (44% of fills at >0.90 = low edge-per-dollar)
- Confirm train/test separation is clean (pool trained <2025-07-01, test ≥2025-07-01)
- -11pp degradation is below expected band — check for phantom signals

---

## Strategy 3: Crypto YES — DO NOT DEPLOY YET

| Metric | All Fills | Genuine (price<0.70) |
|--------|-----------|---------------------|
| Fills | 478 | 98 |
| HR | 82.6% | 45.9% |
| Excess HR | +67.5pp | +30.9pp |

**Blocker**: 67.5% of fills are at price=0.99 (in-play artifacts). Must rerun with max_price=0.65 filter built into the strategy. After filtering:
- Compounding score = 67.5 (strong)
- But November 2025 outlier = 47% of genuine PnL from 15 fills → underpowered
- No walk-forward validation (HR-only pools collapsed in fold 3 for other tags)

**Required before promotion**:
1. Rebuild with max_price=0.65 in strategy
2. Walk-forward validation (3 folds)
3. Re-validate with tick

---

## Cross-Strategy Portfolio

If Sports + Politics both promote to paper_dev:

| Portfolio | Monthly Signals | Expected Excess |
|-----------|----------------|-----------------|
| Sports YES only | ~87/month | +40pp |
| Politics YES only | ~33/month | +41pp (price≤0.80) |
| Combined | ~120/month | ~+40pp blended |

Diversification across 2 uncorrelated tags reduces regime risk. Crypto adds a third leg once max_price filter is validated.

---

## Scorecard Architecture (Final)

The research journey revealed:

| Component | Role | Status |
|-----------|------|--------|
| **excess_hr** (0.45) | Primary quality signal | Proven (IC=0.744) |
| **consistency_sharpe** (0.25) | Walk-forward stability | Proven (prevents pool collapse) |
| **avg_edge_usd** (0.15) | Profitability signal | Proven (composite traders 5-12x higher) |
| **bucket_excess_hr** (0.15) | Entry quality control | Useful in composite, NOT as gate |
| **calibration_gap** | Exclusion gate | **REJECTED** (hurts performance) |
| **Direction filter** | YES-only for Sports/Crypto | **CRITICAL** (biggest single improvement) |
| **max_price filter** | Remove in-play artifacts | **CRITICAL** for Crypto, useful for Politics |

---

## Immediate Action Items

### This week
1. Fix `ConsensusStrategy`: remove `min_hold_hours`, add `max_price` parameter
2. Rerun Crypto with max_price=0.65
3. Start Sports YES paper_dev at small size ($50/trade)

### Next 2 weeks
4. Politics YES paper_dev with max_price=0.80
5. Walk-forward validation for Crypto (3 folds)
6. Monitor paper results — compare to tick expectations

### Month 2
7. If paper_dev results within 10pp of tick → promote to paper_prod
8. Multi-tag portfolio (Sports + Politics)
9. Crypto promotion if walk-forward passes

---

## Risk Factors

| Risk | Mitigation |
|------|-----------|
| Regime shift (base rates change) | Rolling 30d base rate monitor, suspend if >12pp deviation |
| Pool decay (elite traders go inactive) | Composite ranking selects stable traders; retrain quarterly |
| In-play leakage (Sports) | N=3 consensus is natural filter; monitor hold time distribution |
| Thin edge at scale | Start small ($50/trade), increase only after paper_prod validation |

---

## Research Artifacts (Complete)

```
research/hypotheses/
├── trader-scorecard/            # Round 1: Scorecard fundamentals
├── scorecard-strategies/        # Round 2: V1 strategies (Politics NO survivor)
├── entry-price-quality/         # Round 3: Calibration gap research
└── scorecard-v2-strategies/     # Round 4: V2 strategies + tick validation
    ├── discovery/               # 4 vectorized sweeps
    ├── scripts/                 # Pool builders + validation scripts
    ├── validation/              # 3 tick results + skeptic + this doc
    └── synthesis.md             # Vectorized synthesis

research/strategies/
└── consensus_v2.py             # Strategy implementations

research/knowledge/
├── signals/entry_price_quality.md  # Calibration gap finding
├── signals/hr_persistence.md
├── signals/stability_bonus.md
├── signals/vol_weighted_direction.md
├── pitfalls/direction_decomposition.md
├── pitfalls/semi_tick_methodology.md
├── pitfalls/in_play_contamination.md
└── data/gambling_market_taxonomy.md
```
