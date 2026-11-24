"""Shopper chat page (/r/{token}) and local-only Admin control panel (/admin, /api/admin/*).

Security invariants:
- Every /admin and /api/admin/* route requires is_local(request) (403 otherwise).
- /r/{token}, /r/{token}/events, /r/{token}/photo are public and strictly scoped to that token's request.
- Secrets (continue_url, Shop checkout id, API keys, contract/receipt signatures, local file paths)
  are NEVER returned by any route in this module.
"""
from __future__ import annotations

import asyncio
import mimetypes
import pathlib
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import guard
from server import config

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "dashboard"
UPLOADS_DIR = (ROOT / "uploads").resolve()

router = APIRouter()


def _deny_if_remote(request: Request) -> JSONResponse | None:
    from server.app import is_local

    if not is_local(request):
        return JSONResponse({"error": "admin is local only"}, status_code=403)
    return None


def _find_request_by_token(token: str) -> dict | None:
    from server.app import expire_if_stale, store

    return expire_if_stale(store.find_by_token(token))


def _find_request_by_id(req_id: str) -> dict | None:
    from server.app import STATE

    for r in STATE["requests"]:
        if r.get("id") == req_id:
            return r
    return None


def _safe_photo_path(r: dict) -> tuple[pathlib.Path, str] | None:
    photo = (r.get("files") or {}).get("photo")
    if not photo:
        return None
    p: pathlib.Path | None = None
    if photo.get("path"):
        p = pathlib.Path(photo["path"]).resolve()
    else:
        req_id = r.get("id")
        if req_id and UPLOADS_DIR.is_dir():
            matches = sorted(UPLOADS_DIR.glob(f"{req_id}_photo*"))
            if matches:
                p = matches[0].resolve()
    if p is None:
        return None
    try:
        p.relative_to(UPLOADS_DIR)
    except ValueError:
        return None
    if not p.is_file():
        return None
    mime = photo.get("type") or mimetypes.guess_type(p.name)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return p, mime


def sanitize_contract(c: dict | None, *, include_attempts: bool = False) -> dict | None:
    """Return contract fields without cryptographic signature or nonce."""
    if not c:
        return None
    keep = (
        "contract_id", "created_at", "expires_at", "summary", "user_request", "item",
        "must", "must_not", "max_total_cents", "budget_stated", "amended_by",
        "quantity", "returns", "currency",
        "ships_to", "photo_observations", "compiled_by", "assumptions", "search_query",
    )
    out = {k: c.get(k) for k in keep if k in c}
    out["signed"] = bool(c.get("signature") or c.get("signed"))
    if include_attempts:
        out["compile_attempts"] = c.get("compile_attempts") or []
    return out


def sanitize_verdict(v: dict | None, *, admin: bool = False, contract: dict | None = None) -> dict | None:
    """Return verdict without receipt signatures or secrets. Shoppers get plain-English reasons."""
    if not v:
        return None
    xray = v.get("xray") or {}
    reasons = xray.get("reasons") or []
    if not admin:
        from common.plain import plain_reasons
        reasons = plain_reasons(reasons, contract)
        return {
            "verdict": v.get("verdict"),
            "enforced": v.get("enforced", True),
            "reason": reasons[0] if reasons else "",
            "reasons": reasons,
            "asked_for": xray.get("asked_for", ""),
            "hidden_in_listing": xray.get("hidden_in_listing", ""),
            "agent_tried": xray.get("agent_tried", ""),
        }
    receipt = v.get("receipt")
    safe_receipt = None
    if isinstance(receipt, dict):
        safe_receipt = {k: val for k, val in receipt.items() if k != "signature"}
        safe_receipt["signed"] = bool(receipt.get("signature") or receipt.get("signed"))
    return {
        "verdict": v.get("verdict"),
        "contract_id": v.get("contract_id"),
        "cart_id": v.get("cart_id"),
        "enforced": v.get("enforced", True),
        "stages": v.get("stages") or {},
        "xray": {
            "asked_for": xray.get("asked_for", ""),
            "hidden_in_listing": xray.get("hidden_in_listing", ""),
            "agent_tried": xray.get("agent_tried", ""),
            "reasons": reasons,
        },
        "receipt": safe_receipt,
        "latency_ms": v.get("latency_ms"),
    }


def sanitize_checkout(c: dict | None) -> dict | None:
    """Return checkout view strictly without checkout id or continue_url."""
    if not c:
        return None
    keep = (
        "cart_id", "shop_name", "status", "currency", "lines", "totals",
        "messages", "to_fill_on_shop", "links", "order", "expires_at",
    )
    return {k: c.get(k) for k in keep if k in c}


def _collect_req_carts(req_id: str, sess, snap: dict | None) -> set[str]:
    from server.app import STATE

    carts = set(sess.carts.keys()) if sess else set()
    if snap:
        for c in snap.get("carts") or []:
            if isinstance(c, dict) and c.get("cart_id"):
                carts.add(c["cart_id"])
    for ev in STATE["events"]:
        if ev.get("request_id") == req_id and ev.get("cart_id"):
            carts.add(ev["cart_id"])
    return carts


def _safe_shopper_events(req_id: str, sess=None) -> list[dict]:
    from server.app import SNAPSHOTS, store

    allowed_fields: dict[str, tuple[str, ...]] = {
        "request_received": ("text", "client"),
        "pipeline_started": (),
        "contract_ready": ("summary", "compiled_by"),
        "contract_failed": ("error",),
        "search": ("query", "results", "top_titles", "delivers"),
        "cart": ("cart_id", "summary", "title", "price_cents", "currency", "seller", "quantity", "image_url", "product_url"),
        "delivery_check": ("cart_id", "ok", "reason", "summary"),
        "budget_raised": ("cart_id", "new_budget_cents", "old_budget_cents", "verdict"),
        "verdict": ("cart_id", "verdict", "enforced", "reason"),
        "checkout_created": ("cart_id", "status", "totals"),
        "checkout_refreshed": ("cart_id", "status", "has_order"),
        "checkout_failed": ("error",),
        "human_decision": ("cart_id", "decision"),
        "agent_done": ("status", "summary", "seconds"),
        "agent_failed": ("error",),
    }
    out = []
    snap = SNAPSHOTS.get(req_id)
    req_carts = _collect_req_carts(req_id, sess, snap)

    for ev in store.events(req_id):
        ev_req = ev.get("request_id")
        ev_cart = ev.get("cart_id")
        if ev_req != req_id and not (ev_req is None and ev_cart and ev_cart in req_carts):
            continue
        kind = ev.get("kind")
        if kind not in allowed_fields:
            continue
        row = {"ts": ev.get("ts"), "kind": kind}
        for f in allowed_fields[kind]:
            if f in ev:
                row[f] = ev[f]
        out.append(row)
    return out


def _picked_product(session) -> dict | None:
    if not session or not session.carts:
        return None
    # Pick the pending cart, verdict cart, or most recent cart.
    cart = session.pending_cart
    if cart is None and session.verdict and session.verdict.get("cart_id"):
        cart = session.carts.get(session.verdict["cart_id"])
    if cart is None:
        cart = list(session.carts.values())[-1]
    line = (cart.get("lines") or [{}])[0]
    return {
        "cart_id": cart.get("cart_id"),
        "title": line.get("title"),
        "quantity": line.get("quantity", 1),
        "unit_price_cents": line.get("unit_price_cents"),
        "total_cents": cart.get("total_cents"),
        "currency": cart.get("currency", "USD"),
        "seller": (cart.get("payee") or {}).get("name"),
        "image_url": line.get("image_url"),
        "product_url": line.get("product_url"),
        "summary": cart.get("summary"),
    }


# ---------------------------------------------------------------------------
# Public assets & Page 1: Shopper chat page (/r/{token})
# ---------------------------------------------------------------------------

@router.get("/assets/styles.css")
async def asset_styles():
    return FileResponse(DASHBOARD_DIR / "styles.css", media_type="text/css")


@router.get("/assets/shopper.js")
async def asset_shopper_js():
    return FileResponse(DASHBOARD_DIR / "shopper.js", media_type="application/javascript")


@router.get("/assets/buy.js")
async def asset_buy_js():
    return FileResponse(DASHBOARD_DIR / "buy.js", media_type="application/javascript")


@router.get("/r/{token}")
async def shopper_page(token: str):
    r = _find_request_by_token(token)
    if not r:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Tripwire — Not found</title>"
            "<body style='font-family:system-ui;padding:2rem;max-width:32rem;margin:0 auto'>"
            "<h1>Request not found</h1><p>This Tripwire link is invalid or the server was restarted.</p></body>",
            status_code=404,
        )
    return FileResponse(DASHBOARD_DIR / "shopper.html", media_type="text/html")


@router.get("/r/{token}/events")
async def shopper_events(token: str):
    from server.app import SNAPSHOTS, store

    r = _find_request_by_token(token)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    req_id = r["id"]
    sess = store.load_session(req_id)
    snap = SNAPSHOTS.get(req_id) or {}
    contract = store.get_contract(r.get("contract_id"))
    if sess and getattr(sess, "contract", None) and sess.contract.get("amended_by") == "shopper":
        contract = sess.contract
    pending_global = store.get_pending()
    pending_approval = bool(
        (sess and sess.pending_cart)
        or (pending_global and pending_global.get("request_id") == req_id)
    )
    picked = _picked_product(sess) if sess else snap.get("picked")
    verdict_raw = sess.verdict if sess else snap.get("verdict")
    checkout_raw = sess.checkout if sess else snap.get("checkout")
    agent_summary = getattr(sess, "agent_summary", None) if sess else snap.get("agent_summary")
    return {
        "request": {
            "id": req_id,
            "ts": r.get("ts"),
            "status": r.get("status"),
            "client": r.get("client"),
            "has_photo": _safe_photo_path(r) is not None,
            "has_audio": bool((r.get("files") or {}).get("audio")),
            "text": r.get("text"),
            "pay_ready": bool((sess and sess.continue_url) or r.get("pay_ready")),
        },
        "contract": sanitize_contract(contract, include_attempts=False),
        "picked": picked,
        "verdict": sanitize_verdict(verdict_raw, admin=False, contract=contract),
        "checkout": sanitize_checkout(checkout_raw),
        "pending_approval": pending_approval,
        "agent_summary": agent_summary,
        "events": _plain_events(_safe_shopper_events(req_id, sess), contract),
    }


def _plain_events(events: list, contract: dict | None) -> list:
    from common.plain import plain_reason
    out = []
    for e in events or []:
        if isinstance(e, dict) and e.get("reason") and e.get("kind") in ("verdict", "final_check"):
            e = {**e, "reason": plain_reason(e["reason"], contract)}
        out.append(e)
    return out


@router.get("/r/{token}/photo")
async def shopper_photo(token: str):
    r = _find_request_by_token(token)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    found = _safe_photo_path(r)
    if not found:
        return JSONResponse({"error": "no photo"}, status_code=404)
    path, mime = found
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------------------
# Local-only Page 2: Admin page (/admin) and /api/admin/*
# ---------------------------------------------------------------------------

@router.get("/admin")
async def admin_page(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    return FileResponse(DASHBOARD_DIR / "admin.html", media_type="text/html")


@router.get("/admin/admin.js")
async def admin_js(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    return FileResponse(DASHBOARD_DIR / "admin.js", media_type="application/javascript")


@router.get("/api/admin/requests/{req_id}/photo")
async def admin_request_photo(req_id: str, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    r = _find_request_by_id(req_id)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    found = _safe_photo_path(r)
    if not found:
        return JSONResponse({"error": "no photo"}, status_code=404)
    path, mime = found
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/api/admin/live")
async def admin_live(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    from server.app import SESSIONS, SNAPSHOTS, STATE

    items = []
    for r in reversed(STATE["requests"]):
        req_id = r["id"]
        sess = SESSIONS.get(req_id)
        snap = SNAPSHOTS.get(req_id) or {}
        contract = STATE.get("contracts", {}).get(r.get("contract_id")) if r.get("contract_id") else None
        if sess and getattr(sess, "contract", None) and sess.contract.get("amended_by") == "shopper":
            contract = sess.contract
        req_carts = _collect_req_carts(req_id, sess, snap)
        req_events = [
            ev for ev in STATE["events"]
            if ev.get("request_id") == req_id or (ev.get("request_id") is None and ev.get("cart_id") in req_carts)
        ]
        token = r.get("token")
        picked = _picked_product(sess) if sess else snap.get("picked")
        carts = list(sess.carts.values()) if sess else (snap.get("carts") or [])
        verdict_raw = sess.verdict if sess else snap.get("verdict")
        checkout_raw = sess.checkout if sess else snap.get("checkout")
        agent_summary = getattr(sess, "agent_summary", None) if sess else snap.get("agent_summary")
        items.append({
            "id": req_id,
            "ts": r.get("ts"),
            "status": r.get("status"),
            "client": r.get("client"),
            "text": r.get("text"),
            "problem": r.get("problem"),
            "offer": r.get("offer"),
            "has_photo": _safe_photo_path(r) is not None,
            "has_audio": bool((r.get("files") or {}).get("audio")),
            "view_url": f"/r/{token}" if token else None,
            "photo_url": f"/api/admin/requests/{req_id}/photo" if _safe_photo_path(r) else None,
            "pay_ready": bool((sess and sess.continue_url) or r.get("pay_ready")),
            "contract": sanitize_contract(contract, include_attempts=True),
            "picked": picked,
            "carts": carts,
            "verdict": sanitize_verdict(verdict_raw, admin=True),
            "checkout": sanitize_checkout(checkout_raw),
            "pending_cart": sess.pending_cart if sess else None,
            "agent_summary": agent_summary,
            "events": req_events,
        })

    return {
        "enforced": guard.enforced(),
        "pending": STATE.get("pending"),
        "requests": items,
    }


@router.post("/api/admin/approve")
async def admin_approve(body: dict, request: Request):
    """Laptop fallback for phone approval: runs the same logic as /approve, without returning continue_url."""
    denied = _deny_if_remote(request)
    if denied:
        return denied
    from server.app import SESSIONS, STATE, decide

    decision = "approve" if str(body.get("decision", "")).lower() == "approve" else "block"
    cart_id = body.get("cart_id")
    p = STATE.get("pending")
    if not p or p.get("cart_id") != cart_id:
        # Also allow deciding a per-session pending cart if multiple requests exist.
        p = None
        for rid, sess in SESSIONS.items():
            if sess.pending_cart and sess.pending_cart.get("cart_id") == cart_id:
                p = {"cart_id": cart_id, "request_id": rid}
                break
    if not p:
        return JSONResponse({"ok": False, "speak": "That cart is no longer pending."}, status_code=404)

    res = await decide(p, decision)
    # Never expose continue_url in the admin API / browser.
    return {
        "ok": bool(res.get("ok")),
        "speak": res.get("speak", ""),
        "pay_ready": bool(res.get("url")),
    }


@router.post("/api/admin/enforcement")
async def admin_enforcement(body: dict, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    from server.app import event

    enforced = bool(body.get("enforced", True))
    config.write_env_vars({"TRIPWIRE_ENFORCED": "1" if enforced else "0"})
    event("enforcement_toggled", enforced=guard.enforced())
    return {"enforced": guard.enforced()}


def _order_payload(order: dict) -> dict:
    from server.app import SESSIONS, STATE

    req_id = order.get("request_id")
    sess = SESSIONS.get(req_id)
    r = _find_request_by_id(req_id) if req_id else None
    c = STATE.get("contracts", {}).get(r.get("contract_id")) if (r and r.get("contract_id")) else None
    checkout = sanitize_checkout(order.get("checkout"))
    return {
        "cart_id": order.get("cart_id"),
        "request_id": req_id,
        "ts": order.get("ts"),
        "status": (checkout or {}).get("status") or order.get("status"),
        "checkout": checkout,
        "agent_summary": order.get("agent_summary") or (getattr(sess, "agent_summary", None) if sess else None),
        "request_summary": c.get("summary") if c else (r.get("text") if r else None),
        "can_refresh": bool(sess and getattr(sess, "_checkout_id", None) and getattr(sess, "_shop_domain", None)),
    }


@router.get("/api/admin/orders")
async def admin_orders(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    from server.app import STATE

    orders = sorted(STATE["orders"], key=lambda o: o.get("ts") or 0, reverse=True)  # newest purchase first
    return {"orders": [_order_payload(o) for o in orders]}


@router.post("/api/admin/orders/{cart_id}/refresh")
async def admin_order_refresh(cart_id: str, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    from server.app import SESSIONS, STATE

    order = next((o for o in reversed(STATE["orders"]) if o.get("cart_id") == cart_id), None)
    if not order:
        return JSONResponse({"error": "order not found"}, status_code=404)
    sess = SESSIONS.get(order.get("request_id"))
    if not sess or not getattr(sess, "_checkout_id", None):
        return JSONResponse({"error": "checkout handle not available in memory"}, status_code=409)
    try:
        view = await asyncio.to_thread(sess.refresh_checkout)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Shop MCP get_checkout failed: {str(e)[:240]}"}, status_code=502)

    order["checkout"] = view
    if view.get("status"):
        order["status"] = view["status"]
    from server.app import persist
    persist()
    return {"ok": True, "order": _order_payload(order)}


@router.get("/api/admin/usage")
async def admin_usage(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    return config.aggregate_usage()


@router.post("/api/admin/settings")
async def admin_settings(body: dict, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    import server.app as server_app

    try:
        updated = config.update_runtime_settings(
            rate_limit_per_min=body.get("rate_limit_per_min"),
            daily_token_budget=body.get("daily_token_budget"),
        )
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    server_app.RATE_LIMIT_PER_MIN = updated["rate_limit_per_min"]
    return {"ok": True, "settings": updated}


@router.get("/api/admin/models")
async def admin_models_get(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    cfg = config.get_model_config()
    usage = config.aggregate_usage(recent_limit=30)
    return {**cfg, "recent_calls": usage["recent"]}


@router.post("/api/admin/models")
async def admin_models_post(body: dict, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    try:
        updated = config.update_model_config(body)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, **updated}


@router.get("/api/admin/keys")
async def admin_keys_get(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    return {"keys": config.get_key_status()}


@router.post("/api/admin/keys")
async def admin_keys_set(body: dict, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    name = str(body.get("name", "")).strip()
    value = str(body.get("value", ""))
    try:
        status = config.set_api_key(name, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "key": status}


@router.delete("/api/admin/keys/{name}")
async def admin_keys_delete(name: str, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    try:
        status = config.remove_api_key(name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "key": status}


@router.post("/api/admin/keys/{name}/test")
async def admin_keys_test(name: str, request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    if name not in config.MANAGED_KEYS:
        return JSONResponse({"error": f"Unsupported key: {name}"}, status_code=400)
    result = await asyncio.to_thread(config.test_api_key, name)
    return result


@router.get("/api/admin/redteam")
async def admin_redteam_get(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    return config.get_redteam_results()


@router.post("/api/admin/redteam/run")
async def admin_redteam_run(request: Request):
    denied = _deny_if_remote(request)
    if denied:
        return denied
    runner = config._find_redteam_runner()
    if runner is None:
        return {"ok": False, "available": False, "message": "Not available yet (Tanu's evals runner is not wired yet)."}
    asyncio.create_task(asyncio.to_thread(runner))
    return {"ok": True, "available": True, "message": "Red-team run started in background."}
