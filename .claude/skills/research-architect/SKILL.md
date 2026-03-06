---
name: research-architect
description: "Harness evolution methodology — config validation, degradation monitoring, incremental harness fixes. Used by the Architect agent."
user-invocable: false
---

# Architect Methodology

You own the production execution harness. Your job is to keep it accurate and evolve it incrementally.

## Owned Files

See `owned-files.md` for the complete list. You may ONLY modify files on that list.

## Config Validation

Before any validation run, review the hypothesis config.toml:

1. **Executor**: must be `"realistic"` for validation (not `"simulated"`)
2. **Settlement**: must be `true` (mid-replay settlement frees capital)
3. **Resolution source**: must be `"asset_id"` (never string matching)
4. **Bootstrap hours**: must be sufficient for the strategy's consensus window
5. **Walk-forward**: if enabled, verify train/test split is reasonable

Write config review to `validation/notes.md`.

## Degradation Monitoring

After validation results arrive, check the vectorized→tick-by-tick degradation band:

### Expected: 20-40pp
Normal simulation friction from:
- Realistic fill simulation (slippage, impact)
- Sequential trade processing (no look-ahead)
- Capital constraints (position limits, budget gates)
- Settlement timing

### Suspicious: <10pp
Likely look-ahead bias in the strategy. Investigate:
- Does the strategy use future data in feature computation?
- Is consensus counting trades instead of unique traders?
- Are resolution events leaking into pre-resolution features?

### Excessive: >40pp
Investigate harness fidelity (not strategy logic):
- Fill model too pessimistic? Check `calibrate_spreads()` output
- Capital constraints too tight? Check budget gate rejections
- Settlement bug? Check `n_settled` vs expected settlements
- Trade loading issue? Compare trade count in CH vs replay

## Investigation Protocol

When degradation is anomalous:

1. Read `validation/replay_log.jsonl` — look for rejection patterns
2. Read `validation/summary.json` — compare fills vs intents
3. Check capital utilization — are positions too large for budget?
4. Check fill prices — is slippage estimation realistic?
5. Document findings in `validation/notes.md`

## Evolution Rules

1. **Generic only** — changes must benefit ALL strategies
2. **Tests required** — `uv run pytest tests/ -x -q` after every change
3. **Type check** — `uv run mypy --strict <file>` on modified files
4. **Incremental** — small targeted fixes, not refactors
5. **No full rewrites** — unless absolutely necessary and user-approved
6. **Document** — write observations to hypothesis `validation/notes.md`
