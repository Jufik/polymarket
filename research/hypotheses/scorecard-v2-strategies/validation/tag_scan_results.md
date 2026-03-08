# Tag Scan Results — All Polymarket Tags

**Date**: 2026-03-07
**Method**: Vectorized UPPER BOUNDS (expect 20-40pp tick degradation)
**Train cutoff**: 2025-07-01 | **Test start**: 2025-07-01
**Pool**: Top-K=25 composite score | **Consensus**: N=2
**Filter**: ≥50 test markets, ≥100 train markets

---

## Summary

- Total tags in universe: 220
- Tags meeting data threshold (≥50 test markets, ≥100 train): **14**
- VIABLE by raw vectorized metric (excess_hr ≥ 30pp, n_signals ≥ 5): 5
- After in-play contamination review: **3 genuine** (Sports, Politics, Crypto)
- **New viable tags beyond known three: 0**

> [!CRITICAL]
> These are VECTORIZED upper bounds. Real tick-validated results will be 20-40pp lower.
> Only tags with excess_hr ≥ 30pp (vectorized) are expected to yield ≥10pp after tick validation.
> Known validated: Sports +39.8pp, Politics +41pp, Crypto +37.4pp.

---

## VIABLE Tags (excess_hr >= 30pp, vectorized UB)

| Rank | Tag | Test Markets | Pool | Signals | HR | Base | Excess | Hold (d) | Rank Score | Status |
|------|-----|-------------|------|---------|-----|------|--------|----------|------------|--------|
| 1 | **Crypto** | 16,748 | 25 | 290 | 93.5% | 15.0% | **+78.4%** | 0.0 | 13.35 | GENUINE |
| 2 | **Sports** | 161,662 | 25 | 571 | 88.8% | 33.2% | **+55.5%** | 0.0 | 13.27 | GENUINE |
| 3 | **Politics** | 11,897 | 25 | 84 | 77.4% | 19.0% | **+58.4%** | 1.0 | 5.35 | GENUINE |
| 4 | ~~Trump~~ | 366 | 25 | 35 | 80.0% | 26.8% | +53.2% | 0.0 | 3.15 | IN-PLAY |
| 5 | ~~Awards~~ | 2,430 | 25 | 28 | 71.4% | 12.2% | +59.2% | 0.0 | 3.14 | IN-PLAY |

> [!WARNING]
> **Trump** and **Awards** are in-play contaminated (see analysis below).
> Their 0-hold signals fire during live events — the "edge" is watching the event unfold.
> Do NOT proceed to tick validation without same-day exclusion filtering.

---

## MARGINAL Tags (10pp ≤ excess_hr < 30pp)

| Rank | Tag | Test Markets | Pool | Signals | HR | Base | Excess | Hold (d) |
|------|-----|-------------|------|---------|-----|------|--------|----------|
| 1 | Music | 998 | 25 | 10 | 40.0% | 11.8% | +28.2% | 4.0 |
| 2 | Business | 527 | 25 | 11 | 36.4% | 13.4% | +23.0% | 25.0 |
| 3 | Movies | 1,303 | 25 | 42 | 33.3% | 15.0% | +18.3% | 3.0 |

All three marginals are below the 30pp threshold needed to survive tick degradation (~10pp expected post-tick).

---

## In-Play Contamination Analysis

### Awards — IN-PLAY CONTAMINATED

All 15 zero-hold signals trace to live ceremony events:

| Sample signal | Entry Time (UTC) | Resolve (UTC) | Won | Notes |
|---------------|-----------------|--------------|-----|-------|
| Emmy: Seth Rogen lead actor | 2025-09-15 00:09 | 03:33 | 1 | Emmy ceremony starts 8pm ET |
| Emmy: The Studio comedy series | 2025-09-15 00:19 | 06:15 | 1 | Mid-ceremony entries |
| Emmy: The Penguin limited series | 2025-09-15 01:08 | 06:06 | 0 | 1h into broadcast |
| Nobel Literature winner | 2025-10-09 11:01 | 14:24 | 1 | During press announcement |
| Oscar nominations | 2026-01-22 13:41-51 | 15:51-16:52 | Mixed | During nomination broadcast |
| Grammy/Brit winners | 2026-02-02-16 | 3-4h later | Mixed | Live ceremony |

**Root cause**: Pool traders enter during live award broadcasts, before Polymarket resolves each category. They watch Emmy/Oscar/Grammy ceremonies and bet on winners as they are announced. A copy strategy would fire on the 2nd trade — but by then the winner is already live on TV and the market is heavily skewed.

**Signal quality**: The 73.3% HR on 0-hold signals is explained entirely by insider/live-viewer trading. NOT a predictive signal — it's contemporaneous information, not advance prediction.

### Trump "Will he say X" — PARTIALLY IN-PLAY CONTAMINATED

60% of signals (21/35) resolve same calendar day. Timing analysis:

| Signal | Entry (UTC) | Resolution (UTC) | Gap | Status |
|--------|------------|-----------------|-----|--------|
| Will Trump say TARIFF (energy summit) | 2025-07-15 14:23 | 21:56 | 7.5h | Pre-speech |
| Will Trump say FRACK (energy summit) | 16:25 | 22:40 | 6.2h | Pre-speech |
| Will Trump say AI (energy summit) | 18:19 | 21:47 | 3.5h | Mid-speech |
| Will Trump say DRILL BABY DRILL | 19:24 | 21:46 | 2.3h | Mid-speech |
| Will Trump say JOB 7 TIMES | 20:03 | 22:42 | 2.6h | Mid-speech |
| Will Trump say MILLION/BILLION 15X | 22:17 | 22:26 | 9 min | End of speech |

Multiple signals fire after Trump has started speaking. The pool trades into Trump-word markets based on what they're hearing during the speech — not advance prediction.

**With same-day exclusion**: Only 40% of signals remain (14/35), estimated HR drops to ~60% (vs 80% with in-play). At 14 signals over 8 months (~1.75/month), too thin for reliable tick validation.

---

## Full Tag Universe Overview

All 14 tags meeting data threshold, sorted by test market count:

| Tag | Train Mkts | Train Traders | Train Base | Test Mkts | Test Base | Signals | Excess | Status |
|-----|-----------|---------------|-----------|----------|----------|---------|--------|--------|
| Sports | 18,331 | 192,644 | 0.238 | 161,662 | 0.332 | 571 | +55.5% | VIABLE |
| Crypto | 2,039 | 65,622 | 0.175 | 16,748 | 0.150 | 290 | +78.4% | VIABLE |
| Politics | 9,264 | 178,165 | 0.264 | 11,897 | 0.190 | 84 | +58.4% | VIABLE |
| Weather | 2,229 | 8,954 | 0.132 | 8,755 | 0.117 | 153 | +9.2% | weak |
| Awards | 538 | 8,992 | 0.149 | 2,430 | 0.122 | 28 | +59.2% | IN-PLAY |
| Movies | 402 | 10,521 | 0.154 | 1,303 | 0.150 | 42 | +18.3% | marginal |
| Culture | 312 | 7,181 | 0.235 | 1,259 | 0.112 | 0 | -11.2% | weak |
| Music | 317 | 22,440 | 0.109 | 998 | 0.118 | 10 | +28.2% | marginal |
| Business | 468 | 20,642 | 0.160 | 527 | 0.134 | 11 | +23.0% | marginal |
| Science | 208 | 8,898 | 0.152 | 426 | 0.244 | 0 | -24.4% | weak |
| Trump | 398 | 7,815 | 0.190 | 366 | 0.268 | 35 | +53.2% | IN-PLAY |
| Elon Musk | 301 | 11,630 | 0.084 | 315 | 0.200 | 20 | -15.0% | weak |
| box office | 152 | 4,132 | 0.225 | 292 | 0.147 | 2 | +35.3% | too thin |
| NFL | 180 | 833 | 0.434 | 54 | 0.287 | 0 | -28.7% | weak |

---

## Notes on Known Tags vs Scan Results

### Sports (K=25, N=2 vectorized here vs K=25, N=3 validated)
- Vectorized excess HR here: **+55.5%** (K=25, N=2)
- Tick-validated result: **+39.8pp** (K=25, N=3)
- Looser consensus (N=2 vs N=3) inflates vectorized number; not a fair comparison
- The 0-hold median reflects same-day sports markets — legitimate (bet placed morning, game resolves evening)

### Crypto (K=25, N=2 vectorized here vs K=50, N=2 validated)
- Vectorized excess HR here: **+78.4%** (K=25, N=2)
- Previous tick-validated: **+37.4pp** (K=50, N=2)
- K=25 (tighter pool) gives higher vectorized signal; need tick validation at K=25

### Politics (K=25, N=2 vectorized here vs K=100, N=5 validated)
- Vectorized excess HR here: **+58.4%** (K=25, N=2)
- Previous tick-validated: **+41pp** (K=100, N=5)
- These are different configurations; both directions remain valid
- Training base rate (26.4%) shifted to test base rate (19.0%): test period has fewer YES wins

---

## All-Tags Universe (top 50 by test market count)

Tags with <50 test markets or <100 train markets were excluded from pool-building.

| Tag | Train Mkts | Test Mkts | Test Base HR |
|-----|-----------|----------|--------------|
| Sports | 18,331 | 161,662 | 0.332 |
| Crypto | 2,039 | 16,748 | 0.150 |
| Politics | 9,264 | 11,897 | 0.190 |
| Weather | 2,229 | 8,755 | 0.117 |
| Finance | 68 | 5,363 | 0.191 |
| Awards | 538 | 2,430 | 0.122 |
| Esports | 42 | 1,977 | 0.443 |
| Movies | 402 | 1,303 | 0.150 |
| Culture | 312 | 1,259 | 0.112 |
| Music | 317 | 998 | 0.118 |
| MrBeast | 33 | 567 | 0.205 |
| Business | 468 | 527 | 0.134 |
| AI | 21 | 478 | 0.175 |
| Science | 208 | 426 | 0.244 |
| Trump | 398 | 366 | 0.268 |
| Elon Musk | 301 | 315 | 0.200 |
| box office | 152 | 292 | 0.147 |
| Elections | 59 | 245 | 0.147 |
| YouTube | 29 | 244 | 0.169 |
| Inflation | 42 | 209 | 0.174 |
| Economy | 61 | 185 | 0.208 |
| Celebrities | 22 | 135 | 0.072 |
| App Store | 0 | 119 | 0.106 |
| Fed | 19 | 89 | 0.403 |
| GDP | 0 | 71 | 0.052 |
| Iran | 20 | 70 | 0.217 |
| Middle East | 11 | 69 | 0.270 |
| Earnings | 1 | 66 | 0.682 |
| SpaceX | 46 | 60 | 0.167 |
| NFL | 180 | 54 | 0.287 |
| Prediction Markets | 0 | 50 | 0.399 |

Note: Esports has 1,977 test markets but only 42 training markets (insufficient for pool building under current 100-market threshold). This is a tag that appeared primarily in the test window — worth monitoring as more training data accumulates.

---

## Key Conclusion: No New Tags Discovered

The 220-tag universe confirms that beyond Sports, Politics, and Crypto, **no tag has sufficient data depth and genuine (non-in-play) signal** for the composite scorecard methodology.

The distribution:
- **3 viable tags** (Sports, Politics, Crypto): 2K–18K training markets, genuine pre-event information edge, tick-validated
- **2 in-play contaminated** (Awards, Trump): High vectorized HR driven by live-event watching, not advance prediction
- **3 marginal** (Music, Business, Movies): Below 30pp vectorized threshold; will not survive 20-40pp tick degradation
- **206 tags** with <50 test markets or <100 train markets: Insufficient data volume

---

## Recommendations

### 1. Do NOT pursue Awards or Trump consensus strategies
Both show high vectorized HR but the signals fire during live events.
A copy strategy enters AFTER the informational event (Emmy category announced, Trump speaking).
The market price already reflects the outcome by the time a copy order could execute.

### 2. Sports, Politics, Crypto are the complete viable universe
These three tags have:
- Sufficient data depth (2K+ training markets, 65K+ training traders)
- Genuine pre-event information edge
- Tick-validated confirmed results from prior research

### 3. Watch Esports as training data grows
Esports has 1,977 test markets but only 42 training markets (tag emerged post-cutoff).
Re-run scan after 2026-07-01 to check if Esports has enough training history by then.

### 4. Trump with same-day exclusion: not worth pursuing
Filtering to multi-day-hold signals leaves only ~14 signals over 8 months.
Insufficient volume for reliable tick validation or production deployment.

---

## Methodology Notes

- **Pool building**: Top-K=25 by composite score (0.45*excess_hr + 0.25*consistency_sharpe + 0.15*avg_edge_usd + 0.15*bucket_excess_hr)
- **Min trader qualifications**: ≥10 markets in training (relaxed from production ≥20), conviction ≥0.90, <10000 trades (bot filter)
- **Signal**: N=2 distinct pool traders enter YES in the same market during test period (test_start = first_trade date filter applied)
- **Hold time**: date_diff(day, max(first_trade), resolved_at) — market level, not trader level
- **Counting**: MARKET level (distinct condition_ids), not trader-position level
- **Rank score**: excess_hr × sqrt(n_signals) — balances edge with volume
- **Vectorized bias**: ~20-40pp excess HR will be lost in tick-by-tick validation
- **Tick validation threshold**: Only recommend if excess_hr ≥ 30pp (vectorized)
