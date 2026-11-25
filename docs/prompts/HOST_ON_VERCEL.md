# Prompt for Claude Code: make the Tripwire Buy Shortcut work online, free, on Vercel

Open Claude Code in `~/Desktop/Projects/tripwire-event` and say: "Do docs/prompts/HOST_ON_VERCEL.md".

---

## Goal
Anyone can install **Tripwire Buy** from a QR code and use it, with Akaash's laptop off. It must be **free**: Vercel Hobby
plus the Upstash Redis free plan (via the Vercel Marketplace). No card, no paid plan. Done means: the Shortcut built for
`https://tripwire-labs.vercel.app` works end to end on an iPhone with no laptop running: voice + photo → live page →
agent shops → guard → Approve → the merchant's Shop checkout opens.

Out of scope: the MCP server for Claude or Cursor, the admin dashboard online (it stays local-only), new features.

## Read first
`README.md`, `docs/HOSTING_PLAN.md` (the "What blocks a plain Vercel deploy" table), `docs/ARCHITECTURE.md`,
`docs/SECURITY.md`, `server/app.py`, `server/web.py`, `server/persistence.py`, `server/config.py`, `agent/tools.py`
(`ShopSession`), `agent/run.py`, `common/signing.py`, `guard/replay.py`, `phone/make_shortcuts.py`,
`~/.claude/skills/ios-shortcuts/SKILL.md`. Run `.venv/bin/python -m pytest -q tests` first (44 should pass).

## Rules (do not break)
- Never print or log keys (Gemini, Upstash, the signing key). Show lengths or hashes only. `.env` stays gitignored.
- Never log or show the checkout id or `continue_url`, except as the redirect after the shopper taps Approve.
- Never call `complete_checkout`. Don't cache Shop results or re-host images.
- `guard/` and `evals/` are Tanushree's: change them only if you must, and list the changes for Akaash to tell her.
- The laptop mode must keep working (`uvicorn server.app:app` with local files) and all tests must pass. Add tests for
  the new store layer.
- No paid features. If something needs a paid plan or a card, stop and ask Akaash.
- Ask Akaash before anything only he can do (below). Do everything else yourself.

## What only Akaash does (ask him at the right step, don't guess)
1. **Gemini key:** he runs `vercel env add GEMINI_API_KEY production` himself in `~/Desktop/Projects/tripwire-event`
   and pastes the older key at the prompt (never in chat). For local use he updates `.env` the same way. Before going
   live, he checks in Google AI Studio that the key's project has no billing, or accepts the cost. With a free-tier key,
   check that `gemini-4-neon`, `gemini-decision-1` and the Antigravity agent answer on it (a one-call smoke test per
   model, print only status and latency). If one doesn't, report which, and propose the free fallback already in config
   (`gemini-3.5-flash-lite`) for that role.
2. **Upstash:** he adds the Upstash for Redis integration (free plan) to the `tripwire-labs` project in the Vercel
   dashboard, which accepts Upstash's terms. Then `vercel env pull` gives you the REST URL and token variable names; read
   whichever pair the integration created (`KV_REST_API_URL`/`KV_REST_API_TOKEN` or `UPSTASH_REDIS_REST_URL`/`_TOKEN`).
3. **Signing key:** generate a new Ed25519 key locally, then have him add it with `vercel env add TRIPWIRE_SIGNING_KEY production`
   (base64 of the PEM). Never print it.

## Design (keep it this simple)
**One Vercel project, `tripwire-labs`**, replacing the redirect-only `short-link/` project (delete `short-link/` once the
app serves the same paths). FastAPI runs as a Vercel Python function (check the current Vercel Python/FastAPI docs
before writing config; don't rely on memory). Serve the app at the root; keep `/tripwire/*` working too (a rewrite) so
old links and Shortcuts don't break.

**The agent run is driven by the live page** (no background tasks on serverless):
- `POST /request` (Shortcut) and `POST /request_text` (web): compile the contract (~10 s) and return
  `{token, view_url}`. No `asyncio.create_task`.
- New `POST /r/{token}/run`: the live page calls this once on load (guard against double runs with a Redis lock
  `SET NX` per request). It runs the agent in this one function call and writes every event to Redis as it goes. Set
  the function's max duration to what Hobby allows (check the docs; the run is 30 to 90 s). The page keeps polling
  `/w/{token}` as it does today.
- If the run dies (timeout, closed tab), the request is marked `expired` with a plain reason, and the page shows "start
  again".

**A small store interface** (`server/store.py`), two backends: local files (today's behavior) and Upstash Redis
(REST, plain `httpx`, no new heavy SDK). It holds:
- requests, events, orders, pending (what `data/state.json` holds now), with a TTL (e.g. 7 days)
- the per-request session: what `ShopSession` needs after the run (pending cart, shop domain, checkout id,
  `continue_url`, the products used for the live-price check). Encrypt the checkout id and `continue_url` at rest
  (AES-GCM with a key derived from the signing key). Approve/decide/raise_budget load this instead of `SESSIONS`.
- nonces: atomic `SET NX` with the contract's expiry (replaces `data/used_nonces.json`)
- the rate limit: per IP and global, `INCR` with a 60 s TTL
- a **daily request cap** (`DAILY_REQUEST_CAP`, default 50): `INCR` on a key per UTC day. Over the cap, `/request`
  answers `{"speak": "Tripwire is resting for today, try tomorrow."}` and the Shortcut shows it.

**Signing:** `common/signing.py` reads `TRIPWIRE_SIGNING_KEY` from env when set, else today's file.

**Admin:** `is_local()` must be false on Vercel for every request (no trusting `X-Forwarded-For` or the `Host`
header). The admin and its APIs answer 403 online; that's intended.

**Uploads:** keep the 15 MB cap; read audio and photo into memory, no temp files on disk.

## Shortcut
Rebuild with base `https://tripwire-labs.vercel.app` (update `make_shortcuts.py` so the base no longer has to end in
`/tripwire`), bump `VERSION` to 2.0 here and in `server/web.py`, sign with `shortcuts sign --mode anyone`, and serve it
at `/shortcuts/buy`. `/install` shows the QR for that. Advertise only Tripwire Buy. Follow the checklist in
`~/.claude/skills/ios-shortcuts/SKILL.md`.

## Verify (real outputs only; write them in `docs/STATUS.md` under "Hosted (v2.0)")
1. `pytest` passes (old + new tests), with the local backend and with a fake Redis backend.
2. Preview deploy (`vercel deploy`), then `curl` `/install`, `/buy`, `/shortcuts/buy` (200, and the file matches the
   local signed one with `cmp`).
3. `curl -X POST <preview>/request_text -d '{"text":"one can of original Red Bull"}'`, open `view_url`, and confirm the
   run finishes with a verdict and `pending` or `pay_ready`. Do the same with "That's it apple strawberry mini fruit bars,
   budget 5 dollars", and confirm it shows the over-budget offer.
4. Replay: reuse a contract nonce, and confirm it's blocked. Rate limit: 11 quick requests, and confirm the 11th is refused.
5. `/admin` and `/api/admin/state` return 403 on the preview.
6. Then `vercel deploy --prod` (ask Akaash first), and ask him to install the 2.0 Shortcut from the QR with Tailscale
   **off** and run one request. Record what he reports.

## Update docs
README "Try it" (the QR and short link now work without the laptop), `docs/STATUS.md`, `docs/HOSTING_PLAN.md`
(mark done), `docs/SECURITY.md` (the platform hardening line: Redis-backed nonces, rate limit, daily cap, encrypted
session secrets). Small commits; ask Akaash before pushing, because the repo is public.
