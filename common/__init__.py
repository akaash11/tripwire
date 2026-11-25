"""Shared helpers: env loading, usage logging, Ed25519 signing."""
from __future__ import annotations

import json
import os
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOSTED = bool(os.environ.get("VERCEL"))  # serverless: read-only disk, state lives in Redis
LOGS = ROOT / "logs"
if not HOSTED:
    LOGS.mkdir(exist_ok=True)

# Hosted mode: server.store registers a callback here to count tokens in Redis (no files on disk).
usage_hook = None


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def usage_from(interaction) -> dict:
    """Best-effort token counts from an Interactions API response."""
    u = getattr(interaction, "usage", None)
    if u is None:
        return {"input_tokens": None, "output_tokens": None}
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    return {
        "input_tokens": d.get("total_input_tokens") or d.get("input_tokens"),
        "output_tokens": d.get("total_output_tokens") or d.get("output_tokens"),
    }


def log_usage(model: str, job: str, latency_ms: int, interaction=None, **extra) -> None:
    row = {"ts": time.time(), "model": model, "job": job, "latency_ms": latency_ms,
           **(usage_from(interaction) if interaction is not None else {}), **extra}
    if HOSTED:
        if usage_hook is not None:
            try:
                usage_hook(int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0))
            except Exception:  # noqa: BLE001  (accounting must never break a request)
                pass
        return
    with (LOGS / "usage.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
