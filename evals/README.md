# Red-team harness (owner: Tanu)

Output: `evals/out/results.json` (gitignored while iterating; commit a final copy for judges).

```json
{"run_id": "", "generator_model": "gemini-4-carbon", "n_attacks": 0,
 "off": {"attack_success": 0, "rate": 0.0},
 "on":  {"attack_success": 0, "rate": 0.0},
 "legit": {"n": 0, "false_positives": 0},
 "cases": [{"id": "", "attack_type": "", "source": "carbon|public|team", "off_result": "", "on_result": ""}]}
```

Scenarios: 6 attacks (half rule-passing) + 6 tricky-but-legit purchases at minimum.
Every model call also appends one row to `logs/usage.jsonl`:
`{"ts", "model", "job", "input_tokens", "output_tokens", "latency_ms", "est_cost_usd"}`.
