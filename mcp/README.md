# mcp — x402 payment bridge for AI agents

An MCP server that acts as the **payment bridge for an AI agent**: the agent has
an EVM wallet but no account or card, so this component lets it buy a resource
pay-per-request over the x402 protocol.

## Flow

```
1. agent -> MCP tool fetch_paid_resource(url, max_amount_wei)
2. bridge requests the url    -> 402 + PAYMENT-REQUIRED (Base64 JSON, accepts[])
3. bridge checks the quote    -> above max_amount_wei: refuse, no retry
4. bridge signs the promise   -> EIP-3009 / EIP-712 (ExactEvmScheme + EthAccountSigner)
5. bridge retries the request -> PAYMENT-SIGNATURE carrying the PaymentPayload
6. resource server verifies    -> 200 + content (or a verificationResult)
7. bridge returns it to the agent
```

The signature moves no money — it is a wallet-signed promise to pay; settlement
is a separate step owned by the resource server.

## Layout

```
src/x402_mcp/server.py    MCP tool layer (FastMCP): fetch_paid_resource
src/x402_mcp/client.py    402 handling, budget check, signing, retry
src/x402_mcp/signer.py    EthAccountSigner built from X402_AGENT_PRIVATE_KEY
examples/mock_seller.py   tiny seller: quote with 402, verify, deliver
tests/                    wire-level flow tests + end-to-end test (no chain)
```

Header names, codecs, typed-data structures and payload models all come from the
official x402 Python SDK; only policy (offer selection, budget, key management)
stays application code. Details: DESIGN.md.

## Other components

`../verifier/` is the seller side of the same protocol: it issues the 402
challenge, verifies PAYMENT-SIGNATURE (EIP-712 reconstruction + EOA recovery)
and reports the result. `../client/` is a TypeScript client that signs the same
payloads in the browser.

## Run

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest -q   # tests, no chain
X402_AGENT_PRIVATE_KEY=0x... .venv/bin/python -m x402_mcp.server     # MCP server mode (stdio)
```

The MCP SDK and the official x402 SDK are regular dependencies, so the same
install covers both the test suite and server mode.
