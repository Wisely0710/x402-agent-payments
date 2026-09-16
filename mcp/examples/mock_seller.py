"""Mock x402 seller (resource server) — standard library only, no chain.

Role: a miniature seller that quotes with 402, verifies the payment signature
and delivers the resource.

Purpose:
  1. Run the whole x402 loop locally (402 -> buyer signs -> verification -> 200)
     and see how a seller verifies a payment.
  2. Verification uses the official components: decode PAYMENT-SIGNATURE,
     rebuild the EIP-712 typed data (official eip712 helpers) and check the EOA
     signature. It does not settle on chain — settlement is a separate,
     wallet-initiated step.

Run (local, no Docker, no chain):
  python3 examples/mock_seller.py            # defaults to 127.0.0.1:8888
Environment: X402_MOCK_PORT (default 8888)
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from x402.http.constants import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_SIGNATURE_HEADER,
)
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
)
from x402.mechanisms.evm import hash_typed_data, verify_eoa_signature
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.types import ExactEIP3009Authorization
from x402.schemas import PaymentPayload, PaymentRequired, PaymentRequirements, ResourceInfo

# ── Seller configuration (hard-coded for the demo) ─────────────────
TOKEN = "0x3333333333333333333333333333333333333333"  # local mock token (EIP-3009 domain)
PAY_TO = "0x4444444444444444444444444444444444444444"  # seller receiving address
NETWORK = "eip155:31337"  # localhost hardhat
PRICE_ATOMIC = "100"  # price per request (smallest unit)
EIP712_NAME = "USD Coin"  # token EIP-712 domain name (official custom-token rule)
EIP712_VERSION = "2"


def make_requirements() -> PaymentRequirements:
    return PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        asset=TOKEN,
        amount=PRICE_ATOMIC,
        pay_to=PAY_TO,
        max_timeout_seconds=3600,
        extra={"name": EIP712_NAME, "version": EIP712_VERSION},
    )


def make_payment_required() -> PaymentRequired:
    return PaymentRequired(
        x402_version=2,
        error="PAYMENT_REQUIRED",
        resource=ResourceInfo(url="mock://seller/pay"),
        accepts=[make_requirements()],
    )


def verify_payment_signature(header_value: str) -> tuple[bool, str, dict[str, Any]]:
    """Seller verification: decode -> rebuild typed data -> official EOA check.

    Returns (ok, message, authorization dict).
    """
    decoded = decode_payment_signature_header(header_value)
    if not isinstance(decoded, PaymentPayload):
        # The official decoder also accepts x402 v1 headers; this seller quotes
        # and verifies v2 only.
        return False, "unsupported x402 version", {}
    payload = decoded
    accepted = payload.accepted
    auth = payload.payload["authorization"]
    signature = payload.payload["signature"]

    # 1) Payment terms align: what the buyer accepted must equal what the
    #    seller quoted.
    if accepted != make_requirements():
        return False, "accepted requirements mismatch", auth
    # 2) Authorization aligns: payee and amount must match the quote.
    if auth["to"].lower() != PAY_TO.lower() or auth["value"] != PRICE_ATOMIC:
        return False, "authorization to/value mismatch", auth
    # 3) Not expired.
    if int(auth["validBefore"]) < time.time():
        return False, "authorization expired", auth

    chain_id = int(NETWORK.split(":")[1])

    authorization = ExactEIP3009Authorization(
        from_address=auth["from"],
        to=auth["to"],
        value=auth["value"],
        valid_after=auth["validAfter"],
        valid_before=auth["validBefore"],
        nonce=auth["nonce"],
    )
    domain, types, primary_type, message = build_typed_data_for_signing(
        authorization,
        chain_id=chain_id,
        verifying_contract=TOKEN,
        token_name=EIP712_NAME,
        token_version=EIP712_VERSION,
    )
    digest = hash_typed_data(domain, types, primary_type, message)
    hex_signature = signature[2:] if signature.startswith("0x") else signature
    signature_bytes = bytes.fromhex(hex_signature)
    if not verify_eoa_signature(digest, signature_bytes, auth["from"]):
        return False, "signature invalid", auth
    return True, "paid", auth


class MockSellerState:
    """Shared state attached to the server."""

    def __init__(self) -> None:
        self.payment_required = make_payment_required()


class MockSellerHandler(BaseHTTPRequestHandler):
    """GET and POST share one path.

    No PAYMENT-SIGNATURE -> 402 quote; with it -> verify and deliver.
    """

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        sig_header = self.headers.get(PAYMENT_SIGNATURE_HEADER)
        if not sig_header:
            self._respond_402()
            return
        ok, message, auth = verify_payment_signature(sig_header)
        if ok:
            self._respond_json(
                200,
                {"status": "paid", "payer": auth["from"], "amount": auth["value"]},
            )
        else:
            self._respond_json(400, {"error": message})

    def _respond_402(self) -> None:
        body = json.dumps({"error": "payment required"}).encode()
        self.send_response(402)
        self.send_header(
            PAYMENT_REQUIRED_HEADER,
            encode_payment_required_header(self.server.state.payment_required),  # type: ignore[attr-defined]
        )
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[mock-seller]", fmt % args, flush=True)


def create_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), MockSellerHandler)
    server.state = MockSellerState()  # type: ignore[attr-defined]
    return server


def main() -> None:
    port = int(os.environ.get("X402_MOCK_PORT", "8888"))
    server = create_server(port=port)
    print(f"[mock-seller] listening on http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
