"""Shop MCP client (https://mcp.shop.com/, docs: shop.dev/docs). Owner: Akaash.

Stateless JSON-RPC over HTTP: one POST per tool call, no session. Live data only: never cache results or
re-host merchant images, and keep every URL exactly as returned (query string included).

Real purchases: Tripwire creates the checkout only after the guard approves, then hands the shopper the
checkout's continue_url to review, enter their address and pay on the merchant's page.
The checkout id and continue_url carry an access key: never log them; share continue_url only with the shopper.
"""
from __future__ import annotations

import time
import uuid

import httpx

from common import log_usage

URL = "https://mcp.shop.com/"
PROTO = "2026-07-28"
CLIENT = {"name": "tripwire", "version": "0.1"}


class ShopError(RuntimeError):
    pass


def call(tool: str, arguments: dict, timeout: float = 30) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments,
                       "_meta": {"io.modelcontextprotocol/protocolVersion": PROTO,
                                 "io.modelcontextprotocol/clientInfo": CLIENT,
                                 "io.modelcontextprotocol/clientCapabilities": {}}}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTO, "Mcp-Method": "tools/call", "Mcp-Name": tool}
    t0 = time.time()
    r = httpx.post(URL, json=body, headers=headers, timeout=timeout)
    ms = int((time.time() - t0) * 1000)
    log_usage("shop-mcp", tool, ms, None)
    d = r.json()
    if "error" in d:
        raise ShopError(f"{tool}: {d['error'].get('message')}")
    res = d.get("result") or {}
    if res.get("isError"):
        text = " ".join(p.get("text", "") for p in res.get("content", []) if p.get("type") == "text")
        raise ShopError(f"{tool}: {text[:300]}")
    return res.get("structuredContent") or {}


def search(query: str, max_price_cents: int | None = None, country: str = "US", currency: str = "USD",
           limit: int = 8) -> dict:
    catalog: dict = {"query": query, "context": {"address_country": country, "currency": currency},
                     "pagination": {"limit": limit}}
    if max_price_cents:
        catalog["filters"] = {"price": {"max": int(max_price_cents)}}
    return call("search_catalog", {"catalog": catalog})


def get_product(product_id: str) -> dict:
    return call("get_product", {"id": product_id})


def create_checkout(shop_domain: str, variant_id: str, quantity: int = 1, country: str = "US",
                    destination: dict | None = None) -> dict:
    """destination: coarse locality only (country, region, city, postal code; never a street or name). With it, Shop
    returns real shipping + tax, and `delivery_no_delivery_available_for_merchandise_line` if the store can't deliver."""
    checkout: dict = {"line_items": [{"item": {"id": variant_id}, "quantity": quantity}],
                      "context": {"address_country": country}}
    if destination:
        checkout["context"] = {k: v for k, v in {"address_country": destination.get("address_country", country),
                                                 "address_region": destination.get("address_region"),
                                                 "postal_code": destination.get("postal_code")}.items() if v}
        checkout["fulfillment"] = {"methods": [{"type": "shipping",
                                                "destinations": [{"type": "shipping_address", **destination}]}]}
    return call("create_checkout", {"shop": shop_domain, "checkout": checkout,
                                    "meta": {"idempotency-key": str(uuid.uuid4())}})


NO_DELIVERY = "delivery_no_delivery_available_for_merchandise_line"


def get_checkout(shop_domain: str, checkout_id: str) -> dict:
    return call("get_checkout", {"shop": shop_domain, "id": checkout_id})


def refresh_checkout_view(shop_domain: str, checkout_id: str) -> dict:
    """Poll Shop MCP get_checkout and return a secret-free view (never returns id or continue_url)."""
    return checkout_view(get_checkout(shop_domain, checkout_id))


def checkout_view(c: dict) -> dict:
    """What we show the shopper (and store): no checkout id, continue_url kept separately."""
    order = c.get("order")
    order_view = None
    if isinstance(order, dict):
        order_view = {k: order.get(k) for k in ("id", "name", "order_number", "permalink_url", "status_url", "created_at")
                      if order.get(k) is not None}
    return {
        "status": c.get("status"),
        "currency": c.get("currency"),
        "lines": [{"title": (l.get("item") or {}).get("title"), "quantity": l.get("quantity")}
                  for l in c.get("line_items", [])],
        "totals": [{"type": t.get("type"), "amount": t.get("amount"), "label": t.get("display_text")}
                   for t in c.get("totals", [])],
        "messages": [{"type": m.get("type"), "code": m.get("code"), "severity": m.get("severity"),
                      "content": m.get("content")} for m in c.get("messages", [])],
        "links": [{"type": l.get("type"), "url": l.get("url")} for l in c.get("links", [])],
        "order": order_view,
        "expires_at": c.get("expires_at"),
    }
