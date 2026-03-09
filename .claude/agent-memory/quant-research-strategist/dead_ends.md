# Dead Ends (full list)

- **Dual-skill market selector** (2026-03-09): 557 dual-skill traders (BEH>=0.02 both YES+NO). Filter passes 99.6% of Sports YES signals (zero discrimination). ANTI-PREDICTIVE for Politics NO (-12pp). Root cause: dual-skill = popular market proxy, not quality signal.
- **Copy-trader contamination**: all 4 proposed fixes (first-mover, independence, leader-only, order-penalty) HARMFUL.
- **S2 HRC Gap Fixes (dedup+cap+direction)**: 51.2% avg HR at C>=3, -$7,185. C>=4 marginal (+$2,275). Dedup hurts.
- **S2 Tag-Aware Hit-Rate Copy**: 46.7% HR tick (WORSE than global 50.6%). Tag-specific base too permissive.
- **S2 Hit-Rate Copy (BOTH direction)**: 45.9-50.6% HR tick-by-tick (base rate), negative PnL. NO direction kills it.
- **Crypto insider copy**: negative PnL at all consensus/price params despite 79-85% HR (vectorized). Tick: 55.7% HR, -20.5pp NO excess.
- **Esports insider copy (S2)**: -$185 to -$1238/pos vectorized; tick: 54.3% HR, $5K total PnL (marginal at best)
- **Culture/weather insider excess HR**: negative despite 70%+ absolute HR. PnL from price asymmetry, not alpha.
- **S2 HRC position dedup**: -2.6pp HR. Ongoing conviction from repeat trades is informative. See `pitfalls/dedup_counterproductive.md`
- **Tennis NO consensus (tag-hr-consensus)**: 56.0% HR vs 56.9% market NO base = zero edge. Position-level base (36.5%) was misleading.
- **Tennis YES consensus**: barely above base rate at all pool sizes. Not viable.
- **tag-hr-copy (individual signal)**: Esports 45.8%, 1H 49.8%, Tennis 40.6% tick HR. Consensus was the signal, not individual trades.
