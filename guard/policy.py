"""Deterministic policy backstop (docs/TANU.md). No model calls."""

from __future__ import annotations

from .replay import hash_cart, verify_contract


def check_policy(contract: dict, cart: dict, products: list[dict] | None = None) -> dict:
    """Return {pass, failures[]} for signature, expiry, nonce, cap, currency, cart_hash, and live prices."""
    contract = contract or {}
    cart = cart or {}
    failures: list[str] = []

    ok, reason = verify_contract(contract)
    if not ok:
        failures.append(reason)

    failures.extend(_check_total(contract, cart))
    failures.extend(_check_currency(contract, cart))
    failures.extend(_check_cart_hash(cart))
    failures.extend(_check_live_prices(cart, products or []))

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


def _find_variant(line: dict, products: list[dict]) -> dict | None:
    """Return the live variant matching the cart line, or None if it is missing."""
    variant_id = line.get("variant_id")
    product_id = line.get("product_id")
    if variant_id:
        for product in products:
            if not isinstance(product, dict):
                continue
            for variant in product.get("variants") or []:
                if isinstance(variant, dict) and variant.get("id") == variant_id:
                    return variant
        return None
    if product_id:
        for product in products:
            if not isinstance(product, dict) or product.get("id") != product_id:
                continue
            for variant in product.get("variants") or []:
                if isinstance(variant, dict):
                    return variant
    return None


def _check_live_prices(cart: dict, products: list[dict]) -> list[str]:
    """Require each line's unit price and the cart total to match live Shop variant prices."""
    lines = cart.get("lines")
    if not isinstance(lines, list) or not lines:
        return ["price_tamper: cart has no lines"]

    failures: list[str] = []
    live_total = 0
    live_currency = None
    cart_currency = (cart.get("currency") or "").strip().upper()

    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            failures.append(f"price_tamper: line {i} is not an object")
            continue
        variant = _find_variant(line, products)
        label = line.get("variant_id") or line.get("product_id") or f"line {i}"
        if variant is None:
            failures.append(f"price_tamper: {label} is missing from live products")
            continue

        price = variant.get("price") or {}
        live_amount = _as_int(price.get("amount"))
        if live_amount is None:
            failures.append(f"price_tamper: {label} is missing a live variant price")
            continue
        unit = _as_int(line.get("unit_price_cents"))
        if unit is None:
            failures.append(f"price_tamper: {label} is missing unit_price_cents")
        elif unit != live_amount:
            failures.append(
                f"price_tamper: {label} unit_price_cents {unit} != live variant price {live_amount}"
            )

        qty = _as_int(line.get("quantity"))
        if qty is None or qty < 1:
            failures.append(f"price_tamper: {label} has invalid quantity {line.get('quantity')}")
            qty = 0
        live_total += live_amount * qty

        variant_currency = (price.get("currency") or "").strip().upper()
        if variant_currency:
            if live_currency is None:
                live_currency = variant_currency
            elif live_currency != variant_currency:
                failures.append(
                    f"price_tamper: {label} live currency {variant_currency} != {live_currency}"
                )
            if cart_currency and variant_currency != cart_currency:
                failures.append(
                    f"price_tamper: {label} live currency {variant_currency} != cart {cart_currency}"
                )

    cart_total = _as_int(cart.get("total_cents"))
    if cart_total is None:
        failures.append("price_tamper: cart is missing total_cents")
    elif live_total and cart_total != live_total:
        # live_total is 0 only if every line failed lookup/price/qty; skip a redundant total mismatch then.
        failures.append(
            f"price_tamper: cart total_cents {cart_total} != recomputed live total {live_total}"
        )
    return failures
