"""Final check on the real Shop checkout before the shopper gets the pay link (docs/STATUS.md)."""

from __future__ import annotations

from .refund import refund_policy_signal
from .replay import bound_record


def _as_int(value, default=None):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _checkout_total(checkout: dict) -> int | None:
    for row in checkout.get("totals") or []:
        if isinstance(row, dict) and row.get("type") == "total":
            return _as_int(row.get("amount"))
    return None


def _warning_lines(checkout: dict) -> list[str]:
    out = []
    for msg in checkout.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        code = str(msg.get("code") or "")
        content = str(msg.get("content") or "")
        typ = str(msg.get("type") or "")
        line = " ".join(p for p in (typ, code, content) if p).strip()
        if line:
            out.append(line)
    return out


def final_check(contract: dict, cart: dict, checkout: dict | None = None) -> dict:
    """Re-check the quoted Shop checkout: total ≤ cap, payee unchanged, warnings listed, refund link if required.

    `checkout` is the secret-free view from shop_mcp.checkout_view (plus shop_name/cart_id).
    """
    contract = contract or {}
    cart = cart or {}
    checkout = checkout or {}
    failures: list[str] = []
    warnings = _warning_lines(checkout)

    cap = _as_int(contract.get("max_total_cents"))
    total = _checkout_total(checkout)
    if total is None:
        total = _as_int(cart.get("total_cents"))
    if cap is not None and total is not None and total > cap:
        failures.append(f"final_check: checkout total {total} cents exceeds max_total_cents {cap}")

    cart_payee = cart.get("payee") if isinstance(cart.get("payee"), dict) else {}
    rec = bound_record(contract, cart) or {}
    bound_payee = rec.get("payee") if isinstance(rec.get("payee"), dict) else {}
    if bound_payee.get("shop_domain") and cart_payee.get("shop_domain"):
        if bound_payee["shop_domain"] != cart_payee["shop_domain"]:
            failures.append(
                f"final_check: payee shop_domain changed from {bound_payee['shop_domain']} "
                f"to {cart_payee['shop_domain']}"
            )
    shop_name = checkout.get("shop_name")
    if shop_name and cart_payee.get("name") and shop_name != cart_payee.get("name"):
        failures.append(f"final_check: checkout shop {shop_name!r} does not match cart payee {cart_payee.get('name')!r}")

    ccur = str(contract.get("currency") or "").strip().upper()
    xcur = str(checkout.get("currency") or "").strip().upper()
    if ccur and xcur and ccur != xcur:
        failures.append(f"final_check: checkout currency {xcur} does not match contract {ccur}")

    returns_required = str(contract.get("returns") or "").strip().lower() == "required"
    blob = " ".join(warnings).lower()
    if returns_required and ("final_sale" in blob or "final sale" in blob or "non-returnable" in blob):
        failures.append("final_check: checkout warning is final sale but returns are required")
    if returns_required:
        signal = refund_policy_signal([], cart={"links": checkout.get("links") or []})
        if not signal:
            failures.append("final_check: returns required but checkout has no refund_policy link")

    return {"pass": not failures, "failures": failures, "warnings": warnings}
