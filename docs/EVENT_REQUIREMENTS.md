# Event requirements (read before building anything)

What the organizers and sponsors said at the opening session, plus what we verified against their docs.
If a design choice conflicts with this file, this file wins.

## Hard rules
- **No previous work.** Everything in this repo is built at the event.
- **Theme: 5 Sigma** (every project must fit one theme):
  > Pick a real task today's agents struggle with and build a Gemini 4 system that does it reliably, using the right
  > models for each job. Then prove it: test on messy inputs you didn't prepare for, compare against a simple baseline,
  > and show where it still fails.

  What this means for Tripwire:
  - Real task: letting an agent buy things for you without being tricked into buying the wrong thing.
  - Right model per job: each model has one named job (table below), visible on the dashboard.
  - **Messy inputs we didn't prepare for:** Carbon-generated attacks, outside injection examples, real catalog listings, and judges' own voice + photo requests.
  - **Simple baseline:** (a) no guard, and (b) a naive rules-only guard (price cap + keyword filter). Report Tripwire against both.
  - **Show where it still fails:** a failures section in the results and on the dashboard. Don't hide misses.
- (Theme 2, Element 119, is "only works because Gemini 4 can see, hear and generate media", pushed with the Decisions API. Not our theme, but our voice + photo contract leans on it.)
- **At least 2 Gemini 4 models** with clear jobs. **Use model selection** (the right model per job, switchable).
- **Use the Decisions API** (20% of judging).
- Submit: public repo + 1-minute video, by 5:00 PM. Judges must be able to see what was built here.

## Shopify (Best use of Shop MCP: $2,500 Shop Cash)
From the Shopify talk:
- "**If I can't buy anything, you can't win.**" A judge must be able to make a **real purchase** through our project.
- **Checkouts are real:** one checkout per merchant; the shopper pays on its page, with real money, through the **Shop wallet** (Shop Pay).
- **Use live data:** fetch products live. Don't cache results or re-host merchant images.
- **Keep links as returned:** product and checkout URLs keep every query parameter.
- One endpoint does it all: **`https://mcp.shop.com/`** (search, carts, checkout, account). Docs: **shop.dev/docs** (every page has a `.md` version). Skill: **`https://mcp.shop.com/skill.md`**.
- Signed in with Shop, the checkout **auto-fills the shopper's address and billing**, and the agent can show order info afterwards. The demo they showed: search → pick → cart → checkout with saved address and Shop Pay → ask about the order.
- **Anyone must be able to install and use it** (our Shortcuts), not just our phones.

Shopify's challenge gist (gist.github.com/ryan-ormsby/51a14672336a8c2c16ea32e26e708235), rules verbatim in spirit:
1. **Checkouts are real.** Paying on the merchant's checkout page places a real order with real money.
2. **Use live data.** Fetch products from Shop MCP when you show them. Don't cache results or re-host merchant images.
3. **Keep links exactly as returned.** Product URLs and checkout links keep every query parameter.
4. **Purchases happen on the merchant's page.** One checkout per merchant; give the shopper its `continue_url` to
   review and pay. **Our project can't complete checkouts itself** (so no `complete_checkout`, even signed in).

Doc index (each is Markdown): shop.dev/docs/{index,quickstart,connect,search,checkout,tools,errors,sign-in,authentication,cli,locations,spend-requests,orders,catalog-api}.md

Verified against shop.dev docs and live calls:
- Search, carts and checkout work **without sign-in**. `create_checkout` (verified) returns `status`, `totals`, `messages`,
  `links` (including **`refund_policy`**, a real source for the "returnable" clause), `expires_at` (6h) and a
  **`continue_url`** on shop.app where the shopper enters their details and pays.
- Signed out, a new checkout is `incomplete` (contact + address missing) with no payment instruments: hand the shopper `continue_url`.
- Signed in (`get_account`): saved addresses, Shop Pay, spend permissions. The docs describe `complete_checkout`, but the
  challenge rules say our project must not complete checkouts. **Only `status: completed` with an `order` confirms a purchase**:
  poll `get_checkout` after the shopper pays to show the order.
- Sign-in: CLI device flow (`shop account login --wait 0 --device-name ...`, public client, fine for prototyping), or
  OAuth code + PKCE with scopes `openid personal_agent`, `resource=https://mcp.shop.com`, for a distributed app.
- The checkout `id` and `continue_url` contain an access key: **never log them**, share `continue_url` only with the shopper.
- Show the shopper every `warning`, every link in `links`, and the totals with their labels.
- Every MCP call is one HTTP POST with headers `MCP-Protocol-Version: 2026-07-28`, `Mcp-Method`, `Mcp-Name` and
  `params._meta` naming the protocol version and client. Client: `agent/shop_mcp.py`.

### How Tripwire does a real purchase
1. Agent picks a variant through `search_products` (Shop MCP live search, quarantined by Tripwire).
2. Agent calls `request_checkout`. **The agent never gets a checkout or a payment link.**
3. Guard checks the cart against the signed Intent Contract.
4. Approve (or the human approves on the phone) → **Tripwire** calls `create_checkout` with the exact variant and shop
   domain from search, checks the checkout's real totals/warnings/refund policy against the contract again, then
   hands `continue_url` to the shopper's phone. The shopper pays there with Shop Pay.
5. The shopper always pays on the merchant's page (`continue_url`), signed in to Shop there for saved address and
   Shop Pay. Tripwire never calls `complete_checkout` (gist rule 4). Stretch: Sign in with Shop in Tripwire so
   Approve can show the shopper's saved address next to the total before handing over the link.

## Gemini
| Model | Job | Notes |
|---|---|---|
| gemini-4-neon | Contract from voice + photo; contract checker on escalations | GA Nov 18; $3 / $15 per 1M. **Tier 3 event key:** text 4.1s, voice+photo 16s; contract with JSON output 11s at `thinking_level: low` (13.9s medium, >40s at default high). Old key: no reply |
| gemini-4-carbon | Red-team attack generation | GA Nov 10; $2 / $10 per 1M through Nov 30; 3.6s text on tier 3 |
| gemini-4-hydrogen | Quarantine (images), default Antigravity agent model | GA Nov 12; $1.50 / $7.50; free tier; 6.8s text on tier 3 |
| gemini-4-argon | Option for the checker | GA Oct 13; "most capable" for agentic tasks; 9.6s text on tier 3 |
| gemini-decision-1 | Per-clause gate | Decisions API |
| gemini-3.5-flash-lite | Agent; contract fallback | ~2-6s, works with audio + image |

**Decisions API** (GA Nov 11; docs `aistudio.google.com/docs/decisions-api`):
- `client.decisions.create(model="gemini-decision-1", input=..., questions={...})`, REST `POST /v1beta/decisions`.
- Question types: `noul` (yes/no → `noul` 0.01 to 0.99 + `confidence`, optional `criteria.true_description/false_description`),
  `choice` (`criteria` maps options → `choice`, `probabilities`, `confidence`), `score` (ordered levels → `score`, `probabilities`).
- 1 to 1,000 questions per request, scored in parallel and isolated from each other; shared input billed once.
- Input: text, image, audio, video, PDF blocks (Interactions content schema). 262,144-token context. Zero output tokens.
- Safety blocks return HTTP 400 with `error.code` (`safety`, `prohibited_content`, ...). Handle it as "ask the human".
- Docs warn input can argue for its own classification: never the only gate when an injection signal fires.

Use `generation_config={"thinking_level": "low"}` for Gemini 4 calls on the hot path.

Model selection: every job's model is a setting (env now, dashboard later), every call logs the model that actually ran.

## API keys
- The event gave us a **tier 3** Gemini key. It goes in `.env` as `GEMINI_API_KEY=` (gitignored, chmod 600). Never in chat, code, logs or commits.
