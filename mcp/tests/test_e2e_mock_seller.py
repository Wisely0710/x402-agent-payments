"""End-to-end test: mock seller (examples/mock_seller.py) <-> client.

Local, no chain, all official: real 402 header, real EIP-3009 signature, real
EIP-712 reconstruction and EOA signature verification on the seller side, real
200 delivery.
"""

from __future__ import annotations

import threading

import pytest
from eth_account import Account
from x402.mechanisms.evm import EthAccountSigner

from x402_mcp.client import PaidResourceClient
from examples.mock_seller import create_server

PAYER_KEY = "0x" + "22" * 32  # fixed test key (the mock seller accepts any payer)
BASE_URL = "http://127.0.0.1"


@pytest.fixture()
def seller_url() -> str:
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"{BASE_URL}:{server.server_address[1]}/api/data"
    server.shutdown()
    server.server_close()


def test_end_to_end_paid_flow(seller_url: str) -> None:
    signer = EthAccountSigner(Account.from_key(PAYER_KEY))
    client = PaidResourceClient(signer)

    body = client.fetch(seller_url, max_amount_wei=1000)

    assert "paid" in body


def test_end_to_end_rejects_above_budget(seller_url: str) -> None:
    signer = EthAccountSigner(Account.from_key(PAYER_KEY))
    client = PaidResourceClient(signer)

    with pytest.raises(ValueError, match="exceeds"):
        client.fetch(seller_url, max_amount_wei=10)
