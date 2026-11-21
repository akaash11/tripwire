# guard/

Tripwire's checkout firewall. Owner: Tanu. Rules: `docs/TANU.md`.

```python
from guard import check, reduced, canary_token, scan_tool_args

verdict = check(contract, cart, products)   # three arguments; TRIPWIRE_ENFORCED sets enforced
reduced(verdict)                            # {"verdict", "reason"} for the agent
```

## Pipeline

1. **Policy** (no model): `common.signing.verify(contract)`, `expires_at` not passed, `nonce` unused,
   `cart.total_cents <= contract.max_total_cents`, currency match, `cart_hash` recomputes the same way
   as `agent/tools.py` `add_to_cart`. Any fail → `block`, reason in `xray.reasons`.
2. **Canary:** plant `canary_token()` in the agent instructions. If `scan_tool_args(args)` (or the
   cart/listing) contains it → `block`, `stages.canary.tripped = true`.
3. **Decision 1** via Akaash's `ask()` + `clause_questions()` (`guard/decisions.py`, v1beta).
   Input is listing text (title, variant options, `metadata.top_features`, seller, price) plus
   checkout `messages[]` when present, plus `{"type":"image","uri":media[0].url,"mime_type":"image/jpeg","resolution":"low"}`.
   Images are never downloaded. Routing (docs/TANU.md):
   - every must noul ≥ 0.9 and conf ≥ 0.85, every must_not ≤ 0.1, injection ≤ 0.1, returns OK → approve
   - a must ≤ 0.1 or must_not ≥ 0.9 with conf ≥ 0.85 → block
   - `returns == "required"` and choice `final_sale` → block; `unknown` → escalate
   - HTTP 400 safety block → ask_human
   - else escalate
4. **Neon checker** on escalation: `gemini-4-neon`, `thinking_level: low`, ~30s timeout → ask_human.
   Clear violation → block, else ask_human. Fills `xray`.
5. **Receipt** on approve: `common.signing.sign({order_ref, intent_hash, cart_hash, issued_at})`.

`photo_match(shopper_photo, product_image_uri)` is an optional Decisions helper (STATUS.md).
