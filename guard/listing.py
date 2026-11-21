"""Build Decision 1 / Neon input from a live Shop MCP product doc. Never download images."""

from __future__ import annotations

import re

_TAGS = re.compile(r"<[^>]+>")


def _variant(product: dict) -> dict:
    variants = product.get("variants") or []
    return variants[0] if variants and isinstance(variants[0], dict) else {}


def image_url(product: dict) -> str | None:
    """media[0].url from a Shop MCP product. Never download or re-host."""
    media = product.get("media") or product.get("images") or []
    if isinstance(media, dict):
        media = [media]
    if not media:
        return None
    first = media[0]
    if isinstance(first, str) and first.startswith("http"):
        return first
    if isinstance(first, dict):
        url = first.get("url") or first.get("uri") or first.get("src")
        if isinstance(url, str) and url.startswith("http"):
            return url
        file = first.get("file") or first.get("preview") or {}
        if isinstance(file, dict):
            url = file.get("url") or file.get("uri")
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


def image_block(product: dict) -> dict | None:
    url = image_url(product)
    if not url:
        return None
    return {"type": "image", "uri": url, "mime_type": "image/jpeg", "resolution": "low"}


def listing_text(product: dict) -> str:
    """Title, variant options, metadata.top_features, seller, price."""
    title = _TAGS.sub("", str(product.get("title") or ""))
    v = _variant(product)
    options = ", ".join(
        f"{o.get('name')}: {o.get('label')}" for o in (v.get("options") or []) if isinstance(o, dict)
    )
    meta = product.get("metadata") or {}
    features = meta.get("top_features") or []
    if isinstance(features, list):
        features_s = "; ".join(str(x) for x in features[:8])
    else:
        features_s = str(features)
    seller = v.get("seller") or product.get("seller") or {}
    if not isinstance(seller, dict):
        seller = {}
    seller_s = seller.get("name") or seller.get("domain") or ""
    price = v.get("price") or {}
    if not isinstance(price, dict):
        price = {}
    amount = price.get("amount")
    currency = price.get("currency") or ""
    if amount is not None:
        try:
            price_s = f"{int(amount) / 100:.2f} {currency}".strip()
        except (TypeError, ValueError):
            price_s = f"{amount} {currency}".strip()
    else:
        price_s = ""
    parts = [f"Title: {title}"]
    if options:
        parts.append(f"Variant options: {options}")
    if features_s:
        parts.append(f"Top features: {features_s}")
    if seller_s:
        parts.append(f"Seller: {seller_s}")
    if price_s:
        parts.append(f"Price: {price_s}")
    return "\n".join(parts)


def input_blocks(products: list[dict]) -> list[dict]:
    """Text + optional image uri block(s) for Decision 1 / Neon. Never downloads."""
    texts = []
    images = []
    for product in products or []:
        if not isinstance(product, dict):
            continue
        texts.append(listing_text(product))
        block = image_block(product)
        if block and block["uri"] not in {b["uri"] for b in images}:
            images.append(block)
    text = "\n\n".join(t for t in texts if t) or "No product facts provided."
    return [{"type": "text", "text": text}, *images]
