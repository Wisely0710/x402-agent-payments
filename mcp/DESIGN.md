# Design notes

The agent (an MCP client) wants to buy data or an API call per request, and has
a wallet instead of an account. This component sits between the agent and any
x402 resource server and completes the payment handshake on the agent's behalf.

## Roles

| Role | Who | Note |
|---|---|---|
| Agent | Claude Code, Codex, ... | the MCP **client** |
| This server | `src/x402_mcp` | an MCP **server**, but the x402 **buyer** side |
| Resource server | any x402 seller | quotes with 402, verifies, delivers |
| Facilitator | optional third party | settles on chain; unused here |

## Data flow

```
server.py  fetch_paid_resource(url, max_amount_wei, method, json_body)
  └─ client.fetch(url, max_amount_wei, ...)
       ├─ _request           -> 402 + PAYMENT-REQUIRED
       ├─ decode_payment_required_header (official) -> PaymentRequired
       ├─ _select_requirements -> accepts[0]         # the policy seam
       ├─ budget check       -> above max_amount_wei: ValueError, no retry
       ├─ ExactEvmScheme.create_payment_payload(req) -> {authorization, signature}
       ├─ PaymentPayload(payload=..., accepted=req)  # official model
       ├─ encode_payment_signature_header -> PAYMENT-SIGNATURE value
       └─ _request (retry)   -> 200 content / verificationResult
```

**The signature moves no money.** It is a wallet-signed promise to pay a given
amount to a given address before a deadline; settlement is a separate, later
step — which is why every test here runs without a chain.

## Wire format (x402 v2, plain HTTP)

| Header | Direction | Content |
|---|---|---|
| `PAYMENT-REQUIRED` | seller -> buyer | `PaymentRequired`: error, resource, `accepts[]` |
| `PAYMENT-SIGNATURE` | buyer -> seller | `PaymentPayload`: `payload` (authorization + signature) + `accepted` |
| `PAYMENT-RESPONSE` | seller -> buyer | settlement result (unused here) |

```
PaymentRequirements {
  scheme:  "exact"          # fixed price; upto / batch-settlement also exist
  network: "eip155:31337"   # CAIP-2 chain id (any EVM chain, hardhat included)
  asset:   "0x..."          # token contract (EIP-3009 or Permit2)
  amount:  "100"            # smallest unit, string
  pay_to:  "0x..."          # payee
  extra:   {name, version}  # EIP-712 domain; required for custom tokens
}
```

Signed content: EIP-3009 `TransferWithAuthorization` (`from`, `to`, `value`,
`validAfter`, `validBefore`, `nonce`) plus the EIP-712 domain; the nonce comes
from the official SDK, so signing is fully offline.

## Tool interface

| Tool | Arguments | Returns |
|---|---|---|
| `fetch_paid_resource` | `resource_url`, `max_amount_wei`, `method="GET"`, `json_body=None` | the resource body, or the `verificationResult` of a paid request |

`max_amount_wei` is the spending cap the agent declares: anything above it is refused before a second request is sent. The signer is loaded lazily, so importing the server never requires a private key.

## Seller side (examples/mock_seller.py)

1. no `PAYMENT-SIGNATURE` -> 402 + `PaymentRequired` quote
2. with it -> decode the `PaymentPayload`
3. check that the buyer's `accepted` offer equals the seller's quote
4. rebuild the EIP-712 typed data (official `build_typed_data_for_signing`), hash it and verify the EOA signature
5. deliver 200, or answer 400 with the reason

## Design decisions

- **Official SDK first**: header names, codecs, typed-data structures, nonce generation and payload models all come from the `x402` package — hand-writing the wire format only introduces drift.
- **No high-level seller framework**: `x402ResourceServer` needs facilitator-provided supported kinds, so the seller is assembled from the low-level official components instead.
- **The remaining seam is policy**: which `accepts[]` entry to take, the budget rule, and where the key lives.
