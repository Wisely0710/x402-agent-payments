# x402-client

Browser client for the x402 v2 payment flow. It performs the HTTP 402 challenge/response handshake, signs an EIP-3009 `TransferWithAuthorization` with the injected wallet (`window.ethereum`), and retries the original request with a base64 `PAYMENT-SIGNATURE` header. No runtime dependencies.

## Install

```bash
yarn install   # also builds node_modules for check-types
```

## Usage

```ts
import { X402Client, PaymentRequiredError, X402Error } from "x402-client";

const client = new X402Client({
  providerUrl: "http://127.0.0.1:8000", // required, no trailing slash
  maxRetries: 1,                        // optional
  retryDelayMs: 1200,                   // optional
});

const result = await client.payWithX402("/api/payment/x402", {
  userAddress,                          // account that signs the authorization
  body: { resource: "report" },         // request body is caller-owned
});
```

## x402 v2 wire flow

1. `POST providerUrl + endpoint` with the caller's JSON body.
2. On `402`, the challenge is parsed from the base64 `PAYMENT-REQUIRED` header (JSON body fallback), and one requirement with `scheme === "exact"` plus a CAIP-2 `eip155:<chainId>` network is selected.
3. The client builds the EIP-712 `TransferWithAuthorization` typed data (domain `name`/`version` prefer `requirement.extra`, then the `tokenMetadata` option, then USDC defaults) and calls `eth_signTypedData_v4`.
4. The signed `{ x402Version: 2, accepted, payload: { signature, authorization } }` is base64-encoded into `PAYMENT-SIGNATURE` and the request is retried (`maxRetries`, `retryDelayMs`).
5. Non-402 failures reject with `X402Error`; an exhausted retry budget rejects with `PaymentRequiredError` carrying the challenge. Error `.code` is `X402_ERROR` or `X402_PAYMENT_REQUIRED`.

## Checks

```bash
yarn lint          # biome check
yarn check-types   # tsc --noEmit, no emit side effects
yarn test          # builds dist/ and runs the node:test suite
```

`yarn test` drives the built package (`dist/`, the same entry point `exports` publishes) against a
`node:http` mock seller and a stubbed `window.ethereum` wallet: real `fetch`, real header codec, no
network, no chain, no test dependencies. The build emits ESM with explicit `.js` specifiers, so
`dist/` loads in Node as well as in bundlers.
