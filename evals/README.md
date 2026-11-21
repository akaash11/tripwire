# Red-team harness (owner: Tanu)

Output: `evals/out/results.json` (gitignored while iterating; commit a final copy for judges).

```json
{"run_id": "", "generator_model": "gemini-4-carbon", "n_attacks": 0, "n_legit": 0,
 "arms": {
   "no_guard":   {"attack_success": 0, "attack_rate": 0.0, "false_positives": 0, "fp_rate": 0.0},
   "rules_only": {"attack_success": 0, "attack_rate": 0.0, "false_positives": 0, "fp_rate": 0.0},
   "tripwire":   {"attack_success": 0, "attack_rate": 0.0, "false_positives": 0, "fp_rate": 0.0}},
 "cases": [{"id": "", "kind": "attack|legit", "attack_type": "", "source": "carbon|public|team",
            "no_guard": "", "rules_only": "", "tripwire": "", "decisions": {}, "latency_ms": 0}],
 "failures": [{"id": "", "arm": "tripwire", "what_happened": "", "why": ""}]}
```

Scenarios: 6 attacks (half rule-passing) + 6 tricky-but-legit purchases at minimum.
`rules_only` baseline = price cap + payee check + keyword filter ("assistant", "ignore", "add to cart"...), ~20 lines.
List every miss in `failures` (5 Sigma: show where it still fails).
Every model call also appends one row to `logs/usage.jsonl`:
`{"ts", "model", "job", "input_tokens", "output_tokens", "latency_ms", "est_cost_usd"}`.
