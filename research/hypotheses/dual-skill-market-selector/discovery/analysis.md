# Dual-Skill Market Selector: Analysis

> **TL;DR**: Dual-skill filtering is **NOT useful as a pre-filter** for existing consensus strategies.
> It captures 99.6% of Sports YES signals and slightly degrades Politics NO HR.
> As a standalone signal, dual-skill entry has near-zero predictive power (HR at base rate).

> [!WARNING] Vectorized results. All HR values are UPPER BOUNDS.
> These are position-level vectorized metrics, not tick-by-tick validated.

Date: 2026-03-09

## Hypothesis

964 traders (from prior decomposition study) have positive bucket-excess-HR on **both** YES and NO entries. These "dual-skill" traders are highly selective about which markets they enter. Their market entry could serve as a "market quality" pre-filter for existing consensus strategies.

## Population

Using BEH >= 0.02, >= 10 positions per direction, train cutoff 2025-07-01:

| Category | Count |
|----------|-------|
| YES-skilled only | 1,283 |
| NO-skilled only | 4,414 |
| **Dual-skilled** | **557** |

*Note: 557 vs the decomposition study's 964 due to stricter weighted BEH computation here (weighted by bucket count vs unweighted).*

Distribution:
- Median positions: 66 (min 20, max 3,472)
- Average YES BEH: 0.089, NO BEH: 0.081
- P25/P75 overall BEH: 0.052 / 0.105

## Finding 1: Dual-Skill Traders Select High-Volume Markets

Markets where dual-skill traders enter (OOS, Jul 2025+):

| Metric | Not entered | Dual entered |
|--------|------------|--------------|
| N markets | 187,545 | 65,292 |
| Avg volume | $4,841 | $129,532 |
| Avg traders | 17 | 98 |
| YES base rate | 38.1% | 37.3% |

**Per-tag:**

| Tag | Dual entered | N markets | Avg vol | Avg traders | YES BR |
|-----|-------------|-----------|---------|-------------|--------|
| Sports | No | 150,923 | $3,890 | 14 | 42.2% |
| Sports | Yes | 41,584 | $112,618 | 81 | 42.0% |
| Politics | No | 3,689 | $20,711 | 59 | 16.3% |
| Politics | Yes | 8,295 | $249,170 | 171 | 28.1% |
| Crypto | No | 16,038 | $10,776 | 23 | 21.8% |
| Crypto | Yes | 3,232 | $239,177 | 141 | 31.0% |
| Esports | No | 1,490 | $1,490 | 6 | 64.2% |
| Esports | Yes | 1,236 | $105,008 | 58 | 48.5% |

**Interpretation**: Dual-skill traders simply trade popular, liquid markets. They are **not** selecting markets with unusual resolution patterns — YES base rates are similar or even worse (Sports: 42.0% vs 42.2%). In Politics, dual-entered markets have higher YES rates (28.1% vs 16.3%), but this is driven by market composition, not prediction.

## Finding 2: Population HR Lift is Confounded by Market Size

Positions in dual-entered markets:

| Dual entered | Position | N positions | HR | Avg PnL |
|-------------|----------|-------------|-----|---------|
| No | YES | 995,404 | 26.0% | -$170 |
| No | NO | 865,636 | 38.2% | -$288 |
| Yes | YES | 1,423,894 | 41.7% | -$906 |
| Yes | NO | 1,464,359 | 56.6% | -$1,090 |

YES HR is higher on dual-entered markets (41.7% vs 26.0%) but this is because dual-entered = popular markets with more 50/50 odds. The avg PnL is **worse** (-$906 vs -$170) — larger positions lose more.

## Finding 3: Near-Zero Filtering Power for Sports YES

Sports YES consensus (K=25, N>=2), OOS:

| Filter | N markets | YES HR |
|--------|-----------|--------|
| All signals | 1,038 | **72.1%** |
| Dual-filtered | 1,034 | 72.0% |
| NOT dual-filtered | 4 | 100% (n=4) |

**99.6% of consensus signals already occur in dual-skill-entered markets.** The filter removes only 4 markets. This is because dual-skill traders enter 41,584 Sports markets out of ~192K — and the consensus pool's K=25 traders overwhelmingly trade in the same popular markets.

By consensus level:

| N | All | HR | Dual-filtered | HR |
|---|-----|-----|--------------|-----|
| 2 | 796 | 70.0% | 792 | 69.8% |
| 3 | 187 | 77.5% | 187 | 77.5% |
| 4 | 41 | 87.8% | 41 | 87.8% |
| 5 | 13 | 69.2% | 13 | 69.2% |

**Verdict: No signal. Filter passes everything.**

## Finding 4: Dual-Skill Filter HURTS Politics NO

Politics NO consensus (K=100, N>=2), OOS:

| Filter | N markets | NO HR |
|--------|-----------|-------|
| All signals | 880 | **88.6%** |
| Dual-filtered | 647 | 85.5% |
| NOT dual-filtered | 233 | **97.4%** |

Politics OOS NO base rate: **78.6%**

**The filter is ANTI-predictive.** Markets where dual-skill traders have NOT entered have a 97.4% NO HR (11.6pp above the dual-filtered set). This makes sense: dual-skill traders enter high-volume, contested markets (where outcomes are uncertain). Markets they avoid tend to be low-volume, obvious-resolution markets — exactly the ones where NO is near-certain.

By consensus level — the pattern holds at every N:

| N | All HR | Dual HR | No-dual HR |
|---|--------|---------|-----------|
| 2 | 82.6% | 80.7% | **93.4%** |
| 3 | 91.8% | 89.9% | **97.5%** |
| 4 | 89.4% | 84.8% | **100%** |
| 5 | 98.3% | 97.0% | **100%** |

## Finding 5: Dual-Skill Consensus Has No Predictive Power

Standalone dual-skill consensus (N dual-skill YES entries in market), Sports:

| N dual | Markets | YES HR |
|--------|---------|--------|
| 1 | 15,978 | 50.5% |
| 2 | 4,204 | 51.6% |
| 3 | 1,593 | 51.8% |
| 5 | 335 | 49.3% |
| 8 | 16 | 56.3% |

Sports OOS YES base rate: 40.7%

YES HR is above base rate but barely (50-52% vs 40.7%). This is a position-direction selection effect — they're picking YES in markets that have ~50% outcomes, not predicting winners. The signal is at the base rate of the **markets they choose to enter**, not the population base rate.

For Politics NO:

| N dual | Markets | NO HR |
|--------|---------|-------|
| 1 | 3,036 | 76.8% |
| 2 | 1,636 | 71.6% |
| 3 | 826 | 70.2% |
| 5 | 210 | 68.1% |

Politics OOS NO base rate: 78.6%. **Dual-skill NO consensus is BELOW base rate** — more dual-skill traders = lower HR. This is the "popular market" effect: contested markets attract more traders, and contested markets have lower base rates.

## Finding 6: Dual-Skill Traders Enter Early

For the 1,034 overlap markets (Sports YES consensus + dual-skill entered):
- Dual-skill entered first in **81.6%** of cases (844/1,034)
- Average 32.6 hours ahead of pool
- Median 12 hours ahead

This is interesting for execution timing but irrelevant since the filter has no predictive value.

## Finding 7: Hold Times Are Longer in Dual-Entered Markets

| Tag | Dual | Avg hold | Med hold |
|-----|------|----------|----------|
| Sports | No | 2.3d | 1d |
| Sports | Yes | 7.4d | 2d |
| Politics | No | 25.7d | 7d |
| Politics | Yes | 28.8d | 8d |

Dual-entered markets take longer to resolve — consistent with "popular, contested" markets having less clear-cut outcomes.

## BEH Threshold Sensitivity

| BEH threshold | N traders |
|---------------|-----------|
| >= 0.02 | 557 |
| >= 0.05 | 243 |
| >= 0.10 | 59 |
| >= 0.15 | 8 |
| >= 0.20 | 3 |

Tighter thresholds produce too few traders to be useful as a market selector. At BEH >= 0.10, only 59 traders enter far fewer markets.

## Verdict: REJECTED

The dual-skill market selector hypothesis fails on all fronts:

1. **Sports YES**: Filter passes 99.6% of signals (no discrimination power)
2. **Politics NO**: Filter is anti-predictive (-12pp HR vs unfiltered)
3. **Standalone signal**: Near base-rate HR (no predictive power)
4. **Root cause**: Dual-skill = active traders who trade popular markets. Their market selection is a proxy for market popularity, not market quality.

> [!CRITICAL]
> "Market quality" via dual-skill entry is confounded with market popularity/volume.
> Popular markets attract more traders (including dual-skill), but popular markets
> have more balanced outcomes and lower directional HR — the opposite of what we want.

## Implications

- **Do not use dual-skill entry as a pre-filter for consensus strategies.**
- The finding that "NOT dual-filtered" markets have HIGHER NO HR in Politics suggests an inverse strategy could work: **prefer low-volume, low-activity markets for NO bets.** But this is just restating the well-known "quiet market = easy market" heuristic.
- The 557 dual-skill traders themselves are not a useful consensus pool — their entry direction has near-zero predictive power beyond the market's own base rate.

## Scripts

- Analysis: `research/hypotheses/dual-skill-market-selector/discovery/analyze_v2.py`
- Data: DuckDB over Parquet snapshot (`research/db.py`)
