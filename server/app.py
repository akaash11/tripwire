"""Tripwire server. Owner: Akaash.

Phone routes (iPhone Shortcuts; on the laptop via tailscale serve at /tripwire, hosted on Vercel at the root):
  POST /request       multipart audio + photo (or `text`) -> {"speak", "contract_id", "token", "view_url"}
                      compiles the contract (~10 s) and returns; the agent run is started by the live page
  POST /r/{token}/run the live page calls this once; runs the agent inside this one call (no background tasks)
  GET  /pending, POST /approve   legacy Approve shortcut (local only)
Dashboard API (localhost only): GET /api/state

All request/session/limit state goes through `store` (server/store.py): local files on the laptop, Upstash Redis
when hosted on Vercel.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import re
import secrets
import time
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

import common
import guard
from common import HOSTED, load_env
from server import config, persistence
from server.store import STALE_AFTER_S, make_store

load_env()

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
if not HOSTED:
    UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="Tripwire")

STATE: dict = {"requests": [], "pending": None, "orders": [], "events": [], "contracts": {}}
SESSIONS: dict = {}   # laptop mode only: request_id -> live ShopSession (hosted mode keeps sessions in Redis)
SNAPSHOTS: dict = {}  # laptop mode only: sanitized read-only view (survives restarts; zero secrets)

if not HOSTED:
    persistence.load_state(STATE, SNAPSHOTS)


def persist() -> None:
    """Atomically save sanitized STATE (never continue_url, checkout ids, signatures, or file paths). Laptop only."""
    if not HOSTED:
        persistence.save_state(STATE, SESSIONS, SNAPSHOTS)


store = make_store(STATE, SESSIONS, SNAPSHOTS, persist)
if store.nonce_backend is not None:
    guard.replay.set_nonce_backend(store.nonce_backend)  # replay protection in Redis (atomic set-if-absent)
if HOSTED:
    common.usage_hook = store.add_tokens  # no usage file on a read-only disk; count tokens in Redis

RUN_DEADLINE_S = 250  # the agent run must finish inside one function call (Vercel Hobby allows up to 300 s)


def event(kind: str, **data) -> None:
    store.append_event({"ts": time.time(), "kind": kind, **data})


@app.middleware("http")
async def strip_prefix(request: Request, call_next):
    # tailscale serve strips /tripwire, but accept it either way (also keeps old Shortcut links working online).
    p = request.scope["path"]
    if p.startswith("/tripwire/"):
        request.scope["path"] = p[len("/tripwire"):]
    return await call_next(request)


@app.middleware("http")
async def hosted_no_admin(request: Request, call_next):
    """Hosted: the admin pages and APIs are local-only by design; every path under them answers 403."""
    p = request.scope["path"]
    if p.startswith("/tripwire/"):  # this middleware runs before strip_prefix
        p = p[len("/tripwire"):]
    if HOSTED and (p == "/admin" or p.startswith(("/admin/", "/api/admin"))):
        return JSONResponse({"error": "admin is local only"}, status_code=403)
    return await call_next(request)


def is_local(request: Request) -> bool:
    """True only for direct calls on this machine. tailscale serve/funnel also connect from 127.0.0.1,
    but they add X-Forwarded-For and Tailscale-* headers (funnel traffic carries no user login at all).
    Never true when hosted: no header or Host value is trusted for admin access."""
    if HOSTED:
        return False
    if request.client is None or request.client.host != "127.0.0.1":
        return False
    h = {k.lower() for k in request.headers.keys()}
    return not ({"x-forwarded-for", "x-forwarded-host", "forwarded"} & h or any(k.startswith("tailscale-") for k in h))


def client_ip(request: Request) -> str:
    """Only used for rate limiting. Vercel sets x-real-ip itself (a caller can't forge it)."""
    if HOSTED:
        ip = request.headers.get("x-real-ip") or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if ip:
            return re.sub(r"[^0-9A-Za-z:.]", "", ip)[:45]
    return request.client.host if request.client else "unknown"


# Public phone routes: a rate limit and a daily cap so a public URL can't burn the Gemini key.
RATE_LIMIT_PER_MIN = config.get_runtime_settings()["rate_limit_per_min"]
GLOBAL_RATE_LIMIT_PER_MIN = int(os.environ.get("GLOBAL_RATE_LIMIT_PER_MIN", "30"))
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def daily_cap() -> int:
    """DAILY_REQUEST_CAP: valid requests per UTC day (default 50 when hosted; 0 = unlimited, the laptop default)."""
    try:
        return int(os.environ.get("DAILY_REQUEST_CAP", "50" if HOSTED else "0"))
    except ValueError:
        return 50


def rate_limited(request: Request) -> bool:
    return store.rate_hit(client_ip(request), RATE_LIMIT_PER_MIN, GLOBAL_RATE_LIMIT_PER_MIN)


def budget_reached() -> bool:
    budget = config.get_runtime_settings()["daily_token_budget"]
    return budget > 0 and store.today_tokens() >= budget


def expire_if_stale(r: dict | None) -> dict | None:
    """Hosted only: a run whose function was killed leaves status 'shopping' forever. If no event arrived for
    STALE_AFTER_S (or the run lock is gone), mark it expired so the page says 'start again' instead of spinning."""
    if not store.hosted or not r or r.get("status") not in ("contract_ready", "shopping"):
        return r
    started = store.run_started(r["id"])
    if started is None and r["status"] == "contract_ready":
        return r  # the page has not started it yet
    last = max(store.last_event_ts(r["id"]) or 0, started or 0)
    if started is None or time.time() - last > STALE_AFTER_S:
        store.mark_cancelled(r["id"])
        return store.update_request(r["id"], status="expired",
                                    problem="Tripwire ran out of time before finishing. Nothing was bought. Start again.")
    return r


def run_request(req_id: str) -> None:
    """The agent pipeline for one request: agent -> guard -> outcome. Blocking; runs in a worker thread.
    Writes every event to the store as it goes."""
    from agent.run import RunCancelled, run as run_agent
    from agent.tools import ShopSession

    r = store.get_request(req_id)
    contract = store.get_contract(r["contract_id"])
    session: ShopSession | None = None

    def emit(kind, **d):
        event(kind, request_id=req_id, **d)
        if store.hosted and session is not None and store.is_cancelled(req_id):
            session.cancelled = True

    session = ShopSession(contract, on_event=emit)
    event("pipeline_started", request_id=req_id)
    store.update_request(req_id, status="shopping")
    if not store.hosted:
        store.save_session(req_id, session)  # laptop: the live page reads this object while the agent works
    deadline = time.time() + RUN_DEADLINE_S if store.hosted else None

    def expired(reason: str, kind: str, err: str) -> None:
        event(kind, request_id=req_id, error=err[:500])
        store.update_request(req_id, status="expired" if store.hosted else "agent_failed", problem=reason)
        store.save_session(req_id, session)

    try:
        run_agent(contract, session, deadline=deadline)
    except RunCancelled:
        expired("Tripwire ran out of time before finishing. Nothing was bought. Start again.", "agent_failed", "timed out")
        return
    except Exception as e:  # noqa: BLE001
        if store.hosted:
            expired("Something went wrong while shopping. Nothing was bought. Start again.", "agent_failed",
                    f"{type(e).__name__}: {e}")
        else:
            event("agent_failed", request_id=req_id, error=f"{type(e).__name__}: {e}"[:500])
            store.update_request(req_id, status="agent_failed")
        return
    cur = store.get_request(req_id) or {}
    if cur.get("status") == "expired":  # the page already gave up on this run; don't resurrect it
        session.cancelled = True
        return

    v = session.verdict
    if v is None:
        updates = {"status": "no_checkout", "problem": getattr(session, "last_problem", None)}
        ob = getattr(session, "over_budget", None)
        if ob:
            updates["status"] = "over_budget"
            updates["offer"] = {"cart_id": ob["cart"]["cart_id"], "summary": ob["summary"],
                                "total_cents": ob["total_cents"], "budget_cents": ob["budget_cents"],
                                "budget_stated": bool(contract.get("budget_stated", True))}
        store.update_request(req_id, **updates)
        store.save_session(req_id, session)
        return
    updates = {"status": v["verdict"]}
    if session.pending_cart:
        c = session.pending_cart
        store.set_pending({"cart_id": c["cart_id"], "request_id": req_id, "verdict": v["verdict"],
                           "summary": f"Agent wants: {c['summary']}. You asked: {contract.get('summary')}",
                           "reason": "; ".join(v.get("xray", {}).get("reasons", []))[:300]})
    if session.continue_url and not session.pending_cart:
        updates["pay_ready"] = True
    if session.checkout and not session.pending_cart:
        cid = session.checkout.get("cart_id") or v.get("cart_id")
        if not store.has_order(cid, req_id):
            store.add_order({"cart_id": cid, "request_id": req_id, "ts": time.time(), "checkout": session.checkout,
                             "status": session.checkout.get("status") or "awaiting_payment",
                             "agent_summary": getattr(session, "agent_summary", None)})
    store.update_request(req_id, **updates)
    store.save_session(req_id, session)


async def start_run(req_id: str) -> bool:
    """Run the pipeline once (the lock stops a second caller). Returns False if it was already started."""
    if not store.acquire_run(req_id):
        return False
    await asyncio.to_thread(run_request, req_id)
    return True


@app.post("/r/{token}/run")
async def run_route(token: str):
    """Called by the live page once on load. Runs the agent in this one call (hosted: no background tasks)."""
    r = store.find_by_token(token)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    if r.get("status") != "contract_ready":
        return {"ok": True, "started": False, "status": r.get("status")}
    started = await start_run(r["id"])
    return {"ok": True, "started": started, "status": (store.get_request(r["id"]) or {}).get("status")}


async def _create_request(files: dict, text: str | None, client: str, default_client: str) -> dict:
    """files: {"audio"|"photo": (bytes, filename, content_type)}. Compiles the contract and returns the page token."""
    from agent.contract import compile_contract

    cap = daily_cap()
    if cap and store.daily_incr() > cap:  # counted only after the request is known to be valid
        return {"speak": "Tripwire is resting for today, try tomorrow.", "contract_id": None}
    req_id = "rq_" + uuid.uuid4().hex[:8]
    token = secrets.token_urlsafe(16)  # per-request view/decide token (web page + shopper chat page)
    client = re.sub(r"[^A-Za-z0-9 ._-]", "", client or "")[:40] or default_client  # e.g. "Tripwire Buy 2.0"
    meta = {}
    for name, (data, filename, ctype) in files.items():
        meta[name] = {"bytes": len(data), "type": ctype}
        if not HOSTED:  # laptop keeps the upload so the page can show the photo; hosted never stores it
            out = UPLOADS / f"{req_id}_{name}{pathlib.Path(filename or '').suffix or ''}"
            out.write_bytes(data)
            meta[name]["path"] = str(out)
    store.new_request({"id": req_id, "ts": time.time(), "files": meta, "text": text, "status": "compiling_contract",
                       "token": token, "client": client})
    event("request_received", request_id=req_id, files={k: v["bytes"] for k, v in meta.items()}, text=text, client=client)
    audio = (files["audio"][0], files["audio"][1]) if "audio" in files else None
    photo = files["photo"][0] if "photo" in files else None
    try:
        contract = await asyncio.to_thread(compile_contract, audio, photo, text)
    except Exception as e:  # noqa: BLE001
        store.update_request(req_id, status="contract_failed")
        event("contract_failed", request_id=req_id, error=str(e)[:500])
        return {"speak": "Got it.", "contract_id": req_id, "token": token, "view_url": f"/r/{token}"}
    store.save_contract(contract)
    store.update_request(req_id, status="contract_ready", contract_id=contract["contract_id"])
    event("contract_ready", request_id=req_id, contract_id=contract["contract_id"],
          summary=contract.get("summary"), compiled_by=contract.get("compiled_by"))
    if not store.hosted:
        asyncio.create_task(start_run(req_id))  # laptop: also works for requests that never open the page (admin)
    return {"speak": "Got it. Opening your Tripwire page.", "contract_id": req_id, "token": token,
            "view_url": f"/r/{token}"}


@app.post("/request")
async def request_route(request: Request, audio: UploadFile | None = File(None), photo: UploadFile | None = File(None),
                        text: str | None = Form(None), client: str | None = Form(None)):
    if rate_limited(request):
        return JSONResponse({"speak": "Tripwire is busy. Try again in a minute.", "contract_id": None}, status_code=429)
    if budget_reached():
        return JSONResponse({"speak": "Daily token budget reached.", "contract_id": None}, status_code=429)
    files = {}
    for name, f in (("audio", audio), ("photo", photo)):
        if f is None:
            continue
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse({"speak": f"The {name} is too large.", "contract_id": None}, status_code=413)
        if data:
            files[name] = (data, f.filename or "", f.content_type)
    text = (text or "").strip()[:500] or None
    if not files and not text:
        return JSONResponse({"speak": "Tripwire didn't receive your voice or photo. Please try again.",
                             "contract_id": None}, status_code=400)
    return await _create_request(files, text, client or "", "unknown")


@app.post("/request_text")
async def request_text(body: dict, request: Request):
    """Typed request (dashboard / testing without the phone). Same limits as /request."""
    if rate_limited(request):
        return JSONResponse({"speak": "Tripwire is busy. Try again in a minute.", "contract_id": None}, status_code=429)
    if budget_reached():
        return JSONResponse({"speak": "Daily token budget reached.", "contract_id": None}, status_code=429)
    text = str(body.get("text") or "").strip()[:500] or None
    if not text:
        return JSONResponse({"speak": "Type what you want to buy.", "contract_id": None}, status_code=400)
    return await _create_request({}, text, str(body.get("client") or ""), "local" if not HOSTED else "web")


@app.get("/pending")
async def pending():
    p = store.get_pending()
    if not p:
        c = store.get_contract(STATE.get("current_contract")) if not store.hosted else None
        summary = f"Nothing to approve yet. Your contract: {c['summary']}" if c else "Nothing waiting for approval."
        return {"cart_id": None, "summary": summary, "verdict": None, "reason": ""}
    return {k: p.get(k) for k in ("cart_id", "summary", "verdict", "reason")}


async def decide(p: dict, decision: str) -> dict:
    """Shared by the Approve shortcut (/approve), web page (/w/{token}/decide), and admin (/api/admin/approve)."""
    req_id = p.get("request_id")
    event("human_decision", request_id=req_id, cart_id=p["cart_id"], decision=decision)
    store.clear_pending(p["cart_id"])
    session = store.load_session(req_id)
    if session is not None:
        session.pending_cart = None
    if decision != "approve" or session is None:
        store.update_request(req_id, status="block")
        if session is not None:
            store.save_session(req_id, session)
        return {"ok": True, "speak": "Blocked. Nothing was bought.", "url": ""}
    try:
        view = await asyncio.to_thread(session.open_checkout, session.carts[p["cart_id"]])
    except Exception as e:  # noqa: BLE001
        event("checkout_failed", request_id=req_id, cart_id=p["cart_id"], error=str(e)[:300])
        return {"ok": False, "speak": "Shop checkout failed. Try again.", "url": ""}
    store.update_request(req_id, status="approve", pay_ready=bool(session.continue_url))
    total = next((t for t in view["totals"] if t["type"] == "total"), {})
    store.add_order({"cart_id": p["cart_id"], "request_id": req_id, "ts": time.time(), "checkout": view,
                     "status": view.get("status") or "awaiting_payment",
                     "agent_summary": getattr(session, "agent_summary", None)})
    store.save_session(req_id, session)
    amount = f"${total.get('amount', 0)/100:.2f}" if total.get("amount") is not None else ""
    return {"ok": True, "url": session.continue_url or "",
            "speak": f"Approved. Opening the Shop checkout for {amount}. Pay there with Shop Pay."}


@app.post("/approve")
async def approve(body: dict):
    decision = str(body.get("decision", "")).lower()
    p = store.get_pending()
    if not p or p.get("cart_id") != body.get("cart_id"):
        return {"ok": False, "speak": "That cart is no longer pending.", "url": ""}
    return await decide(p, decision)


@app.get("/api/state")
async def api_state(request: Request):
    if not is_local(request):
        return JSONResponse({"error": "dashboard is local only"}, status_code=403)
    from server.dashboard_api import sanitize_checkout, sanitize_contract, sanitize_verdict

    sessions = {
        rid: {
            "verdict": sanitize_verdict(s.verdict, admin=True),
            "checkout": sanitize_checkout(s.checkout),
            "carts": s.carts,
            "agent_summary": getattr(s, "agent_summary", None),
            "pay_ready": bool(s.continue_url),
        }
        for rid, s in SESSIONS.items()
    }
    safe_requests = [
        {**{k: v for k, v in r.items() if k != "files"},
         "files": {fk: {"bytes": fv.get("bytes"), "type": fv.get("type")} for fk, fv in (r.get("files") or {}).items()},
         "view_url": f"/r/{r['token']}" if r.get("token") else None}
        for r in STATE["requests"]
    ]
    safe_contracts = {
        cid: sanitize_contract(c, include_attempts=True)
        for cid, c in STATE.get("contracts", {}).items()
    }
    return {
        **STATE,
        "requests": safe_requests,
        "contracts": safe_contracts,
        "enforced": guard.enforced(),
        "sessions": sessions,
    }


@app.get("/shortcuts/{name}")
async def shortcut_file(name: str):
    """Signed iPhone Shortcuts, for anyone with the link. Only the known files are served."""
    from fastapi.responses import FileResponse
    files = {"buy": "Tripwire Buy.shortcut", "check": "Check Shopify.shortcut", "approve": "Tripwire Approve.shortcut"}
    if name not in files:
        return JSONResponse({"error": "unknown shortcut"}, status_code=404)
    for folder in (ROOT / "phone" / "dist", ROOT / "phone" / "build" / "public"):  # dist is tracked, build is local
        path = folder / files[name]
        if path.exists():
            return FileResponse(path, filename=files[name], media_type="application/octet-stream")
    return JSONResponse({"error": "not built"}, status_code=404)


@app.get("/pubkey")
async def pubkey():
    from common.signing import KEY_ID, public_pem
    return {"key_id": KEY_ID, "alg": "Ed25519", "public_key_pem": public_pem()}


from server.web import router as web_router  # noqa: E402  (phone web page for judges: /buy)
from server.dashboard_api import router as dashboard_router  # noqa: E402  (shopper /r/{token} + admin /admin)

app.include_router(web_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {"ok": True}
