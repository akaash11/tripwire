from agent import storefront

META = {"name": "Monday Cartoons", "currency": "USD", "myshopify_domain": "monday-cartoons.myshopify.com"}
PRODUCTS = {"products": [{"id": 1, "handle": "digital-sticker", "title": "Digital Sticker", "body_html": "<p>Cat</p>",
                          "images": [{"src": "https://cdn.shopify.com/x.png"}],
                          "variants": [{"id": 9, "title": "Default Title", "price": "0.50", "available": True,
                                        "requires_shipping": False}]}]}


def test_lookup_shapes_shop_product(monkeypatch):
    monkeypatch.setattr(storefront, "_get_json", lambda url, timeout=6: META if url.endswith("meta.json") else PRODUCTS)
    p = storefront.lookup("https://mondaycartoons.com/products/digital-sticker?_pos=1&x=y")
    v = p["variants"][0]
    assert v["id"] == "gid://shopify/ProductVariant/9"
    assert v["price"] == {"amount": 50, "currency": "USD"}
    assert v["seller"] == {"name": "Monday Cartoons", "domain": "monday-cartoons.myshopify.com"}
    assert p["media"][0]["url"] == "https://cdn.shopify.com/x.png"


def test_lookup_not_shopify(monkeypatch):
    monkeypatch.setattr(storefront, "_get_json", lambda url, timeout=6: {"name": "x"})
    assert storefront.lookup("https://example.com/products/a") is None
    assert storefront.lookup("http://example.com/products/a") is None
    assert storefront.lookup("https://example.com/about") is None
