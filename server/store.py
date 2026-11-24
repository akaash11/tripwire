"""Storage for requests, events, sessions, nonces and limits. Owner: Akaash.

Two backends behind one interface:
- LocalStore: today's behaviour (in-memory STATE/SESSIONS, data/state.json, data/used_nonces.json). Laptop mode.
- RedisStore: Upstash Redis over REST (plain httpx). Used on Vercel, where the disk is read-only and every request
  may land on a different instance.

Secrets at rest (Shop checkout id, continue_url, the raw over-budget checkout) are AES-GCM encrypted with a key
derived from the signing key. Nothing here ever logs or prints a secret.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import secrets
import threading
import time

TTL_S = 7 * 24 * 3600  # how long a request, its events and its session live in Redis
NONCE_TTL_S = 8 * 24 * 3600  # longer than any contract lifetime
STALE_AFTER_S = 120  # a run with no new event for this long is treated as dead


# ---- at-rest encryption for session secrets ------------------------------------------------------------------------
class SecretBox:
    """AES-GCM with a key derived (HKDF-SHA256) from the signing key. enc(None) -> None."""

    def __init__(self, key_material: bytes | None = None):
        self._key_material = key_material
        self._aead = None

    def _get(self):
        if self._aead is None:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            material = self._key_material
            if material is None:
                from common.signing import private_bytes
                material = private_bytes()
            key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"tripwire-session-v1").derive(material)
            self._aead = AESGCM(key)
        return self._aead

    def enc(self, value: str | None) -> str | None:
        if value is None:
            return None
        nonce = secrets.token_bytes(12)
        return base64.b64encode(nonce + self._get().encrypt(nonce, value.encode(), None)).decode()

    def dec(self, blob: str | None) -> str | None:
        if blob is None:
            return None
        raw = base64.b64decode(blob)
        return self._get().decrypt(raw[:12], raw[12:], None).decode()


def utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


# ---- local backend ---------------------------------------------------------------------------------------------------
class LocalStore:
    hosted = False

    def __init__(self, state: dict, sessions: dict, snapshots: dict, persist):
        self.state, self.sessions, self.snapshots, self._persist = state, sessions, snapshots, persist
        self._runs: dict[str, float] = {}
        self._cancelled: set[str] = set()
        self._hits: list[float] = []
        self._day: dict[str, int] = {}
        self._lock = threading.Lock()

    # requests
    def new_request(self, r: dict) -> None:
        self.state["requests"].append(r)
        self._persist()

    def get_request(self, req_id: str) -> dict | None:
        return next((r for r in self.state["requests"] if r["id"] == req_id), None)

    def find_by_token(self, token: str) -> dict | None:
        if not token or len(token) < 8:
            return None
        return next((r for r in self.state["requests"] if r.get("token") and secrets.compare_digest(r["token"], token)), None)

    def update_request(self, req_id: str, **fields) -> dict | None:
        r = self.get_request(req_id)
        if r is not None:
            r.update(fields)
            self._persist()
        return r

    # events (local returns every event; callers filter by request / cart)
    def append_event(self, ev: dict) -> None:
        self.state["events"].append(ev)
        self._persist()

    def events(self, req_id: str) -> list[dict]:
        return self.state["events"]

    def last_event_ts(self, req_id: str) -> float | None:
        ts = [e["ts"] for e in self.state["events"] if e.get("request_id") == req_id]
        return max(ts) if ts else None

    # contracts
    def save_contract(self, c: dict) -> None:
        self.state.setdefault("contracts", {})[c["contract_id"]] = c
        self.state["current_contract"] = c["contract_id"]
        self._persist()

    def get_contract(self, cid: str | None) -> dict | None:
        return self.state.get("contracts", {}).get(cid) if cid else None

    # sessions (live objects in memory)
    def load_session(self, req_id: str):
        return self.sessions.get(req_id)

    def save_session(self, req_id: str, session) -> None:
        self.sessions[req_id] = session
        self._persist()

    # legacy "pending" pointer (used by the Approve shortcut; local only)
    def set_pending(self, p: dict) -> None:
        self.state["pending"] = p

    def get_pending(self) -> dict | None:
        return self.state.get("pending")

    def clear_pending(self, cart_id: str) -> None:
        if self.state.get("pending") and self.state["pending"].get("cart_id") == cart_id:
            self.state["pending"] = None

    # orders
    def add_order(self, o: dict) -> None:
        self.state["orders"].append(o)

    def has_order(self, cart_id, req_id) -> bool:
        return any(o.get("cart_id") == cart_id and o.get("request_id") == req_id for o in self.state["orders"])

    # run lock / cancel
    def acquire_run(self, req_id: str) -> bool:
        with self._lock:
            if req_id in self._runs:
                return False
            self._runs[req_id] = time.time()
            return True

    def run_started(self, req_id: str) -> float | None:
        return self._runs.get(req_id)

    def mark_cancelled(self, req_id: str) -> None:
        self._cancelled.add(req_id)

    def is_cancelled(self, req_id: str) -> bool:
        return req_id in self._cancelled

    # limits
    def rate_hit(self, ip: str, per_ip: int, global_limit: int) -> bool:
        now = time.time()
        with self._lock:
            self._hits[:] = [t for t in self._hits if now - t < 60]
            if len(self._hits) >= per_ip:
                return True
            self._hits.append(now)
            return False

    def daily_incr(self) -> int:
        day = utc_day()
        with self._lock:
            self._day = {day: self._day.get(day, 0) + 1}
            return self._day[day]

    # tokens (local reads logs/usage.jsonl through server.config)
    def add_tokens(self, n: int) -> None:
        pass

    def today_tokens(self) -> int:
        from server import config
        return config.today_tokens()

    # nonces: None -> guard.replay keeps using data/used_nonces.json
    nonce_backend = None


# ---- Upstash REST client + Redis backend -----------------------------------------------------------------------------
class UpstashClient:
    """Minimal Upstash Redis REST client. Never logs the token."""

    def __init__(self, url: str, token: str):
        import httpx
        self._http = httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=10.0)

    def command(self, *args):
        r = self._http.post("/", json=list(args))
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"redis error: {str(body['error'])[:120]}")
        return body["result"]

    def pipeline(self, cmds: list[list]):
        r = self._http.post("/pipeline", json=cmds)
        out = []
        for item in r.json():
            if "error" in item:
                raise RuntimeError(f"redis error: {str(item['error'])[:120]}")
            out.append(item["result"])
        return out

    def __repr__(self) -> str:  # never show credentials
        return "UpstashClient(<redacted>)"


def upstash_from_env() -> UpstashClient | None:
    for u, t in (("KV_REST_API_URL", "KV_REST_API_TOKEN"), ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN")):
        if os.environ.get(u) and os.environ.get(t):
            return UpstashClient(os.environ[u], os.environ[t])
    return None


class RedisStore:
    hosted = True

    def __init__(self, client, prefix: str | None = None, box: SecretBox | None = None):
        self.c = client
        env = os.environ.get("VERCEL_ENV") or "local"
        self.p = prefix if prefix is not None else f"tw:{env}:"
        self.box = box or SecretBox()
        self.nonce_backend = self  # guard.replay uses is_used / consume

    def __repr__(self) -> str:
        return "RedisStore(<redacted>)"

    def _k(self, *parts: str) -> str:
        return self.p + ":".join(parts)

    def _get_json(self, key: str):
        raw = self.c.command("GET", key)
        return json.loads(raw) if raw else None

    def _set_json(self, key: str, obj, ttl: int = TTL_S):
        self.c.command("SET", key, json.dumps(obj, default=str), "EX", ttl)

    # requests
    def new_request(self, r: dict) -> None:
        self.c.pipeline([["SET", self._k("req", r["id"]), json.dumps(r, default=str), "EX", TTL_S],
                         ["SET", self._k("tok", r["token"]), r["id"], "EX", TTL_S]])

    def get_request(self, req_id: str) -> dict | None:
        return self._get_json(self._k("req", req_id))

    def find_by_token(self, token: str) -> dict | None:
        if not token or len(token) < 8 or len(token) > 64 or not all(ch.isalnum() or ch in "-_" for ch in token):
            return None
        rid = self.c.command("GET", self._k("tok", token))
        return self.get_request(rid) if rid else None

    def update_request(self, req_id: str, **fields) -> dict | None:
        r = self.get_request(req_id)
        if r is not None:
            r.update(fields)
            self._set_json(self._k("req", req_id), r)
        return r

    # events
    def append_event(self, ev: dict) -> None:
        rid = ev.get("request_id")
        if not rid:
            return
        k = self._k("ev", rid)
        self.c.pipeline([["RPUSH", k, json.dumps(ev, default=str)], ["EXPIRE", k, TTL_S]])

    def events(self, req_id: str) -> list[dict]:
        return [json.loads(x) for x in (self.c.command("LRANGE", self._k("ev", req_id), 0, -1) or [])]

    def last_event_ts(self, req_id: str) -> float | None:
        raw = self.c.command("LRANGE", self._k("ev", req_id), -1, -1)
        return json.loads(raw[0]).get("ts") if raw else None

    # contracts
    def save_contract(self, c: dict) -> None:
        self._set_json(self._k("contract", c["contract_id"]), c)

    def get_contract(self, cid: str | None) -> dict | None:
        return self._get_json(self._k("contract", cid)) if cid else None

    # sessions
    def load_session(self, req_id: str):
        state = self._get_json(self._k("sess", req_id))
        if not state:
            return None
        from agent.tools import ShopSession
        return ShopSession.from_state(state, self.box.dec)

    def save_session(self, req_id: str, session) -> None:
        self._set_json(self._k("sess", req_id), session.to_state(self.box.enc))

    # legacy pending pointer: not shared between visitors online
    def set_pending(self, p: dict) -> None:
        pass

    def get_pending(self) -> dict | None:
        return None

    def clear_pending(self, cart_id: str) -> None:
        pass

    # orders
    def add_order(self, o: dict) -> None:
        k = self._k("orders")
        self.c.pipeline([["RPUSH", k, json.dumps(o, default=str)], ["LTRIM", k, -200, -1], ["EXPIRE", k, TTL_S]])

    def has_order(self, cart_id, req_id) -> bool:
        for raw in self.c.command("LRANGE", self._k("orders"), 0, -1) or []:
            o = json.loads(raw)
            if o.get("cart_id") == cart_id and o.get("request_id") == req_id:
                return True
        return False

    # run lock / cancel
    def acquire_run(self, req_id: str) -> bool:
        return self.c.command("SET", self._k("lock", req_id), str(time.time()), "NX", "EX", 300) == "OK"

    def run_started(self, req_id: str) -> float | None:
        v = self.c.command("GET", self._k("lock", req_id))
        return float(v) if v else None

    def mark_cancelled(self, req_id: str) -> None:
        self.c.command("SET", self._k("cancel", req_id), "1", "EX", TTL_S)

    def is_cancelled(self, req_id: str) -> bool:
        return self.c.command("GET", self._k("cancel", req_id)) is not None

    # limits
    def rate_hit(self, ip: str, per_ip: int, global_limit: int) -> bool:
        minute = str(int(time.time() // 60))
        ki, kg = self._k("rl", "ip", ip, minute), self._k("rl", "all", minute)
        res = self.c.pipeline([["INCR", ki], ["EXPIRE", ki, 70], ["INCR", kg], ["EXPIRE", kg, 70]])
        return res[0] > per_ip or res[2] > global_limit

    def daily_incr(self) -> int:
        k = self._k("day", utc_day())
        res = self.c.pipeline([["INCR", k], ["EXPIRE", k, 2 * 86400]])
        return int(res[0])

    def add_tokens(self, n: int) -> None:
        if n > 0:
            k = self._k("tokens", utc_day())
            self.c.pipeline([["INCRBY", k, n], ["EXPIRE", k, 2 * 86400]])

    def today_tokens(self) -> int:
        return int(self.c.command("GET", self._k("tokens", utc_day())) or 0)

    # nonces (guard.replay backend): atomic set-if-absent
    def is_used(self, nonce: str) -> bool:
        return self.c.command("GET", self._k("nonce", nonce)) is not None

    def consume(self, nonce: str) -> bool:
        """True if this call recorded the nonce, False if it was already used."""
        return self.c.command("SET", self._k("nonce", nonce), "1", "NX", "EX", NONCE_TTL_S) == "OK"


def make_store(state: dict, sessions: dict, snapshots: dict, persist):
    """Hosted (VERCEL set) requires Redis; laptop mode uses local files unless Redis env vars are present and
    TRIPWIRE_STORE=redis is set."""
    hosted = bool(os.environ.get("VERCEL"))
    want_redis = hosted or os.environ.get("TRIPWIRE_STORE") == "redis"
    if want_redis:
        client = upstash_from_env()
        if client is None:
            if hosted:
                raise RuntimeError("Upstash Redis env vars are missing (KV_REST_API_URL/TOKEN or UPSTASH_REDIS_REST_URL/TOKEN)")
        else:
            return RedisStore(client)
    return LocalStore(state, sessions, snapshots, persist)
