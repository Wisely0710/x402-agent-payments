"""One-command demo: an AI agent pays for an x402-protected resource.

Everything runs in-process on loopback — no chain, no facilitator, no real
money, no real key:

    paying client (mcp side)  →  seller (built on the real x402_v2 verifier)
    402 + PAYMENT-REQUIRED    →  sign EIP-3009  →  retry with PAYMENT-SIGNATURE
                              →  verify  →  200

Run it with `scripts/demo.sh` from the repository root.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from eth_account import Account
from x402.http.constants import PAYMENT_REQUIRED_HEADER, PAYMENT_SIGNATURE_HEADER
from x402.mechanisms.evm import EthAccountSigner
from x402.schemas.payments import PaymentRequired, PaymentRequirements
from x402_v2.provider import X402V2Provider, X402VerificationError

from x402_mcp.client import PaidResourceClient

# ── demo fixtures: localhost values only, no real asset and no real key ──────
CHAIN_ID = 31337  # local hardhat chain, used as CAIP-2 eip155:31337
TOKEN = "0x3333333333333333333333333333333333333333"
PAY_TO = "0x4444444444444444444444444444444444444444"
PRICE_ATOMIC = "100"
PAYER_KEY = "0x" + "22" * 32  # fixed demo key; the demo seller accepts any payer
BUDGET_ATOMIC = 1_000
RESOURCE_PATH = "/paid-resource"

provider = X402V2Provider(
    wallet_address=PAY_TO,
    chain_id=CHAIN_ID,
    accepted_tokens=[TOKEN],
    token_name="USD Coin",
    token_version="2",
    service_name="demo-resource",
)
requirement: PaymentRequirements = provider.create_payment_requirement(
    amount=PRICE_ATOMIC, endpoint=RESOURCE_PATH, token=TOKEN
)
challenge: PaymentRequired = provider.build_payment_required(
    requirement, resource_url=f"http://127.0.0.1{RESOURCE_PATH}"
)


def log(side: str, message: str) -> None:
    print(f"[{side:>6}] {message}", flush=True)


class _LoggingSigner(EthAccountSigner):
    """EthAccountSigner that announces the signing step, so the transcript reads
    in chronological order. Signing itself is unchanged."""

    def sign_typed_data(
        self,
        domain: Any,
        types: Any,
        primary_type: str,
        message: dict[str, Any],
    ) -> bytes:
        log(
            "agent",
            f"  signing EIP-712 TransferWithAuthorization value={message['value']} "
            f"to={message['to'][:10]}… chainId={domain.chain_id}",
        )
        return super().sign_typed_data(domain, types, primary_type, message)


class _PaidResourceHandler(BaseHTTPRequestHandler):
    """Seller side: answer 402 with a challenge, or verify and deliver."""

    server_version = "x402-demo"

    def do_GET(self) -> None:  # stdlib handler naming
        if self.path != RESOURCE_PATH:
            self.send_error(404, "unknown resource")
            return

        signature_header = self.headers.get(PAYMENT_SIGNATURE_HEADER)
        if not signature_header:
            log("seller", "request without PAYMENT-SIGNATURE → 402")
            log(
                "seller",
                f"  accepts[0]: scheme={requirement.scheme} network={requirement.network} "
                f"amount={requirement.amount} asset={requirement.asset[:10]}…",
            )
            self._respond(
                402,
                json.dumps({"error": "payment required"}).encode(),
                extra_headers={
                    PAYMENT_REQUIRED_HEADER: provider.encode_payment_required(challenge)
                },
            )
            return

        log("seller", "retry with PAYMENT-SIGNATURE → verifying (x402_v2.provider)")
        try:
            payload = provider.parse_payment_signature(signature_header)
            verified = provider.verify_payment(payload, requirement)
        except X402VerificationError as exc:
            log("seller", f"  rejected: {exc.code}")
            self._respond(402, json.dumps({"error": exc.code}).encode())
            return

        log(
            "seller",
            f"  verified payer={verified.payer} amount={verified.atomic_amount} "
            f"network={verified.network}",
        )
        log("seller", "  settlement is out of scope here — no transaction is sent")
        self._respond(200, json.dumps({"status": "paid", "data": "premium data"}).encode())

    def log_message(self, format: str, *args: Any) -> None:  # stdlib signature
        """Silence the default request logging; the demo prints its own lines."""

    def _respond(
        self,
        status: int,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PaidResourceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}{RESOURCE_PATH}"

    try:
        log("agent", f"GET {url} (budget {BUDGET_ATOMIC} atomic units)")
        client = PaidResourceClient(_LoggingSigner(Account.from_key(PAYER_KEY)))
        body = client.fetch(url, max_amount_wei=BUDGET_ATOMIC)
        log("agent", f"received: {body}")
    finally:
        server.shutdown()
        server.server_close()

    log("demo", "402 → signed authorization → 200, in-process and chain-free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
