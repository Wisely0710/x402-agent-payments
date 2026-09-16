"""Thin adapter isolating application code from the official x402 SDK.

The official x402 SDK types (``PaymentRequired``, ``PaymentPayload``) must not
cross the provider boundary into application code. This adapter decodes
the wire headers into SDK models and maps them onto the SDK-free
:class:`x402_v2.verified_payment.VerifiedPayment` business DTO.
"""

from __future__ import annotations

from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
)
from x402.schemas import PaymentPayloadV1
from x402.schemas.payments import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
)

from x402_v2.verified_payment import VerifiedPayment


class X402V2Adapter:
    """Adapts x402 V2 HTTP headers into SDK models and business DTOs.

    Only the header decode functions from the SDK are exercised here; the
    returned SDK models are mapped immediately into :class:`VerifiedPayment`
    so no SDK type leaks into application code.
    """

    def parse_payment_payload(self, header: str) -> PaymentPayload | PaymentPayloadV1:
        """Decode a payment signature header value into an SDK model.

        The official decoder is shared with x402 v1, so the result may be a v1
        payload; callers must narrow to :class:`PaymentPayload` (v2) before use.
        """
        return decode_payment_signature_header(header)

    def build_requirements(
        self,
        *,
        scheme: str,
        network: str,
        asset: str,
        amount: str,
        pay_to: str,
        max_timeout_seconds: int,
        extra: dict | None = None,
    ) -> PaymentRequirements:
        """Build an official ``PaymentRequirements`` model for the 402 challenge.

        ``extra`` carries the EIP-712 domain parameters (``name`` / ``version``)
        the client needs to sign the EIP-3009 TransferWithAuthorization message.
        """
        return PaymentRequirements(
            scheme=scheme,
            network=network,
            asset=asset,
            amount=amount,
            pay_to=pay_to,
            max_timeout_seconds=max_timeout_seconds,
            extra=extra or {},
        )

    def build_payment_required(
        self,
        accepts: list[PaymentRequirements],
        resource: ResourceInfo | None = None,
    ) -> PaymentRequired:
        """Build an official ``PaymentRequired`` (V2) model for the 402 response."""
        return PaymentRequired(x402_version=2, resource=resource, accepts=accepts)

    def encode_payment_required(self, required: PaymentRequired) -> str:
        """Encode a ``PaymentRequired`` as the standard base64 ``PAYMENT-REQUIRED`` header."""
        return encode_payment_required_header(required)

    def to_verified_payment(self, payload: PaymentPayload) -> VerifiedPayment:
        """Map an SDK ``PaymentPayload`` onto the business :class:`VerifiedPayment`.

        The payer identity and authorization metadata (``nonce``,
        ``validAfter``, ``validBefore``) come from the scheme-specific
        ``payload["authorization"]`` dict; the recipient, asset, amount, and
        network come from the accepted ``PaymentRequirements``. Numeric
        authorization fields are stringified to satisfy the all-``str`` DTO.
        """
        accepted = payload.accepted
        authorization = payload.payload["authorization"]
        return VerifiedPayment(
            payer=str(authorization["from"]),
            recipient=str(accepted.pay_to),
            asset=str(accepted.asset),
            atomic_amount=str(accepted.amount),
            network=str(accepted.network),
            nonce=str(authorization["nonce"]),
            valid_after=str(authorization["validAfter"]),
            valid_before=str(authorization["validBefore"]),
            authorization_identifier=str(authorization["nonce"]),
        )
