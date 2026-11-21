"""Tripwire guard. Owner: Tanu.

check(contract, cart, products) -> verdict dict (shape: docs/ARCHITECTURE.md, rules: docs/TANU.md).
reduced(verdict) is what the agent gets back from request_checkout.
canary_token() is planted in the agent's instructions; scan_tool_args() checks tool-call arguments.
"""
from . import canary, replay
from .canary import canary_token, scan, scan_tool_args
from .photo import photo_match
from .check import check, enforced, reduced

__all__ = [
    "check",
    "reduced",
    "enforced",
    "canary_token",
    "scan",
    "scan_tool_args",
    "photo_match",
]
