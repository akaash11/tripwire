# Team plan (@labs: Akaash + Tanu)

**Current state and what's left: [`STATUS.md`](STATUS.md)** (this plan is the morning plan; POC done ~12:30, v1.2 stable at 14:05).

**Rules first: [`EVENT_REQUIREMENTS.md`](EVENT_REQUIREMENTS.md).** Architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md). Commit small and often.

## Strategy: POC first, then add features
### POC (target: ~12:30)
1. Phone: Tripwire Buy sends voice + photo → contract (**done**: Neon, 11s at thinking low, with Flash-Lite fallback).
2. Agent: `search_products` (Shop MCP) → `add_to_cart` → `request_checkout`.
3. Guard: policy backstop + Decision 1 per clause + canary.
4. Approval: `ask_human` shows in Tripwire Approve; Approve → Tripwire creates a **real** Shop MCP checkout → the phone opens `continue_url` → shopper pays with Shop Pay. Signed receipt records it.
5. Dashboard: Live (contract, X-ray, pending, on/off toggle) + Orders.

### After the POC (decide together)
- **Neon contract checker + X-ray diff** (first priority, the headline): catches the rule-passing attack
  (right store, right price, wrong item).
- **12 scenarios:** 6 attacks (half rule-passing) + 6 tricky-but-legit, so we report attack success **and** false positives.
- Replay/tamper fully enforced (nonce, expiry, cart hash).
- Dashboard Red-team, Usage and API keys tabs.
- Live Red vs Blue: Carbon writes a new attack on stage, with a pre-generated backup.
- Quarantine with Hydrogen (hidden text in product images).
- Claude Code / Grokbot adapter (same 3 tools over MCP).

## Who builds what
| Akaash: product path | Tanu: security core + evals |
|---|---|
| Server, phone routes, iPhone Shortcuts | `guard/check()` (keep the verdict shape in ARCHITECTURE.md) |
| Contract compiler (done) | Policy backstop: payee, cap, signature, expiry, nonce, cart hash |
| Shopping agent + Shop MCP search and real checkout (`agent/shop_mcp.py`, working) | Canary planting + detection |
| Dashboard (Live, Orders, Usage, API keys) | Decision 1 per-clause gate, then the Neon checker + X-ray |
| Usage logging, model settings | Re-check the real checkout (totals, `messages[]` warnings, refund policy link) against the contract; signed receipts |
| Demo script and phone | Red-team harness: Carbon attacks + outside examples; baselines (no guard, rules-only guard) vs Tripwire; failures listed → `evals/out/results.json` |
| | Outside eval data so evals aren't all team-written; video with her bot |

### Where the halves meet
- `guard.check(contract: dict, cart: dict, products: list[dict]) -> dict` and `guard.reduced(verdict)`.
- Contract fields: `contract_id, created_at, expires_at, nonce, user_request, photo_observations, item,
  must[{id,clause}], must_not[{id,clause}], max_total_cents, quantity, returns, merchant, currency, ships_to,
  search_query, summary, assumptions, compiled_by, signature`. Verify with `common.signing.verify(contract)`.
- `evals/out/results.json` and `logs/usage.jsonl`: shapes in [`../evals/README.md`](../evals/README.md).

## Settings page (control panel, not the product)
Dashboards as the main feature are banned at this event. Lead the pitch with the block moment.
| Tab | Contents |
|---|---|
| Live | Contract, photo + transcript, X-ray, pending approval (laptop fallback), **TRIPWIRE_ENFORCED on/off** |
| Orders | Past orders with signed receipts, pending orders |
| Usage | Tokens, latency and estimated spend per model; rpm limit and daily token budget |
| Red-team | Attack count, generator model, Run, attack success off vs on, false positives |
| API keys | Add / update / remove per provider (Gemini; optional Anthropic, xAI); pick the model for each job; Test key button |

API keys: written server-side to `.env` (chmod 600, gitignored); the page only ever sees "set / not set"; never logged;
settings routes refuse requests that come through Tailscale.

## Demo (phone + live dashboard)
1. Hook, one sentence: an AI agent was tricked into moving money because the payment layer trusted its output.
2. Judge speaks into the iPhone and photographs the item or label. Dashboard shows the contract.
3. Tripwire off: a poisoned listing passes every rule but is the wrong item; the agent buys it.
4. Tripwire on: blocked, X-ray on screen; a borderline cart goes to the phone for approval.
5. Red-team numbers: attack success off vs on, false positives.

## Gemini models (rule: 2+ Gemini 4 models with clear jobs)
Neon (contract + checker) and Carbon (attacks) are the two. On the event's tier 3 key every Gemini 4 model answers
(Carbon 3.6s, Neon 4.1s, Hydrogen 6.8s, Argon 9.6s on text). Use `thinking_level: low` on the hot path. The compiler
falls back to Flash-Lite and logs which model ran.

## Shopify (sponsor track)
A judge must make a **real purchase** or we can't win. Shop MCP for live search and real checkout, Shop `skill.md` (`https://mcp.shop.com/skill.md`) as the agent's instructions (Tripwire enforces its rules), Sign in with Shop for saved address + Shop Pay (stretch). Shortcuts must be installable by anyone (public URL via Tailscale Funnel).

## Checkpoints
POC ~12:30 · Neon checker + scenarios ~2:30 · feature freeze 3:30 · video 4:00 · repo public + submit 4:45.
Cut order if behind: Grokbot adapter, Claude Code adapter, Usage tab, live Red vs Blue, scenarios down to 8.
