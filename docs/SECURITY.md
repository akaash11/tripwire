# What Tripwire protects, and how (talk track)

**30-second version:** "The agent reads untrusted product pages, so we never trust it. Your words and photo become a
signed contract. The agent can only *ask* to buy; Tripwire checks the cart against the contract with deterministic
rules, the Decisions API per clause, and a Neon second opinion, then checks the merchant's real checkout for the
delivered price and delivery. Only then do you get the store's own Shop checkout, and you pay there. The agent never
sees a payment link, and only you can change the budget."

## The layers (in the order a purchase goes through them)

| # | Layer | Stops | Where |
|---|---|---|---|
| 1 | **Signed Intent Contract.** Gemini 4 Neon turns voice + photo into must / must-not clauses and a delivered-total budget, signed with Ed25519, 30-min expiry, one-time nonce | The agent (or a page) quietly changing what you asked for | `agent/contract.py`, `common/signing.py` |
| 2 | **Quarantine.** The agent only sees stripped product data (title, price, seller, short facts; no HTML, no descriptions), labeled "data, not instructions" | Prompt injection hidden in listings | `agent/tools.py` `search_products` |
| 3 | **No payment power.** The agent has 2 tools: search and *ask to buy*. It never receives the checkout link or id | A hijacked agent paying anyone | `agent/tools.py`, `server/` |
| 4 | **Policy backstop** (no AI): signature valid, not expired, nonce not reused, total ≤ budget, currency, quantity, cart not altered (hash), price re-checked against the live Shop variant | Price tampering, replay, swapped carts, quantity inflation | `guard/policy.py`, `guard/replay.py` |
| 5 | **Canary.** A secret code planted in the agent's instructions; if it ever shows up in a tool call, the agent has been steered → block | Exfiltration / instruction-following attacks | `guard/canary.py` |
| 6 | **Decisions API per clause** (`gemini-decision-1`, ~1 s): one calibrated yes/no per must / must-not clause, plus "does the listing contain instructions to an AI?" and the return terms | Wrong item that passes price rules (60W sold as 100W, wrong brand, final sale) | `guard/gate.py`, `guard/decisions.py` |
| 7 | **Neon second opinion** when Decision 1 is unsure (anything not clearly pass/fail, injection > 0.1) | Borderline and adversarial listings | `guard/neon.py` |
| 8 | **Real delivered price + delivery check** on the merchant's own checkout (no order): can it ship to you, what does it really cost with shipping and tax | "$10" cable that is $57.45 delivered; stores that can't deliver | `agent/tools.py` `quote()` |
| 9 | **Final check before pay**: the real checkout total vs budget, payee unchanged, merchant warnings (final sale) | Last-second changes at checkout | `guard/checkout.py` |
| 10 | **Human in the loop where it matters.** If anything is uncertain, you see the X-ray (what you asked / what the listing really says / what the agent tried / why) and decide. Only you can raise the budget (contract re-signed, "amended by shopper") | Rubber-stamping | `server/web.py`, `/r/{token}` |
| 11 | **You pay on the merchant's page.** Tripwire never calls `complete_checkout`; the store's Shop checkout is where you see your address and card | Agent-completed payments | Shopify rules |
| 12 | **Receipts.** Every approval gets a signed receipt (order ref, contract hash, cart hash) | Disputes: proves what was authorized | `guard/check.py` |

**Platform hardening:** every request has its own unguessable link (people can't see or approve each other's
carts); the admin page and APIs answer only on the laptop itself (403 through Tailscale/Funnel); rate limit 10/min;
15 MB upload cap; checkout links and ids are never logged or shown except to the shopper; keys only in `.env` (laptop) or
encrypted Vercel env vars (hosted).

**Hosted (Vercel + Upstash Redis):** contract nonces are an atomic Redis set-if-absent (replay protection survives
every serverless instance); rate limit per IP and global, plus a daily request cap (`DAILY_REQUEST_CAP`, default 50;
over it the app says "resting for today"); the Shop checkout id and `continue_url` are stored AES-GCM encrypted with a
key derived from the signing key, never in the clear; uploads stay in memory (no disk); the admin pages and APIs
answer 403 for every request online (no header or Host value is trusted); the signing key is an encrypted env var.

## Proof
35 live cases, 2 runs: no guard let **23/23** attacks through, a rules-only guard **10/23**, Tripwire **0/23**.
Cost: 7/12 legit buys still ask a human (returns unknown before checkout), 1/12 blocked by mistake
(`evals/out/results.md`). Decision 1 alone scored an obvious planted instruction at only 0.28, which is why layers 5
and 7 exist.

## Real example from today
Asked for "the orange cat digital sticker from Monday Cartoons". The first time, it wasn't in Shop's catalog, so the agent
grabbed "Monday Cat - Sticker" from a different seller and Tripwire blocked it (the "from Monday Cartoons" clause failed,
Decision 1: 0.08). Now, when a product link isn't in Shop's catalog, Tripwire checks whether the site is a Shopify store
(`/meta.json` has a `myshopify_domain`), reads that exact product from the store, and runs it through the same guard and
Shop checkout: the right sticker, $0.54 delivered, every clause passed (`agent/storefront.py`).
