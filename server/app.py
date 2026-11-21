"""Tripwire server. Owner: Akaash.

Phone routes (iPhone Shortcuts, via tailscale serve at /tripwire on 443):
  POST /request   multipart audio + photo -> {"speak", "contract_id"}   (replies fast, works in background)
  GET  /pending   -> {"cart_id", "summary", "verdict", "reason"} or {"cart_id": null}
  POST /approve   JSON {"cart_id", "decision"} -> {"ok", "speak"}
Dashboard API (localhost only): GET /api/state
"""
from __future__ import annotations

import asyncio
import re
import pathlib
import secrets
import time
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

import guard
from common import load_env
from server import config

load_env()

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="Tripwire")

# In-memory state for the POC. Replace with a small JSON/SQLite store if needed.
STATE: dict = {"requests": [], "pending": None, "orders": [], "events": []}
SESSIONS: dict = {}  # request_id -> agent.tools.ShopSession (holds the secret continue_url; never serialized)


def event(kind: str, **data) -> None:
    STATE["events"].append({"ts": time.time(), "kind": kind, **data})


@app.middleware("http")
async def strip_prefix(request: Request, call_next):
    # tailscale serve strips /tripwire, but accept it either way.
    p = request.scope["path"]
    if p.startswith("/tripwire/"):
        request.scope["path"] = p[len("/tripwire"):]
    return await call_next(request)


def is_local(request: Request) -> bool:
    """True only for direct calls on this machine. tailscale serve/funnel also connect from 127.0.0.1,
    but they add X-Forwarded-For and Tailscale-* headers (funnel traffic carries no user login at all)."""
    if request.client is None or request.client.host != "127.0.0.1":
        return False
    h = {k.lower() for k in request.headers.keys()}
    return not ({"x-forwarded-for", "x-forwarded-host", "forwarded"} & h or any(k.startswith("tailscale-") for k in h))


# Public phone routes: a global rate limit so a public URL can't burn the Gemini key.
_HITS: list[float] = []
RATE_LIMIT_PER_MIN = config.get_runtime_settings()["rate_limit_per_min"]
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def rate_limited() -> bool:
    now = time.time()
    _HITS[:] = [t for t in _HITS if now - t < 60]
    if len(_HITS) >= RATE_LIMIT_PER_MIN:
        return True
    _HITS.append(now)
    return False


def budget_reached() -> bool:
    budget = config.get_runtime_settings()["daily_token_budget"]
    return budget > 0 and config.today_tokens() >= budget


def _req(req_id: str) -> dict:
    return next(r for r in STATE["requests"] if r["id"] == req_id)


async def process_request(req_id: str) -> None:
    """Background pipeline: Neon contract -> agent -> guard."""
    from agent.contract import compile_contract

    r = _req(req_id)
    files = r["files"]
    event("pipeline_started", request_id=req_id)
    r["status"] = "compiling_contract"
    try:
        contract = await asyncio.to_thread(
            compile_contract,
            files.get("audio", {}).get("path"), files.get("photo", {}).get("path"), r.get("text"))
    except Exception as e:  # noqa: BLE001
        r["status"] = "contract_failed"
        event("contract_failed", request_id=req_id, error=str(e)[:500])
        return
    r["status"] = "contract_ready"
    r["contract_id"] = contract["contract_id"]
    STATE.setdefault("contracts", {})[contract["contract_id"]] = contract
    STATE["current_contract"] = contract["contract_id"]
    event("contract_ready", request_id=req_id, contract_id=contract["contract_id"],
          summary=contract.get("summary"), compiled_by=contract.get("compiled_by"))

    from agent.run import run as run_agent
    from agent.tools import ShopSession

    def emit(kind, **d):
        event(kind, request_id=req_id, **d)

    session = ShopSession(contract, on_event=emit)
    SESSIONS[req_id] = session
    r["status"] = "shopping"
    try:
        await asyncio.to_thread(run_agent, contract, session)
    except Exception as e:  # noqa: BLE001
        r["status"] = "agent_failed"
        event("agent_failed", request_id=req_id, error=f"{type(e).__name__}: {e}"[:500])
        return
    v = session.verdict
    if v is None:
        r["status"] = "no_checkout"
        r["problem"] = getattr(session, "last_problem", None)
        return
    r["status"] = v["verdict"]
    if session.pending_cart:
        c = session.pending_cart
        STATE["pending"] = {"cart_id": c["cart_id"], "request_id": req_id, "verdict": v["verdict"],
                            "summary": f"Agent wants: {c['summary']}. You asked: {contract.get('summary')}",
                            "reason": "; ".join(v.get("xray", {}).get("reasons", []))[:300]}
    if session.continue_url:
        r["pay_ready"] = True
    if session.checkout:
        cid = session.checkout.get("cart_id") or (v.get("cart_id") if v else None)
        if not any(o.get("cart_id") == cid and o.get("request_id") == req_id for o in STATE["orders"]):
            STATE["orders"].append({
                "cart_id": cid,
                "request_id": req_id,
                "ts": time.time(),
                "checkout": session.checkout,
                "status": session.checkout.get("status") or "awaiting_payment",
                "agent_summary": getattr(session, "agent_summary", None),
            })


@app.post("/request")
async def request_route(audio: UploadFile | None = File(None), photo: UploadFile | None = File(None),
                        text: str | None = Form(None), client: str | None = Form(None)):
    if rate_limited():
        return JSONResponse({"speak": "Tripwire is busy. Try again in a minute.", "contract_id": None}, status_code=429)
    if budget_reached():
        return JSONResponse({"speak": "Daily token budget reached.", "contract_id": None}, status_code=429)
    req_id = "rq_" + uuid.uuid4().hex[:8]
    saved = {}
    for name, f in (("audio", audio), ("photo", photo)):
        if f is None:
            continue
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse({"speak": f"The {name} is too large.", "contract_id": None}, status_code=413)
        out = UPLOADS / f"{req_id}_{name}{pathlib.Path(f.filename or '').suffix or ''}"
        out.write_bytes(data)
        saved[name] = {"path": str(out), "bytes": len(data), "type": f.content_type}
    text = (text or "").strip()[:500] or None
    if not saved and not text:
        event("request_empty", request_id=req_id)
        return JSONResponse({"speak": "Tripwire didn't receive your voice or photo. Please try again.",
                             "contract_id": None}, status_code=400)
    token = secrets.token_urlsafe(16)  # per-request view/decide token (web page + shopper chat page)
    client = re.sub(r"[^A-Za-z0-9 ._-]", "", client or "")[:40] or "unknown"  # e.g. "Tripwire Buy 1.3", "web 1.3"
    STATE["requests"].append({"id": req_id, "ts": time.time(), "files": saved, "text": text, "status": "received",
                              "token": token, "client": client})
    event("request_received", request_id=req_id, files={k: v["bytes"] for k, v in saved.items()}, text=text,
          client=client)
    asyncio.create_task(process_request(req_id))
    return {"speak": "Got it. Opening your Tripwire page.",
            "contract_id": req_id, "token": token, "view_url": f"/r/{token}"}


@app.get("/pending")
async def pending():
    p = STATE["pending"]
    if not p:
        cid = STATE.get("current_contract")
        c = STATE.get("contracts", {}).get(cid) if cid else None
        summary = f"Nothing to approve yet. Your contract: {c['summary']}" if c else "Nothing waiting for approval."
        return {"cart_id": None, "summary": summary, "verdict": None, "reason": ""}
    return {k: p.get(k) for k in ("cart_id", "summary", "verdict", "reason")}


async def decide(p: dict, decision: str) -> dict:
    """Shared by the Approve shortcut (/approve), web page (/w/{token}/decide), and admin (/api/admin/approve)."""
    event("human_decision", request_id=p.get("request_id"), cart_id=p["cart_id"], decision=decision)
    if STATE["pending"] and STATE["pending"].get("cart_id") == p["cart_id"]:
        STATE["pending"] = None
    session = SESSIONS.get(p["request_id"])
    req = next((r for r in STATE["requests"] if r["id"] == p.get("request_id")), None)
    if session is not None:
        session.pending_cart = None
    if decision != "approve" or session is None:
        if req is not None:
            req["status"] = "block"
        return {"ok": True, "speak": "Blocked. Nothing was bought.", "url": ""}
    try:
        view = await asyncio.to_thread(session.open_checkout, session.carts[p["cart_id"]])
    except Exception as e:  # noqa: BLE001
        event("checkout_failed", request_id=p.get("request_id"), cart_id=p["cart_id"], error=str(e)[:300])
        return {"ok": False, "speak": "Shop checkout failed. Try again.", "url": ""}
    if req is not None:
        req["status"] = "approve"
        req["pay_ready"] = bool(session.continue_url)
    total = next((t for t in view["totals"] if t["type"] == "total"), {})
    STATE["orders"].append({"cart_id": p["cart_id"], "request_id": p["request_id"], "ts": time.time(),
                            "checkout": view, "status": view.get("status") or "awaiting_payment",
                            "agent_summary": getattr(session, "agent_summary", None)})
    amount = f"${total.get('amount', 0)/100:.2f}" if total.get("amount") is not None else ""
    return {"ok": True, "url": session.continue_url or "",
            "speak": f"Approved. Opening the Shop checkout for {amount}. Pay there with Shop Pay."}


@app.post("/approve")
async def approve(body: dict):
    decision = str(body.get("decision", "")).lower()
    p = STATE["pending"]
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
    """Signed iPhone Shortcuts, for anyone with the link. Only the two known files are served."""
    from fastapi.responses import FileResponse
    files = {"buy": "Tripwire Buy.shortcut", "approve": "Tripwire Approve.shortcut"}
    if name not in files:
        return JSONResponse({"error": "unknown shortcut"}, status_code=404)
    path = ROOT / "phone" / "build" / "public" / files[name]
    if not path.exists():
        return JSONResponse({"error": "not built"}, status_code=404)
    return FileResponse(path, filename=files[name], media_type="application/octet-stream")


@app.post("/request_text")
async def request_text(body: dict, request: Request):
    """Typed request (dashboard / testing without the phone). Local only."""
    if not is_local(request):
        return JSONResponse({"error": "local only"}, status_code=403)
    req_id = "rq_" + uuid.uuid4().hex[:8]
    token = secrets.token_urlsafe(16)
    STATE["requests"].append({"id": req_id, "ts": time.time(), "files": {}, "text": body.get("text", ""),
                              "status": "received", "token": token})
    event("request_received", request_id=req_id, text=body.get("text", ""))
    asyncio.create_task(process_request(req_id))
    return {"speak": "Got it.", "contract_id": req_id, "token": token, "view_url": f"/r/{token}"}


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
