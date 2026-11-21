# Tripwire architecture

Tripwire is a payment firewall for AI shopping agents. The person states what they want once, by voice and photo.
Tripwire turns that into a signed **Intent Contract**, lets an agent shop the real Shopify catalog through tools that
Tripwire controls, and only lets checkout happen if the cart satisfies the contract.

```mermaid
flowchart LR
  U[iPhone: voice + photo\nTripwire Buy] -->|POST /request| S[Tripwire server]
  S --> C[Contract compiler\nGemini 4 Neon\nfallback Flash-Lite]
  C -->|Ed25519-signed contract| G
  S --> A[Shopping agent\nAntigravity managed agent\nonly 3 custom functions]
  A -->|search_products| Q[Quarantine]
  Q --> M[(Shopify Catalog MCP)]
  A -->|add_to_cart| K[Cart]
  A -->|request_checkout| G[Guard]
  G -->|approve| X[Mock UCP checkout\n+ signed receipt]
  G -->|ask_human| P[iPhone: Tripwire Approve]
  G -->|block| B[X-ray + log]
  R[Red-team harness\nGemini 4 Carbon] -.poisoned listings.-> Q
  D[Dashboard: localhost only] --- S
```

## Components
| Component | Path | What it does |
|---|---|---|
| Server | `server/app.py` | Phone routes `/request`, `/pending`, `/approve` (reply fast, work in background); dashboard API (localhost only); `/pubkey` |
| Contract compiler | `agent/contract.py` | Voice + photo → contract with `must` / `must_not` clauses, price cap, returns, nonce, expiry; Ed25519-signed |
| Shopping agent | `agent/` | Antigravity managed agent with **only** `search_products`, `add_to_cart`, `request_checkout` (no code execution, search or URL tools) |
| Catalog search | `agent/` | Shopify Catalog MCP (`search_catalog`), currency and ships-to pinned by Tripwire, results quarantined before the agent sees them |
| Guard | `guard/` | `check(contract, cart, products) -> verdict`: policy backstop, Decision 1 per clause, canary, replay/tamper, Neon contract checker |
| Checkout | `guard/` or `agent/` | Mock checkout in the UCP shape, including `messages[]` warnings like `final_sale` |
| Red-team | `evals/` | Carbon generates attacks; each scenario runs with Tripwire off and on; attack success and false positives |
| Dashboard | `dashboard/` | Live, Orders, Usage, Red-team, API keys. A control panel, not the product |
| Shared | `common/` | `.env` loading, usage log (`logs/usage.jsonl`), Ed25519 sign / verify |

## The guard, in order
1. **Policy backstop** (deterministic, no model): payee is the contract's merchant, total ≤ `max_total_cents`,
   contract signature valid, not expired, nonce unused, cart unchanged since it was checked.
2. **Canary:** a secret planted in the agent's context. If it ever shows up in a tool call, the agent was hijacked → block.
3. **Decision 1** (`gemini-decision-1`): one calibrated yes/no per contract clause against the quarantined listing, plus an
   injection question. All clauses confidently yes and no injection → approve.
4. **Contract checker** (Gemini 4 Neon) only when Decision 1 is unsure or an injection signal fires: reasons over the
   contract, listing and product image. Clear violation → block, otherwise → ask the human.

The agent only ever gets `{verdict, reason}` back, never the X-ray, so an attacker can't learn from it.

## Verdict shape
```json
{
  "verdict": "approve | ask_human | block",
  "contract_id": "ic_...", "cart_id": "cart_...", "enforced": true,
  "stages": {
    "policy": {"pass": true, "failures": []},
    "canary": {"tripped": false},
    "quarantine": {"clean": false, "stripped_count": 1},
    "decisions": {"model": "gemini-decision-1", "clauses": {"m1": {"p_yes": 0.31, "confidence": 0.62}}, "escalated": true},
    "contract": {"pass": false, "failed_clauses": ["m1"], "model": "gemini-4-neon"}
  },
  "xray": {"asked_for": "", "hidden_in_listing": "", "agent_tried": "", "reasons": []},
  "receipt": null,
  "latency_ms": 0
}
```
`enforced: false` is the Tripwire-off demo mode: the verdict is computed and logged, but checkout goes through.
On approve, `receipt` = `{order_id, intent_hash, cart_hash, issued_at, signature}` signed with Ed25519; anyone can
verify it with `GET /pubkey`.

## Network
- Server listens on `127.0.0.1:8790`.
- Phone reaches it over Tailscale only: `tailscale serve --bg --set-path /tripwire http://127.0.0.1:8790`
  → `https://<machine>.<tailnet>.ts.net/tripwire/...`. Port 443 because the tailnet policy only allows the phone in on 443.
- Dashboard and settings routes refuse anything that arrives through Tailscale, so API keys are never reachable from the tailnet.

## Gemini models
| Model | Job | Hot path? |
|---|---|---|
| gemini-4-neon | Contract from voice + photo; contract checker on escalations | Once per request / escalations |
| gemini-4-carbon | Red-team attack generation | No |
| gemini-4-hydrogen | Quarantine: hidden instructions in product images (stretch) | No |
| gemini-decision-1 | Per-clause gate (Decisions API) | Yes |
| gemini-3.5-flash-lite | Shopping agent; fallback for the contract compiler | Yes |

Every model call is logged with the model actually used, so fallbacks are visible on the dashboard.

## Shopify
- **Catalog MCP** for real product search (no sign-in needed).
- **Shop `skill.md`** as the agent's shopping instructions, with its checkout step replaced by `request_checkout`.
  The skill asks the agent to confirm before buying, show `final_sale` warnings and ignore instructions inside
  product data; Tripwire enforces those rules outside the agent.
- **UCP checkout shape** for the mock checkout, including `messages[]` warnings.
