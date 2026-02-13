# Analysis: Initial Skilled Traders

**Stage ID:** `00_initial`
**Path:** Initial Skilled Traders
**Generated:** 2026-02-13 07:37 UTC
**Confidence:** 45%

## Summary

Core counts validated (238,953 traders, $8.6B volume match exactly), but the initial exploration has critical flaws: (1) it lacks actual PnL computation — proper token-aware PnL shows only 47% of $100K+ traders are profitable with negative average PnL (-$88K), invalidating volume-based 'skill' identification; (2) the top volume traders (highlighted as top-20) are actually the biggest losers (market makers losing $35-46M each); (3) the 'cheap_buys'/'expensive_sells' features conflate BUY/SELL side with YES/NO token direction.

## Key Insights

- Volume is anti-correlated with profitability: the top-2 traders by volume ($124M and $97M) have the worst PnL (-$35M and -$46M respectively) — they are market makers, not skilled predictors
- With proper YES/NO token-aware PnL, only 47% of $100K+ volume traders are profitable (median PnL: -$1,485), vs the naive 70% estimate that ignored token direction
- 52,516 traders (22% of 239K) have avg trade size under $5, likely bots or automated market makers that contaminate any skill signal
- Only 21 of the top-100 volume traders maintain their rank across all three bi-monthly periods (Aug-Sep, Oct-Nov, Dec-Jan), showing very low rank stability
- 402,460 taker-only traders (with 39.7M trades) are completely excluded from the maker-only analysis, missing a large population of potentially skilled directional traders
- BUY trades outnumber SELL 4:1 (136M vs 35M), creating strong bias in the cheap_buys feature which doesn't distinguish YES from NO tokens

## Concerns

- CRITICAL: The 'unique_markets' metric of 63,143 is actually the MAX per-trader value, not the global count (actual: 245,029) — misleading summary statistic
- CRITICAL: No PnL or profitability metric exists in the initial stage — volume tiers and activity tiers cannot distinguish skilled traders from market makers or noise traders
- The cheap_buys/expensive_sells features are computed without token direction (YES/NO), so a BUY of a NO token at 0.45 is counted as a 'cheap buy' even though the trader is effectively shorting the event at 0.55
- Resolution inference via last-trade-price is imperfect: ~10K markets (4.3%) have ambiguous last prices (0.05-0.95), and closed_at/resolved_at fields are all NULL in the markets table
- Quantile values differ slightly between reported and ClickHouse validation (p50: 791 vs 786, p95: 35042 vs 38439), likely Float32 precision artifacts but worth noting
- The lookback period (Aug 2025 - Jan 2026) is only 6 months; survivorship bias affects traders who started early in the period vs those who joined later

## Proposed Refinements

### 1. pnl_based_skill_scoring
**Type:** feature | **Priority:** 1/5 | **Complexity:** 4/5

Replace volume-based skill identification with proper PnL computation using token_market_map for YES/NO direction and inferred resolution from last trade prices on closed markets

**Hypothesis:** Token-aware PnL will identify a distinct set of 'skilled' traders who differ from high-volume traders, with top PnL traders showing consistent positive returns across time periods

**Expected Outcome:** Identify 200-500 traders with statistically significant positive PnL (>2 std above random), with at least 50% maintaining positive PnL across 2+ sub-periods

### 2. bot_and_mm_filter
**Type:** filter | **Priority:** 2/5 | **Complexity:** 2/5

Classify and filter out market makers and bots before skill analysis using trade pattern signatures (tiny avg size, extreme trade frequency, symmetric BUY/SELL ratios)

**Hypothesis:** Removing traders with avg_trade_size < $5 or trade_count > 500K (who account for 22% of addresses but are predominantly automated) will improve signal-to-noise in skill detection

**Expected Outcome:** Reduce trader universe by 50-60K addresses while retaining 95%+ of total PnL variance; remaining traders show clearer skill separation

### 3. temporal_consistency_test
**Type:** hypothesis | **Priority:** 2/5 | **Complexity:** 3/5

Split the 6-month period into 3 non-overlapping windows and measure PnL rank correlation (Spearman) across periods to test if skill persists

**Hypothesis:** If trader skill is real and persistent, the Spearman rank correlation of PnL across consecutive 2-month periods should be >0.3 (vs ~0 for random); only 21/100 top-volume traders are stable, but PnL-ranked traders may show higher stability

**Expected Outcome:** Spearman rho > 0.3 between adjacent periods for PnL-ranked traders, identifying a stable core of 50-200 consistently profitable traders

### 4. taker_side_analysis
**Type:** feature | **Priority:** 3/5 | **Complexity:** 2/5

Include taker-side trades in skill analysis since 402K taker-only traders with 39.7M trades are completely excluded from the current maker-only view

**Hypothesis:** Taker-side traders who aggressively cross the spread may include informed traders with superior information, showing higher PnL per trade despite paying the spread

**Expected Outcome:** Discover a meaningful subset of taker-heavy traders with positive PnL, potentially with different skill signatures (event timing, market selection) than maker-side traders

### 5. market_category_specialization
**Type:** hypothesis | **Priority:** 3/5 | **Complexity:** 3/5

Test whether skilled traders specialize in specific market categories (sports, politics, crypto, etc.) and whether category-specific skill is more persistent than overall skill

**Hypothesis:** Traders who concentrate in 1-2 market categories will show higher and more persistent PnL than diversified traders, because domain expertise is category-specific

**Expected Outcome:** Identify category-specialist traders with >60% of volume in one category and significantly higher PnL/dollar than diversified peers

## Exploration Tree

```mermaid
graph TD
    00_initial["Initial Skilled Traders"]:::reviewing

    classDef pending fill:#f9f,stroke:#333
    classDef running fill:#ff9,stroke:#333
    classDef completed fill:#9f9,stroke:#333
    classDef failed fill:#f99,stroke:#333
    classDef reviewing fill:#99f,stroke:#333
    classDef archived fill:#999,stroke:#333
```
