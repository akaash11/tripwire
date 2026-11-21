"""Phone web page for anyone (judges included): GET /buy. No Shortcut install needed. Owner: Akaash.

Flow: photo (camera) + voice (mic) or typed request -> POST /request -> poll /w/{token} -> Approve / Block
-> /w/{token}/decide -> open the real Shop checkout (continue_url) to pay with Shop Pay.
Each request has its own unguessable token, so people don't see or approve each other's carts.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()


def _find(token: str):
    from server.app import STATE, SESSIONS
    r = next((r for r in STATE["requests"] if r.get("token") == token), None)
    return r, (SESSIONS.get(r["id"]) if r else None)


@router.get("/w/{token}")
async def web_status(token: str):
    from server.app import STATE
    r, s = _find(token)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    c = STATE.get("contracts", {}).get(r.get("contract_id")) if r.get("contract_id") else None
    out = {"status": r["status"], "contract": None, "pending": None, "verdict": None, "checkout": None,
           "agent_summary": getattr(s, "agent_summary", None) if s else None}
    if c:
        out["contract"] = {k: c.get(k) for k in ("summary", "item", "must", "must_not", "max_total_cents",
                                                  "photo_observations", "compiled_by")}
    if s and s.verdict:
        x = s.verdict.get("xray", {})
        out["verdict"] = {"verdict": s.verdict["verdict"], "reasons": x.get("reasons", [])}
    if s and s.pending_cart:
        out["pending"] = {"cart_id": s.pending_cart["cart_id"], "summary": s.pending_cart["summary"]}
    if s and s.checkout:
        out["checkout"] = {k: s.checkout.get(k) for k in ("status", "totals", "lines", "messages", "links")}
    return out


@router.post("/w/{token}/decide")
async def web_decide(token: str, body: dict):
    from server.app import decide
    r, s = _find(token)
    if not r or not s or not s.pending_cart or s.pending_cart["cart_id"] != body.get("cart_id"):
        return {"ok": False, "speak": "Nothing to approve for this request.", "url": ""}
    decision = "approve" if str(body.get("decision", "")).lower() == "approve" else "block"
    return await decide({"cart_id": s.pending_cart["cart_id"], "request_id": r["id"]}, decision)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tripwire</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#14213d;--muted:#5b667a;--line:#d6dbe3;--accent:#e3a400;--ok:#23795a;--no:#c2372c;--ask:#2f5da8}
@media (prefers-color-scheme:dark){:root{--bg:#0f1626;--card:#18223a;--ink:#e7ecf4;--muted:#9aa7be;--line:#2c3a57;--accent:#f2bc2e;--ok:#4fc796;--no:#f06a5e;--ask:#7fa6ec}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,sans-serif}
main{max-width:34rem;margin:0 auto;padding:16px}h1{font-size:1.6rem;margin:.2rem 0}.tape{height:8px;border-radius:2px;margin:.4rem 0 1rem;
background:repeating-linear-gradient(-45deg,var(--accent) 0 12px,var(--ink) 12px 24px)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
label{display:block;font-weight:600;margin:.6rem 0 .3rem}textarea,input[type=file]{width:100%}
textarea{min-height:4.5rem;border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit;background:transparent;color:inherit}
button{font:inherit;font-weight:700;border:0;border-radius:10px;padding:12px 16px;cursor:pointer;width:100%;margin-top:8px}
.primary{background:var(--ink);color:var(--bg)}.ok{background:var(--ok);color:#fff}.no{background:var(--no);color:#fff}.ghost{background:transparent;border:1px solid var(--line);color:inherit}
.muted{color:var(--muted)}.row{display:flex;gap:8px}.row button{flex:1}ul{padding-left:1.1rem;margin:.3rem 0}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-weight:700;font-size:.85rem}
img.prev{max-width:100%;border-radius:8px;margin-top:6px}
</style></head><body><main>
<h1>Tripwire</h1><div class="tape"></div>
<p class="muted">Tell Tripwire what to buy. An AI agent shops real Shopify stores; Tripwire only lets it check out if the
cart matches what you asked for. You pay on the Shop checkout page.</p>
<div class="card" id="ask">
<label for="photo">Photo of the item or its label (optional)</label>
<input id="photo" type="file" accept="image/*" capture="environment"><img id="prev" class="prev" alt="" hidden>
<label>Say it</label><div class="row"><button class="ghost" id="rec" type="button">Hold to talk</button></div>
<p class="muted" id="recstate"></p>
<label for="text">Or type it</label><textarea id="text" placeholder="A 100W USB-C cable under $20 with free returns"></textarea>
<button class="primary" id="go" type="button">Send to Tripwire</button>
</div>
<div id="out"></div>
</main><script>
const $=s=>document.querySelector(s);let audio=null,mr=null,chunks=[],token=null,timer=null;
function el(tag,attrs={},...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);
 for(const k of kids)e.append(k instanceof Node?k:document.createTextNode(String(k??"")));return e}
$("#photo").onchange=e=>{const f=e.target.files[0];if(f){$("#prev").src=URL.createObjectURL(f);$("#prev").hidden=false}};
async function startRec(){try{const st=await navigator.mediaDevices.getUserMedia({audio:true});chunks=[];
 mr=new MediaRecorder(st);mr.ondataavailable=e=>chunks.push(e.data);mr.onstop=()=>{audio=new Blob(chunks,{type:mr.mimeType||"audio/mp4"});
 st.getTracks().forEach(t=>t.stop());$("#recstate").textContent="Voice recorded ("+Math.round(audio.size/1024)+" KB)."};
 mr.start();$("#recstate").textContent="Listening… release to stop."}catch(e){$("#recstate").textContent="Mic not available: type instead."}}
function stopRec(){if(mr&&mr.state==="recording")mr.stop()}
const rec=$("#rec");rec.onpointerdown=startRec;rec.onpointerup=stopRec;rec.onpointerleave=stopRec;
$("#go").onclick=async()=>{const fd=new FormData();const f=$("#photo").files[0];if(f)fd.append("photo",f);
 if(audio)fd.append("audio",audio,"voice."+((audio.type||"").includes("webm")?"webm":"m4a"));
 const t=$("#text").value.trim();if(t)fd.append("text",t);
 if(!f&&!audio&&!t){alert("Add a photo, your voice or some text.");return}
 $("#go").disabled=true;$("#go").textContent="Sending…";
 const r=await fetch("request",{method:"POST",body:fd});const d=await r.json();
 if(!d.token){$("#go").disabled=false;$("#go").textContent="Send to Tripwire";alert(d.speak||"Failed");return}
 token=d.token;$("#ask").hidden=true;poll()};
async function poll(){const r=await fetch("w/"+token);const d=await r.json();render(d);
 const done=["block","approve","no_checkout","agent_failed","contract_failed"].includes(d.status)&&!d.pending;
 if(!done&&!(d.checkout))timer=setTimeout(poll,2000)}
function render(d){const out=$("#out");out.replaceChildren();
 const steps={received:"Got your request",compiling_contract:"Understanding what you want (Gemini 4 Neon)…",contract_ready:"Contract signed",
  shopping:"Agent is shopping real Shopify stores…",ask_human:"Needs your OK",approve:"Approved",block:"Blocked",no_checkout:"Agent didn't pick anything",
  agent_failed:"Agent failed",contract_failed:"Couldn't understand the request"};
 out.append(el("div",{class:"card"},el("b",{},steps[d.status]||d.status)));
 if(d.contract){const c=d.contract;const card=el("div",{class:"card"},el("b",{},"What you asked for"),el("p",{},c.summary||""));
  const ul=el("ul");(c.must||[]).forEach(m=>ul.append(el("li",{},"Must: "+m.clause)));(c.must_not||[]).forEach(m=>ul.append(el("li",{},"Must not: "+m.clause)));
  card.append(ul,el("p",{class:"muted"},"Budget $"+((c.max_total_cents||0)/100).toFixed(2)+" · understood by "+(c.compiled_by||"")));out.append(card)}
 if(d.pending){const card=el("div",{class:"card"},el("b",{},"The agent wants to buy"),el("p",{},d.pending.summary));
  if(d.verdict&&d.verdict.reasons&&d.verdict.reasons.length)card.append(el("p",{class:"muted"},"Tripwire: "+d.verdict.reasons.join("; ")));
  const a=el("button",{class:"ok",type:"button"},"Approve and pay on Shop"),b=el("button",{class:"no",type:"button"},"Block");
  a.onclick=()=>decide(d.pending.cart_id,"approve");b.onclick=()=>decide(d.pending.cart_id,"block");card.append(el("div",{class:"row"},b,a));out.append(card)}
 else if(d.verdict&&d.verdict.verdict==="block"){out.append(el("div",{class:"card"},el("b",{},"Tripwire blocked this purchase"),el("p",{},(d.verdict.reasons||[]).join("; "))))}
 if(d.checkout){const c=d.checkout;const card=el("div",{class:"card"},el("b",{},"Shop checkout: "+c.status));
  (c.totals||[]).forEach(t=>card.append(el("div",{},(t.label||t.type)+": $"+((t.amount||0)/100).toFixed(2))));
  (c.messages||[]).filter(m=>m.type==="warning").forEach(m=>card.append(el("p",{},"⚠ "+m.content)));
  const ls=el("p",{class:"muted"});(c.links||[]).forEach(l=>{const a=el("a",{href:l.url,target:"_blank",rel:"noopener"},l.type.replace(/_/g," "));ls.append(a," · ")});card.append(ls);out.append(card)}
 if(!d.pending&&d.agent_summary&&d.status!=="shopping"){out.append(el("div",{class:"card"},el("b",{},"Agent"),el("p",{class:"muted"},d.agent_summary)))}}
async function decide(cart_id,decision){const r=await fetch("w/"+token+"/decide",{method:"POST",headers:{"content-type":"application/json"},
 body:JSON.stringify({cart_id,decision})});const d=await r.json();if(d.url){location.href=d.url}else{alert(d.speak);poll()}}
</script></body></html>"""


@router.get("/buy", response_class=HTMLResponse)
async def buy_page():
    return PAGE
