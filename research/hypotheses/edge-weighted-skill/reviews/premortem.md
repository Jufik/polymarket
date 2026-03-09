# Pre-Mortem: Edge-Weighted Skill Scoring

**Scope**: hypothesis framing only — no code or data reviewed.

## Against CRITICAL Knowledge

### 1. Elite Copy (Track A) — largely safe

The framing correctly cites the validated top-100 in-play result (94.2% HR, $52,932/month, 3pp degradation). The CRITICAL note in `signals/in_play_elite_traders.md` that elite traders LEAD by 58 minutes and require real-time wallet monitoring — not consensus — is acknowledged in Axis 5 ("real-time wallet following"). The longshot focus (<0.30) is correctly prioritized over the dead 0.95-0.97 zone.

### 2. Consensus Pool (Track B) — framing conflict

> [!CRITICAL]
> Track B frames consensus pooling (N=2-3 traders agree) as applicable to mid-price (0.30-0.85) entries. But `data/price_level_base_rates.md` shows the 0.30-0.50 bucket has a **-11.7pp structural headwind** and 0.50-0.70 has **-7.9pp**. The composite scorecard (`signals/composite_scorecard.md`) already validated this regime at K=25/N=3 for Sports (+39.8pp excess) and K=100/N=5 for Politics (+41pp). Track B as described is largely a re-discovery of prior validated work, not a new hypothesis — the primary risk is duplicating effort without adding new signal.

### 3. Bucket-excess-HR as PRIMARY signal — tested claim

> [!WARNING]
> The scorecard already uses `bucket_excess_hr` at 0.15 weight and found it has IC=+0.918 within price buckets but is a secondary stabilizer, not the strongest primary. The existing composite uses `excess_hr` at 0.45 as primary. Elevating `bucket_excess_hr` to primary is an untested inversion — the 7% overlap finding (HR-ranked vs edge-ranked top-200) is compelling but may reflect the 0.30-0.70 structural headwind misleading edge scores, not genuine skill identification.

### 4. Walk-forward look-ahead (CRITICAL known failure mode)

> [!CRITICAL]
> Axis 4 (time stability, sliding window) must implement `train_end = fold_test_start` per-fold, not `datetime.now()`. This exact bug corrupted 42% of tag-hr-copy validation signals (`pitfalls/training_window_lookahead.md`). The framing does not mention this guard explicitly — it must be a first-class implementation requirement.

### 5. No contradictions with other CRITICAL items

Direction decomposition is correctly mandated (Axis 3). Phantom test signals (`first_trade >= test_start`) are listed in the framing's known CRITICAL items. Consensus dedup (unique traders) is referenced. Tags-not-categories is acknowledged.

## Fundamental Flaw Check

None found. The two-track structure (RT copy vs consensus pool) is sound. The concern is Track B overlaps extensively with already-validated composite scorecard work.

## Obvious Confounders Missing

> [!TIP]
> The 3D decomposition (direction x price x tag) will produce very thin cells. A trader with 20 positions split across 5 tags, 2 directions, and 5 price buckets has on average 2 positions per cell — far below the 50-position minimum for reliable excess HR estimation. The framing should specify minimum cell occupancy before a bucket-specific score is credible.

## Verdict

**Conditional greenlight on Track A (RT copy, longshot elite).** Track B needs sharper differentiation from the already-validated composite scorecard to justify the research cost. The walk-forward look-ahead guard must be made explicit in implementation before any sweep runs.
