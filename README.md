# Tripwire

A payment firewall for AI shopping agents. Built at the Gemini 4 NYC Hackathon by **@labs** (Akaash and Tanushree).

You tell your phone what you want (voice + a photo). Gemini 4 Neon turns that into a signed **Intent Contract**.
A shopping agent shops the real Shopify catalog, and checkout only goes through if the cart satisfies the contract.

- **Event rules and sponsor requirements: [`docs/EVENT_REQUIREMENTS.md`](docs/EVENT_REQUIREMENTS.md)**
- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Team plan and task split: [`docs/TEAM_PLAN.md`](docs/TEAM_PLAN.md)
- **What works now and what is next: [`docs/STATUS.md`](docs/STATUS.md)**

## Layout
| Path | Owner | What |
|---|---|---|
| `server/` | Akaash | FastAPI app: phone routes (`/request`, `/pending`, `/approve`), dashboard API |
| `agent/` | Akaash | Flash shopping agent (custom functions), Shop MCP search + real checkout, Neon contract |
| `guard/` | Tanu | `check(contract, cart, products) -> verdict`: policy, Decision 1, canary, replay/tamper, Neon checker |
| `evals/` | Tanu | Red-team harness (Carbon attacks, off vs on) → `evals/out/results.json` |
| `phone/` | Akaash | iPhone Shortcuts generator (Tripwire Buy, Tripwire Approve) |
| `dashboard/` | Akaash | Control panel page (Live, Orders, Usage, Red-team, API keys) |

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add GEMINI_API_KEY, chmod 600 .env
.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8790 --reload
```
Phone access (tailnet only): `tailscale serve --bg --set-path /tripwire http://127.0.0.1:8790`.
