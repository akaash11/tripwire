"""One door for every Gemini call on our key (contract, checker, agent turns, Decisions).

Why: on the owner's key, several calls in flight at once make some of them hang for minutes (measured 2026-11-25:
14 of 44 Flash-Lite calls hung for 120 s with 3 at a time; the same requests one at a time all answered, the slowest
around 34 s). So this helper:

- runs ONE call at a time: a process-wide lock (laptop), plus a Redis semaphore when hosted (Vercel can run several
  instances), taken through the store injected with set_store();
- puts a real HTTP timeout on the request (genai HttpOptions.timeout, ms) and switches the SDK's own retries off
  (for the Interactions and Decisions clients the SDK default is 5 attempts with backoff, which hid 429s and made
  one call last 30+ s). A watchdog thread is only a backstop: the lock is released by the worker thread when the
  underlying call truly ends, never when the watchdog gives up;
- retries exactly once on a timeout, 429 or 5xx, and logs every attempt with log_usage (a 429 shows as error="429").

common must not import server: the Redis store is injected (server/app.py calls set_store when hosted).
"""
from __future__ import annotations

import os
import random
import threading
import time
import uuid
from typing import Any, Callable

from common import HOSTED, log_usage

CALL_TIMEOUT_S = float(os.environ.get("MODEL_CALL_TIMEOUT_S", "45"))
LOCK_WAIT_S = float(os.environ.get("MODEL_LOCK_WAIT_S", "40"))
RETRY_DELAY_S = float(os.environ.get("MODEL_RETRY_DELAY_S", "3"))  # retry n waits n * this (3, 6, 9 s) plus jitter
DEFAULT_RETRIES = int(os.environ.get("MODEL_RETRIES", "3"))  # Google 503 "high demand" spikes outlast one retry
WATCHDOG_GRACE_S = 3.0  # the HTTP timeout should fire first; this only bounds the caller if it does not
LOCK_NAME = "modelcall"
POLL_S = 1.0

_LOCAL = threading.Lock()
_TL = threading.local()  # per-thread run deadline (set by agent.run for a hosted run)
MIN_ATTEMPT_S = 10.0  # near a deadline an attempt still gets this long, never less
_store: Any = None  # object with lock_acquire(name, token, ttl_s) -> bool and lock_release(name, token)


def set_store(store) -> None:
    """Hosted: register the Redis-backed store used for the cross-instance semaphore."""
    global _store
    _store = store


def set_deadline(deadline: float | None) -> None:
    """Default deadline (epoch s) for every call made from this thread, including the checker and Decisions calls
    the agent's tools make. Pass None to clear."""
    _TL.deadline = deadline


class ModelTimeout(TimeoutError):
    """No answer in time (HTTP timeout, or could not get the key's lock). Subclasses TimeoutError on purpose."""


def _status_of(exc: BaseException) -> int | None:
    for obj in (exc, getattr(exc, "response", None)):
        for attr in ("status_code", "code", "status"):
            v = getattr(obj, attr, None)
            if isinstance(v, int) and 100 <= v < 600:
                return v
    return None


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    names = " ".join(c.__name__.lower() for c in type(exc).__mro__)
    return "timeout" in names or "timedout" in names


def classify(exc: BaseException) -> tuple[str, bool]:
    """(error label for log_usage, retryable)."""
    status = _status_of(exc)
    if status == 429:
        return "429", True
    if status is not None and status >= 500:
        return str(status), True
    if _is_timeout(exc):
        return "timeout", True
    return type(exc).__name__, False


def _client(timeout_s: float, api_version: str | None):
    from google import genai
    from google.genai import types

    opts: dict = {"timeout": int(timeout_s * 1000), "retry_options": types.HttpRetryOptions(attempts=1)}
    if api_version:
        opts["api_version"] = api_version
    return genai.Client(http_options=opts)


def _acquire(wait_until: float, ttl_s: int) -> tuple[bool, str | None]:
    """Take the local lock, then (hosted with a store) the Redis semaphore. Returns (ok, redis_token)."""
    if not _LOCAL.acquire(timeout=max(0.0, wait_until - time.time())):
        return False, None
    if HOSTED and _store is not None:
        token = uuid.uuid4().hex
        while True:
            try:
                if _store.lock_acquire(LOCK_NAME, token, ttl_s):
                    return True, token
            except Exception:  # noqa: BLE001  (Redis hiccup: keep trying until the wait bound)
                pass
            if time.time() + POLL_S > wait_until:
                _LOCAL.release()
                return False, None
            time.sleep(POLL_S * (0.7 + 0.6 * random.random()))
    return True, None


def _release(token: str | None) -> None:
    try:
        if token is not None and _store is not None:
            try:
                _store.lock_release(LOCK_NAME, token)
            except Exception:  # noqa: BLE001  (the TTL frees it anyway)
                pass
    finally:
        _LOCAL.release()


def _attempt(fn: Callable[[Any], Any], timeout_s: float, api_version: str | None, wait_until: float):
    ok, token = _acquire(wait_until, int(timeout_s + WATCHDOG_GRACE_S + 5))
    if not ok:
        raise ModelTimeout("could not get the model lock in time")
    box: dict = {}

    def work() -> None:
        try:
            box["result"] = fn(_client(timeout_s, api_version))
        except BaseException as e:  # noqa: BLE001
            box["error"] = e
        finally:
            _release(token)  # only now, when the underlying call has really ended

    t = threading.Thread(target=work, name="modelcall", daemon=True)
    t.start()
    t.join(timeout_s + WATCHDOG_GRACE_S)
    if t.is_alive():
        raise ModelTimeout(f"no reply in {timeout_s:g}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def call(fn: Callable[[Any], Any], *, model: str, job: str, timeout_s: float | None = None, retries: int | None = None,
         deadline: float | None = None, api_version: str | None = None, lock_wait_s: float | None = None,
         **log_extra):
    """Run fn(client) under the key's lock with a real timeout and at most `retries` retries (timeout/429/5xx).

    deadline: epoch seconds. The lock wait never goes past it and no retry starts after it.
    Raises ModelTimeout (a TimeoutError) on no answer, or the SDK's own exception for other failures.
    """
    timeout_s = CALL_TIMEOUT_S if timeout_s is None else timeout_s
    retries = DEFAULT_RETRIES if retries is None else retries
    if deadline is None:
        deadline = getattr(_TL, "deadline", None)
    wait_s = LOCK_WAIT_S if lock_wait_s is None else lock_wait_s
    last: BaseException | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        wait_until = t0 + wait_s
        if deadline is not None:
            wait_until = min(wait_until, deadline)
        t_s = timeout_s if deadline is None else min(timeout_s, max(MIN_ATTEMPT_S, deadline - t0))
        try:
            result = _attempt(fn, t_s, api_version, wait_until)
        except BaseException as e:  # noqa: BLE001
            label, retryable = classify(e)
            log_usage(model, job, int((time.time() - t0) * 1000), None, error=label, attempt=attempt + 1, **log_extra)
            last = e
            delay = RETRY_DELAY_S * (attempt + 1) + random.uniform(0, RETRY_DELAY_S / 3)
            if not retryable or attempt >= retries or (
                    deadline is not None and time.time() + delay + MIN_ATTEMPT_S >= deadline):
                if _is_timeout(e) and not isinstance(e, ModelTimeout):
                    raise ModelTimeout(f"no reply in {timeout_s:g}s") from e
                raise
            time.sleep(delay)
            continue
        log_usage(model, job, int((time.time() - t0) * 1000), result, attempt=attempt + 1, **log_extra)
        return result
    raise last  # pragma: no cover
