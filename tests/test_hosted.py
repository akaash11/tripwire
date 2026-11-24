"""Hosted mode (Vercel + Upstash Redis): store layer, encrypted sessions, page-driven run, limits, replay.

Everything runs against a fake Upstash client, so no network, no Gemini and no Shop calls.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import guard
from agent.tools import ShopSession
from common.signing import sign
from server import app as server_app
from server.store import LocalStore, RedisStore, SecretBox


class FakeUpstash:
    """Just enough Redis for RedisStore: GET SET(NX,EX) INCR INCRBY EXPIRE RPUSH LRANGE LTRIM, plus pipeline."""

    def __init__(self):
        self.d: dict = {}
        self.ttl: dict = {}

    def command(self, *args):
        return self._do(list(args))

    def pipeline(self, cmds):
        return [self._do(c) for c in cmds]

    def _do(self, a):
        op = str(a[0]).upper()
        if op == "GET":
            return self.d.get(a[1])
        if op == "SET":
            opts = [str(x).upper() for x in a[3:]]
            if "NX" in opts and a[1] in self.d:
                return None
            self.d[a[1]] = a[2]
            if "EX" in opts:
                self.ttl[a[1]] = a[3:][opts.index("EX") + 1]
            return "OK"
        if op in ("INCR", "INCRBY"):
            self.d[a[1]] = str(int(self.d.get(a[1], 0)) + (int(a[2]) if op == "INCRBY" else 1))
            return int(self.d[a[1]])
        if op == "EXPIRE":
            self.ttl[a[1]] = a[2]
            return 1
        if op == "RPUSH":
            self.d.setdefault(a[1], []).append(a[2])
            return len(self.d[a[1]])
        if op == "LRANGE":
            lst, start, stop = self.d.get(a[1], []), int(a[2]), int(a[3])
            return lst[start:(None if stop == -1 else stop + 1)]
        if op == "LTRIM":
            lst, start, stop = self.d.get(a[1], []), int(a[2]), int(a[3])
            self.d[a[1]] = lst[start:(None if stop == -1 else stop + 1)]
            return "OK"
        raise NotImplementedError(op)


SECRET_URL = "https://shop.example/checkouts/cn/SECRET-CONTINUE-123"
SECRET_ID = "gid://shopify/Checkout/SECRET-ID-456"


@pytest.fixture
def rs():
    fake = FakeUpstash()
    return RedisStore(fake, prefix="tw:test:", box=SecretBox(b"k" * 32)), fake


def _contract(text="a can of red bull"):
    now = time.time()
    from datetime import datetime, timedelta, timezone
    nowdt = datetime.now(timezone.utc)
    return sign({"contract_id": "ic_" + str(int(now * 1000))[-8:], "nonce": "n" + str(int(now * 1e6)),
                 "created_at": nowdt.isoformat(timespec="seconds"),
                 "expires_at": (nowdt + timedelta(minutes=30)).isoformat(timespec="seconds"),
                 "summary": text, "item": text, "must": [], "must_not": [], "max_total_cents": 1000,
                 "budget_stated": True, "currency": "USD", "ships_to": "US", "compiled_by": "stub"})


def _session_with_secrets(contract):
    s = ShopSession(contract)
    cart = {"cart_id": "cart_t1", "contract_id": contract["contract_id"],
            "payee": {"shop_domain": "x.myshopify.com", "name": "X"},
            "lines": [{"product_id": "p", "variant_id": "v1", "title": "T", "quantity": 1, "unit_price_cents": 500,
                       "image_url": None, "product_url": None}],
            "total_cents": 500, "currency": "USD", "summary": "T x1, $5.00 from X"}
    s.carts[cart["cart_id"]] = cart
    s.products["v1"] = {"id": "p", "variants": [{"id": "v1", "price": {"amount": 500}}]}
    s.products["unused"] = {"id": "q", "variants": [{"id": "unused"}]}
    s.pending_cart = cart
    s.verdict = {"verdict": "ask_human", "xray": {"reasons": ["r"]}}
    s.continue_url, s._checkout_id, s._shop_domain, s._checkout_cart = SECRET_URL, SECRET_ID, "x.myshopify.com", "cart_t1"
    s.checkout = {"cart_id": "cart_t1", "status": "awaiting_payment", "totals": [{"type": "total", "amount": 500}]}
    s.over_budget = {"cart": cart, "checkout": {"id": SECRET_ID, "continue_url": SECRET_URL}, "total_cents": 900,
                     "budget_cents": 500, "summary": "over"}
    return s


# ---- store primitives ------------------------------------------------------------------------------------------------
def test_session_round_trip_never_stores_secrets_in_clear(rs):
    store, fake = rs
    c = _contract()
    store.save_session("rq_1", _session_with_secrets(c))
    blob = "".join(str(v) for v in fake.d.values())
    assert SECRET_URL not in blob and SECRET_ID not in blob and "SECRET" not in blob
    back = store.load_session("rq_1")
    assert back.continue_url == SECRET_URL and back._checkout_id == SECRET_ID
    assert back.pending_cart is back.carts["cart_t1"]
    assert back.over_budget["checkout"]["id"] == SECRET_ID and back.over_budget["cart"] is back.carts["cart_t1"]
    assert "unused" not in back.products and "v1" in back.products  # only variants in carts are kept


def test_repr_redacts_credentials(rs):
    store, _ = rs
    assert "RedisStore(<redacted>)" == repr(store)


def test_nonce_set_if_absent_is_atomic(rs):
    store, _ = rs
    assert store.is_used("abc") is False
    assert store.consume("abc") is True
    assert store.consume("abc") is False
    assert store.is_used("abc") is True


def test_run_lock_allows_one_run(rs):
    store, _ = rs
    assert store.acquire_run("rq_1") is True
    assert store.acquire_run("rq_1") is False
    assert store.run_started("rq_1") is not None


def test_rate_limit_per_ip_and_daily_counter(rs):
    store, _ = rs
    assert [store.rate_hit("1.1.1.1", 3, 100) for _ in range(4)] == [False, False, False, True]
    assert store.rate_hit("2.2.2.2", 3, 100) is False  # another IP is not affected
    assert [store.daily_incr() for _ in range(3)] == [1, 2, 3]
    store.add_tokens(120)
    store.add_tokens(30)
    assert store.today_tokens() == 150


def test_find_by_token_rejects_odd_tokens(rs):
    store, _ = rs
    assert store.find_by_token("../../etc") is None and store.find_by_token("short") is None


def test_replay_blocked_with_redis_nonce_backend(rs):
    store, _ = rs
    guard.replay.set_nonce_backend(store)
    try:
        c = _contract()
        assert guard.replay.verify_contract(c)[0] is True
        guard.replay.consume_nonce(c["nonce"])
        ok, why = guard.replay.verify_contract(c)
        assert ok is False and "replay" in why
    finally:
        guard.replay.set_nonce_backend(None)


def test_cancelled_session_refuses_checkout_and_tools():
    s = _session_with_secrets(_contract())
    s.cancelled = True
    assert "expired" in s.dispatch("search_products", {"query": "x"})["error"]
    with pytest.raises(Exception):
        s.open_checkout(s.carts["cart_t1"])


# ---- hosted routes (fake Redis, stubbed contract + agent) ----------------------------------------------------------
@pytest.fixture
def hosted(monkeypatch, rs):
    store, fake = rs
    monkeypatch.setattr(server_app, "store", store)
    monkeypatch.setattr(server_app, "HOSTED", True)
    monkeypatch.setattr(server_app, "RATE_LIMIT_PER_MIN", 10)
    monkeypatch.setenv("DAILY_REQUEST_CAP", "50")
    guard.replay.set_nonce_backend(store)
    calls = {"compile": 0, "agent": 0}

    def fake_compile(audio=None, photo=None, text=None, primary_timeout_s=None):
        calls["compile"] += 1
        return _contract(text or "stub")

    def fake_agent(contract, session=None, model=None, on_event=None, deadline=None):
        calls["agent"] += 1
        s = _session_with_secrets(contract)
        session.carts, session.products = s.carts, s.products
        session.verdict, session.pending_cart = s.verdict, s.carts["cart_t1"]
        session.continue_url, session._checkout_id = s.continue_url, s._checkout_id
        session._shop_domain, session._checkout_cart, session.checkout = s._shop_domain, s._checkout_cart, s.checkout
        session.on_event("cart", cart_id="cart_t1", summary="T x1")
        session.on_event("verdict", cart_id="cart_t1", verdict="ask_human", reason="r")
        session.agent_summary = "stub"
        return session

    monkeypatch.setattr("agent.contract.compile_contract", fake_compile)
    monkeypatch.setattr("agent.run.run", fake_agent)
    yield TestClient(server_app.app, client=("203.0.113.9", 4000)), store, fake, calls
    guard.replay.set_nonce_backend(None)


def test_hosted_flow_run_pending_approve(hosted):
    c, store, fake, calls = hosted
    r = c.post("/request_text", json={"text": "one can of original Red Bull"})
    assert r.status_code == 200
    tok = r.json()["token"]
    assert store.find_by_token(tok)["status"] == "contract_ready"  # nothing ran in the background
    assert calls["agent"] == 0

    a = c.post(f"/r/{tok}/run").json()
    assert a["started"] is True and calls["agent"] == 1
    b = c.post(f"/r/{tok}/run").json()  # the lock: a second call must not run the agent again
    assert b["started"] is False and calls["agent"] == 1

    w = c.get(f"/w/{tok}").json()
    assert w["status"] == "ask_human" and w["pending"]["cart_id"] == "cart_t1" and w["verdict"]["verdict"] == "ask_human"
    assert SECRET_URL not in c.get(f"/w/{tok}").text and SECRET_URL not in c.get(f"/r/{tok}/events").text
    kinds = [e["kind"] for e in c.get(f"/r/{tok}/events").json()["events"]]
    assert "cart" in kinds and "verdict" in kinds

    d = c.post(f"/w/{tok}/decide", json={"cart_id": "cart_t1", "decision": "approve"}).json()
    assert d["ok"] is True and d["url"] == SECRET_URL  # the continue_url only comes back after the shopper approves
    nonce = store.get_contract(store.find_by_token(tok)["contract_id"])["nonce"]
    assert store.is_used(nonce)  # contract nonce consumed in Redis
    assert store.find_by_token(tok)["status"] == "approve"
    assert c.post(f"/w/{tok}/decide", json={"cart_id": "cart_t1", "decision": "approve"}).json()["ok"] is False


def test_hosted_block_clears_pending(hosted):
    c, store, _, _ = hosted
    tok = c.post("/request_text", json={"text": "x"}).json()["token"]
    c.post(f"/r/{tok}/run")
    d = c.post(f"/w/{tok}/decide", json={"cart_id": "cart_t1", "decision": "block"}).json()
    assert d["ok"] is True and d["url"] == ""
    assert c.get(f"/w/{tok}").json()["pending"] is None and c.get(f"/w/{tok}").json()["status"] == "block"


def test_hosted_admin_is_never_local(hosted):
    c, *_ = hosted
    for path in ("/admin", "/api/admin/state", "/api/admin/live", "/api/state", "/tripwire/admin", "/tripwire/api/admin/live"):
        assert c.get(path).status_code == 403
    # spoofing the Host or a loopback address does not help
    spoof = TestClient(server_app.app, client=("127.0.0.1", 1), base_url="http://localhost")
    assert spoof.get("/api/admin/live").status_code == 403


def test_empty_requests_hit_the_rate_limit_without_gemini(hosted):
    c, _, _, calls = hosted
    codes = [c.post("/request").status_code for _ in range(11)]
    assert codes == [400] * 10 + [429]
    assert calls["compile"] == 0


def test_daily_cap_returns_speak_without_token_and_invalid_does_not_burn_it(hosted, monkeypatch):
    c, store, _, calls = hosted
    monkeypatch.setenv("DAILY_REQUEST_CAP", "2")
    assert c.post("/request_text", json={"text": ""}).status_code == 400  # invalid: not counted
    assert c.post("/request_text", json={"text": "one"}).json()["token"]
    assert c.post("/request_text", json={"text": "two"}).json()["token"]
    over = c.post("/request_text", json={"text": "three"})
    assert over.status_code == 200 and "token" not in over.json()
    assert over.json()["speak"] == "Tripwire is resting for today, try tomorrow."
    assert calls["compile"] == 2


def test_dead_run_expires_and_late_writer_cannot_resurrect(hosted):
    c, store, fake, _ = hosted
    tok = c.post("/request_text", json={"text": "x"}).json()["token"]
    rid = store.find_by_token(tok)["id"]
    assert store.acquire_run(rid)
    store.update_request(rid, status="shopping")
    store.append_event({"ts": time.time() - 500, "kind": "search", "request_id": rid})
    fake.d[store._k("lock", rid)] = str(time.time() - 500)  # run started long ago, no event since
    w = c.get(f"/w/{tok}").json()
    assert w["status"] == "expired" and "Start again" in (w["problem"] or "")
    assert store.is_cancelled(rid)


def test_upload_is_kept_in_memory_only(hosted, monkeypatch, tmp_path):
    c, store, _, calls = hosted
    seen = {}

    def fake_compile(audio=None, photo=None, text=None, primary_timeout_s=None):
        seen["audio"], seen["photo"] = audio, photo
        return _contract("pic")

    monkeypatch.setattr("agent.contract.compile_contract", fake_compile)
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "JPEG")
    r = c.post("/request", files={"photo": ("p.jpg", buf.getvalue(), "image/jpeg"),
                                  "audio": ("v.m4a", b"abc", "audio/m4a")}, data={"client": "Tripwire Buy 2.0"})
    assert r.json()["token"] and isinstance(seen["photo"], bytes) and seen["audio"] == (b"abc", "v.m4a")
    req = store.find_by_token(r.json()["token"])
    assert "path" not in req["files"]["photo"] and req["client"] == "Tripwire Buy 2.0"


def test_local_store_is_the_default_without_redis_env(monkeypatch):
    for k in ("VERCEL", "TRIPWIRE_STORE", "KV_REST_API_URL", "UPSTASH_REDIS_REST_URL"):
        monkeypatch.delenv(k, raising=False)
    from server.store import make_store
    assert isinstance(make_store({}, {}, {}, lambda: None), LocalStore)
