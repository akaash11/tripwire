"""Shared helpers: env loading, usage logging, Ed25519 signing."""
from __future__ import annotations

import json
import os
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)


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
    with (LOGS / "usage.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
