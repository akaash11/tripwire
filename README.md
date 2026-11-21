# Tripwire

A payment firewall for AI shopping agents. Built at the Gemini 4 NYC Hackathon by Akaash and Tanushree.

You tell your phone what you want (voice + a photo). Gemini 4 Neon turns that into a signed **Intent Contract**.
A shopping agent shops the real Shopify catalog, and checkout only goes through if the cart satisfies the contract.

**Everything in this repo was built at the event.** Pre-event planning, API smoke tests and the iPhone Shortcuts
live in a separate repo, `akaash11/tripwire-prep` (see its `prep/TEAM_PLAN.md`, `TESTED.md`, `prep/INTERFACES.md`).

## Layout
| Path | Owner | What |
|---|---|---|
| `server/` | Akaash | FastAPI app: phone routes (`/request`, `/pending`, `/approve`), dashboard API |
| `agent/` | Akaash | Flash shopping agent (custom functions), Catalog MCP search, Neon contract |
| `guard/` | Tanu | `check(contract, cart, products) -> verdict`: policy, Decision 1, canary, replay/tamper, Neon checker |
| `evals/` | Tanu | Red-team harness (Carbon attacks, off vs on) → `evals/out/results.json` |
| `dashboard/` | Akaash | Control panel page (Live, Orders, Usage, Red-team, API keys) |

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add GEMINI_API_KEY, chmod 600 .env
.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8790 --reload
```
Phone access (tailnet only): `tailscale serve --bg --set-path /tripwire http://127.0.0.1:8790`.
