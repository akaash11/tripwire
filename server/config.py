"""Server configuration, safe .env management, usage aggregation, and key testing.

Secrets (API keys) are written only to .env (chmod 600) and os.environ. They are NEVER returned
to any caller, logged, or written to metadata files.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import tempfile
import threading
import time

import httpx

from common import LOGS, ROOT, load_env, log_usage

ENV_PATH = ROOT / ".env"
KEY_META_PATH = LOGS / "key_meta.json"
USAGE_PATH = LOGS / "usage.jsonl"
EVALS_RESULTS_PATH = ROOT / "evals" / "out" / "results.json"

_ENV_LOCK = threading.Lock()

ALLOWED_MODELS = [
    "gemini-4-neon",
    "gemini-4-carbon",
    "gemini-4-hydrogen",
    "gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]

ALLOWED_THINKING = ["low", "medium", "high", "xhigh"]

MODEL_DEFAULTS = {
    "CONTRACT_MODEL": "gemini-4-neon",
    "CONTRACT_FALLBACK_MODEL": "gemini-3.5-flash-lite",
    "CONTRACT_THINKING": "low",
    "AGENT_MODEL": "gemini-3.5-flash-lite",
    "CHECKER_MODEL": "gemini-4-neon",
    "ATTACK_MODEL": "gemini-4-carbon",
}

MANAGED_KEYS = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY")

# USD per 1,000,000 tokens: (input_per_1m, output_per_1m)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gemini-4-neon": (3.00, 15.00),
    "gemini-4-carbon": (2.00, 10.00),
    "gemini-4-hydrogen": (1.50, 7.50),
    "gemini-4-argon": (2.00, 10.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-decision-1": (0.10, 0.00),
    "shop-mcp": (0.00, 0.00),
}


def _sanitize_env_value(val: str) -> str:
    # Strip CR/LF to prevent line injection in .env.
    return val.replace("\r", "").replace("\n", "").strip()


def write_env_vars(updates: dict[str, str | None]) -> None:
    """Update or remove keys in .env (chmod 600, atomic replace) and sync os.environ."""
    with _ENV_LOCK:
        lines: list[str] = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
        remaining = dict(updates)
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "=" in line and not stripped.startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in remaining:
                    val = remaining.pop(k)
                    if val is not None:
                        clean = _sanitize_env_value(val)
                        new_lines.append(f"{k}={clean}")
                    # If val is None, drop the line.
                    continue
            new_lines.append(line)

        for k, val in remaining.items():
            if val is not None:
                clean = _sanitize_env_value(val)
                new_lines.append(f"{k}={clean}")

        content = "\n".join(new_lines) + ("\n" if new_lines else "")

        # Write atomically with 0600 permissions so secrets are never world-readable.
        fd, tmp_name = tempfile.mkstemp(prefix=".env.tmp.", dir=str(ROOT))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, ENV_PATH)
            os.chmod(ENV_PATH, 0o600)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        for k, val in updates.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = _sanitize_env_value(val)


def _read_key_meta() -> dict[str, float]:
    if not KEY_META_PATH.exists():
        return {}
    try:
        data = json.loads(KEY_META_PATH.read_text())
        if isinstance(data, dict):
            return {k: float(v) for k, v in data.items() if k in MANAGED_KEYS and isinstance(v, (int, float))}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _record_key_meta(name: str) -> float:
    meta = _read_key_meta()
    now = time.time()
    meta[name] = now
    KEY_META_PATH.write_text(json.dumps(meta, indent=2) + "\n")
    return now


def get_key_status() -> dict[str, dict]:
    """Return ONLY set/not-set and last-updated timestamp for managed API keys. Never returns values."""
    load_env()
    meta = _read_key_meta()
    env_mtime = ENV_PATH.stat().st_mtime if ENV_PATH.exists() else None
    out = {}
    for name in MANAGED_KEYS:
        is_set = bool((os.environ.get(name) or "").strip())
        updated_at = meta.get(name)
        if updated_at is None and is_set and env_mtime is not None:
            updated_at = env_mtime
        out[name] = {"is_set": is_set, "updated_at": updated_at}
    return out


def set_api_key(name: str, value: str) -> dict:
    if name not in MANAGED_KEYS:
        raise ValueError(f"Unsupported key: {name}")
    clean = _sanitize_env_value(value)
    if not clean:
        raise ValueError("Key value cannot be empty")
    write_env_vars({name: clean})
    ts = _record_key_meta(name)
    return {"name": name, "is_set": True, "updated_at": ts}


def remove_api_key(name: str) -> dict:
    if name not in MANAGED_KEYS:
        raise ValueError(f"Unsupported key: {name}")
    write_env_vars({name: None})
    ts = _record_key_meta(name)
    return {"name": name, "is_set": False, "updated_at": ts}


def _scrub(text: str, secret: str) -> str:
    if secret and len(secret) >= 4:
        text = text.replace(secret, "[REDACTED]")
    return text[:260]


def test_api_key(name: str) -> dict:
    """Make one cheap API check for the requested key. Never logs or returns the key."""
    load_env()
    if name not in MANAGED_KEYS:
        raise ValueError(f"Unsupported key: {name}")
    secret = (os.environ.get(name) or "").strip()
    if not secret:
        return {"ok": False, "latency_ms": 0, "error": f"{name} is not set"}

    t0 = time.time()
    try:
        if name == "GEMINI_API_KEY":
            from google import genai

            client = genai.Client(api_key=secret, http_options={"api_version": "v1beta"})
            resp = client.decisions.create(
                model="gemini-decision-1",
                input="100W USB-C braided charging cable",
                questions={"ping": {"type": "noul", "instructions": "Is this a USB-C cable?"}},
            )
            ms = int((time.time() - t0) * 1000)
            log_usage("gemini-decision-1", "key_test", ms, None, n_questions=1)
            ans = resp.answers.get("ping")
            noul = getattr(ans, "noul", None) if ans is not None else None
            detail = f"Decisions API OK (noul={noul:.2f})" if isinstance(noul, (int, float)) else "Decisions API OK"
            return {"ok": True, "latency_ms": ms, "detail": detail}

        if name == "ANTHROPIC_API_KEY":
            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": secret, "anthropic-version": "2023-06-01"},
                timeout=15,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                return {"ok": True, "latency_ms": ms, "detail": "Anthropic API OK"}
            return {"ok": False, "latency_ms": ms, "error": f"HTTP {r.status_code}"}

        if name == "XAI_API_KEY":
            r = httpx.get(
                "https://api.x.ai/v1/models",
                headers={"Authorization": f"Bearer {secret}"},
                timeout=15,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                return {"ok": True, "latency_ms": ms, "detail": "xAI API OK"}
            return {"ok": False, "latency_ms": ms, "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        if name == "GEMINI_API_KEY":
            log_usage("gemini-decision-1", "key_test", ms, None, error=type(e).__name__)
        return {"ok": False, "latency_ms": ms, "error": _scrub(f"{type(e).__name__}: {e}", secret)}

    return {"ok": False, "latency_ms": 0, "error": "Unknown key"}


def get_model_config() -> dict:
    load_env()
    current = {k: os.environ.get(k, default) for k, default in MODEL_DEFAULTS.items()}
    return {
        "models": current,
        "allowed_models": ALLOWED_MODELS,
        "allowed_thinking": ALLOWED_THINKING,
        "pinned": {"DECISIONS_MODEL": "gemini-decision-1"},
    }


def update_model_config(payload: dict) -> dict:
    updates: dict[str, str] = {}
    for k in MODEL_DEFAULTS:
        if k not in payload:
            continue
        val = str(payload[k]).strip()
        if k == "CONTRACT_THINKING":
            if val not in ALLOWED_THINKING:
                raise ValueError(f"Invalid thinking level: {val}")
        else:
            if val not in ALLOWED_MODELS:
                raise ValueError(f"Model not allowed for {k}: {val}")
        updates[k] = val
    if updates:
        write_env_vars(updates)
    return get_model_config()


def get_runtime_settings() -> dict:
    load_env()
    try:
        rpm = int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
    except ValueError:
        rpm = 10
    try:
        budget = int(os.environ.get("DAILY_TOKEN_BUDGET", "0"))
    except ValueError:
        budget = 0
    return {"rate_limit_per_min": max(1, rpm), "daily_token_budget": max(0, budget)}


def update_runtime_settings(rate_limit_per_min: int | None = None, daily_token_budget: int | None = None) -> dict:
    updates: dict[str, str] = {}
    if rate_limit_per_min is not None:
        rpm = int(rate_limit_per_min)
        if rpm < 1 or rpm > 1000:
            raise ValueError("rate_limit_per_min must be between 1 and 1000")
        updates["RATE_LIMIT_PER_MIN"] = str(rpm)
    if daily_token_budget is not None:
        budget = int(daily_token_budget)
        if budget < 0:
            raise ValueError("daily_token_budget must be >= 0 (0 = unlimited)")
        updates["DAILY_TOKEN_BUDGET"] = str(budget)
    if updates:
        write_env_vars(updates)
    return get_runtime_settings()


def _percentile(values: list[float], p: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(len(s) * p) - 1))
    return int(round(s[idx]))


def _estimate_cost(model: str, in_tok: int, out_tok: int) -> float | None:
    rates = MODEL_PRICING.get(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return (in_tok * in_rate + out_tok * out_rate) / 1_000_000.0


def read_usage_rows() -> list[dict]:
    if not USAGE_PATH.exists():
        return []
    rows: list[dict] = []
    for line in USAGE_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def today_tokens() -> int:
    today = dt.datetime.now().date()
    total = 0
    for r in read_usage_rows():
        ts = r.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if dt.datetime.fromtimestamp(ts).date() != today:
            continue
        total += int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
    return total


def aggregate_usage(recent_limit: int = 40) -> dict:
    rows = read_usage_rows()
    by_model: dict[str, dict] = {}
    by_job: dict[str, dict] = {}
    all_latencies: list[float] = []
    total_in = 0
    total_out = 0
    total_errors = 0
    total_cost = 0.0
    unknown_cost_models: set[str] = set()
    today = dt.datetime.now().date()
    daily_tokens = 0
    daily_calls = 0

    def _bucket(d: dict, key: str) -> dict:
        if key not in d:
            d[key] = {
                "calls": 0,
                "errors": 0,
                "latencies": [],
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "cost_known": True,
            }
        return d[key]

    for r in rows:
        model = str(r.get("model") or "unknown")
        job = str(r.get("job") or "unknown")
        lat = r.get("latency_ms")
        in_tok = int(r.get("input_tokens") or 0)
        out_tok = int(r.get("output_tokens") or 0)
        err = bool(r.get("error"))
        ts = r.get("ts")

        if isinstance(ts, (int, float)) and dt.datetime.fromtimestamp(ts).date() == today:
            daily_tokens += in_tok + out_tok
            daily_calls += 1

        cost = r.get("est_cost_usd")
        if not isinstance(cost, (int, float)):
            cost = _estimate_cost(model, in_tok, out_tok)

        total_in += in_tok
        total_out += out_tok
        if err:
            total_errors += 1
        if isinstance(lat, (int, float)):
            all_latencies.append(float(lat))
        if cost is not None:
            total_cost += float(cost)
        elif in_tok or out_tok:
            unknown_cost_models.add(model)

        for target, key in ((by_model, model), (by_job, job)):
            b = _bucket(target, key)
            b["calls"] += 1
            if err:
                b["errors"] += 1
            if isinstance(lat, (int, float)):
                b["latencies"].append(float(lat))
            b["input_tokens"] += in_tok
            b["output_tokens"] += out_tok
            if cost is not None:
                b["cost_usd"] += float(cost)
            elif in_tok or out_tok:
                b["cost_known"] = False

    def _finalize(buckets: dict[str, dict], include_rates: bool = False) -> list[dict]:
        out = []
        for k, b in sorted(buckets.items(), key=lambda kv: (-kv[1]["calls"], kv[0])):
            entry = {
                "name": k,
                "calls": b["calls"],
                "errors": b["errors"],
                "p50_ms": _percentile(b["latencies"], 0.50),
                "p95_ms": _percentile(b["latencies"], 0.95),
                "input_tokens": b["input_tokens"],
                "output_tokens": b["output_tokens"],
                "est_cost_usd": round(b["cost_usd"], 6) if b["cost_known"] else None,
            }
            if include_rates:
                rates = MODEL_PRICING.get(k)
                entry["rate_per_1m"] = {"input": rates[0], "output": rates[1]} if rates else None
            out.append(entry)
        return out

    recent = []
    for r in reversed(rows[-recent_limit:]):
        model = str(r.get("model") or "unknown")
        in_tok = r.get("input_tokens")
        out_tok = r.get("output_tokens")
        cost = _estimate_cost(model, int(in_tok or 0), int(out_tok or 0))
        recent.append({
            "ts": r.get("ts"),
            "model": model,
            "job": r.get("job"),
            "latency_ms": r.get("latency_ms"),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "error": r.get("error"),
            "n_questions": r.get("n_questions"),
            "est_cost_usd": round(cost, 6) if cost is not None else None,
        })

    settings = get_runtime_settings()
    return {
        "totals": {
            "calls": len(rows),
            "errors": total_errors,
            "p50_ms": _percentile(all_latencies, 0.50),
            "p95_ms": _percentile(all_latencies, 0.95),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "est_cost_usd": round(total_cost, 6),
            "unknown_cost_models": sorted(unknown_cost_models),
            "today_tokens": daily_tokens,
            "today_calls": daily_calls,
        },
        "by_model": _finalize(by_model, include_rates=True),
        "by_job": _finalize(by_job, include_rates=False),
        "recent": recent,
        "pricing": {k: {"input": v[0], "output": v[1]} for k, v in MODEL_PRICING.items()},
        "settings": settings,
    }


def _find_redteam_runner():
    """Check if Tanu's evals package exposes a callable runner."""
    for mod_name in ("evals.run", "evals.harness", "evals"):
        try:
            mod = __import__(mod_name, fromlist=["*"])
        except Exception:  # noqa: BLE001
            continue
        for fn_name in ("run_evals", "run_all", "run_harness", "run"):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                return fn
    return None


def get_redteam_results() -> dict:
    runner_available = _find_redteam_runner() is not None
    if not EVALS_RESULTS_PATH.exists():
        return {"available": False, "runner_available": runner_available, "results": None}
    try:
        raw = json.loads(EVALS_RESULTS_PATH.read_text())
    except Exception as e:  # noqa: BLE001
        return {"available": False, "runner_available": runner_available, "error": f"Failed to parse results.json: {e}"}

    cases = raw.get("cases") or []
    arms_raw = raw.get("arms") or {}
    # Normalize both the 3-arm schema (arms.no_guard / rules_only / tripwire) and legacy off / on / legit schema.
    legit_raw = raw.get("legit") or {}
    n_legit = raw.get("n_legit") if raw.get("n_legit") is not None else legit_raw.get("n", 0)

    def _norm_arm(arm_key: str, legacy_key: str | None = None) -> dict | None:
        a = arms_raw.get(arm_key)
        if isinstance(a, dict):
            return {
                "attack_success": a.get("attack_success", 0),
                "attack_rate": a.get("attack_rate", a.get("rate", 0.0)),
                "false_positives": a.get("false_positives", 0),
                "fp_rate": a.get("fp_rate", 0.0),
            }
        if legacy_key and isinstance(raw.get(legacy_key), dict):
            la = raw[legacy_key]
            fp = legit_raw.get("false_positives", 0) if arm_key == "tripwire" else None
            fp_rate = (fp / n_legit) if (fp is not None and n_legit) else (0.0 if fp is not None else None)
            return {
                "attack_success": la.get("attack_success", 0),
                "attack_rate": la.get("attack_rate", la.get("rate", 0.0)),
                "false_positives": fp,
                "fp_rate": fp_rate,
            }
        return None

    arms = {
        "no_guard": _norm_arm("no_guard", "off"),
        "rules_only": _norm_arm("rules_only", "rules_only"),
        "tripwire": _norm_arm("tripwire", "on"),
    }

    failures = raw.get("failures")
    if failures is None:
        failures = []
        for c in cases:
            tw_res = str(c.get("tripwire") or c.get("on_result") or "").lower()
            kind = str(c.get("kind") or "").lower()
            atype = str(c.get("attack_type") or "").lower()
            is_legit = kind == "legit" or atype in ("legit", "benign", "none") or c.get("legit") is True
            if is_legit and tw_res in ("block", "blocked", "false_positive", "fp"):
                failures.append({"id": c.get("id", ""), "arm": "tripwire",
                                 "what_happened": f"Legit purchase blocked ({tw_res})",
                                 "why": c.get("attack_type") or "false positive"})
            elif not is_legit and tw_res in ("approve", "approved", "success", "bypassed", "attack_success"):
                failures.append({"id": c.get("id", ""), "arm": "tripwire",
                                 "what_happened": f"Attack succeeded ({tw_res})",
                                 "why": c.get("attack_type") or "bypass"})

    return {
        "available": True,
        "runner_available": runner_available,
        "updated_at": EVALS_RESULTS_PATH.stat().st_mtime,
        "results": {
            "run_id": raw.get("run_id", ""),
            "generator_model": raw.get("generator_model", "gemini-4-carbon"),
            "n_attacks": raw.get("n_attacks", 0),
            "n_legit": n_legit,
            "arms": arms,
            "cases": cases,
            "failures": failures,
        },
    }
