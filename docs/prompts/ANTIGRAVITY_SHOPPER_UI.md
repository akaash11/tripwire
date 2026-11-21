# Prompt for Antigravity: one shopper page (round 2)

Paste everything below the line into Antigravity (Gemini 4 Neon, thinking high or xhigh), opened on the repo.

---

Read first: `docs/EVENT_REQUIREMENTS.md`, `docs/STATUS.md`, `server/web.py`, `server/dashboard_api.py`,
`dashboard/shopper.js`, `dashboard/styles.css`, `agent/tools.py` (`quote`, `raise_budget`), `agent/shop_mcp.py`
(`checkout_view`). Don't touch `guard/`, `evals/`, or the request/response shapes of `/request`, `/pending`, `/approve`,
`/w/{token}`, `/w/{token}/decide`, `/w/{token}/raise_budget`.

## Goal
Today there are two shopper pages: `/t/{token}` (in `server/web.py`, has the actions but looks plain) and your
`/r/{token}` (the nice chat timeline, view only). Make `/r/{token}` THE shopper page, then point `/t/{token}` at it
(302 redirect), so the iPhone "Tripwire Buy" shortcut, which opens `/t/{token}`, lands on the good page. Also make
`/buy` (the no-install page) use the same look and redirect to `/r/{token}` after sending.

## What the shopper must be able to do on `/r/{token}`
1. **Follow along live** (already there): their request + photo, what Tripwire understood, each agent search, each
   product the agent tried, and **each delivery check** (`delivery_check` events: "BevMo! can't deliver to New York",
   "Delivered total $22.45 is over the $15 budget"). Show these as short timeline rows, red/amber/green.
2. **Approve or Block** when `GET /w/{token}` has `pending`: big sticky bottom bar on mobile with the item, seller,
   delivered total, and the Tripwire reason. Approve → `POST /w/{token}/decide {cart_id, decision:"approve"}` → the
   response has `url` → `location.href = url` (the merchant's real Shop checkout; keep it exactly as returned).
3. **Raise the budget** when `GET /w/{token}` has `offer` (status `over_budget`): card "Everything is over your budget.
   Cheapest that delivers: <summary>. Your budget $X (a guess if `budget_stated` is false). Only you can raise it, the
   agent can't." Buttons: "Raise to $Y" → `POST /w/{token}/raise_budget {cart_id}` → then show the Approve bar.
4. **Know what's next on Shop**: when there is a checkout, show totals (item, shipping, tax, total) and
   `to_fill_on_shop` as one friendly line ("You'll enter on Shop: email, first name, street address"). These are NOT
   errors; never show them in red.
5. **See the verdict X-ray** when Tripwire blocks: asked for / found in listing / agent tried / reasons.

## Look
- One column, phone first (375px), big tap targets, sticky action bar, readable at arm's length on a projector too.
- Status header that changes: Understanding → Shopping → Checking delivery → Needs your OK → Opening Shop.
- A small progress line with elapsed seconds (the whole run is 20 to 50 s; show that it's working).
- Product card with the merchant's image (`image_url`, original Shop URL, never re-hosted) and a link to the product.
- Light + dark. Keep the yellow/black tape motif. Footer: `Tripwire server <server_version> · sent from <client>`
  (both in `GET /w/{token}`).
- Text from Shop/the agent is untrusted: `textContent` only, never `innerHTML`.

## Never show
`continue_url` except as the redirect after the shopper taps Approve, the checkout id, signatures, file paths, keys,
other people's requests.

## Also
- **Persistence:** `STATE` and `SESSIONS` are in memory, so a server restart wipes every request and order (the admin
  page goes empty). Save `STATE` (requests, events, orders, pending; NOT continue_url / checkout ids / file paths) to
  `data/state.json` on change and load it at startup. Sessions can't be restored; mark those requests "expired".
- Admin Live tab: show `delivery_check` and `budget_raised` events and the request's `client` (Shortcut version).

## Done means
On a phone-sized window: run `curl -X POST localhost:8790/request_text -H 'content-type: application/json'
-d '{"text":"Black Forest organic gummy bears, budget 6 dollars"}'`, open `/t/<token>` → lands on `/r/<token>` → shows
the live timeline → over-budget card → Raise → Approve bar → Approve redirects to a shop.app URL. Then a normal one
(`"a braided 100W USB-C cable under 20 dollars"`) → Approve. Screenshot both. Update `docs/STATUS.md`. Small commits.
