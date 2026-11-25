"""Nonce / expiry / cart-hash helpers on top of common.signing. No second keypair.

cart_hash must match agent/tools.py ShopSession.add_to_cart exactly:
sha256(json.dumps({payee, lines, total_cents, currency}, sort_keys=True).encode())
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT
from common.signing import verify

_memory_nonces: set[str] = set()
_nonce_path: Path | None = None
_loaded_path: Path | None = None
_bound: dict[str, dict] = {}
_nonce_backend = None  # hosted mode: object with is_used(nonce) and consume(nonce); None = data/used_nonces.json


def set_nonce_backend(backend) -> None:
    """Hosted mode (Redis): nonces use an atomic set-if-absent instead of the local file."""
    global _nonce_backend
    _nonce_backend = backend


def export_bound(contract_id: str) -> dict:
    """Bound cart fingerprints for one contract, so they survive between serverless invocations."""
    prefix = f"{contract_id}:"
    return {k: v for k, v in _bound.items() if k.startswith(prefix)}


def import_bound(records: dict | None) -> None:
    for k, v in (records or {}).items():
        _bound.setdefault(k, v)


def nonce_path() -> Path:
    return _nonce_path if _nonce_path is not None else (ROOT / "data" / "used_nonces.json")


def configure(*, nonce_path: Path | str | None = None, reset_nonce_store: bool = False) -> None:
    global _nonce_path
    if nonce_path is not None:
        _nonce_path = Path(nonce_path)
    if reset_nonce_store:
        reset()


def reset() -> None:
    global _memory_nonces, _loaded_path, _bound
    _memory_nonces = set()
    _loaded_path = None
    _bound = {}


def hash_cart(cart: dict) -> str:
    """Recompute the cart hash. Identical to agent/tools.py add_to_cart."""
    return hashlib.sha256(json.dumps(
        {k: cart[k] for k in ("payee", "lines", "total_cents", "currency")}, sort_keys=True
    ).encode()).hexdigest()


def intent_hash(contract: dict) -> str:
    """SHA-256 of the canonical signed contract, including the signature field."""
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_iso(ts: str) -> datetime:
    raw = (ts or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_nonces() -> set[str]:
    global _memory_nonces, _loaded_path
    path = nonce_path()
    if _loaded_path != path:
        _memory_nonces = set()
        _loaded_path = path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    _memory_nonces = {str(x) for x in data}
            except (OSError, json.JSONDecodeError, TypeError):
                _memory_nonces = set()
    return _memory_nonces


def _persist_nonces() -> None:
    path = nonce_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(_load_nonces())), encoding="utf-8")


def consume_nonce(nonce: str | None) -> None:
    """Record a nonce only after an approved receipt is issued."""
    if not nonce:
        return
    if _nonce_backend is not None:
        _nonce_backend.consume(str(nonce))
        return
    store = _load_nonces()
    token = str(nonce)
    if token not in store:
        store.add(token)
        _persist_nonces()


def verify_contract(contract: dict, *, now: datetime | None = None) -> tuple[bool, str]:
    """Validate signature, expiry, and that the nonce is not already used for an approved receipt.

    Does not record the nonce. Call consume_nonce() only after issuing a receipt.
    """
    if not isinstance(contract, dict) or not contract:
        return False, "contract: missing"
    if not verify(contract):
        return False, "contract: invalid Ed25519 signature"

    expires_at = contract.get("expires_at")
    if expires_at:
        try:
            expiry = parse_iso(str(expires_at))
        except (ValueError, TypeError):
            return False, "contract: expires_at is not a valid ISO-8601 timestamp"
        if (now or utc_now()) >= expiry:
            return False, "contract: expired"
    else:
        return False, "contract: missing expires_at"

    nonce = contract.get("nonce")
    if not nonce:
        return False, "contract: missing nonce"
    nonce = str(nonce)
    if (_nonce_backend.is_used(nonce) if _nonce_backend is not None else nonce in _load_nonces()):
        return False, "contract: nonce already used (replay)"
    return True, ""


def _bind_key(contract: dict, cart: dict) -> str:
    return f"{contract.get('contract_id')}:{cart.get('cart_id')}"


def bind_cart_hash(contract: dict, cart: dict) -> str:
    """Record the final cart fingerprint (and payee) for this contract+cart. Call when the cart is final."""
    if not isinstance(contract, dict) or not isinstance(cart, dict):
        raise ValueError("bind_cart_hash requires a contract and a cart")
    key = _bind_key(contract, cart)
    digest = hash_cart(cart)
    payee = cart.get("payee")
    _bound[key] = {
        "cart_hash": digest,
        "payee": dict(payee) if isinstance(payee, dict) else payee,
    }
    return digest


def bound_record(contract: dict, cart: dict) -> dict | None:
    return _bound.get(_bind_key(contract, cart))


def verify_bound_cart(contract: dict, cart: dict) -> tuple[bool, str]:
    """If a cart was bound, require the current cart (and payee) to match. Unbound carts are skipped."""
    rec = bound_record(contract, cart)
    if rec is None:
        return True, ""
    try:
        actual = hash_cart(cart)
    except KeyError as exc:
        return False, f"cart_hash: cart is missing {exc}"
    if actual != rec.get("cart_hash"):
        return False, "cart_hash: cart changed after it was finalized"
    expected_payee = rec.get("payee") or {}
    got = cart.get("payee") or {}
    if isinstance(expected_payee, dict) and isinstance(got, dict):
        exp_domain = expected_payee.get("shop_domain")
        got_domain = got.get("shop_domain")
        if exp_domain and got_domain and exp_domain != got_domain:
            return False, f"payee: shop_domain changed from {exp_domain} to {got_domain} after the cart was finalized"
    return True, ""

