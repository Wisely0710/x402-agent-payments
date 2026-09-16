"""Provider-level x402 V2 wire & verification tests (real crypto).

Exercises the official x402 V2 wire handling and EIP-712 TransferWithAuthorization
verification against a *real* :class:`x402_v2.provider.X402V2Provider`
(no mocked ``verify_payment``). Signatures are produced with eth-account using the
same EIP-712 domain/type/message construction the reference client and provider share,
so valid signatures pass and tampered / mismatched inputs fail closed.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eth_account import Account  # noqa: E402
from x402.http.utils import (  # noqa: E402
    encode_payment_signature_header,
)
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing  # noqa: E402
from x402.mechanisms.evm.types import ExactEIP3009Authorization  # noqa: E402
from x402.schemas.payments import PaymentPayload, PaymentRequirements  # noqa: E402

from x402_v2.provider import X402V2Provider, X402VerificationError  # noqa: E402

USDC = "0x2222222222222222222222222222222222222222"
SALE = "0x6666666666666666666666666666666666666666"
ATTACKER = "0x9999999999999999999999999999999999999999"
NONCE = "0x" + "11" * 32
# Fixture 不再用 2100 年常數掩蓋 max-timeout cap：default window 為
# 「sign 當下 + FIXTURE_VALIDITY_SECONDS」，落在 6s 最小剩餘窗口與 3600s cap 之間。
FIXTURE_VALIDITY_SECONDS = 300


def make_provider(wallet: str = SALE) -> X402V2Provider:
    return X402V2Provider(
        wallet_address=wallet,
        chain_id=31337,
        accepted_tokens=[USDC],
        network=None,  # legacy non-CAIP config → canonicalized to eip155:31337
    )


def build_requirement(provider: X402V2Provider, amount: str = "1000000") -> PaymentRequirements:
    return provider.create_payment_requirement(
        amount=amount,
        endpoint="/api/paid/resource",
        token=USDC,
        scheme="exact",
    )


def sign_authorization(
    acct,
    from_addr: str,
    to: str,
    value: str,
    nonce: str,
    requirement: PaymentRequirements,
    chain_id: int = 31337,
    valid_before: int | None = None,
) -> str:
    """Sign a TransferWithAuthorization exactly like the reference client / provider hash it."""
    if valid_before is None:
        valid_before = int(time.time()) + FIXTURE_VALIDITY_SECONDS
    auth = ExactEIP3009Authorization(
        from_address=from_addr,
        to=to,
        value=value,
        valid_after="0",
        valid_before=str(valid_before),
        nonce=nonce,
    )
    name = requirement.extra["name"]
    version = requirement.extra["version"]
    domain, types, primary_type, message = build_typed_data_for_signing(
        auth, chain_id, requirement.asset, name, version
    )
    typed_data = {
        "types": {
            k: [{"name": f["name"], "type": f["type"]} for f in fields]
            for k, fields in types.items()
        },
        "primaryType": primary_type,
        "domain": {
            "name": name,
            "version": version,
            "chainId": chain_id,
            "verifyingContract": requirement.asset,
        },
        "message": dict(message),
    }
    typed_data["message"]["nonce"] = "0x" + typed_data["message"]["nonce"].hex()
    typed_data["types"]["EIP712Domain"] = [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ]
    signed = acct.sign_typed_data(full_message=typed_data)
    return "0x" + signed.signature.hex()


def build_payload(
    acct,
    requirement: PaymentRequirements,
    *,
    from_addr: str | None = None,
    to: str | None = None,
    value: str = "1000000",
    nonce: str = NONCE,
    valid_before: int | None = None,
    signature: str | None = None,
) -> PaymentPayload:
    """Build an official PaymentPayload. Signature defaults to a real one for ``from_addr``.

    ``valid_before`` 未指定時取 sign 當下 + FIXTURE_VALIDITY_SECONDS（落在 cap 內）；
    指定時簽名與 payload 都以同一 deadline 建構（供 window boundary regression 使用）。
    """
    from_addr = from_addr or acct.address
    to = to or requirement.pay_to
    if valid_before is None:
        valid_before = int(time.time()) + FIXTURE_VALIDITY_SECONDS
    if signature is None:
        signature = sign_authorization(
            acct, from_addr, to, value, nonce, requirement, valid_before=valid_before
        )
    return PaymentPayload(
        x402_version=2,
        # Deep copy so callers may mutate ``accepted`` (mismatch tests) without
        # corrupting the server-side requirement object.
        accepted=requirement.model_copy(deep=True),
        payload={
            "authorization": {
                "from": from_addr,
                "to": to,
                "value": value,
                "validAfter": 0,
                "validBefore": valid_before,
                "nonce": nonce,
            },
            "signature": signature,
        },
    )


class X402WireVerificationTest(unittest.TestCase):
    """X402-WIRE：官方 v2 wire 與 EIP-712 驗證（真實 provider、真實簽名）。"""

    def test_valid_signature_returns_verified_payment(self) -> None:
        """真實簽名 → verify_payment 回傳 VerifiedPayment，payer 為簽名者。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)

        verified = provider.verify_payment(payload, requirement)

        self.assertIsNotNone(verified)
        self.assertEqual(verified.payer, acct.address)
        self.assertEqual(verified.recipient, SALE)
        self.assertEqual(verified.asset, USDC)
        self.assertEqual(verified.atomic_amount, "1000000")
        self.assertEqual(verified.network, "eip155:31337")
        self.assertEqual(verified.nonce, NONCE)
        self.assertEqual(verified.authorization_identifier, NONCE)

    def test_invalid_signature_rejected(self) -> None:
        """壞簽名 → X402VerificationError（invalid_signature）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement, signature="0x" + "ab" * 65)

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "invalid_signature")

    def test_signature_not_recovering_to_claimed_payer_rejected(self) -> None:
        """payload 宣稱的 from 與實際簽名者不同（crypto identity mismatch）→ 拒絕。

        From 固定為 real signer；把 authorization.from 改成 ATTACKER 後，簽名無法
        還原到 ATTACKER → invalid_signature。這驗證身份由 crypto 決定，而非 payload。
        """
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)
        # Tamper the claimed payer without re-signing.
        payload.payload["authorization"]["from"] = ATTACKER

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "invalid_signature")

    def test_requirement_mismatch_chain_id_rejected(self) -> None:
        """accepted.network 為不同 CAIP-2 chain id → 拒絕（requirement_mismatch）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)
        payload.accepted.network = "eip155:1"  # wrong chain

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "requirement_mismatch")

    def test_requirement_mismatch_amount_rejected(self) -> None:
        """accepted.amount 與 server requirement 不同 → 拒絕（requirement_mismatch）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider, amount="1000000")
        payload = build_payload(acct, requirement)
        payload.accepted.amount = "999999"

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "requirement_mismatch")

    def test_requirement_mismatch_pay_to_rejected(self) -> None:
        """accepted.pay_to 與 server requirement 不同 → 拒絕（requirement_mismatch）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)
        payload.accepted.pay_to = ATTACKER

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "requirement_mismatch")

    def test_requirement_mismatch_scheme_rejected(self) -> None:
        """accepted.scheme 與 server requirement 不同 → 拒絕（requirement_mismatch）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)
        payload.accepted.scheme = "upto"

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "requirement_mismatch")

    def test_missing_signature_rejected(self) -> None:
        """缺 signature → 拒絕（invalid_signature）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)
        payload.payload["signature"] = None

        with self.assertRaises(X402VerificationError) as ctx:
            provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "invalid_signature")

    # ------------------------------------------- X402-EIP3009-BOUND-01 window

    def test_over_max_timeout_deadline_rejected(self) -> None:
        """valid_before 超過 now + max_timeout_seconds（now+3601）→ invalid_time_window。

        X402-EIP3009-BOUND-01：cap 檢查位於簽名驗證之前，因此即使 over-cap
        deadline 有真實簽名背書也必須拒絕。
        """
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        fixed_now = 1_800_000_000
        over_cap = fixed_now + provider.max_timeout_seconds + 1
        payload = build_payload(acct, requirement, valid_before=over_cap)

        with patch("x402_v2.provider.time.time", return_value=fixed_now):
            with self.assertRaises(X402VerificationError) as ctx:
                provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "invalid_time_window")

    def test_exact_max_timeout_deadline_accepted(self) -> None:
        """valid_before 恰好等於 now + max_timeout_seconds（now+3600）→ 接受。

        cap 為 inclusive：等於上限的 deadline 屬合法 x402 exact window，
        走完整真實簽名 pipeline。
        """
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        fixed_now = 1_800_000_000
        exact_cap = fixed_now + provider.max_timeout_seconds
        payload = build_payload(acct, requirement, valid_before=exact_cap)

        with patch("x402_v2.provider.time.time", return_value=fixed_now):
            verified = provider.verify_payment(payload, requirement)

        self.assertEqual(verified.payer, acct.address)

    def test_deadline_in_past_rejected(self) -> None:
        """valid_before 已過期（now-1）→ invalid_time_window（既有過去窗檢查不變）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        fixed_now = 1_800_000_000
        payload = build_payload(acct, requirement, valid_before=fixed_now - 1)

        with patch("x402_v2.provider.time.time", return_value=fixed_now):
            with self.assertRaises(X402VerificationError) as ctx:
                provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "invalid_time_window")

    def test_below_minimum_remaining_window_rejected(self) -> None:
        """剩餘窗口不足 6 秒（now+5）→ invalid_time_window（既有最小窗口檢查不變）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        fixed_now = 1_800_000_000
        payload = build_payload(acct, requirement, valid_before=fixed_now + 5)

        with patch("x402_v2.provider.time.time", return_value=fixed_now):
            with self.assertRaises(X402VerificationError) as ctx:
                provider.verify_payment(payload, requirement)
        self.assertEqual(ctx.exception.code, "invalid_time_window")

    def test_exact_minimum_remaining_window_accepted(self) -> None:
        """剩餘窗口恰好 6 秒（now+6）→ 接受（既有邊界語意不變）。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        fixed_now = 1_800_000_000
        payload = build_payload(acct, requirement, valid_before=fixed_now + 6)

        with patch("x402_v2.provider.time.time", return_value=fixed_now):
            verified = provider.verify_payment(payload, requirement)

        self.assertEqual(verified.payer, acct.address)

    # -------------------------------------------------- migrated cache-bypass test

    def test_verification_always_runs_real_crypto_no_cache(self) -> None:
        """X402-WIRE-NO-CACHE：每個驗證都重新執行 crypto，不依賴 signature+nonce 快取。

        繼承原本 test_provider_does_not_cache_verification_across_identity 的意圖：
        Provider 不得在 signature+nonce 相同但 from 改變時命中舊驗證結果。
        新實作無快取；此測試以真實簽名驗證：第一個 payload 通過，改 from（不重簽）
        必須被拒，證明身份由 crypto 決定且每次重跑。
        """
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)

        # First: legitimate signature for the real signer → accepted.
        first = build_payload(acct, requirement)
        verified = provider.verify_payment(first, requirement)
        self.assertEqual(verified.payer, acct.address)

        # Second: same signature/nonce but from=ATTACKER → must be rejected, not cached.
        tampered = build_payload(acct, requirement)
        tampered.payload["authorization"]["from"] = ATTACKER
        with self.assertRaises(X402VerificationError):
            provider.verify_payment(tampered, requirement)


class X402WireHeaderTest(unittest.TestCase):
    """X402-WIRE-HEADER：official base64 header roundtrip 與 canon 網路解析。"""

    def test_payment_signature_header_roundtrip(self) -> None:
        """base64 PAYMENT-SIGNATURE header 可由 provider 解回等價 PaymentPayload。"""
        acct = Account.create()
        provider = make_provider()
        requirement = build_requirement(provider)
        payload = build_payload(acct, requirement)
        header = encode_payment_signature_header(payload)

        decoded = provider.parse_payment_signature(header)

        self.assertEqual(decoded, payload)
        self.assertEqual(decoded.x402_version, 2)

    def test_non_caip_config_network_canonicalized(self) -> None:
        """legacy 非 CAIP network 設定 → 產生 eip155:<chain_id> requirement。"""
        provider = X402V2Provider(
            wallet_address=SALE,
            chain_id=31337,
            accepted_tokens=[USDC],
            network="hardhat",
        )
        self.assertEqual(provider.network, "eip155:31337")

    def test_mismatched_caip_config_network_fails_closed(self) -> None:
        """已 CAIP 但 chain id 與 chain_id 不符的設定 → 建構時即失敗。"""
        with self.assertRaises(X402VerificationError):
            X402V2Provider(
                wallet_address=SALE,
                chain_id=31337,
                accepted_tokens=[USDC],
                network="eip155:1",
            )


if __name__ == "__main__":
    unittest.main()
