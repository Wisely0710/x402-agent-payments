"""End-to-end test: a real MCP client drives the server over stdio.

`test_e2e_mock_seller.py` drives `PaidResourceClient` directly; this file goes
through the MCP boundary the way an agent does — the official SDK client spawns
`x402_mcp.server` as a subprocess, discovers the tool and calls it, and the
402 -> sign -> verify -> 200 loop runs against the chain-free mock seller. The
seller's 200 body echoes the payer address it recovered from the signature, so
these assertions also prove the signature was verified rather than accepted.

Local, no chain, all official: real stdio transport, real tool schema, real
EIP-3009 signature, real EIP-712 reconstruction on the seller side.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from eth_account import Account
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent, Tool

from examples.mock_seller import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PAYER_KEY = "0x" + "22" * 32  # fixed test key (the mock seller accepts any payer)
PAYER_ADDRESS = Account.from_key(PAYER_KEY).address
TOOL_NAME = "fetch_paid_resource"
SESSION_TIMEOUT = 30.0  # seconds; a hung server must fail the test, not the CI job
PRICE = "100"  # mock seller's price in the smallest unit


@pytest.fixture()
def seller_url() -> Iterator[str]:
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/data"
    server.shutdown()
    server.server_close()


@asynccontextmanager
async def mcp_session() -> AsyncIterator[ClientSession]:
    """Connect to `x402_mcp.server` the way an MCP client does: stdio subprocess."""
    env = dict(os.environ)
    env["X402_AGENT_PRIVATE_KEY"] = PAYER_KEY
    params = StdioServerParameters(command=sys.executable, args=["-m", "x402_mcp.server"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def call_fetch_tool(
    seller_url: str, max_amount_wei: int
) -> tuple[list[Tool], CallToolResult]:
    async with mcp_session() as session:
        tools = (await session.list_tools()).tools
        result = await session.call_tool(
            TOOL_NAME, {"resource_url": seller_url, "max_amount_wei": max_amount_wei}
        )
        return tools, result


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_mcp_client_discovers_the_tool_and_pays(seller_url: str) -> None:
    tools, result = asyncio.run(
        asyncio.wait_for(call_fetch_tool(seller_url, max_amount_wei=1000), SESSION_TIMEOUT)
    )

    assert [tool.name for tool in tools] == [TOOL_NAME]
    schema = tools[0].inputSchema
    assert set(schema.get("required", [])) >= {"resource_url", "max_amount_wei"}

    assert result.isError is False
    body = json.loads(text_of(result))
    assert body["status"] == "paid"
    assert body["payer"] == PAYER_ADDRESS  # the seller recovered this agent's key
    assert body["amount"] == PRICE


def test_mcp_client_refuses_to_pay_above_the_budget(seller_url: str) -> None:
    _, result = asyncio.run(
        asyncio.wait_for(call_fetch_tool(seller_url, max_amount_wei=10), SESSION_TIMEOUT)
    )

    assert result.isError is True
    assert "exceeds max 10" in text_of(result)
