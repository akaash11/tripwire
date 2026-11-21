"""Find a pre-checkout refund-policy signal in Shop MCP product, variant, seller, or cart data.

Catalog search results often have no such link. Checkout `links[]` with type `refund_policy`
is the documented source, and may already be copied onto the cart in the live app path.
"""

from __future__ import annotations

_POLICY_TYPES = ("refund_policy", "return_policy", "refund-policy", "return-policy", "refund", "returns")
_POLICY_KEYS = (
    "refund_policy",
    "return_policy",
    "refundPolicy",
    "returnPolicy",
    "refund_policy_url",
    "return_policy_url",
)


def _as_url(value) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, dict):
        for key in ("url", "href", "uri", "link"):
            found = _as_url(value.get(key))
            if found:
                return found
    return None


def _type_is_refund(value: str) -> bool:
    text = (value or "").strip().lower().replace(" ", "_")
    return text in _POLICY_TYPES or "refund_policy" in text or "return_policy" in text


def _from_links(links, source: str) -> dict | None:
    if isinstance(links, dict):
        links = [links]
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            url = _as_url(link)
            if url and "refund" in url.lower():
                return {"url": url, "type": "refund_policy", "source": source}
            continue
        typ = str(link.get("type") or link.get("rel") or link.get("name") or "")
        url = _as_url(link)
        if url and _type_is_refund(typ):
            return {"url": url, "type": typ or "refund_policy", "source": source}
        if url and "refund" in url.lower() and "policy" in url.lower():
            return {"url": url, "type": typ or "refund_policy", "source": source}
    return None


def _from_mapping(obj: dict, source: str) -> dict | None:
    found = _from_links(obj.get("links"), f"{source}.links")
    if found:
        return found
    for key in _POLICY_KEYS:
        if key not in obj:
            continue
        url = _as_url(obj.get(key))
        if url:
            return {"url": url, "type": "refund_policy", "source": f"{source}.{key}"}
    policies = obj.get("policies")
    if isinstance(policies, dict):
        for key, val in policies.items():
            url = _as_url(val)
            if url and _type_is_refund(str(key)):
                return {"url": url, "type": str(key), "source": f"{source}.policies.{key}"}
        found = _from_links(list(policies.values()), f"{source}.policies")
        if found:
            return found
    elif isinstance(policies, list):
        found = _from_links(policies, f"{source}.policies")
        if found:
            return found
    return None


def refund_policy_signal(products: list[dict] | None, cart: dict | None = None) -> dict | None:
    """Return {url, type, source} if a refund/return policy link is present; else None."""
    if cart and isinstance(cart, dict):
        found = _from_mapping(cart, "cart")
        if found:
            return found
    for i, product in enumerate(products or []):
        if not isinstance(product, dict):
            continue
        found = _from_mapping(product, f"product[{i}]")
        if found:
            return found
        seller = product.get("seller")
        if isinstance(seller, dict):
            found = _from_mapping(seller, f"product[{i}].seller")
            if found:
                return found
        for j, variant in enumerate(product.get("variants") or []):
            if not isinstance(variant, dict):
                continue
            found = _from_mapping(variant, f"product[{i}].variants[{j}]")
            if found:
                return found
            vseller = variant.get("seller")
            if isinstance(vseller, dict):
                found = _from_mapping(vseller, f"product[{i}].variants[{j}].seller")
                if found:
                    return found
    return None
