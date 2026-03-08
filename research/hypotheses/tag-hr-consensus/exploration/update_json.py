"""Add summary to copy_trader_analysis.json."""
import json

with open("research/hypotheses/tag-hr-consensus/exploration/copy_trader_analysis.json") as f:
    data = json.load(f)

data["summary"] = {
    "hypothesis": "copy-trader contamination causes pool explosion and consensus failure",
    "verdict": "REJECTED",
    "key_findings": [
        {
            "id": 1,
            "finding": "Entry time clustering increases over time (Esports: 25% -> 58% within 30min)",
            "implication": "Copy behavior IS present and growing, but does NOT degrade signal quality",
        },
        {
            "id": 2,
            "finding": "Followers have HIGHER HR than leaders in ALL folds for BOTH tags",
            "implication": "Qualified pool filters already exclude bad copiers. Remaining followers are genuinely skilled.",
        },
        {
            "id": 3,
            "finding": "HR monotonically increases with entry order (1st: 47-52% -> 5th: 65-72%)",
            "implication": "Consensus depth IS the signal. More traders = stronger prediction.",
        },
        {
            "id": 4,
            "finding": "Temporal independence filter reduces sample 60-95% with LOWER HR",
            "implication": "Clustered entries are actually BETTER than independent ones.",
        },
        {
            "id": 5,
            "finding": "New pool members have equal/higher HR than returning members",
            "implication": "Pool explosion driven by genuine market growth, not copier invasion.",
        },
    ],
    "actual_failure_modes": [
        "Base rate non-stationarity (Esports 2025-10: 65.4% base destroys ALL signals)",
        "Fill price compression (avg fill ~0.45-0.55; break-even requires 50%+ HR)",
        "Sparse market coverage in large regimes (212 traders / 2379 markets = 0.089)",
    ],
    "proposed_fixes_all_rejected": [
        "First-mover filter: HARMFUL (later entrants have higher HR)",
        "Temporal independence: HARMFUL (reduces sample, no HR improvement)",
        "Leader-only pool: HARMFUL (leaders have lower HR than followers)",
        "Entry-order penalty: HARMFUL (penalizes the most informative entries)",
    ],
    "recommended_next_steps": [
        "Deep consensus (N>=4-5): +5-10pp excess over N>=3, 40-60% fewer signals",
        "Base rate regime gate: skip Esports when train_base > 0.50",
        "Consensus depth as continuous signal: weight position by n_qualified/5",
    ],
}

with open("research/hypotheses/tag-hr-consensus/exploration/copy_trader_analysis.json", "w") as f:
    json.dump(data, f, indent=2, default=str)
print("Updated copy_trader_analysis.json with summary")
