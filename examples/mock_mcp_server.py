"""Safe Mock MCP Server for Walled AI demonstrations and testing.

Provides non-destructive, safe tools:
1. get_system_info: Host operating system and runtime metadata.
2. calculate_sum: Simple mathematical addition.
3. read_demo_file: Read-only access confined strictly to demo sandbox.
4. create_demo_file: Confined write access strictly inside demo sandbox.

Stdout is reserved exclusively for the MCP JSON-RPC protocol.
Diagnostics and operational logs are routed to sys.stderr.
"""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

from mcp.server import MCPServer

# Dedicated demonstration directory
DEMO_DIR = Path("data/demo_sandbox").resolve()
DEMO_DIR.mkdir(parents=True, exist_ok=True)

# Logging configuration: Strictly to stderr
logger = logging.getLogger("mock_mcp_server")
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [mock_mcp] %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# MCP Server instance
server = MCPServer(name="Safe Mock MCP Server")


def _resolve_safe_path(filename: str) -> Path:
    """Resolve file path and ensure no path traversal outside DEMO_DIR."""
    # Prevent traversal attacks like ../../etc/passwd or C:\Windows
    target = (DEMO_DIR / filename).resolve()
    if not target.is_relative_to(DEMO_DIR):
        raise ValueError(f"Path traversal detected: {filename} is outside demo sandbox")
    return target


@server.tool(
    name="get_system_info",
    description="Get safe operating system and Python environment information.",
)
def get_system_info() -> dict[str, str]:
    """Return non-sensitive system environment details."""
    logger.info("Executing get_system_info")
    return {
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
    }


@server.tool(
    name="calculate_sum",
    description="Calculate the arithmetic sum of two numbers.",
)
def calculate_sum(a: float, b: float) -> float:
    """Return the arithmetic sum of two floating point numbers."""
    logger.info("Executing calculate_sum with a=%s, b=%s", a, b)
    return float(a + b)


@server.tool(
    name="read_demo_file",
    description="Read text contents of a file inside the demo sandbox.",
)
def read_demo_file(filename: str) -> str:
    """Safely read a file from the demo directory."""
    logger.info("Executing read_demo_file for %s", filename)
    safe_path = _resolve_safe_path(filename)
    if not safe_path.exists():
        raise FileNotFoundError(f"File not found: {filename}")
    return safe_path.read_text(encoding="utf-8")


@server.tool(
    name="create_demo_file",
    description="Create a new text file inside the demo sandbox.",
)
def create_demo_file(filename: str, content: str) -> str:
    """Safely create or overwrite a file in the demo directory."""
    logger.info("Executing create_demo_file for %s (%d bytes)", filename, len(content))
    safe_path = _resolve_safe_path(filename)
    safe_path.write_text(content, encoding="utf-8")
    return f"Successfully created {safe_path.name} ({len(content)} bytes)"


def main() -> None:
    """Main server entrypoint."""
    logger.info("Starting Safe Mock MCP Server on stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
