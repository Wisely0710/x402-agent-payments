# x402-agent-payments

An [x402](https://docs.x402.org) v2 payment stack for EVM networks: verify payments on the server, sign them in the browser, and let an AI agent pay for a resource over MCP.

| Component | Language | What it is |
|---|---|---|
| [`verifier/`](verifier/) | Python | builds the 402 challenge (`PAYMENT-REQUIRED`) and verifies the client's EIP-712 / EIP-3009 `PAYMENT-SIGNATURE` |
| [`client/`](client/) | TypeScript | browser client: 402 → EIP-3009 authorization → retry with `PAYMENT-SIGNATURE` |
| [`mcp/`](mcp/) | Python | MCP server that exposes paid resources to an agent, signing with the official x402 SDK |

## Scope

Verification-only: nothing here custodies keys, calls a facilitator, or sends a transaction. Settlement is the resource owner's step — either the payer wallet calls the resource contract itself, or a facilitator settles the same signed authorization where the token supports EIP-3009.

Wire: x402 v2 (`PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`), `exact` scheme, CAIP-2 `eip155:*` networks.

## Quickstart

```bash
# verifier — Python 3.11+
cd verifier
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

```bash
# client — TypeScript
cd client
yarn install
yarn check-types
```

```bash
# mcp server — Python 3.11+
cd mcp
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Status

Early. CI, a full quickstart, and an end-to-end demo (mock seller + local facilitator) are in progress.

## License

MIT — see [LICENSE](LICENSE).
