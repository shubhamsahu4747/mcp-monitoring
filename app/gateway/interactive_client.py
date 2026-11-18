"""Interactive Real MCP Client using official MCP Python SDK.

Acts as a downstream MCP client (simulating Claude Desktop) connecting over
standard I/O to the Walled AI Local MCP Proxy.
Used to demonstrate real end-to-end tool interception and authorization.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


async def run_client_demo(
    tool_name: str = "calculate_sum",
    arguments: Dict[str, Any] | None = None,
) -> None:
    """Run an authentic MCP client session interacting with Walled AI Local Proxy.

    Args:
        tool_name: Name of tool to call.
        arguments: Arguments dictionary for the tool.
    """
    if arguments is None:
        arguments = {"a": 25.0, "b": 75.0}

    python_exe = str(Path(sys.executable).resolve())
    mock_server = str(Path("examples/mock_mcp_server.py").resolve())

    server_params = StdioServerParameters(
        command=python_exe,
        args=[
            "-m",
            "app.main",
            "local-proxy",
            "--upstream-cmd",
            python_exe,
            "--upstream-args",
            f'"{mock_server}"',
            "--server-name",
            "Safe Mock MCP Server",
        ],
        env=None,
    )

    sys.stdout.write("\n============================================================\n")
    sys.stdout.write("  WALLED AI - AUTHENTIC MCP CLIENT TEST RUNNER\n")
    sys.stdout.write("============================================================\n")
    sys.stdout.write("Connecting to Walled AI Local Proxy stdio transport...\n")
    sys.stdout.write("Upstream Server Target: Safe Mock MCP Server\n\n")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # 1. Initialize MCP Protocol
            sys.stdout.write("[1/3] Initializing MCP protocol handshake with gateway...\n")
            init_result = await session.initialize()
            sys.stdout.write(
                f"      Connected to Gateway: {init_result.server_info.name} "
                f"(v{init_result.server_info.version})\n\n"
            )

            # 2. Discover Tools from Gateway
            sys.stdout.write("[2/3] Querying gateway for available tools (tools/list)...\n")
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            tools_summary = ", ".join(tool_names)
            sys.stdout.write(f"      Discovered {len(tool_names)} tools: {tools_summary}\n\n")

            # 3. Intercepted Tool Invocation
            sys.stdout.write(f"[3/3] Invoking tool '{tool_name}' with arguments: {arguments}\n")
            sys.stdout.write("      --> REQUEST INTERCEPTED BY WALLED AI GATEWAY <--\n")
            sys.stdout.write("      [!] Please review the authorization dialog on your desktop.\n")
            sys.stdout.write("      [!] Waiting for user decision (Allow / Block)...\n\n")

            call_result = await session.call_tool(tool_name, arguments)

            sys.stdout.write("------------------------------------------------------------\n")
            sys.stdout.write("GATEWAY RESPONSE RECEIVED:\n")
            sys.stdout.write(f"Status is_error : {call_result.is_error}\n")
            for item in call_result.content:
                if hasattr(item, "text"):
                    sys.stdout.write(f"Output text     : {item.text}\n")
            sys.stdout.write("------------------------------------------------------------\n")
            if not call_result.is_error:
                sys.stdout.write("RESULT: Tool execution APPROVED and completed successfully!\n")
            else:
                sys.stdout.write("RESULT: Tool execution BLOCKED or REJECTED by security policy!\n")
            sys.stdout.write("============================================================\n\n")


def main() -> int:
    """CLI runner for interactive client demo."""
    asyncio.run(run_client_demo())
    return 0


if __name__ == "__main__":
    sys.exit(main())
