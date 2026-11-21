"""Unit tests for the guard pipeline. No network."""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import signing  # noqa: E402
from common.signing import sign, verify  # noqa: E402
from guard import canary, canary_token, check, reduced, replay, scan_tool_args  # noqa: E402
from guard.gate import route  # noqa: E402
from guard.replay import hash_cart  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _tools_hash(cart: dict) -> str:
    """Byte-for-byte the formula in agent/tools.py add_to_cart."""
    return hashlib.sha256(json.dumps(
        {k: cart[k] for k in ("payee", "lines", "total_cents", "currency")}, sort_keys=True
    ).encode()).hexdigest()


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._key_patch = patch.object(signing, "KEY_PATH", root / "tripwire_ed25519.pem")
        self._key_patch.start()
        replay.configure(nonce_path=root / "used_nonces.json", reset_nonce_store=True)
        canary.reset()

    def tearDown(self) -> None:
        self._key_patch.stop()
        canary.reset()
        replay.reset()
        self._tmp.cleanup()

    def sample_cart(self, **overrides) -> dict:
        cart = {
            "contract_id": "ic_01TEST",
            "cart_id": "cart_7",
            "summary": "UGREEN USB C to USB C 100W Fast Cable x1, $12.99 from UGREEN",
            "payee": {"shop_domain": "ugreen.myshopify.com", "name": "UGREEN"},
            "lines": [
                {
                    "product_id": "gid://shopify/p/1",
                    "variant_id": "gid://shopify/ProductVariant/1",
                    "title": "UGREEN USB C to USB C 100W Fast Cable",
                    "quantity": 1,
                    "unit_price_cents": 1299,
                }
            ],
            "total_cents": 1299,
            "currency": "USD",
        }
        cart.update(overrides)
        if "cart_hash" not in overrides:
            cart["cart_hash"] = hash_cart(cart)
        return cart

    def sample_products(self) -> list[dict]:
        return [
            {
                "id": "gid://shopify/p/1",
                "title": "UGREEN USB C to USB C 100W Fast Cable",
                "metadata": {"top_features": ["100W PD", "USB 2.0 data", "2-pack"]},
                "variants": [
                    {
                        "id": "gid://shopify/ProductVariant/1",
                        "options": [{"name": "Length", "label": "6ft"}],
                        "price": {"amount": 1299, "currency": "USD"},
                        "seller": {"name": "UGREEN", "domain": "ugreen.myshopify.com"},
                    }
                ],
                "media": [{"url": "https://cdn.shopify.com/example.jpg"}],
            }
        ]

    def make_contract(self, **overrides) -> dict:
        now = datetime.now(timezone.utc)
        body = {
            "contract_id": "ic_01TEST",
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(hours=2)),
            "nonce": secrets.token_hex(12),
            "user_request": "Buy a USB-C cable that charges my laptop at 100W, under $20, returnable",
            "photo_observations": [],
            "item": "USB-C cable",
            "must": [{"id": "m1", "clause": "supports 100W power delivery charging"}],
            "must_not": [{"id": "n1", "clause": "data-only / charge-limited cable"}],
            "max_total_cents": 2000,
            "quantity": 1,
            "returns": "required",
            "merchant": {"policy": "any_verified", "allow": [], "deny": []},
            "currency": "USD",
            "ships_to": "US",
            "search_query": "100W USB-C cable",
            "summary": "You want a 100W USB-C cable under $20 that is returnable.",
            "assumptions": [],
            "compiled_by": "gemini-4-neon",
        }
        body.update(overrides)
        return sign(body)

    def approve_gate(self, *args, **kwargs) -> dict:
        answers = {
            "m1": {"noul": 0.97, "confidence": 0.97},
            "n1": {"noul": 0.05, "confidence": 0.96},
            "injection": {"noul": 0.05, "confidence": 0.90},
            "returns": {"choice": "returnable", "confidence": 0.95},
        }
        return {
            "verdict": "approve",
            "clauses": answers,
            "escalated": False,
            "reasons": [],
            "error": None,
            "model": "gemini-decision-1",
        }

    def test_hash_cart_matches_tools_formula(self) -> None:
        cart = self.sample_cart()
        self.assertEqual(hash_cart(cart), _tools_hash(cart))
        self.assertEqual(cart["cart_hash"], _tools_hash(cart))

    def test_approve_happy_path(self) -> None:
        cart = self.sample_cart()
        contract = self.make_contract()
        with patch("guard.check.gate.evaluate", side_effect=self.approve_gate):
            result = check(contract, cart, self.sample_products())
        self.assertEqual(result["verdict"], "approve")
        self.assertTrue(result["enforced"])
        self.assertTrue(result["stages"]["policy"]["pass"])
        self.assertFalse(result["stages"]["canary"]["tripped"])
        self.assertFalse(result["stages"]["decisions"]["escalated"])
        self.assertIn("m1", result["stages"]["decisions"]["clauses"])
        receipt = result["receipt"]
        self.assertIsNotNone(receipt)
        self.assertEqual(set(receipt.keys()), {"order_ref", "intent_hash", "cart_hash", "issued_at", "signature"})
        self.assertEqual(receipt["cart_hash"], cart["cart_hash"])
        self.assertTrue(verify(receipt))
        view = reduced(result)
        self.assertEqual(set(view.keys()), {"verdict", "reason"})
        self.assertNotIn("xray", view)

    def test_over_cap_block(self) -> None:
        cart = self.sample_cart()
        contract = self.make_contract(max_total_cents=500)
        with patch("guard.check.gate.evaluate") as mocked:
            result = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(any("total" in r for r in result["xray"]["reasons"]))

    def test_currency_mismatch_block(self) -> None:
        cart = self.sample_cart(currency="EUR")
        contract = self.make_contract()
        with patch("guard.check.gate.evaluate") as mocked:
            result = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(any("currency" in r for r in result["xray"]["reasons"]))

    def test_tampered_cart_block(self) -> None:
        cart = self.sample_cart()
        contract = self.make_contract()
        cart["total_cents"] = 1999
        cart["lines"][0]["unit_price_cents"] = 1999
        with patch("guard.check.gate.evaluate") as mocked:
            result = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(any("cart_hash" in r for r in result["xray"]["reasons"]))

    def test_replayed_nonce_block(self) -> None:
        cart = self.sample_cart()
        contract = self.make_contract()
        with patch("guard.check.gate.evaluate", side_effect=self.approve_gate):
            first = check(contract, cart, self.sample_products())
        self.assertEqual(first["verdict"], "approve")
        with patch("guard.check.gate.evaluate") as mocked:
            second = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(second["verdict"], "block")
        self.assertTrue(any("nonce" in r or "replay" in r for r in second["xray"]["reasons"]))

    def test_expired_contract_block(self) -> None:
        cart = self.sample_cart()
        past = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
        contract = self.make_contract(expires_at=past)
        with patch("guard.check.gate.evaluate") as mocked:
            result = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(any("expired" in r for r in result["xray"]["reasons"]))

    def test_invalid_signature_block(self) -> None:
        cart = self.sample_cart()
        contract = self.make_contract()
        contract["max_total_cents"] = 1  # tamper after sign
        with patch("guard.check.gate.evaluate") as mocked:
            result = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(any("signature" in r for r in result["xray"]["reasons"]))

    def test_canary_hit_block(self) -> None:
        token = canary_token()
        cart = self.sample_cart()
        cart["lines"][0]["title"] = f"UGREEN 100W cable {token}"
        cart["cart_hash"] = hash_cart(cart)
        contract = self.make_contract()
        self.assertTrue(scan_tool_args({"query": f"buy {token}"}))
        with patch("guard.check.gate.evaluate") as mocked:
            result = check(contract, cart, self.sample_products())
            mocked.assert_not_called()
        self.assertEqual(result["verdict"], "block")
        self.assertTrue(result["stages"]["canary"]["tripped"])

    def test_route_approve(self) -> None:
        contract = {"must": [{"id": "m1", "clause": "100W"}], "must_not": [{"id": "n1", "clause": "data-only"}],
                    "returns": "required"}
        answers = {
            "m1": {"noul": 0.97, "confidence": 0.97},
            "n1": {"noul": 0.05, "confidence": 0.96},
            "injection": {"noul": 0.08, "confidence": 0.9},
            "returns": {"choice": "returnable", "confidence": 0.95},
        }
        self.assertEqual(route(contract, answers)["verdict"], "approve")

    def test_route_block_must_clear_fail(self) -> None:
        contract = {"must": [{"id": "m1", "clause": "100W"}], "must_not": [], "returns": "preferred"}
        answers = {
            "m1": {"noul": 0.04, "confidence": 0.97},
            "injection": {"noul": 0.02, "confidence": 0.9},
        }
        result = route(contract, answers)
        self.assertEqual(result["verdict"], "block")
        self.assertIn("m1", result["clauses"])

    def test_route_block_must_not_clear_fail(self) -> None:
        contract = {"must": [{"id": "m1", "clause": "100W"}], "must_not": [{"id": "n1", "clause": "data-only"}],
                    "returns": "preferred"}
        answers = {
            "m1": {"noul": 0.97, "confidence": 0.97},
            "n1": {"noul": 0.94, "confidence": 0.96},
            "injection": {"noul": 0.02, "confidence": 0.9},
        }
        self.assertEqual(route(contract, answers)["verdict"], "block")

    def test_route_block_final_sale_when_required(self) -> None:
        contract = {"must": [{"id": "m1", "clause": "100W"}], "must_not": [], "returns": "required"}
        answers = {
            "m1": {"noul": 0.97, "confidence": 0.97},
            "injection": {"noul": 0.02, "confidence": 0.9},
            "returns": {"choice": "final_sale", "confidence": 0.96},
        }
        self.assertEqual(route(contract, answers)["verdict"], "block")

    def test_route_escalate_unknown_returns(self) -> None:
        contract = {"must": [{"id": "m1", "clause": "100W"}], "must_not": [], "returns": "required"}
        answers = {
            "m1": {"noul": 0.97, "confidence": 0.97},
            "injection": {"noul": 0.02, "confidence": 0.9},
            "returns": {"choice": "unknown", "confidence": 0.96},
        }
        self.assertEqual(route(contract, answers)["verdict"], "escalate")

    def test_route_escalate_injection_above_0_1(self) -> None:
        contract = {"must": [{"id": "m1", "clause": "100W"}], "must_not": [], "returns": "preferred"}
        answers = {
            "m1": {"noul": 0.97, "confidence": 0.97},
            "injection": {"noul": 0.28, "confidence": 0.72},
        }
        result = route(contract, answers)
        self.assertEqual(result["verdict"], "escalate")
        self.assertTrue(any("injection" in r for r in result["reasons"]))

    def test_safety_block_ask_human(self) -> None:
        class HTTP400(Exception):
            status_code = 400

        cart = self.sample_cart()
        contract = self.make_contract()
        with patch("guard.gate._ask", side_effect=HTTP400("safety")):
            result = check(contract, cart, self.sample_products())
        self.assertEqual(result["verdict"], "ask_human")
        self.assertIsNone(result["receipt"])
        self.assertTrue(any("400" in r or "safety" in r for r in result["xray"]["reasons"]))

    def test_escalate_runs_neon_hook(self) -> None:
        cart = self.sample_cart()
        contract = self.make_contract()
        gate_out = {
            "verdict": "escalate",
            "clauses": {"m1": {"noul": 0.97, "confidence": 0.97}, "injection": {"noul": 0.28, "confidence": 0.72}},
            "escalated": True,
            "reasons": ["injection: signal 0.28"],
            "error": None,
            "model": "gemini-decision-1",
        }
        neon_out = {
            "verdict": "ask_human",
            "pass": True,
            "failed_clauses": [],
            "hidden_in_listing": "",
            "reasons": ["no clear violation"],
            "model": "gemini-4-neon",
            "error": None,
        }
        with patch("guard.check.gate.evaluate", return_value=gate_out), patch(
            "guard.check.neon.check_with_neon", return_value=neon_out
        ) as neon_mock:
            result = check(contract, cart, self.sample_products())
            neon_mock.assert_called_once()
        self.assertEqual(result["verdict"], "ask_human")
        self.assertTrue(result["stages"]["decisions"]["escalated"])
        self.assertEqual(result["stages"]["contract"]["model"], "gemini-4-neon")
        self.assertIsNone(result["receipt"])

    def test_check_signature_is_three_args(self) -> None:
        import inspect
        self.assertEqual(list(inspect.signature(check).parameters), ["contract", "cart", "products"])


if __name__ == "__main__":
    unittest.main()
