"""Tripwire server. Owner: Akaash.

Phone routes (iPhone Shortcuts, via tailscale serve at /tripwire on 443):
  POST /request   multipart audio + photo -> {"speak", "contract_id"}   (replies fast, works in background)
  GET  /pending   -> {"cart_id", "summary", "verdict", "reason"} or {"cart_id": null}
  POST /approve   JSON {"cart_id", "decision"} -> {"ok", "speak"}
Dashboard API (localhost only): GET /api/state
"""
from __future__ import annotations

import asyncio
import pathlib
import time
import uuid

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="Tripwire")

# In-memory state for the POC. Replace with a small JSON/SQLite store if needed.
STATE: dict = {"requests": [], "pending": None, "orders": [], "events": []}


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
    # tailscale serve forwards from 127.0.0.1 but adds Tailscale-User-Login; direct local calls don't.
    return request.client.host == "127.0.0.1" and "tailscale-user-login" not in request.headers


async def process_request(req_id: str) -> None:
    """Background pipeline: Neon contract -> agent -> guard. Stubbed until wired."""
    event("pipeline_started", request_id=req_id)
    # TODO(akaash): agent.contract.compile(audio, photo) -> contract; agent.run(contract) -> cart; guard.check(...)


@app.post("/request")
async def request_route(audio: UploadFile | None = File(None), photo: UploadFile | None = File(None)):
    req_id = "rq_" + uuid.uuid4().hex[:8]
    saved = {}
    for name, f in (("audio", audio), ("photo", photo)):
        if f is None:
            continue
        data = await f.read()
        out = UPLOADS / f"{req_id}_{name}{pathlib.Path(f.filename or '').suffix or ''}"
        out.write_bytes(data)
        saved[name] = {"path": str(out), "bytes": len(data), "type": f.content_type}
    STATE["requests"].append({"id": req_id, "ts": time.time(), "files": saved, "status": "received"})
    event("request_received", request_id=req_id, files={k: v["bytes"] for k, v in saved.items()})
    asyncio.create_task(process_request(req_id))
    return {"speak": "Got it. Tripwire is building your contract. Check the dashboard.", "contract_id": req_id}


@app.get("/pending")
async def pending():
    p = STATE["pending"]
    if not p:
        return {"cart_id": None, "summary": "Nothing waiting for approval.", "verdict": None, "reason": ""}
    return {k: p.get(k) for k in ("cart_id", "summary", "verdict", "reason")}


@app.post("/approve")
async def approve(body: dict):
    decision = str(body.get("decision", "")).lower()
    p = STATE["pending"]
    if not p or p.get("cart_id") != body.get("cart_id"):
        return {"ok": False, "speak": "That cart is no longer pending."}
    event("human_decision", cart_id=p["cart_id"], decision=decision)
    STATE["pending"] = None
    # TODO(akaash): on approve -> mock checkout + signed receipt into STATE["orders"]
    return {"ok": True, "speak": f"Recorded: {decision}."}


@app.get("/api/state")
async def api_state(request: Request):
    if not is_local(request):
        return JSONResponse({"error": "dashboard is local only"}, status_code=403)
    return STATE


@app.get("/health")
async def health():
    return {"ok": True}
