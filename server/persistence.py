"""Atomic JSON persistence for Tripwire STATE (data/state.json).

Saves requests, contracts, events, orders, pending, and sanitized session snapshots
across server restarts. NEVER persists secret or host-local fields:
- No continue_url
- No Shop checkout id
- No local filesystem paths (uploads/... paths stripped)
- No cryptographic signatures or nonces

On startup, live ShopSessions cannot be restored, so any in-flight or actionable
requests (received, compiling_contract, contract_ready, shopping, ask_human, over_budget)
are marked "expired" and stale pending/offer actions are cleared.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"

_LOCK = threading.Lock()

# Statuses that depend on an active in-memory ShopSession or background task.
EXPIRE_ON_RESTART = frozenset({
    "received",
    "compiling_contract",
    "contract_ready",
    "shopping",
    "ask_human",
    "over_budget",
})

_FORBIDDEN_KEYS = frozenset({
    "continue_url",
    "checkout_id",
    "_checkout_id",
    "signature",
    "nonce",
    "path",
})


def _scrub(obj: Any, *, in_checkout: bool = False) -> Any:
    """Recursively strip any secret/path keys before writing to disk."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _FORBIDDEN_KEYS:
                continue
            if in_checkout and k == "id":
                continue
            out[k] = _scrub(v, in_checkout=(in_checkout or k == "checkout"))
        return out
    if isinstance(obj, list):
        return [_scrub(x, in_checkout=in_checkout) for x in obj]
    return obj


def _sanitize_request(r: dict) -> dict:
    """Return a disk-safe request dict (zero local file paths)."""
    files = {}
    for fk, fv in (r.get("files") or {}).items():
        if isinstance(fv, dict):
            files[fk] = {"bytes": fv.get("bytes"), "type": fv.get("type")}
    keep = (
        "id", "ts", "text", "status", "token", "client",
        "contract_id", "problem", "offer", "pay_ready",
    )
    out = {k: _scrub(r[k]) for k in keep if k in r}
    out["files"] = files
    return out


def _sanitize_contract(c: dict) -> dict:
    """Return a disk-safe contract dict (no signature, no nonce)."""
    keep = (
        "contract_id", "created_at", "expires_at", "summary", "user_request", "item",
        "must", "must_not", "max_total_cents", "budget_stated", "amended_by",
        "amended_from", "amended_at", "quantity", "returns", "currency",
        "ships_to", "photo_observations", "compiled_by", "assumptions",
        "search_query", "compile_attempts",
    )
    out = {k: _scrub(c[k]) for k in keep if k in c}
    out["signed"] = bool(c.get("signature") or c.get("signed"))
    return out


def _sanitize_checkout(c: dict | None) -> dict | None:
    if not isinstance(c, dict):
        return None
    keep = (
        "cart_id", "shop_name", "status", "currency", "lines", "totals",
        "messages", "to_fill_on_shop", "links", "order", "expires_at",
    )
    return {k: _scrub(c[k], in_checkout=(k != "order")) for k in keep if k in c}


def _sanitize_verdict(v: dict | None) -> dict | None:
    if not isinstance(v, dict):
        return None
    xray = v.get("xray") or {}
    receipt = v.get("receipt")
    safe_receipt = None
    if isinstance(receipt, dict):
        safe_receipt = {k: _scrub(val) for k, val in receipt.items() if k != "signature"}
        safe_receipt["signed"] = bool(receipt.get("signature") or receipt.get("signed"))
    return {
        "verdict": v.get("verdict"),
        "contract_id": v.get("contract_id"),
        "cart_id": v.get("cart_id"),
        "enforced": v.get("enforced", True),
        "stages": _scrub(v.get("stages") or {}),
        "xray": {
            "asked_for": xray.get("asked_for", ""),
            "hidden_in_listing": xray.get("hidden_in_listing", ""),
            "agent_tried": xray.get("agent_tried", ""),
            "reasons": list(xray.get("reasons") or []),
        },
        "receipt": safe_receipt,
        "latency_ms": v.get("latency_ms"),
    }


def _sanitize_cart(cart: dict) -> dict:
    keep = ("cart_id", "contract_id", "payee", "lines", "total_cents", "currency", "created_at", "summary")
    return {k: _scrub(cart[k]) for k in keep if k in cart}


def _picked_from_session(session) -> dict | None:
    if not session or not getattr(session, "carts", None):
        return None
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


def snapshot_from_session(session) -> dict:
    """Build a secret-free snapshot of a live ShopSession for persistence."""
    return {
        "picked": _picked_from_session(session),
        "carts": [_sanitize_cart(c) for c in (session.carts or {}).values()],
        "verdict": _sanitize_verdict(session.verdict),
        "checkout": _sanitize_checkout(session.checkout),
        "agent_summary": getattr(session, "agent_summary", None),
    }


def _sanitize_order(o: dict) -> dict:
    keep = ("cart_id", "request_id", "ts", "status", "agent_summary")
    out = {k: _scrub(o[k]) for k in keep if k in o}
    out["checkout"] = _sanitize_checkout(o.get("checkout"))
    return out


def save_state(state: dict, sessions: dict | None = None, snapshots: dict | None = None,
               path: pathlib.Path | None = None) -> None:
    """Atomically write sanitized STATE to data/state.json."""
    target = path or STATE_PATH
    with _LOCK:
        merged_snaps: dict[str, dict] = {}
        if snapshots:
            for rid, snap in snapshots.items():
                if isinstance(snap, dict):
                    merged_snaps[rid] = _scrub(snap)
        if sessions:
            for rid, sess in sessions.items():
                snap = snapshot_from_session(sess)
                merged_snaps[rid] = snap
                if snapshots is not None:
                    snapshots[rid] = snap

        payload = {
            "version": 1,
            "saved_at": time.time(),
            "requests": [_sanitize_request(r) for r in state.get("requests", [])],
            "contracts": {
                cid: _sanitize_contract(c)
                for cid, c in (state.get("contracts") or {}).items()
                if isinstance(c, dict)
            },
            "current_contract": state.get("current_contract"),
            "events": [_scrub(ev) for ev in state.get("events", [])],
            "orders": [_sanitize_order(o) for o in state.get("orders", [])],
            "pending": _scrub(state.get("pending")),
            "snapshots": merged_snaps,
        }

        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".state.tmp.", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def load_state(state: dict, snapshots: dict | None = None,
               path: pathlib.Path | None = None) -> bool:
    """Load data/state.json into STATE on startup; expire requests whose sessions were lost."""
    target = path or STATE_PATH
    if not target.exists():
        return False
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(raw, dict):
        return False

    requests = []
    for r in raw.get("requests") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        item = _sanitize_request(r)
        if item.get("status") in EXPIRE_ON_RESTART:
            item["status"] = "expired"
            item["offer"] = None
        # continue_url is never persisted, so a restarted server cannot re-open old pay links.
        if item.get("pay_ready"):
            item["pay_ready"] = False
        requests.append(item)

    contracts = {}
    for cid, c in (raw.get("contracts") or {}).items():
        if isinstance(c, dict):
            contracts[cid] = _sanitize_contract(c)

    orders = [_sanitize_order(o) for o in (raw.get("orders") or []) if isinstance(o, dict)]
    events = [_scrub(ev) for ev in (raw.get("events") or []) if isinstance(ev, dict)]

    state["requests"] = requests
    state["contracts"] = contracts
    state["current_contract"] = raw.get("current_contract")
    state["events"] = events
    state["orders"] = orders
    # Pending carts require a live ShopSession to open checkout, so clear on startup.
    state["pending"] = None

    if snapshots is not None:
        snapshots.clear()
        for rid, snap in (raw.get("snapshots") or {}).items():
            if isinstance(snap, dict):
                snapshots[rid] = _scrub(snap)

    return True
