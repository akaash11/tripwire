"""Phone web page for anyone (judges included): GET /buy. No Shortcut install needed. Owner: Akaash.

Flow: photo (camera) + voice (mic) or typed request -> POST /request -> poll /w/{token} -> Approve / Block
-> /w/{token}/decide -> open the real Shop checkout (continue_url) to pay with Shop Pay.
Each request has its own unguessable token, so people don't see or approve each other's carts.
"""
from __future__ import annotations

import pathlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()
VERSION = "1.5"  # bump with phone/make_shortcuts.py VERSION; shown on the page and logged per request
DASHBOARD_DIR = pathlib.Path(__file__).resolve().parents[1] / "dashboard"


def _find(token: str):
    from server.app import STATE, SESSIONS
    r = next((r for r in STATE["requests"] if r.get("token") == token), None)
    return r, (SESSIONS.get(r["id"]) if r else None)


@router.get("/w/{token}")
async def web_status(token: str):
    from server.app import SNAPSHOTS, STATE
    r, s = _find(token)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    snap = SNAPSHOTS.get(r["id"]) or {}
    c = STATE.get("contracts", {}).get(r.get("contract_id")) if r.get("contract_id") else None
    out = {"status": r["status"], "client": r.get("client"), "server_version": VERSION, "contract": None, "pending": None, "verdict": None, "checkout": None,
           "agent_summary": getattr(s, "agent_summary", None) if s else snap.get("agent_summary"), "problem": r.get("problem"),
           "offer": r.get("offer")}
    if c:
        out["contract"] = {k: c.get(k) for k in ("summary", "item", "must", "must_not", "max_total_cents",
                                                  "photo_observations", "compiled_by", "budget_stated")}
    if s and getattr(s, "contract", None) and s.contract.get("amended_by") == "shopper":
        out["contract"] = (out["contract"] or {}) | {"max_total_cents": s.contract["max_total_cents"], "budget_stated": True}
    verdict = s.verdict if s else snap.get("verdict")
    if verdict:
        x = verdict.get("xray", {})
        from common.plain import plain_reasons
        out["verdict"] = {"verdict": verdict["verdict"],
                          "reasons": plain_reasons(x.get("reasons", []), (s.contract if s else None) or c)}
    if s and s.pending_cart:
        out["pending"] = {"cart_id": s.pending_cart["cart_id"], "summary": s.pending_cart["summary"]}
    checkout = s.checkout if s else snap.get("checkout")
    if checkout:
        out["checkout"] = {k: checkout.get(k) for k in ("status", "totals", "lines", "messages", "links", "to_fill_on_shop")}
    # Auto-approved by Tripwire (no human step): shopper taps "Pay" -> POST /w/{token}/pay returns the link.
    out["pay_ready"] = bool(s and s.continue_url and not s.pending_cart and r["status"] == "approve")
    return out


@router.post("/w/{token}/raise_budget")
async def web_raise_budget(token: str, body: dict):
    """Shopper taps 'Raise budget' on their own request page. Only the cheapest quoted option, only by the shopper."""
    import asyncio
    from server.app import STATE, persist
    r, s = _find(token)
    off = (r or {}).get("offer")
    if not r or not s or not off or off["cart_id"] != body.get("cart_id"):
        return {"ok": False, "speak": "Nothing to raise for this request."}
    res = await asyncio.to_thread(s.raise_budget, off["cart_id"])
    if not res:
        return {"ok": False, "speak": "That option is no longer available."}
    if r.get("contract_id") and getattr(s, "contract", None):
        STATE.setdefault("contracts", {})[r["contract_id"]] = s.contract
    r["offer"], r["problem"], r["status"] = None, None, res["verdict"]
    if s.pending_cart:
        c = s.pending_cart
        v = s.verdict or {}
        STATE["pending"] = {"cart_id": c["cart_id"], "request_id": r["id"], "verdict": v.get("verdict"),
                            "summary": f"Agent wants: {c['summary']}. You asked: {s.contract.get('summary')}",
                            "reason": "; ".join(v.get("xray", {}).get("reasons", []))[:300]}
    persist()
    return {"ok": res["verdict"] != "block", "speak": res.get("reason") or "Budget raised. Review and approve."}


@router.post("/w/{token}/pay")
async def web_pay(token: str):
    """Tripwire auto-approved (no human step needed): hand the shopper the merchant's checkout link on tap."""
    from server.app import STATE, persist
    r, s = _find(token)
    if not r or not s or not s.continue_url or s.pending_cart:
        return {"ok": False, "speak": "No checkout ready for this request.", "url": ""}
    if not any(o.get("cart_id") == (s.checkout or {}).get("cart_id") for o in STATE["orders"]):
        STATE["orders"].append({"cart_id": (s.checkout or {}).get("cart_id"), "request_id": r["id"], "ts": __import__("time").time(),
                                "checkout": s.checkout, "status": (s.checkout or {}).get("status") or "awaiting_payment",
                                "agent_summary": getattr(s, "agent_summary", None)})
        persist()
    return {"ok": True, "url": s.continue_url, "speak": "Opening the Shop checkout."}


@router.post("/w/{token}/decide")
async def web_decide(token: str, body: dict):
    from server.app import decide
    r, s = _find(token)
    if not r or not s or not s.pending_cart or s.pending_cart["cart_id"] != body.get("cart_id"):
        return {"ok": False, "speak": "Nothing to approve for this request.", "url": ""}
    decision = "approve" if str(body.get("decision", "")).lower() == "approve" else "block"
    return await decide({"cart_id": s.pending_cart["cart_id"], "request_id": r["id"]}, decision)


@router.get("/buy", response_class=HTMLResponse)
async def buy_page():
    """No-install shopper request page using the shared Tripwire look; redirects to /r/{token}."""
    html = (DASHBOARD_DIR / "buy.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__VERSION__", VERSION))


@router.get("/t/{token}")
async def track_page(token: str):
    """Opened by the Buy shortcut right after /request: 302 redirect to the unified /r/{token} page."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,40}", token) or not _find(token)[0]:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Tripwire — Not found</title>"
            "<body style='font-family:system-ui;padding:2rem;max-width:32rem;margin:0 auto'>"
            "<h1>Request not found</h1><p>This Tripwire link is invalid or has expired.</p></body>",
            status_code=404,
        )
    return RedirectResponse(url=f"../r/{token}", status_code=302)


PUBLIC_BASE = "https://akaashs-macbook-pro.tail3c0993.ts.net:10000/tripwire"  # Funnel; override with TRIPWIRE_PUBLIC_BASE


def _qr_svg(url: str) -> str:
    import io

    import qrcode
    import qrcode.image.svg
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, box_size=10, border=2).save(buf)
    return buf.getvalue().decode().split("?>", 1)[-1]  # drop the XML header so it can be inlined


@router.get("/install", response_class=HTMLResponse)
async def install_page():
    """Scan with the iPhone camera: Safari downloads the signed Shortcut and Shortcuts shows 'Add Shortcut'."""
    import os
    from html import escape
    base = os.environ.get("TRIPWIRE_PUBLIC_BASE", PUBLIC_BASE).rstrip("/")
    buy, web = f"{base}/shortcuts/buy", f"{base}/buy"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Install Tripwire</title><style>
:root{{--bg:#f4f5f7;--card:#fff;--ink:#14213d;--muted:#5b667a;--accent:#e3a400}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1626;--card:#18223a;--ink:#e7ecf4;--muted:#9aa7be;--accent:#f2bc2e}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:17px/1.45 system-ui,-apple-system,sans-serif}}
main{{max-width:60rem;margin:0 auto;padding:16px}}.tape{{height:10px;margin:.4rem 0 1rem;background:repeating-linear-gradient(-45deg,var(--accent) 0 14px,var(--ink) 14px 28px)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(16rem,1fr));gap:16px}}
.card{{background:var(--card);border-radius:14px;padding:18px;text-align:center}}
.qr{{background:#fff;border-radius:10px;padding:10px;display:inline-block;width:min(100%,280px)}}.qr svg{{width:100%;height:auto;display:block}}
a{{color:inherit;word-break:break-all}}.muted{{color:var(--muted)}}ol{{text-align:left}}</style></head><body><main>
<h1>Tripwire</h1><div class="tape"></div>
<div class="grid">
<div class="card"><h2>1 · Install the Shortcut</h2><div class="qr">{_qr_svg(buy)}</div>
<ol><li>Scan with the iPhone Camera, tap the link.</li><li>Safari downloads it, Shortcuts opens: tap <b>Add Shortcut</b>.</li>
<li>Run <b>Tripwire Buy</b> (or "Hey Siri, Tripwire Buy"): say what you want, take a photo.</li></ol>
<p class="muted"><a href="{escape(buy)}">{escape(buy)}</a></p></div>
<div class="card"><h2>2 · Or no install</h2><div class="qr">{_qr_svg(web)}</div>
<p>Opens the Tripwire page in Safari: photo, voice or text.</p><p class="muted"><a href="{escape(web)}">{escape(web)}</a></p></div>
</div>
<p><a href="{escape(base)}/history">My past requests</a></p>
<p class="muted">You pay on the store's own Shop checkout page: a real order with real money. To test, stop at that page.</p>
</main></body></html>"""


HISTORY = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>My Tripwire requests</title><style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#14213d;--muted:#5b667a;--line:#d6dbe3;--accent:#e3a400}
@media (prefers-color-scheme:dark){:root{--bg:#0f1626;--card:#18223a;--ink:#e7ecf4;--muted:#9aa7be;--line:#2c3a57;--accent:#f2bc2e}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,sans-serif}
main{max-width:34rem;margin:0 auto;padding:16px}.tape{height:8px;margin:.4rem 0 1rem;
background:repeating-linear-gradient(-45deg,var(--accent) 0 12px,var(--ink) 12px 24px)}
a.card{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0;
color:inherit;text-decoration:none}.muted{color:var(--muted);font-size:.9rem}.pill{font-weight:700;font-size:.8rem}
</style></head><body><main><h1>My requests</h1><div class="tape"></div>
<p class="muted">Requests you opened on this device. Tripwire has no accounts; nothing here leaves your phone.</p>
<div id="list"></div><p><a href="buy">Start a new request</a></p></main><script>
const L={approve:"Approved",ask_human:"Needs your OK",block:"Blocked",no_checkout:"Not bought",over_budget:"Over budget",
 shopping:"Shopping",expired:"Expired",agent_failed:"Failed",contract_failed:"Failed"};
let items=[];try{items=JSON.parse(localStorage.getItem("tripwire.history")||"[]")}catch(e){}
const list=document.getElementById("list");
if(!items.length){const p=document.createElement("p");p.textContent="No requests on this device yet.";list.append(p)}
for(const x of items){const a=document.createElement("a");a.className="card";a.href="r/"+encodeURIComponent(x.token);
 const t=document.createElement("b");t.textContent=x.summary||"Tripwire request";
 const s=document.createElement("div");s.className="muted";
 s.textContent=new Date((x.ts||0)*1000).toLocaleString()+" · "+(L[x.status]||x.status||"")+(x.picked?" · "+x.picked:"");
 a.append(t,s);list.append(a)}
</script></body></html>"""


@router.get("/history", response_class=HTMLResponse)
async def history_page():
    """This device's past requests (from localStorage on the shopper page). No server-side lookup by person."""
    return HISTORY
