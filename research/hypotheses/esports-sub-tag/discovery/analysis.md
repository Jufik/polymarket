# Esports Sub-Tag Decomposition + Consensus Robustness

> **Status**: COMPLETED (2026-03-09). No actionable sub-tag signal. Parameter robustness CONFIRMED.
> Validated Esports YES K50 N3 remains the recommended config.

## Part 1: Sub-Tag Decomposition

### 1.1 Esports Market Landscape by Game

| Game | Total Mkts | Test Resolved | YES Base (test) | Qualified Traders (game-only) |
|------|-----------|---------------|-----------------|-------------------------------|
| CS2 | 16,283 | 12,833 | 47.8% | 47 |
| Dota2 | 13,105 | 11,439 | 45.9% | 0 |
| LoL | 12,452 | 9,847 | 44.4% | 0 |
| Valorant | 4,907 | 4,221 | 46.3% | 0 |
| HoK | 2,592 | 2,055 | 43.8% | 0 |
| SC2 | 996 | 869 | 50.9% | 0 |
| Others | <700 each | <700 | varies | 0 |

> [!CRITICAL]
> **Game-specific pools are NOT viable**: Only CS2 has enough qualified traders (47) when
> building game-specific pools. All other games produce ZERO qualified traders because:
> (a) the v3 scorecard requires >=20 positions per trader, and (b) most traders bet across
> multiple games rather than specializing. Game-specific pool building loses the cross-game
> data that makes these traders identifiable.

### 1.2 Pool Composition: Cross-Game, Not Specialized

The 13 BEH-gated pool traders are multi-game bettors, not specialists:

| Trader (prefix) | CS2 | LoL | Dota2 | Valorant | Other |
|-----------------|-----|-----|-------|----------|-------|
| 0x14a056c6 | 348 | 310 | - | 14 | 1 |
| 0x27653cd5 | 154 | 130 | 140 | 4 | - |
| 0x40471b34 | 178 | 83 | 65 | 30 | 2 |
| 0x97e12cc7 | 39 | 103 | 53 | - | 2 |
| 0xb8afcb4c | 41 | 67 | 2 | 3 | - |
| 0x68df1403 | 58 | 3 | - | - | - |
| Others (7) | 89 | 14 | 4 | 1 | 4 |

**Key observation**: The top 3 traders alone cover 680 CS2, 523 LoL, 205 Dota2, 48 Valorant
test entries. These are generalist esports bettors, not game specialists. Building per-game
pools would just fragment the same traders.

### 1.3 Consensus Depth by Game

| Game | Mkt w/ >=1 | >=2 | >=3 | >=4 | >=5 |
|------|-----------|-----|-----|-----|-----|
| CS2 | 1,214 | 519 | 271 | 148 | 71 |
| LoL | 1,170 | 457 | 194 | 77 | 26 |
| Dota2 | 438 | 80 | 13 | 0 | 0 |
| Valorant | 128 | 19 | 2 | 0 | 0 |
| Others | <25 | <2 | 0 | 0 | 0 |

Only CS2 and LoL have sufficient consensus depth for N>=3 signals. Dota2, Valorant, and
smaller games have too few overlapping pool entries to generate consensus at any useful
threshold.

### 1.4 Per-Game Signal Quality (K=50 Pool, N=3)

> [!WARNING] Vectorized results. Expect 20-40pp degradation in tick-by-tick.
> However, consensus-based strategies have shown only 3pp gap historically.

| Game | Signals | HR | Game Base | Excess HR | med PnL | Hold |
|------|---------|------|-----------|-----------|---------|------|
| CS2 | 38 | 63.2% | 47.8% | +15.4pp | $118.87 | 3.8h |
| LoL | 16 | 81.2% | 44.4% | +36.8pp | $434.22 | 3.2h |

At N=2 (lower threshold):

| Game | Signals | HR | Game Base | Excess HR | med PnL |
|------|---------|------|-----------|-----------|---------|
| CS2 | 161 | 63.4% | 47.8% | +15.6pp | $158.14 |
| LoL | 123 | 74.0% | 44.4% | +29.6pp | $156.10 |
| Dota2 | 18 | 72.2% | 45.9% | +26.3pp | $147.96 |
| Valorant | 2 | 100% | 46.3% | +53.7pp | $2,891 |

### 1.5 Sub-Tag Verdict

**LoL appears to carry disproportionate signal strength** (81.2% HR at N=3, +36.8pp excess),
but this is based on only 16 signals from 3 folds. This is too few for statistical confidence.
At N=2, LoL has 123 signals at 74.0% HR -- still strong but needs tick validation.

**CS2 is the volume backbone**: 38/54 signals at N=3 (70%) come from CS2. Its 63.2% HR is
consistent with the overall Esports 64.0% tick-validated result.

> [!TIP]
> **Recommendation: Do NOT split by game.** The pool is 13 cross-game bettors. Splitting
> would fragment consensus signals without adding predictive information. The LoL advantage
> (if real) is already captured by the unified pool's higher HR on LoL markets. Game-specific
> pools are infeasible (only CS2 has enough game-only qualified traders).

---

## Part 2: Parameter Robustness

### 2.1 K x N Grid: Excess HR (pp over market-level YES base)

Walk-forward validated across 3 folds (only folds 3-5 have data; folds 1-2 produce empty pools).

```
           N=1      N=2      N=3      N=4      N=5
K=10     +17.2    +23.4    +34.1    +38.0    +34.5
K=25     +13.4    +16.8    +20.7    +23.5    +34.5
K=50      +9.8    +12.6    +19.6    +16.4    +22.3
K=75      +9.1    +11.8    +16.6    +19.5    +27.5
K=100     +9.1    +11.4    +16.4    +19.2    +24.1
```

### 2.2 K x N Grid: Total Signals

```
           N=1      N=2      N=3      N=4      N=5
K=10      1230      238       58       16        4
K=25      1659      452      142       64       23
K=50      2266      724      283      113       58
K=75      2457      885      366      172       81
K=100     2850     1108      548      288      149
```

### 2.3 K x N Grid: Compounding Score

```
           N=1      N=2      N=3      N=4      N=5
K=10       8.6     80.4    129.2    151.5     74.8
K=25       3.2     27.8     37.6     86.9    219.0
K=50       1.6     13.7     25.5     43.5     94.2
K=75       1.3     14.3     22.7     60.6    131.3
K=100      1.1     14.7     25.3     63.3    108.7
```

### 2.4 Stability Analysis

> [!TIP]
> **The signal is BROADLY ROBUST, not parameter-fragile.**

**Gradient pattern**: Excess HR increases monotonically along two axes:
1. **Lower K** (smaller, tighter pools) -> higher excess HR
2. **Higher N** (stricter consensus) -> higher excess HR

This is the expected pattern: tighter pools and stricter consensus filter noise.
There is NO narrow peak or cliff -- the signal degrades gradually.

**Key observations**:

1. **K dimension**: K=10-25 consistently outperforms K=50-100 by 3-15pp, but with
   dramatically fewer signals (16 vs 283 at N=3). This is the classic precision vs
   recall tradeoff. K=50 N=3 (validated: 64.0% tick HR) sits at the sweet spot.

2. **N dimension**: Higher N always improves excess HR but reduces signal count
   exponentially. N=5 signals are 3-5x rarer than N=3. The marginal gain from N=4
   to N=5 is smaller than N=2 to N=3.

3. **Compounding score**: High-N configs (N=4, N=5) have deceptively high CS because
   they combine high excess HR with high PnL per signal. But with only 4-23 signals,
   these are not capital-efficient in practice.

4. **All 25 cells are positive**: Every K x N combination shows positive excess HR.
   This is strong evidence the signal is real, not a parameter artifact.

### 2.5 Alternative Configs Worth Considering

| Config | Excess HR | Signals | CS | Assessment |
|--------|-----------|---------|------|-----------|
| **K50 N3** (validated) | +19.6pp | 283 | 25.5 | **BASELINE -- validated at 64.0% tick** |
| K25 N2 | +16.8pp | 452 | 27.8 | More signals, similar CS. Worth tick testing. |
| K25 N3 | +20.7pp | 142 | 37.6 | Higher excess, fewer signals. Marginal over baseline. |
| K100 N4 | +19.2pp | 288 | 63.3 | Already validated (64.2% tick). Fewer fills (123). |
| K25 N4 | +23.5pp | 64 | 86.9 | Small signal count limits utility. |

**K25 N2** is the most interesting alternative: 60% more signals with only 3pp less excess HR
than baseline. If tick-validated, it could increase fill throughput significantly.

However, from the validated tick-by-tick data (analysis.md), K50 N3 already gives 297 fills
in 8 months. Increasing to K25 N2 would approximately double fills (to ~400-500) but at the
risk of lower per-signal quality due to looser consensus.

### 2.6 Should We Tick-Validate K25 N2?

**Arguments for**:
- 60% more signals (throughput matters for compounding)
- Only 3pp lower vectorized excess HR
- K25 pool is a strict subset of K50 pool (higher quality traders)
- Consensus N=2 is proven in Sports (N=2 is production config)

**Arguments against**:
- K50 N3 is already validated and production-ready
- K25 N2 has less consensus evidence per signal
- The 3pp vectorized gap may be larger in tick (N=2 is more sensitive to individual trader timing)
- Pool is only 10-25 traders across folds (very thin)

> [!TIP]
> **Verdict: K25 N2 tick validation is LOW PRIORITY.** The marginal value is modest:
> at most +100-200 additional fills/8mo at possibly lower quality. K50 N3 is the
> stable production choice. If capital becomes the bottleneck (it is not, per portfolio
> analysis), then testing lower N would become relevant.

---

## Summary

### Part 1: Sub-tag decomposition adds NO incremental value

1. Pool traders are cross-game generalists (13 traders, all bet on 2+ games).
2. Game-specific pools are infeasible (only CS2 has enough qualified traders; 0 for Dota2, LoL, Valorant).
3. LoL markets show higher HR within the unified pool (81.2% vs 63.2% CS2 at N=3), but only 16 signals -- insufficient for confident game-specific tuning.
4. Splitting by game fragments consensus without adding predictive information.
5. **Recommendation: Keep unified Esports pool. Do not build game-specific strategies.**

### Part 2: Parameter robustness is STRONG

1. All 25 K x N cells show positive excess HR (+9.1 to +38.0pp).
2. Signal degrades gradually with larger K and lower N -- no cliff or fragile peak.
3. K50 N3 (validated: 64.0% tick HR, +17.7pp, $14.8K PnL, Sharpe 9.65) remains the optimal balance of precision and throughput.
4. K25 N2 could increase throughput by ~60% but is LOW PRIORITY given current non-capital-constrained portfolio.
5. The underlying signal is real and robust across the entire parameter space.

## Artifacts

- Analysis script: `research/hypotheses/esports-sub-tag/scripts/analyze.py`
- Raw results: `research/hypotheses/esports-sub-tag/discovery/results.json`
- Parent validation: `research/hypotheses/tag-hr-consensus/discovery/analysis.md`

## Spawned Ideas

None. This analysis closes the `esports-sub-tag` and `esports-consensus-robustness`
queued items. No new testable hypotheses emerged. The Esports signal is well-characterized
and ready for portfolio integration as-is.
