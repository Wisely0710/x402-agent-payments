"""x402 v2 payment provider built on the official x402 SDK.

The provider builds official ``PaymentRequired`` requirements, parses the
official base64 ``PAYMENT-SIGNATURE`` header into a ``PaymentPayload``, validates
the client's accepted requirement against the server requirement, and
cryptographically verifies the EIP-712 ``TransferWithAuthorization`` signature
using the official x402 EVM helpers plus eth-account. On success it returns a
:class:`x402_v2.verified_payment.VerifiedPayment` DTO.

Facilitator ``verify``/``settle`` is intentionally *not* called: this provider is
verification-only, so settlement happens afterwards on the resource owner's terms
— either the payer wallet calls the resource contract itself (``transferFrom``),
or a facilitator settles the same signed authorization where the token supports
EIP-3009. Only the payer signature is verified here.
"""

import time

from x402.mechanisms.evm.eip712 import build_typed_data_for_signing, hash_typed_data
from x402.mechanisms.evm.types import ExactEIP3009Authorization
from x402.mechanisms.evm.verify import verify_eoa_signature
from x402.schemas.payments import (
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    ResourceInfo,
)

from x402_v2.adapter import X402V2Adapter
from x402_v2.verified_payment import VerifiedPayment, from_caip2, to_caip2

DEFAULT_MAX_TIMEOUT_SECONDS = 3600
DEFAULT_TOKEN_NAME = "USD Coin"
DEFAULT_TOKEN_VERSION = "2"


class X402VerificationError(Exception):
    """Raised when an x402 V2 payment fails signature or requirement checks."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def canonicalize_network(network: str | None, chain_id: int) -> str:
    """Canonicalize a configured network value into CAIP-2 ``eip155:<chain_id>``.

    - ``None``/empty → derived from the configured ``chain_id``.
    - Already a CAIP-2 ``eip155:<n>`` → used as-is, but a chain id that does not
      match the configured ``chain_id`` fails closed (raises).
    - Any other legacy non-CAIP value (e.g. ``hardhat``) → canonicalized
      from the configured ``chain_id`` for local compatibility.
    """
    if not network:
        return to_caip2(chain_id)
    if network.startswith("eip155:"):
        configured = from_caip2(network)
        if configured != chain_id:
            raise X402VerificationError(
                "network_mismatch",
                f"Configured network {network!r} chain id does not match chain_id {chain_id}",
            )
        return network
    return to_caip2(chain_id)


class X402V2Provider:
    """Verifies official x402 v2 payment signatures for a resource route.

    All methods are synchronous; the EIP-712 verification is pure CPU work and
    performs no network I/O. Settlement is out of scope: this provider never
    calls a facilitator and never sends a transaction.
    """

    def __init__(
        self,
        wallet_address: str,
        chain_id: int,
        accepted_tokens: list[str],
        network: str | None = None,
        max_timeout_seconds: int = DEFAULT_MAX_TIMEOUT_SECONDS,
        token_name: str = DEFAULT_TOKEN_NAME,
        token_version: str = DEFAULT_TOKEN_VERSION,
        service_name: str = "x402",
    ):
        self.wallet_address = wallet_address
        self.chain_id = chain_id
        self.accepted_tokens = accepted_tokens
        self.network = canonicalize_network(network, chain_id)
        self.max_timeout_seconds = max_timeout_seconds
        self.token_name = token_name
        self.token_version = token_version
        self.service_name = service_name
        self._adapter = X402V2Adapter()

    # ------------------------------------------------------------------ wire

    def create_payment_requirement(
        self,
        amount: str,
        endpoint: str,
        token: str,
        scheme: str = "exact",
    ) -> PaymentRequirements:
        """Build the server-side requirement for a 402 challenge / verification.

        ``endpoint`` is accepted for interface parity with the old provider; the
        requirement itself only depends on the configured recipient/asset/network.
        """
        return self._adapter.build_requirements(
            scheme=scheme,
            network=self.network,
            asset=token,
            amount=str(amount),
            pay_to=self.wallet_address,
            max_timeout_seconds=self.max_timeout_seconds,
            extra={"name": self.token_name, "version": self.token_version},
        )

    def parse_payment_signature(self, header: str) -> PaymentPayload:
        """Decode a base64 ``PAYMENT-SIGNATURE`` header into a ``PaymentPayload``.

        The official decoder also accepts x402 **v1** headers; this provider is
        v2-only, so a v1 payload fails closed with ``invalid_version`` here
        rather than surfacing later as a missing attribute.
        """
        payload = self._adapter.parse_payment_payload(header)
        if not isinstance(payload, PaymentPayload):
            raise X402VerificationError("invalid_version", "Unsupported x402 payment version")
        return payload

    def build_payment_required(
        self,
        requirement: PaymentRequirements,
        resource_url: str,
    ) -> PaymentRequired:
        """Build the official ``PaymentRequired`` for a 402 challenge response."""
        resource = ResourceInfo(url=resource_url, service_name=self.service_name)
        return self._adapter.build_payment_required([requirement], resource=resource)

    def encode_payment_required(self, required: PaymentRequired) -> str:
        """Encode a ``PaymentRequired`` as the base64 ``PAYMENT-REQUIRED`` header."""
        return self._adapter.encode_payment_required(required)

    # ------------------------------------------------------------- verification

    def verify_payment(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirements,
    ) -> VerifiedPayment:
        """Verify a signed payment before exposing it to application code."""
        if payload.x402_version != 2:
            raise X402VerificationError("invalid_version", "Unsupported x402 payment version")
        self._validate_accepted_requirement(payload.accepted, requirement)
        self._verify_eip712_signature(payload, requirement)
        return self._adapter.to_verified_payment(payload)

    def _validate_accepted_requirement(
        self,
        accepted: PaymentRequirements,
        requirement: PaymentRequirements,
    ) -> None:
        """Ensure the client's accepted requirement matches the server's."""
        if accepted.scheme != requirement.scheme:
            raise X402VerificationError(
                "requirement_mismatch",
                "Accepted payment scheme does not match the server requirement",
            )
        try:
            accepted_chain = from_caip2(accepted.network)
            required_chain = from_caip2(requirement.network)
        except ValueError as exc:
            raise X402VerificationError(
                "requirement_mismatch", f"Invalid CAIP-2 network: {exc}"
            ) from None
        if accepted_chain != required_chain:
            raise X402VerificationError(
                "requirement_mismatch",
                "Accepted CAIP-2 chain id does not match the server requirement",
            )
        if accepted.asset.lower() != requirement.asset.lower():
            raise X402VerificationError(
                "requirement_mismatch",
                "Accepted asset does not match the server requirement",
            )
        if accepted.amount != requirement.amount:
            raise X402VerificationError(
                "requirement_mismatch",
                "Accepted amount does not match the server requirement",
            )
        if accepted.pay_to.lower() != requirement.pay_to.lower():
            raise X402VerificationError(
                "requirement_mismatch",
                "Accepted pay_to does not match the server requirement",
            )

    def _verify_eip712_signature(
        self,
        payload: PaymentPayload,
        requirement: PaymentRequirements,
    ) -> None:
        """Verify signature and bind authorization fields to the requirement."""
        auth = payload.payload.get("authorization") or {}
        signature = payload.payload.get("signature")
        if not isinstance(auth, dict) or not signature:
            raise X402VerificationError(
                "invalid_signature", "Payment payload is missing authorization or signature"
            )
        try:
            authorization = ExactEIP3009Authorization(
                from_address=str(auth["from"]),
                to=str(auth["to"]),
                value=str(auth["value"]),
                valid_after=str(auth["validAfter"]),
                valid_before=str(auth["validBefore"]),
                nonce=str(auth["nonce"]),
            )
            value = int(authorization.value)
            valid_after = int(authorization.valid_after)
            valid_before = int(authorization.valid_before)
            if authorization.to.lower() != requirement.pay_to.lower():
                raise X402VerificationError(
                    "authorization_mismatch",
                    "Authorization recipient does not match the server requirement",
                )
            if value != int(requirement.amount):
                raise X402VerificationError(
                    "authorization_mismatch",
                    "Authorization amount does not match the server requirement",
                )
            now = int(time.time())
            if valid_after > now or valid_before < now + 6:
                raise X402VerificationError(
                    "invalid_time_window", "Authorization is outside its validity window"
                )
            # X402-EIP3009-BOUND-01：deadline 不得超出 now + max_timeout_seconds；
            # cap 為 inclusive（只拒絕嚴格大於，等於上限合法），validAfter=0 的
            # official exact 相容語意維持不變。
            if valid_before - now > self.max_timeout_seconds:
                raise X402VerificationError(
                    "invalid_time_window",
                    "Authorization deadline exceeds the server max timeout",
                )
            chain_id = from_caip2(requirement.network)
            extra = requirement.extra or {}
            name = extra.get("name") or self.token_name
            version = extra.get("version") or self.token_version
            domain, types, primary_type, message = build_typed_data_for_signing(
                authorization,
                chain_id,
                requirement.asset,
                name,
                version,
            )
            digest = hash_typed_data(domain, types, primary_type, message)
            signature_text = str(signature)
            sig_bytes = bytes.fromhex(signature_text.removeprefix("0x").removeprefix("0X"))
            if len(sig_bytes) != 65:
                raise X402VerificationError(
                    "invalid_signature", "Payment signature has an invalid length"
                )
            valid = verify_eoa_signature(digest, sig_bytes, authorization.from_address)
        except (ValueError, TypeError, KeyError, X402VerificationError) as exc:
            if isinstance(exc, X402VerificationError):
                raise exc from None
            raise X402VerificationError(
                "invalid_signature", f"Payment signature verification failed: {exc}"
            ) from None
        if not valid:
            raise X402VerificationError(
                "invalid_signature", "Payment signature verification failed"
            )
