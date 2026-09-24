# x402-v2-verifier

Verification-only [x402](https://docs.x402.org) **v2** provider for EVM networks: a resource server uses it to issue 402 challenges and to verify the client's `PAYMENT-SIGNATURE`.

Built on the official [`x402`](https://pypi.org/project/x402/) Python SDK. This package adds the requirement/header plumbing and a stable, SDK-free `VerifiedPayment` DTO, so application code never has to touch SDK types.

## Install

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Usage

```python
from x402_v2.provider import X402V2Provider, X402VerificationError

provider = X402V2Provider(
    wallet_address="0xRecipient…",       # payTo
    chain_id=84532,                       # CAIP-2 eip155:84532 (Base Sepolia)
    accepted_tokens=["0xUSDC…"],
    token_name="USD Coin",                # EIP-712 domain
    token_version="2",
)

# 1) challenge: build the 402 response payload and header
requirement = provider.create_payment_requirement(
    amount="1000000",                     # atomic units
    endpoint="/api/paid/resource",
    token="0xUSDC…",
)
required = provider.build_payment_required(
    requirement, resource_url="https://example.com/api/paid/resource"
)
header_value = provider.encode_payment_required(required)   # -> PAYMENT-REQUIRED

# 2) retry: parse and verify the client's signed payment
payload = provider.parse_payment_signature(retry_header)     # <- PAYMENT-SIGNATURE
try:
    verified = provider.verify_payment(payload, requirement)  # -> VerifiedPayment
except X402VerificationError as exc:
    return error_response(exc.code)                          # see table below
```

`VerifiedPayment` carries the verified facts only: `payer`, `recipient`, `asset`, `atomic_amount`, `network` (CAIP-2), `nonce`, `valid_after`, `valid_before`, `authorization_identifier`.

## Error codes

`X402VerificationError.code` is stable and safe to branch on:

| code | raised when | typical next step |
|---|---|---|
| `invalid_version` | payload is not x402 v2 (`x402Version != 2`) | re-challenge with `PAYMENT-REQUIRED` |
| `invalid_signature` | signature missing, malformed, wrong length, or EIP-712 verification failed | ask the client to re-sign; do not retry the same payload |
| `invalid_time_window` | authorization is expired / not yet valid, or its deadline exceeds the server max timeout | re-challenge with a fresh window |
| `authorization_mismatch` | the signed authorization's `to` / `value` differ from the server requirement | reject; the client signed terms it was not asked for |
| `requirement_mismatch` | the client's accepted `accepts[]` entry differs from the server requirement: scheme, CAIP-2 network / chain id, asset, amount, or `payTo` | re-challenge; the client picked the wrong entry |
| `network_mismatch` | **configuration** error: the configured `network` CAIP-2 chain id conflicts with `chain_id` — raised when the provider is constructed | fix the server configuration |
| `invalid_requirement` | **configuration** error: the server requirement is missing its EIP-712 token metadata (`extra.name` / `extra.version`), so no domain may be presumed | fix the server configuration; a requirement must carry `extra.name` / `extra.version` (the provider never falls back to its `token_name` / `token_version` defaults to verify) |

## Design notes

- **Verification-only.** No facilitator calls, no transactions, no key custody. Settlement happens afterwards, on the resource owner's terms.
- **Stateless verification.** Signatures are always re-verified with real crypto — there is no signature/nonce cache, so a replayed payload with a different `from` cannot hit a cached success. This is **not replay protection**: the provider consumes no nonce, so the same still-valid signature (same `from`) can be replayed. Consuming the authorization is the job of on-chain settlement (`transferFrom` / a facilitator) or of the resource owner.
- **Fail closed.** Unknown networks, mismatched requirements, and missing token metadata (`invalid_requirement`) all raise instead of degrading.
- **No built-in rate limiting.** The provider does not throttle requests at all; `x402_v2/rate_limiter.py` is a standalone in-memory helper for an application that wants request admission control, and nothing in this package calls it.

## Test layout

| path | scope |
|---|---|
| `x402_v2/tests/` | adapter + DTO mapping |
| `tests/test_x402_wire.py` | official v2 wire, real EIP-712 signatures (valid, tampered, boundary windows) |
| `tests/test_rate_limiter.py` | standalone in-memory rate-limiter helper (unit tests; the provider itself does not rate-limit) |
