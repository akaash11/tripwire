"""Ed25519 signing for Intent Contracts and receipts.

Signature covers canonical JSON (sorted keys, no whitespace) of every field except "signature".
Private key: keys/tripwire_ed25519.pem (gitignored, created on first use).
Public key: published at GET /pubkey so a merchant can verify with it alone.
"""
from __future__ import annotations

import base64
import functools
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import ROOT

KEY_ID = "tripwire-demo-1"
KEY_PATH = ROOT / "keys" / "tripwire_ed25519.pem"


@functools.lru_cache(maxsize=1)
def _env_key(raw: str) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(base64.b64decode(raw), password=None)


def _private() -> Ed25519PrivateKey:
    env = os.environ.get("TRIPWIRE_SIGNING_KEY", "").strip()
    if env:  # hosted: base64 of the PEM, set with `vercel env add`
        return _env_key(env)
    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(exist_ok=True)
        k = Ed25519PrivateKey.generate()
        KEY_PATH.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        KEY_PATH.chmod(0o600)
    return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)


def private_bytes() -> bytes:
    """Raw private key bytes; only used to derive the at-rest encryption key for session secrets."""
    return _private().private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())


def public_pem() -> str:
    return _private().public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def canonical(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(obj: dict) -> dict:
    sig = _private().sign(canonical(obj))
    return {**obj, "signature": {"alg": "Ed25519", "key_id": KEY_ID, "sig": base64.b64encode(sig).decode()}}


def verify(obj: dict, pub_pem: str | None = None) -> bool:
    try:
        pub = (serialization.load_pem_public_key(pub_pem.encode()) if pub_pem
               else _private().public_key())
        assert isinstance(pub, Ed25519PublicKey)
        pub.verify(base64.b64decode(obj["signature"]["sig"]), canonical(obj))
        return True
    except Exception:
        return False
