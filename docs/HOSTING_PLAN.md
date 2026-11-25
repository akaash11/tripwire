# Hosting Tripwire online + MCP for Claude / Cursor (plan, 2026-11-23)

**Status (2026-11-25): step 1 (Vercel refactor) is built and tested; see `docs/STATUS.md` "Hosted (v2.0)" for what was verified. MCP (steps 2 and 3) is not started.**

Goal: Tripwire keeps running without the laptop, on our older Gemini key, and people can plug it into Claude Code,
Claude Desktop or Cursor as a tool.

## What blocks a plain Vercel deploy today
| Today (laptop) | Why it breaks on serverless | Fix |
|---|---|---|
| `asyncio.create_task(process_request)` runs the 30 to 90 s agent after `/request` returns (`server/app.py:195`) | A serverless function can be frozen once the response is sent; the agent stops mid-run | Durable background job (Vercel Workflow / Queues), or a long-lived container |
| `STATE` and `SESSIONS` in memory (`server/app.py:33`); SESSIONS hold the secret checkout link | Each request may hit a different instance; memory is gone between calls | Shared store (Redis / Postgres); checkout links encrypted at rest |
| `data/state.json`, `data/used_nonces.json` (replay protection) | Read-only, per-instance disk | Same shared store; nonces need an atomic "set if absent" |
| `keys/tripwire_ed25519.pem` created on first use | Disk is not persistent | Key in an encrypted env var |
| Admin page is local-only (`is_local`) and edits `.env` | No localhost; env is read-only at runtime | Real admin login; settings in the store |
| Rate limit in memory (10/min) | Per instance, not global | Rate limit and daily spend cap in the store |

## Constraint: it must cost nothing (Akaash, 2026-11-23)
| Option | Cost | Effort | Catch |
|---|---|---|---|
| **Vercel Hobby + Upstash Redis free** (Marketplace) | Free, no card | 1 to 2 days refactor | Hobby is for personal, non-commercial projects. The agent run must fit in one function call, so the live page drives it (see below) |
| Render free web service | Free | Hours (Dockerfile) | Sleeps after ~15 min idle (first request waits ~1 min); disk resets on sleep, so history and nonces are lost |
| The Spark (already owned, always on) | Free | Hours | Needs a new public Funnel on the Spark: a network exposure only Akaash can approve |
| Google Cloud Run free tier | Free within quota | Hours | Needs a billing account with a card; background work after the response needs paid "CPU always on" |

**Recommendation: Vercel Hobby + Upstash Redis free.** No card, no sleeping, and the short link is already on Vercel.

How the 30 to 90 s agent run fits serverless: `/request` only compiles the contract and returns the token (about 10 s).
The live page `/r/{token}` then calls `POST /r/{token}/run`, which runs the agent inside that one function call and
streams events (well under Vercel's 300 s limit). If the shopper closes the page, the run stops, which is fine for a
"you're watching it" flow. State, sessions (checkout links encrypted), nonces (atomic set-if-absent), rate limits and
the daily cap go in Upstash Redis. The signing key goes in an encrypted env var.

**Gemini cost:** API calls on the older key are billed if that key is on a paid project. A free-tier key has no charge
but tight quotas, and Gemini 4 Neon and the Decisions API may not be on the free tier. Check the key's project billing
before going live; the daily cap below bounds the worst case either way.

## Protecting the Gemini key when it's public
A public URL spends our key for anyone who finds it. Before going live:
- global rate limit (per IP and total per minute), and a **daily request cap**: stop and show "come back tomorrow"
- optional invite code (one shared code in the Shortcut / page) to keep bots out
- usage log already exists (`log_usage`); add an alert when daily spend passes a threshold
- the key stays server-side only; Shortcuts and pages never see it

## MCP for Claude / Cursor
Expose the hosted server as a **remote MCP server** (streamable HTTP at `https://<host>/mcp`). People then add one URL
(no download) in Claude Code (`claude mcp add --transport http tripwire https://<host>/mcp`), Claude Desktop or Cursor.

Tools (thin wrappers over what exists):
- `tripwire_shop(request, budget?, photo_url?)`: compiles the contract, runs the guarded agent, returns the live page
  link (`/r/{token}`) and the outcome. The person approves and pays on that page; the MCP never returns a checkout link.
- `tripwire_status(token)`: the outcome and plain-English reasons.
- Later, `tripwire_check(contract, cart)`: the guard alone, for agents that already shop and want a firewall before
  checkout (the "Tripwire for every agent" roadmap item).

Also ship a tiny `uvx tripwire-mcp` stdio wrapper for people whose client can't add a remote MCP server; it just forwards to the
hosted URL.

## Order of work
1. Vercel refactor: Upstash store, page-driven `/run`, key in env, admin login, global rate limit + daily cap; deploy; point the short link at it.
2. `/mcp` endpoint with `tripwire_shop` + `tripwire_status`; README "Use from Claude / Cursor" section.
3. Speed fix found on demo day: reject on delivery/budget from the prequote before running the guard; hard-cap buy
   attempts at 3.
