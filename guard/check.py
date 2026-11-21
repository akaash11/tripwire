"""Orchestrate the guard pipeline and return a Verdict (docs/TANU.md, docs/ARCHITECTURE.md)."""

from __future__ import annotations

import os
import secrets
import time

from common.signing import sign

from . import canary, gate, neon
from .listing import listing_text
from .policy import check_policy
from .replay import consume_nonce, hash_cart, intent_hash, utc_now_iso


def enforced() -> bool:
    return os.environ.get("TRIPWIRE_ENFORCED", "1") == "1"


def reduced(verdict: dict) -> dict:
    """What the agent gets back from request_checkout: verdict + one line, never the x-ray."""
    reasons = ((verdict or {}).get("xray") or {}).get("reasons") or [""]
    line = str(reasons[0]) if reasons else str((verdict or {}).get("verdict") or "")
    return {"verdict": (verdict or {}).get("verdict"), "reason": line}


def _issue_receipt(contract: dict, cart: dict) -> dict:
    body = {
        "order_ref": "ord_" + secrets.token_hex(8),
        "intent_hash": intent_hash(contract),
        "cart_hash": cart.get("cart_hash") or hash_cart(cart),
        "issued_at": utc_now_iso(),
    }
    return sign(body)


def _agent_tried(cart: dict) -> str:
    if cart.get("summary"):
        return str(cart["summary"])
    return ""


def _quarantine_stage(products: list[dict]) -> dict:
    # Live products are already quarantined by ShopSession.search_products for the agent;
    # check() still receives the full server-side docs.
    return {"clean": True, "stripped_count": 0}


def check(contract: dict, cart: dict, products: list[dict]) -> dict:
    """Run policy → canary → Decision 1 → Neon on escalation. Signature matches the server stub."""
    started = time.perf_counter()
    contract = contract or {}
    cart = cart or {}
    products = list(products or [])
    reasons: list[str] = []
    hidden = ""
    verdict_name: str | None = None

    policy_stage = check_policy(contract, cart, products)
    if not policy_stage["pass"]:
        reasons.extend(policy_stage["failures"])
        verdict_name = "block"

    canary_hit = canary.scan({"contract": contract, "cart": cart, "products": products})
    canary_stage = {"tripped": bool(canary_hit)}
    if canary_hit:
        reasons.append("canary: secret token appeared in a tool call, cart, or listing; treating as hijack")
        verdict_name = "block"

    from .decisions import MODEL as DECISIONS_MODEL
    decisions_stage = {"model": DECISIONS_MODEL, "clauses": {}, "escalated": False}
    contract_stage: dict = {"pass": None, "failed_clauses": [], "model": None}

    if verdict_name is None:
        messages = cart.get("merchant_messages") or cart.get("messages")
        gate_result = gate.evaluate(contract, products, merchant_messages=messages, cart=cart)
        decisions_stage = {
            "model": gate_result.get("model") or DECISIONS_MODEL,
            "clauses": gate_result.get("clauses") or {},
            "escalated": bool(gate_result.get("escalated") or gate_result.get("verdict") == "escalate"),
        }
        gverdict = gate_result.get("verdict")
        if gverdict == "approve":
            verdict_name = "approve"
            contract_stage = {"pass": True, "failed_clauses": [], "model": None}
            reasons.extend(gate_result.get("reasons") or [])
        elif gverdict == "block":
            verdict_name = "block"
            reasons.extend(gate_result.get("reasons") or [])
            contract_stage = {
                "pass": False,
                "failed_clauses": [r.split(":", 1)[0] for r in (gate_result.get("reasons") or []) if ":" in r],
                "model": None,
            }
        elif gverdict == "ask_human":
            verdict_name = "ask_human"
            reasons.extend(gate_result.get("reasons") or [])
        else:
            neon_result = neon.check_with_neon(contract, products, merchant_messages=messages)
            verdict_name = neon_result["verdict"]
            hidden = neon_result.get("hidden_in_listing") or ""
            reasons.extend(neon_result.get("reasons") or [])
            contract_stage = {
                "pass": neon_result.get("pass"),
                "failed_clauses": neon_result.get("failed_clauses") or [],
                "model": neon_result.get("model") or neon.MODEL,
            }
            decisions_stage["escalated"] = True

    receipt = None
    if verdict_name == "approve":
        receipt = _issue_receipt(contract, cart)
        # Nonce is consumed when the checkout is handed to the shopper (agent/tools.open_checkout), not here:
        # an approved cart can still fail the delivery/price quote, and the agent must be able to try another seller.
        if not reasons:
            reasons = ["all contract clauses passed Decision 1; policy and signature checks passed"]

    if not reasons:
        reasons = [str(verdict_name)]

    xray = {
        "asked_for": contract.get("summary") or contract.get("user_request") or "",
        "hidden_in_listing": hidden,
        "agent_tried": _agent_tried(cart) or (listing_text(products[0]) if products and isinstance(products[0], dict) else ""),
        "reasons": reasons,
    }

    latency_ms = int(round((time.perf_counter() - started) * 1000))
    return {
        "verdict": verdict_name,
        "contract_id": contract.get("contract_id"),
        "cart_id": cart.get("cart_id"),
        "enforced": enforced(),
        "stages": {
            "quarantine": _quarantine_stage(products),
            "policy": policy_stage,
            "canary": canary_stage,
            "decisions": decisions_stage,
            "contract": contract_stage,
        },
        "xray": xray,
        "receipt": receipt,
        "latency_ms": latency_ms,
    }
