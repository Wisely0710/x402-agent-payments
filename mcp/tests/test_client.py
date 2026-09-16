"""Flow tests: 402 -> official parsing -> official signing -> PAYMENT-SIGNATURE retry.

Everything uses official components (utils / schemes / EthAccountSigner) and
the mock only plays the resource server, so what is exercised is the real x402
wire behaviour (minus on-chain settlement).
"""

from __future__ import annotations

import json

import httpx
import pytest
from eth_account import Account
from x402.http.constants import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_SIGNATURE_HEADER,
)
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
)
from x402.mechanisms.evm import EthAccountSigner
from x402.schemas import PaymentPayload, PaymentRequired, PaymentRequirements, ResourceInfo

from x402_mcp.client import PaidResourceClient

PAYER_KEY = "0x" + "11" * 32  # fixed test key
PAYER_ACCOUNT = Account.from_key(PAYER_KEY)
TOKEN = "0x3333333333333333333333333333333333333333"  # local EIP-3009 mock token
RECEIVER = "0x4444444444444444444444444444444444444444"
RESOURCE_URL = "http://example.test/api/paid/resource"


def make_requirements(amount_wei: int) -> PaymentRequirements:
    return PaymentRequirements(
        scheme="exact",
        network="eip155:31337",  # localhost hardhat
        asset=TOKEN,
        amount=str(amount_wei),
        pay_to=RECEIVER,
        max_timeout_seconds=3600,
        extra={
            # Custom tokens must supply the EIP-712 domain name/version
            # (official docs, "Using Custom ERC-20 Tokens"); the official
            # default asset (USDC) carries them implicitly.
            "name": "USD Coin",
            "version": "2",
        },
    )


def make_402_headers(amount_wei: int) -> dict[str, str]:
    payment_required = PaymentRequired(
        x402_version=2,
        error="PAYMENT_REQUIRED",
        resource=ResourceInfo(url=RESOURCE_URL),
        accepts=[make_requirements(amount_wei)],
    )
    return {PAYMENT_REQUIRED_HEADER: encode_payment_required_header(payment_required)}


def make_transport(log: list[httpx.Request], *, challenge_amount: int = 100) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        if request.headers.get(PAYMENT_SIGNATURE_HEADER):
            if request.method == "POST":
                return httpx.Response(
                    200, json={"payment_hash": "0x" + "ab" * 32, "status": "verified"}
                )
            return httpx.Response(200, text="paid-content")
        return httpx.Response(
            402,
            headers=make_402_headers(amount_wei=challenge_amount),
            json={"error": "payment required"},
        )

    return httpx.MockTransport(handler)


@pytest.fixture()
def client() -> PaidResourceClient:
    signer = EthAccountSigner(PAYER_ACCOUNT)
    return PaidResourceClient(signer)


def test_pays_then_retries_with_official_header(client: PaidResourceClient) -> None:
    log: list[httpx.Request] = []
    client._http = httpx.Client(transport=make_transport(log))

    body = client.fetch(RESOURCE_URL, max_amount_wei=1000)

    assert body == "paid-content"
    assert len(log) == 2
    header = log[1].headers[PAYMENT_SIGNATURE_HEADER]

    # Official payload: decodable, correct scheme/network/amount, signed.
    payload = decode_payment_signature_header(header)
    assert isinstance(payload, PaymentPayload)  # the official decoder also accepts v1
    assert payload.x402_version == 2
    assert payload.accepted.scheme == "exact"
    assert payload.accepted.network == "eip155:31337"
    assert payload.accepted.amount == "100"
    assert payload.payload["signature"].startswith("0x")


def test_post_body_is_forwarded_verbatim(client: PaidResourceClient) -> None:
    log: list[httpx.Request] = []
    client._http = httpx.Client(transport=make_transport(log))

    body = client.fetch(
        RESOURCE_URL,
        max_amount_wei=1000,
        method="POST",
        json_body={"units": 100},
    )

    assert "payment_hash" in body
    assert len(log) == 2
    # Both requests are POST, the JSON body reaches the resource server
    # unchanged (nothing is added to it), and the retry is paid.
    for request in log:
        assert request.method == "POST"
        assert json.loads(request.content) == {"units": 100}
    assert log[1].headers[PAYMENT_SIGNATURE_HEADER]


def test_post_without_body_sends_no_body_and_no_extra_headers(
    client: PaidResourceClient,
) -> None:
    log: list[httpx.Request] = []
    client._http = httpx.Client(transport=make_transport(log))

    client.fetch(RESOURCE_URL, max_amount_wei=1000, method="POST")

    assert len(log) == 2
    assert [request.method for request in log] == ["POST", "POST"]
    assert log[0].content == b"" and log[1].content == b""
    # The retry differs from the first attempt by the payment header only.
    first = {name.lower() for name in log[0].headers}
    retried = {name.lower() for name in log[1].headers}
    assert retried - first == {PAYMENT_SIGNATURE_HEADER.lower()}
    for name, value in log[0].headers.items():
        assert log[1].headers[name] == value


def test_rejects_when_price_exceeds_max(client: PaidResourceClient) -> None:
    log: list[httpx.Request] = []
    client._http = httpx.Client(transport=make_transport(log))

    with pytest.raises(ValueError, match="exceeds"):
        client.fetch(RESOURCE_URL, max_amount_wei=10)

    # Above budget: no second request is sent.
    assert len(log) == 1
