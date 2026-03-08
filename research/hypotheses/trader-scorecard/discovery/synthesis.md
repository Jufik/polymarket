# Trader Scorecard Synthesis

**Date**: 2026-03-07
**Status**: Discovery complete — vectorized upper bounds only
**Team**: 4 agents (hr-conviction, striking-stability, gambling-taxonomy, framework-design)

---

## Executive Summary

We explored 4 proposed trader scorecard metrics across 574K markets (29.9M positions) using DuckDB over the Parquet snapshot. The research produced several **surprises that reshape the scorecard design** from the original proposal.

### What Survived

| Metric | Verdict | Key Finding |
|--------|---------|-------------|
| **hit_rate** (unweighted) | PRIMARY SIGNAL | IC=0.74 train→test. Naive HR beats all weighted variants. Trader skill is persistent, not streaky. |
| **stability_bonus** | STRONG GATE | Monotone D1=26%→D10=75% HR across deciles. Separates luck from skill. r=0.498 with HR. |
| **gambling exclusion** | CRITICAL FILTER | 169K markets (29.4%), 56% of positions, but only 13% of USD. Must exclude. |

### What Changed

| Original Metric | Revised To | Why |
|----------------|-----------|-----|
| **hit_rate_weighted** (λ=0.007) | **hit_rate_naive** (or λ≤0.003) | Recency weighting is counterproductive. Peak IC at λ=0.003 is only +0.0015 above naive. Skill persists over 3+ years. |
| **conviction** | **market-maker filter** (abs(net_usd)/volume ≥ 0.90) | Token conviction is degenerate (99.5% = 1.0). USDC proxy identifies market makers (HR=0.27) vs directional traders — useful as gate, not score. |
| **striking_score** | **avg_edge_usd** or sweet-spot filter [0.04, 0.20] | Inverted-U relationship with HR. High striking = over-aggression, not skill. Mid-range striking (deciles 3-5) optimal at 54-57% HR. |

### What's New

| Discovery | Impact |
|-----------|--------|
| **PnL IC ≈ 0.005** | HR does not predict PnL. Position sizing dominates. Scorecard needs edge_usd, not just accuracy. |
| **39% of YES positions priced <$0.10** | 1.6% HR — deep underdogs are noise. Entry price floor needed. |
| **Good traders gamble at 50% rate** | Top-tier informational traders also play updown markets. Exclusion is critical even for the best traders. |
| **158K gambling-only traders** | Vanish from informational scorecards (intended). |
| **Tag IC varies 0.67-0.87** | Crypto/Elections (0.87) = strong persistence. Sports (0.67) = weaker. Weight HR signal by tag reliability. |

---

## Recommended Scorecard Architecture (v1)

### Stage 1: Hard Gates (binary pass/fail)

| Gate | Threshold | Rationale |
|------|-----------|-----------|
| Gambling exclusion | `slug NOT LIKE '%updown%' AND NOT LIKE '%up-or-down%'` + crypto above/below | Removes 56% of positions, 13% USD |
| Market-maker exclusion | `avg(abs(net_usd)/volume) ≥ 0.90` | Removes round-trip traders (HR=0.27) |
| Minimum positions | ≥ 10 resolved in tag (non-gambling) | Statistical significance |
| Minimum excess HR | > 0pp above tag-specific base rate | Must show SOME edge |
| Minimum activity windows | ≥ 2 (60-day blocks) | Cannot assess consistency from one burst |
| Entry price floor | Exclude positions with entry price > 0.90 or < 0.05 | Removes gaming and deep-underdog noise |
| Recency | ≥ 1 position in last 90 days | Remove inactive traders |
| Bot guard | < 10,000 total positions | Filter automated MMs |

### Stage 2: Composite Score (for ranking)

```
composite(trader, tag) =
    0.45 * pctl(excess_hr_naive)
  + 0.25 * pctl(consistency_sharpe)
  + 0.20 * pctl(avg_edge_usd)
  + 0.10 * pctl(profit_factor)
```

Where:
- `excess_hr_naive = hit_rate - tag_base_rate` (unweighted — λ=0 is optimal)
- `consistency_sharpe = mean(excess_hr_per_window) / (std(excess_hr_per_window) + 0.05) * min(log(1+n_windows), 1.8)`
- `avg_edge_usd = mean(pnl_i | correct) * excess_hr` (expected dollar edge per position)
- `profit_factor = gross_profit / |gross_loss|`
- `pctl()` = percentile rank within tag cohort (≥20 traders, else fall back to global)

### Copy vs Consensus Weights

| Metric | Consensus (default) | Copy |
|--------|-------------------|------|
| excess_hr | 0.45 | 0.40 |
| consistency_sharpe | 0.25 | 0.15 |
| avg_edge_usd | 0.20 | 0.30 |
| profit_factor | 0.10 | 0.15 |

**Copy strategy WARNING**: Individual copy was rejected in prior research (67% vec → 46% tick). Use with extreme caution.

---

## Tag-Specific Design (Mandatory)

Tag-specific scorecards are **not optional**. Evidence:

| Tag | YES Base Rate | IC (HR persistence) | Median Hold |
|-----|--------------|--------------------|----|
| Elections | 16.2% | 0.874 | long |
| Crypto | 28.3% | 0.869 | 11d |
| Weather | 17.7% | 0.828 | — |
| Sports | 38.9% | 0.675 | 1.4d |
| Esports | 49.1% | — | 0.3d |

A single global base rate (38/62) would make Elections traders look terrible and Esports traders look average.

---

## Gambling Market Filter (Production-Ready)

### Simple (catches 97% of gambling positions)

```sql
WHERE condition_id NOT IN (
    SELECT condition_id FROM markets
    WHERE lower(slug) LIKE '%updown%' OR lower(slug) LIKE '%up-or-down%'
)
```

### Comprehensive (catches multistrike + crypto price levels)

```sql
CREATE OR REPLACE MACRO is_gambling_market(slug) AS (
    lower(slug) LIKE '%updown%'
    OR lower(slug) LIKE '%up-or-down%'
    OR (
        (lower(slug) LIKE '%-above-%' OR lower(slug) LIKE '%-below-%')
        AND (
            lower(slug) LIKE '%btc%' OR lower(slug) LIKE '%bitcoin%'
            OR lower(slug) LIKE '%eth%' OR lower(slug) LIKE '%ethereum%'
            OR lower(slug) LIKE '%xrp%' OR lower(slug) LIKE '%sol%'
            OR lower(slug) LIKE '%-close-%'
            OR lower(slug) LIKE '%tsla%' OR lower(slug) LIKE '%nvda%'
            OR lower(slug) LIKE '%aapl%' OR lower(slug) LIKE '%amzn%'
            OR slug ~ '[0-9]+k-' OR slug ~ '-[0-9]+pt'
        )
    )
);
```

### Impact

| Excluded | Count | % Total |
|----------|-------|---------|
| Markets | 169,074 | 29.4% |
| Positions | ~16.8M | 56.1% |
| USD Volume | $2.5B | 12.9% |
| Gambling-only traders | 158,744 | 16.4% of all traders |

---

## Key Numbers for Strategy Implementation

| Parameter | Value | Source |
|-----------|-------|--------|
| HR train→test IC | 0.744 (naive) | hr_conviction_analysis |
| Optimal λ | 0 (naive) or ≤0.003 | λ sweep: IC degrades above 0.003 |
| Top-decile test HR | 91.9% | train/test split |
| Bottom-decile test HR | 24.6% | train/test split |
| Stability D10 HR | 75.0% | stability decile analysis |
| Stability D1 HR | 26.3% | stability decile analysis |
| Striking sweet spot | [0.04, 0.20] | inverted-U finding |
| PnL IC from HR | 0.005 | near-zero — sizing dominates |
| Market-maker HR | 0.267 | USDC conviction < 0.1 |
| Gambling YES base rate | 49.4% (near-random) | gambling taxonomy |
| Informational YES base rate | 31.5% | gambling taxonomy |

---

## Open Questions for Next Phase

1. **Tick-by-tick validation**: All numbers are vectorized upper bounds. Expect 20-40pp degradation. Which metrics survive?
2. **Tag-specific λ**: Should Esports (fast-resolving) use faster decay than Politics (long-dated)?
3. **Conviction from raw trades**: Token conviction was degenerate from positions. With 134M raw trades (side field), can we compute true BUY/SELL conviction?
4. **Striking with real vol**: The 48h price window vol proxy was unavailable. Market_prices data would enable the original formula.
5. **Herding/co-occurrence**: v2 enhancement — weight down traders who always appear together.
6. **Multistrike markets**: 26K markets invisible to slug filter, only catchable via `Crypto Prices` tag. Need tag-based supplementary filter.
7. **Entry price floor calibration**: < $0.05 and > $0.90 — need to sweep exact thresholds.

---

## Artifacts

| File | Content |
|------|---------|
| `hr_conviction_analysis.md` | λ sweep, train/test IC, conviction proxy, per-tag IC |
| `striking_stability_analysis.md` | Entry price distributions, inverted-U, monthly stability deciles |
| `gambling_market_taxonomy.md` | Slug patterns, tag classification, trader crossover, filter SQL |
| `scorecard_framework.md` | Composition formula, normalization, failure modes, copy vs consensus |
| `synthesis.md` | This document |
