# Tag Scan Jan 2026 — Key Findings (2026-03-07)

## Setup
- Script: `research/hypotheses/scorecard-v2-strategies/scripts/tag_scan_jan2026.py`
- Train cutoff: 2026-01-01 | Test: Jan 2026 only (2026-01-01 to 2026-02-01)
- 19 tags qualifying (vs 14 in July 2025 scan) — threshold relaxed to 30 test / 50 train

## Tick-Validated Results (Jan 2026)

| Tag | Vec Signals | Tick HR | Tick Excess | Tick PnL | Status |
|-----|------------|---------|------------|----------|--------|
| Sports | 31 | 73.9% | +41.3pp | $-353 | monitor |
| Crypto | 31 | 80.0% | +53.8pp | $-492 | monitor |
| Finance | 27 | 81.8% | **+64.8pp** | $-124 | investigate (price gating needed) |
| Weather | 11 | 100.0% | +88.6pp | $+113 | IN-PLAY CONTAMINATED |

## Weather — Third In-Play Contamination Case
- All 5 signals: city temperature markets ("highest temp in Seoul today >= 4°C?")
- All resolve same calendar day. Hold: 1-3 hours. 60% hold <= 1h.
- Traders watch public weather APIs during resolution window — NOT advance prediction.
- Pattern identical to Awards (ceremony watching) and Trump (speech watching).
- Weather joins Awards + Trump as confirmed in-play contaminated tags.

## Finance — NEW Viable Tag
- First time qualifying — only 68 training markets in July 2025 scan, now 2,395.
- Signal composition: quarterly earnings (IBM, JPM, TSM...) + stock price range markets.
- Hold times: 3-135h (genuine pre-event domain, not in-play).
- Negative PnL despite 81.8% HR = high fill price problem.
  - Finance YES at consensus point often priced 0.85-0.95 (already near certain).
  - At p=0.90 break-even requires HR > 90%. At p=0.70 break-even requires HR > 70%.
  - Need max_price=0.70-0.75 gating for positive PnL.
- **Next step**: Re-run tick validation with max_price=0.75.

## PnL vs HR Mismatch — General Pattern
- High HR + negative PnL = consensus fires AFTER price moved to 0.85+.
- Sharp traders move price first, pool consensus is 2nd/3rd → enters at high price.
- Fix: max_price gate (0.65-0.75 depending on tag base rate).
- This affects Sports and Crypto too — not a Finance-specific issue.

## Esports Update
- Training markets: 1,973 (was 42 in July 2025 — 47x growth). Pool now buildable.
- Jan 2026: only 46 markets, 0 signals. January is Esports off-season.
- Re-scan recommended: April 2026 (spring season: CS2, LoL, Valorant majors).

## Tags Still Too Thin (high vec HR, very few signals)
- Politics: 7 signals, 100% HR — needs larger test window
- Movies: 5 signals, 64.3% HR — marginal tag with thin January volume
- Science/AI/Elon Musk: 1-2 signals, >78% HR — random at N=1-2, ignore

## DuckDB Macro Naming
- Used suffix `_jan` on macro name to avoid collision with tag_scan.py's `is_gambling_market`.
- Pattern: `CREATE OR REPLACE MACRO is_gambling_market_jan(slug)` — always use unique names in scripts that may run concurrently or in same session.
