"""Canary tokens planted in the agent's context. If one comes back in a tool call, the agent is hijacked."""

from __future__ import annotations

import secrets
from typing import Any

_issued: set[str] = set()
_current: str | None = None


def reset() -> None:
    global _current
    _issued.clear()
    _current = None


def current() -> str | None:
    return _current


def generate(prefix: str = "TW-CANARY-") -> str:
    """Create a random token, register it, and return it to plant in the system prompt."""
    global _current
    token = prefix + secrets.token_urlsafe(24)
    _issued.add(token)
    _current = token
    return token


def canary_token() -> str:
    """Return the current canary, generating one if needed. Akaash plants this in agent/run.py INSTRUCTIONS."""
    return _current or generate()


def scan(obj: Any, token: str | None = None) -> bool:
    """Return True if a canary token appears anywhere in obj (recursive). Use on tool-call args."""
    tokens = [token] if token else list(_issued)
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    return _walk(obj, tokens)


scan_tool_args = scan


def _walk(obj: Any, tokens: list[str]) -> bool:
    if obj is None:
        return False
    if isinstance(obj, str):
        return any(t in obj for t in tokens)
    if isinstance(obj, (bytes, bytearray)):
        try:
            text = bytes(obj).decode("utf-8")
        except UnicodeDecodeError:
            text = bytes(obj).decode("utf-8", "replace")
        return any(t in text for t in tokens)
    if isinstance(obj, dict):
        return any(_walk(k, tokens) or _walk(v, tokens) for k, v in obj.items())
    if isinstance(obj, (list, tuple, set)):
        return any(_walk(item, tokens) for item in obj)
    if isinstance(obj, (int, float, bool)):
        return False
    return _walk(str(obj), tokens)
