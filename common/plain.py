"""Turn guard reasons ("m1: must clause failed (noul=0.08, conf=0.92)") into plain English for shoppers.

The admin page keeps the raw reasons; the shopper page shows these. Unknown formats pass through unchanged.
"""
from __future__ import annotations

import re

_CLAUSE = re.compile(r"^(?P<id>[mn]\d+): (?P<kind>must|must_not) clause (?P<state>failed|present|uncertain)")
_PREFIX = {
    "total": "The total is over your budget.",
    "final_check": "The store's checkout didn't match what was approved (price, store or currency changed).",
    "price_tamper": "The price in the cart doesn't match the store's live price.",
    "quantity": "The quantity doesn't match what you asked for.",
    "currency": "The currency doesn't match your request.",
    "cart_hash": "The cart was changed after it was checked.",
    "payee": "The store changed after the cart was checked.",
    "contract": "Your request expired or was already used. Please send it again.",
    "canary": "The shopping agent tried to leak a secret, so Tripwire stopped it.",
}


def _clause_text(contract: dict | None, cid: str) -> str | None:
    for key in ("must", "must_not"):
        for c in (contract or {}).get(key) or []:
            if isinstance(c, dict) and c.get("id") == cid:
                return c.get("clause")
    return None


def plain_reason(reason: str, contract: dict | None = None) -> str:
    r = str(reason or "").strip()
    m = _CLAUSE.match(r)
    if m:
        text = _clause_text(contract, m["id"]) or "one of your requirements"
        if m["kind"] == "must":
            return (f"It doesn't match what you asked for: {text}." if m["state"] == "failed"
                    else f"Tripwire couldn't confirm: {text}.")
        return (f"It's something you said you didn't want: {text}." if m["state"] == "present"
                else f"Tripwire couldn't rule out: {text}.")
    if r.startswith("injection:"):
        return "The product listing contains text aimed at AI agents, so Tripwire is double-checking it."
    if r.startswith("returns:"):
        return "The return policy doesn't meet what you asked for (or isn't stated)."
    head = r.split(":", 1)[0].strip().lower()
    if head in _PREFIX and ":" in r:
        return _PREFIX[head]
    return r


def plain_reasons(reasons: list | None, contract: dict | None = None) -> list[str]:
    out: list[str] = []
    for x in reasons or []:
        p = plain_reason(x, contract)
        if p and p not in out:
            out.append(p)
    return out
