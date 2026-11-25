"""common.modelcall (lock, retry, timeout, Redis semaphore) and the contract budget floor. No network, no Gemini."""
from __future__ import annotations

import threading
import time

import pytest

from agent import contract as contract_mod
from common import modelcall
from server.store import RedisStore


class Boom(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    rows = []
    monkeypatch.setattr(modelcall, "_client", lambda timeout_s, api_version: object())
    monkeypatch.setattr(modelcall, "log_usage", lambda model, job, ms, it=None, **kw: rows.append((job, kw)))
    monkeypatch.setattr(modelcall, "RETRY_DELAY_S", 0.01)
    monkeypatch.setattr(modelcall, "_store", None)
    monkeypatch.setattr(modelcall, "HOSTED", False)
    modelcall.set_deadline(None)
    return rows


def test_lock_serializes_overlapping_calls():
    running, peak, lock = 0, 0, threading.Lock()

    def fn(_client):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.15)
        with lock:
            running -= 1
        return "ok"

    out = []
    ts = [threading.Thread(target=lambda: out.append(modelcall.call(fn, model="m", job="t"))) for _ in range(3)]
    t0 = time.time()
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == ["ok"] * 3 and peak == 1
    assert time.time() - t0 >= 0.4  # three 0.15 s calls, one at a time


def test_retry_once_on_timeout_then_ok(fast):
    calls = []

    def fn(_client):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("slow")
        return "ok"

    assert modelcall.call(fn, model="m", job="t") == "ok"
    assert len(calls) == 2
    assert [kw.get("error") for _, kw in fast] == ["timeout", None]


def test_429_is_retried_once_and_logged(fast):
    calls = []

    def fn(_client):
        calls.append(1)
        if len(calls) == 1:
            raise Boom(429)
        return "ok"

    assert modelcall.call(fn, model="m", job="t") == "ok"
    assert fast[0][1]["error"] == "429"


def test_retries_grow_and_stop_at_limit(monkeypatch):
    sleeps = []
    monkeypatch.setattr(modelcall.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(modelcall, "RETRY_DELAY_S", 3.0)
    calls = []

    def fn(_client):
        calls.append(1)
        raise Boom(503)

    with pytest.raises(Boom):
        modelcall.call(fn, model="m", job="t")
    assert len(calls) == 4 and modelcall.DEFAULT_RETRIES == 3
    assert [3 <= s < 4.01 for s in sleeps[:1]] == [True] and 6 <= sleeps[1] < 7.01 and 9 <= sleeps[2] < 10.01


def test_one_retry_when_asked():
    calls = []

    def fn(_client):
        calls.append(1)
        raise Boom(503)

    with pytest.raises(Boom):
        modelcall.call(fn, model="m", job="t", retries=1)
    assert len(calls) == 2


def test_non_retryable_error_is_not_retried():
    calls = []

    def fn(_client):
        calls.append(1)
        raise Boom(400)

    with pytest.raises(Boom):
        modelcall.call(fn, model="m", job="t")
    assert len(calls) == 1


def test_watchdog_keeps_lock_until_call_really_ends(monkeypatch):
    monkeypatch.setattr(modelcall, "WATCHDOG_GRACE_S", 0.0)
    release = threading.Event()
    started = []

    def stuck(_client):
        started.append(1)
        release.wait(2)
        return "late"

    with pytest.raises(modelcall.ModelTimeout):
        modelcall.call(stuck, model="m", job="t", timeout_s=0.1, retries=0)
    # the stuck request is still running, so the next caller cannot get the lock until it ends
    with pytest.raises(modelcall.ModelTimeout):
        modelcall.call(lambda c: "x", model="m", job="t", retries=0, lock_wait_s=0.2)
    release.set()
    time.sleep(0.1)
    assert modelcall.call(lambda c: "x", model="m", job="t", retries=0) == "x"


class FakeRedis:
    def __init__(self):
        self.d, self.ttl = {}, {}

    def command(self, *a):
        op = a[0]
        if op == "SET":
            if "NX" in a and a[1] in self.d:
                return None
            self.d[a[1]] = a[2]
            self.ttl[a[1]] = a[a.index("EX") + 1]
            return "OK"
        if op == "GET":
            return self.d.get(a[1])
        if op == "DEL":
            return 1 if self.d.pop(a[1], None) is not None else 0
        raise NotImplementedError(op)


def test_redis_semaphore_path(monkeypatch):
    fake = FakeRedis()
    store = RedisStore(fake, prefix="t:")
    monkeypatch.setattr(modelcall, "HOSTED", True)
    monkeypatch.setattr(modelcall, "POLL_S", 0.05)
    modelcall.set_store(store)
    seen = {}

    def fn(_client):
        seen["held"] = [k for k in fake.d if "mlock" in k]
        seen["ttl"] = [fake.ttl[k] for k in fake.d if "mlock" in k]
        return "ok"

    assert modelcall.call(fn, model="m", job="t", timeout_s=45) == "ok"
    assert len(seen["held"]) == 1 and seen["ttl"][0] > 45  # TTL longer than the call timeout
    assert not any("mlock" in k for k in fake.d)  # released

    # someone else (another instance) holds it: we wait, then fail like a timeout
    fake.d["t:mlock:modelcall"] = "other"
    with pytest.raises(modelcall.ModelTimeout):
        modelcall.call(lambda c: "x", model="m", job="t", retries=0, lock_wait_s=0.2)
    assert fake.d["t:mlock:modelcall"] == "other"  # we never deleted someone else's lock
    # it frees up while we wait: we get it
    threading.Timer(0.15, lambda: fake.d.pop("t:mlock:modelcall")).start()
    assert modelcall.call(lambda c: "y", model="m", job="t", retries=0, lock_wait_s=2) == "y"
    modelcall.set_store(None)


def test_deadline_stops_retry():
    modelcall.set_deadline(time.time() + 5)  # less than MIN_ATTEMPT_S away from the deadline: no retry
    calls = []

    def fn(_client):
        calls.append(1)
        raise TimeoutError("slow")

    with pytest.raises(modelcall.ModelTimeout):
        modelcall.call(fn, model="m", job="t")
    assert len(calls) == 1
    modelcall.set_deadline(None)


def test_budget_floor_raises_unstated_low_budget():
    body = {"budget_stated": False, "max_total_cents": 500, "assumptions": ["No budget given"]}
    contract_mod.apply_budget_floor(body)
    assert body["max_total_cents"] == 1000
    assert len(body["assumptions"]) == 2 and "$10.00" in body["assumptions"][1]


def test_budget_floor_leaves_stated_or_high_budgets():
    stated = {"budget_stated": True, "max_total_cents": 500, "assumptions": []}
    contract_mod.apply_budget_floor(stated)
    assert stated["max_total_cents"] == 500 and stated["assumptions"] == []
    high = {"budget_stated": False, "max_total_cents": 1500, "assumptions": []}
    contract_mod.apply_budget_floor(high)
    assert high["max_total_cents"] == 1500 and high["assumptions"] == []


def test_contract_retry_is_explicit_with_same_model(monkeypatch):
    """CONTRACT_MODEL == CONTRACT_FALLBACK_MODEL: one model, retries=1 (no skipped fallback, no double chain)."""
    import json
    seen = []

    class It:
        output_text = json.dumps({"user_request": "x", "photo_observations": [], "item": "x", "must": ["a"],
                                  "must_not": [], "max_total_cents": 300, "budget_stated": False, "quantity": 1,
                                  "returns": "preferred", "search_query": "x", "summary": "You want x",
                                  "assumptions": []})

    monkeypatch.setenv("CONTRACT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("CONTRACT_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(contract_mod, "_call", lambda m, b, th, to, retries: seen.append((m, retries)) or It())
    c = contract_mod.compile_contract(text="x")
    assert seen == [("gemini-3.5-flash-lite", 3)]
    assert c["max_total_cents"] == 1000 and c["budget_stated"] is False

    seen.clear()
    monkeypatch.setenv("CONTRACT_MODEL", "gemini-4-neon")
    contract_mod.compile_contract(text="x")
    assert seen == [("gemini-4-neon", 0)]  # primary answered, fallback untouched


def test_contract_falls_back_when_neon_primary_times_out(monkeypatch):
    import json
    seen = []

    class It:
        output_text = json.dumps({"user_request": "x", "photo_observations": [], "item": "x", "must": ["a"],
                                  "must_not": [], "max_total_cents": 2000, "budget_stated": True, "quantity": 1,
                                  "returns": "preferred", "search_query": "x", "summary": "You want x",
                                  "assumptions": []})

    def fake(model, blocks, thinking, timeout, retries):
        seen.append((model, retries))
        if model == "gemini-4-neon":
            raise modelcall.ModelTimeout("slow")
        return It()

    monkeypatch.setenv("CONTRACT_MODEL", "gemini-4-neon")
    monkeypatch.setenv("CONTRACT_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(contract_mod, "_call", fake)
    c = contract_mod.compile_contract(text="x")
    assert seen == [("gemini-4-neon", 0), ("gemini-3.5-flash-lite", 3)]
    assert c["compiled_by"] == "gemini-3.5-flash-lite"
