from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from x402_v2.verified_payment import VerifiedPayment, from_caip2, to_caip2  # noqa: E402


class VerifiedPaymentTest(unittest.TestCase):
    def test_fields_exist_and_are_typed(self) -> None:
        vp = VerifiedPayment(
            payer="0xpayer",
            recipient="0xrecipient",
            asset="ETH",
            atomic_amount="1000000000000000",
            network="eip155:84532",
            nonce="nonce-1",
            valid_after="100",
            valid_before="200",
        )
        self.assertEqual(vp.payer, "0xpayer")
        self.assertEqual(vp.recipient, "0xrecipient")
        self.assertEqual(vp.asset, "ETH")
        self.assertEqual(vp.atomic_amount, "1000000000000000")
        self.assertEqual(vp.network, "eip155:84532")
        self.assertEqual(vp.nonce, "nonce-1")
        self.assertEqual(vp.valid_after, "100")
        self.assertEqual(vp.valid_before, "200")
        self.assertIsNone(vp.authorization_identifier)

    def test_authorization_identifier_optional(self) -> None:
        vp = VerifiedPayment(
            payer="p",
            recipient="r",
            asset="ETH",
            atomic_amount="1",
            network="eip155:1",
            nonce="n",
            valid_after="0",
            valid_before="0",
            authorization_identifier="auth-9",
        )
        self.assertEqual(vp.authorization_identifier, "auth-9")

    def test_to_caip2_roundtrip(self) -> None:
        self.assertEqual(to_caip2(84532), "eip155:84532")
        self.assertEqual(to_caip2(1), "eip155:1")
        self.assertEqual(from_caip2("eip155:84532"), 84532)
        self.assertEqual(from_caip2(to_caip2(137)), 137)

    def test_from_caip2_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            from_caip2("")

    def test_from_caip2_rejects_missing_eip155_prefix(self) -> None:
        with self.assertRaises(ValueError):
            from_caip2("84532")
        with self.assertRaises(ValueError):
            from_caip2("foo:84532")

    def test_from_caip2_rejects_non_numeric_chain_id(self) -> None:
        with self.assertRaises(ValueError):
            from_caip2("eip155:abc")

    def test_from_caip2_rejects_other_namespace(self) -> None:
        with self.assertRaises(ValueError):
            from_caip2("solana:4sGjMW1sUnHzSxGspuhpqLDx6wiyjNtZ")


if __name__ == "__main__":
    unittest.main()
