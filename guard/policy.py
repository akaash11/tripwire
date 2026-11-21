"""Deterministic policy backstop (docs/TANU.md). No model calls."""

from __future__ import annotations

from .replay import hash_cart, verify_contract


def check_policy(contract: dict, cart: dict) -> dict:
    """Return {pass, failures[]} for signature, expiry, nonce, cap, currency, and cart_hash."""
    contract = contract or {}
    cart = cart or {}
    failures: list[str] = []

    ok, reason = verify_contract(contract)
    if not ok:
        failures.append(reason)

    failures.extend(_check_total(contract, cart))
    failures.extend(_check_currency(contract, cart))
    failures.extend(_check_cart_hash(cart))

    return {"pass": not failures, "failures": failures}


def _as_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _check_total(contract: dict, cart: dict) -> list[str]:
    cap = _as_int(contract.get("max_total_cents"))
    total = _as_int(cart.get("total_cents"))
    if total is None:
        return ["total: cart is missing total_cents"]
    if cap is None:
        return ["total: contract is missing max_total_cents"]
    if total > cap:
        return [f"total: {total} cents exceeds max_total_cents {cap}"]
    return []


def _check_currency(contract: dict, cart: dict) -> list[str]:
    expected = (contract.get("currency") or "").strip().upper()
    got = (cart.get("currency") or "").strip().upper()
    if not expected:
        return ["currency: contract is missing currency"]
    if not got:
        return ["currency: cart is missing currency"]
    if expected != got:
        return [f"currency: cart {got} does not match contract {expected}"]
    return []


def _check_cart_hash(cart: dict) -> list[str]:
    expected = cart.get("cart_hash")
    if not expected:
        return ["cart_hash: cart is missing cart_hash"]
    try:
        actual = hash_cart(cart)
    except KeyError as exc:
        return [f"cart_hash: cart is missing {exc}"]
    if actual != expected:
        return ["cart_hash: cart has changed since the agent built it"]
    return []
