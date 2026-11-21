# Prompt for Antigravity: build the Tripwire web pages

Paste everything below the line into Antigravity, opened on the `tripwire` repo folder.

---

You are working in the Tripwire repo (Python 3.14, FastAPI, `.venv`). Before writing code, read these files fully:
`docs/EVENT_REQUIREMENTS.md`, `docs/STATUS.md`, `docs/ARCHITECTURE.md`, `server/app.py`, `agent/tools.py`,
`agent/run.py`, `agent/contract.py`, `common/__init__.py`, `guard/__init__.py`. Follow EVENT_REQUIREMENTS.md over
anything else. Do not touch `guard/` or `evals/` (Tanu owns them), `phone/`, or the three phone routes'
request/response shapes (`/request`, `/pending`, `/approve`): iPhone Shortcuts depend on them.

## Goal
Build two web pages served by the existing FastAPI app in `server/app.py`, plain HTML + vanilla JS + CSS in
`dashboard/` (no build step, no React, no Streamlit, no external JS except optionally one pinned CDN chart lib).
Dashboards as the main feature are banned at this event: these pages show and control Tripwire; the product is the guard.

### Page 1: Shopper chat page (public, per request): `GET /r/{token}`
What a shopper sees for their own request, so they can follow along if they want.
- Generate an unguessable view token per request (`secrets.token_urlsafe(16)`) when `/request` or `/request_text`
  creates it; store it on the request; include a `view_url` field in the `/request` JSON response (keep existing fields).
- The page shows, as a chat-style timeline, live (poll `GET /r/{token}/events` every 2s):
  their photo thumbnail (served via `GET /r/{token}/photo`, the uploaded file), what Tripwire understood (contract
  `summary`, `must`, `must_not`, budget, `photo_observations`, `compiled_by`), each agent step (searches, the product
  it picked with title, price, seller), the verdict with a plain-English reason, and the checkout status.
- Never include: the checkout `continue_url`, checkout id, contract signature, other people's requests, API keys,
  file paths. The pay link is delivered only through the Approve shortcut.
- Mobile-first (it opens on a phone). Works through the public URL, so these routes must NOT require `is_local()`.

### Page 2: Admin page (local only): `GET /admin`
For us to run the demo and show judges the data. Every admin route and API must check `is_local(request)` and
return 403 otherwise (the tailnet and Funnel must never reach it).
Tabs:
1. **Live**: newest request first. Contract (with the shopper's photo), the agent's tool calls, the verdict X-ray
   (`asked_for` / `hidden_in_listing` / `agent_tried` / `reasons`), the Decisions clause scores from
   `verdict.stages.decisions.clauses` as bars, pending approval with Approve / Block buttons (laptop fallback for the
   phone: call the same logic as `/approve`), and a **Tripwire ON/OFF** switch (sets `TRIPWIRE_ENFORCED` in the
   process env and `.env`; `guard.enforced()` reads it).
2. **Orders**: every checkout Tripwire created: items, totals with labels, status, merchant links (refund policy
   etc., exactly as returned), the agent's summary. Add a "Refresh status" button that calls Shop MCP `get_checkout`
   (add `get_checkout` use in `agent/shop_mcp.py`, which already exists) and shows `completed` + order when paid.
   Do NOT show `continue_url` or checkout ids.
3. **Usage**: read `logs/usage.jsonl`: calls, latency p50/p95, tokens and estimated cost per model and per job
   (prices per 1M tokens: neon $3/$15, carbon $2/$10, hydrogen $1.50/$7.50, argon $2/$10, 3.8-flash $0.75/$3.75,
   decision-1 input $0.10 (verify), flash-lite: look up or mark unknown). Settings: requests-per-minute limit
   (`RATE_LIMIT_PER_MIN` in server) and a daily token budget that makes `/request` reply "budget reached".
4. **Models**: model selection per job, saved to `.env` and applied without restart where possible:
   `CONTRACT_MODEL`, `CONTRACT_FALLBACK_MODEL`, `CONTRACT_THINKING`, `AGENT_MODEL`, plus `CHECKER_MODEL`,
   `ATTACK_MODEL` for Tanu's code. Allowed: gemini-4-neon, gemini-4-carbon, gemini-4-hydrogen, gemini-4-argon,
   gemini-3.8-flash, gemini-3.5-flash-lite. Show which model actually ran for each recent call.
5. **API keys**: add / update / remove `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`. Write to `.env`
   server-side (keep chmod 600). The page must never receive a key back: show only "set / not set" and a last-updated
   time. A "Test" button makes one cheap call (Gemini: a 1-question Decisions call) and shows OK + latency.
   Never log keys.
6. **Red-team**: read `evals/out/results.json` if it exists (shape in `evals/README.md`): attack success rate for
   no guard / rules-only / Tripwire, false positives, and the failure list. A "Run" button may call a function
   Tanu will expose later; show "not available yet" until then.

Add a JSON API under `/api/admin/...` for these tabs (all `is_local`). `GET /api/state` already exists; extend
rather than break it.

## Engineering rules
- Keep secrets out of responses, logs and the browser: `continue_url`, checkout ids, keys, contract signatures.
- Escape all text from Shop / the agent before inserting into HTML (it is untrusted; use textContent, not innerHTML).
- Keep Shop links exactly as returned. Don't cache Shop results or re-host merchant images (product images may be
  shown by their original Shop URL).
- Light and dark mode; readable on a projector; mobile-friendly for `/r/{token}`.
- Small commits with clear messages as you go. Don't commit `.env`, `logs/`, `uploads/`, `keys/`.
- After building: run the server (`.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8790`), create a request with
  `curl -X POST localhost:8790/request_text -H 'content-type: application/json' -d '{"text":"a 100W USB-C cable under 20 dollars, returnable"}'`,
  and check both pages render it end to end. Verify `/admin` and `/api/admin/*` return 403 when called with an
  `X-Forwarded-For` header. Then update `docs/STATUS.md` (what works, verified how; anything not run = NOT RUN).
