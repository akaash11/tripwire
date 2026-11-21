"""Off-catalog Shopify stores: when Shop's catalog doesn't list a product link, check whether the site is a Shopify
store (`/meta.json` has `myshopify_domain`) and read the product from the store's own public `products.json`.

The result is shaped like a Shop MCP product so it goes through the same guard, live-price check, delivery quote and
Shop `create_checkout` as any catalog product. Data is fetched live per request (never cached) and the merchant's image
URL is kept exactly as returned. Product text is untrusted data.
"""
from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlparse

_TAGS = re.compile(r"<[^>]+>")
_UA = "Mozilla/5.0 (compatible; Tripwire/1.7)"


def _get_json(url: str, timeout: float = 6) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https only, checked by caller)
        return json.loads(r.read(2_000_000))


def _cents(price) -> int | None:
    try:
        return round(float(price) * 100)
    except (TypeError, ValueError):
        return None


def lookup(url: str) -> dict | None:
    """Return a Shop-shaped product for a Shopify store product link, or None (not Shopify / not found / error)."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return None
    m = re.search(r"/products/([A-Za-z0-9\-_.%]+)", u.path)
    if not m:
        return None
    handle = m.group(1).lower()
    base = f"https://{u.hostname}"
    try:
        meta = _get_json(f"{base}/meta.json")
        shop_domain = meta.get("myshopify_domain")
        if not shop_domain or not str(shop_domain).endswith(".myshopify.com"):
            return None
        currency = meta.get("currency") or "USD"
        found = None
        for page in (1, 2):
            items = _get_json(f"{base}/products.json?limit=250&page={page}").get("products") or []
            found = next((p for p in items if (p.get("handle") or "").lower() == handle), None)
            if found or len(items) < 250:
                break
    except Exception:  # noqa: BLE001 (any network/parse problem: just not resolvable)
        return None
    if not found:
        return None
    variants = [v for v in found.get("variants") or [] if v.get("id")]
    if not variants:
        return None
    v = next((x for x in variants if x.get("available")), variants[0])
    seller = {"name": meta.get("name") or found.get("vendor") or u.hostname, "domain": shop_domain}
    desc = re.sub(r"\s+", " ", _TAGS.sub(" ", found.get("body_html") or "")).strip()[:280]
    facts = [x for x in (desc, f"Type: {found.get('product_type')}" if found.get("product_type") else "",
                         "Digital item (no shipping)" if v.get("requires_shipping") is False else "") if x]
    images = [{"url": i["src"]} for i in found.get("images") or [] if isinstance(i, dict) and i.get("src")]
    opts = [] if (v.get("title") or "") == "Default Title" else [{"name": "Option", "label": v.get("title")}]
    return {
        "id": f"gid://shopify/Product/{found['id']}",
        "title": found.get("title") or "",
        "url": f"{base}/products/{found.get('handle')}",
        "media": images[:1],
        "metadata": {"top_features": facts},
        "_source": "storefront",
        "variants": [{
            "id": f"gid://shopify/ProductVariant/{v['id']}",
            "price": {"amount": _cents(v.get("price")), "currency": currency},
            "availability": {"available": bool(v.get("available"))},
            "seller": seller,
            "options": opts,
        }],
    }
