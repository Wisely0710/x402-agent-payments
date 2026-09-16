"""x402 MCP server — the MCP tool layer (official SDK first).

Exposes the tools through FastMCP's high-level API so that any MCP client
(Claude Code, Codex, ...) can call "fetch a paid resource / service".
Transport: stdio (the MCP default).

Note: a production deployment should not hold the private key at all. This
skeleton loads an agent key from the environment for local demos, which makes
it suitable for learning environments only.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import PaidResourceClient
from .signer import load_account_from_env

mcp = FastMCP("x402-gateway")

_client: PaidResourceClient | None = None


def _get_client() -> PaidResourceClient:
    global _client
    if _client is None:
        signer, _account = load_account_from_env()
        _client = PaidResourceClient(signer)
    return _client


@mcp.tool()
def fetch_paid_resource(
    resource_url: str,
    max_amount_wei: int,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> str:
    """Fetch a resource protected by x402 payments (pay-per-request).

    Args:
        resource_url: URL of the resource / service that requires an x402
            payment.
        max_amount_wei: highest amount the caller is willing to pay, in the
            smallest unit; anything above is refused.
        method: HTTP method. GET for plain paid resources; POST when the
            resource expects a JSON body.
        json_body: JSON body for POST requests; sent unchanged.

    Returns:
        The resource content, or the verificationResult returned by the
        resource server once payment has been accepted.
    """
    return _get_client().fetch(
        resource_url,
        max_amount_wei=max_amount_wei,
        method=method,
        json_body=json_body,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
