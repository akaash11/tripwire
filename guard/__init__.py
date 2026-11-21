"""Tripwire guard. Owner: Tanu.

check(contract, cart, products) -> verdict dict (shape: docs/ARCHITECTURE.md "Verdict shape").
This is a STUB so the server and agent can be wired now. Replace the body; keep the signature and the keys.
"""
from __future__ import annotations

import os
import time


def enforced() -> bool:
    return os.environ.get("TRIPWIRE_ENFORCED", "1") == "1"


def check(contract: dict, cart: dict, products: list[dict]) -> dict:
    t0 = time.time()
    verdict = "ask_human"  # STUB: always asks the human
    return {
        "verdict": verdict,
        "contract_id": contract.get("contract_id"),
        "cart_id": cart.get("cart_id"),
        "enforced": enforced(),
        "stages": {
            "quarantine": {"clean": True, "stripped_count": 0},
            "policy": {"pass": True, "failures": []},
            "decisions": {"model": "gemini-decision-1", "clauses": {}, "escalated": False},
            "contract": {"pass": None, "failed_clauses": [], "model": None},
            "canary": {"tripped": False},
        },
        "xray": {
            "asked_for": contract.get("summary", ""),
            "hidden_in_listing": "",
            "agent_tried": cart.get("summary", ""),
            "reasons": ["stub guard: real checks not wired yet"],
        },
        "receipt": None,
        "latency_ms": int((time.time() - t0) * 1000),
    }


def reduced(verdict: dict) -> dict:
    """What the agent gets back from request_checkout: verdict + one line, never the x-ray."""
    reasons = verdict.get("xray", {}).get("reasons") or [""]
    return {"verdict": verdict["verdict"], "reason": reasons[0]}
