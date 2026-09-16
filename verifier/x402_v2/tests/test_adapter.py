from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from x402.http.utils import (  # noqa: E402
    encode_payment_signature_header,
)
from x402.schemas.payments import (  # noqa: E402
    PaymentPayload,
    PaymentRequirements,
)

from x402_v2.adapter import X402V2Adapter  # noqa: E402
from x402_v2.verified_payment import VerifiedPayment  # noqa: E402


def build_requirements() -> PaymentRequirements:
    return PaymentRequirements(
        scheme="exact",
        network="eip155:84532",
        asset="0xUSDC",
        amount="1000000",
        pay_to="0xrecipient",
        max_timeout_seconds=3600,
    )


def build_payment_payload() -> PaymentPayload:
    return PaymentPayload(
        x402_version=2,
        accepted=build_requirements(),
        payload={
            "authorization": {
                "from": "0xpayer",
                "to": "0xrecipient",
                "value": "1000000",
                "validAfter": 100,
                "validBefore": 200,
                "nonce": "nonce-1",
            },
            "signature": "0xdeadbeef",
        },
    )


class X402V2AdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = X402V2Adapter()

    def test_parse_payment_payload_roundtrip(self) -> None:
        payload = build_payment_payload()
        header = encode_payment_signature_header(payload)
        decoded = self.adapter.parse_payment_payload(header)
        self.assertIsInstance(decoded, PaymentPayload)
        self.assertEqual(decoded, payload)

    def test_to_verified_payment_maps_all_fields(self) -> None:
        vp = self.adapter.to_verified_payment(build_payment_payload())
        self.assertIsInstance(vp, VerifiedPayment)
        self.assertEqual(vp.payer, "0xpayer")
        self.assertEqual(vp.recipient, "0xrecipient")
        self.assertEqual(vp.asset, "0xUSDC")
        self.assertEqual(vp.atomic_amount, "1000000")
        self.assertEqual(vp.network, "eip155:84532")
        self.assertEqual(vp.nonce, "nonce-1")
        self.assertEqual(vp.valid_after, "100")
        self.assertEqual(vp.valid_before, "200")
        self.assertEqual(vp.authorization_identifier, "nonce-1")

    def test_to_verified_payment_stringifies_numeric_authorization(self) -> None:
        payload = build_payment_payload()
        payload.payload["authorization"]["validAfter"] = 100
        payload.payload["authorization"]["validBefore"] = 200
        vp = self.adapter.to_verified_payment(payload)
        self.assertEqual(vp.valid_after, "100")
        self.assertEqual(vp.valid_before, "200")

    def test_parse_payload_then_map_roundtrip(self) -> None:
        payload = build_payment_payload()
        header = encode_payment_signature_header(payload)
        decoded = self.adapter.parse_payment_payload(header)
        vp = self.adapter.to_verified_payment(decoded)
        self.assertEqual(vp.payer, "0xpayer")
        self.assertEqual(vp.recipient, "0xrecipient")
        self.assertEqual(vp.asset, "0xUSDC")
        self.assertEqual(vp.atomic_amount, "1000000")
        self.assertEqual(vp.network, "eip155:84532")
        self.assertEqual(vp.nonce, "nonce-1")
        self.assertEqual(vp.valid_after, "100")
        self.assertEqual(vp.valid_before, "200")
        self.assertEqual(vp.authorization_identifier, "nonce-1")


if __name__ == "__main__":
    unittest.main()
