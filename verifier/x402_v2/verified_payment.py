from __future__ import annotations

from pydantic import BaseModel


class VerifiedPayment(BaseModel):
    """Business-facing verified payment DTO (x402-SDK-free).

    Deliberately isolates application code from the official x402 SDK
    types; SDK objects must be adapted into this model before crossing the
    provider boundary.
    """

    payer: str
    recipient: str
    asset: str
    atomic_amount: str
    network: str  # CAIP-2, e.g. "eip155:84532"
    nonce: str
    valid_after: str
    valid_before: str
    authorization_identifier: str | None = None


def to_caip2(chain_id: int) -> str:
    """Format an EVM chain id as a CAIP-2 network identifier."""
    return f"eip155:{chain_id}"


def from_caip2(network: str) -> int:
    """Parse a CAIP-2 network identifier into an EVM chain id.

    Only ``eip155:<int>`` is accepted; anything else raises ``ValueError``.
    """
    if not isinstance(network, str):
        raise ValueError(f"invalid CAIP-2 network: {network!r}")
    prefix, sep, chain_id = network.partition(":")
    if not sep or prefix != "eip155":
        raise ValueError(f"unsupported CAIP-2 network: {network!r}")
    if not chain_id.isdigit():
        raise ValueError(f"non-numeric chain id in CAIP-2 network: {network!r}")
    return int(chain_id)
