"""Upstream MCP server connection lifecycle manager.

Spawns the upstream MCP server as a subprocess, manages the stdio client session,
negotiates capabilities, forwards calls, and handles graceful teardown.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.config import settings

logger = logging.getLogger(__name__)


class UpstreamError(Exception):
    """Base exception for upstream server communication errors."""


class UpstreamConnectionError(UpstreamError):
    """Raised when establishing an upstream stdio session fails or the process terminates."""


class UpstreamManager:
    """Manages an active stdio client session to an upstream MCP server."""

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | str | None = None,
        server_name: str | None = None,
        env: Dict[str, str] | None = None,
    ) -> None:
        self.server_name = server_name or settings.upstream_server_name
        self.command = command or settings.default_upstream_command

        # Resolve 'python' to virtual environment python executable
        if self.command in ("python", "python3"):
            self.command = sys.executable

        raw_args = args if args is not None else settings.default_upstream_args
        if isinstance(raw_args, str):
            tokens = shlex.split(raw_args, posix=(sys.platform != "win32"))
            self.args = [t.strip("\"'") for t in tokens]
        else:
            self.args = list(raw_args)

        self.env = env
        self._lock = asyncio.Lock()
        self._exit_stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self._is_running = False

    @property
    def is_connected(self) -> bool:
        """Check if an active upstream session is established."""
        return self._session is not None and self._is_running

    async def ensure_connected(self) -> ClientSession:
        """Connect to the upstream MCP server if not already connected.

        Returns:
            The initialized ClientSession connected to the upstream server.

        Raises:
            UpstreamConnectionError: If launching the process or initializing fails.
        """
        if self.is_connected and self._session is not None:
            return self._session

        async with self._lock:
            # Double check inside lock to prevent duplicate subprocesses
            if self.is_connected and self._session is not None:
                return self._session

            logger.info(
                "Establishing upstream connection to '%s' (%s %s)...",
                self.server_name,
                self.command,
                " ".join(self.args),
            )

            server_params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env,
            )

            exit_stack = AsyncExitStack()
            try:
                read_stream, write_stream = await exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                session = await exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                # Perform MCP initialization and capability negotiation
                await session.initialize()
                logger.info(
                    "Connected to upstream '%s'. Protocol version: %s",
                    self.server_name,
                    session.protocol_version,
                )

                self._exit_stack = exit_stack
                self._session = session
                self._is_running = True
                return session

            except Exception as e:
                await exit_stack.aclose()
                self._exit_stack = None
                self._session = None
                self._is_running = False
                logger.error("Failed to connect to upstream '%s': %s", self.server_name, e)
                raise UpstreamConnectionError(
                    f"Could not connect to upstream MCP server '{self.server_name}': {e}"
                ) from e

    async def list_tools(self) -> List[types.Tool]:
        """Fetch available tools from the upstream server.

        Returns:
            List of actual Tool definitions discovered from upstream.

        Raises:
            UpstreamConnectionError: If communication with upstream fails.
        """
        session = await self.ensure_connected()
        try:
            result = await session.list_tools()
            return result.tools
        except Exception as e:
            logger.error("Upstream list_tools failed for '%s': %s", self.server_name, e)
            await self.stop()
            raise UpstreamConnectionError(f"Upstream list_tools failed: {e}") from e

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any] | None = None,
    ) -> types.CallToolResult:
        """Forward an approved tool invocation to the upstream server.

        Args:
            name: Exact tool name.
            arguments: Original untouched argument dictionary.

        Returns:
            The raw CallToolResult returned by the upstream server.

        Raises:
            UpstreamConnectionError: If the upstream call fails or crashes.
        """
        session = await self.ensure_connected()
        try:
            result = await session.call_tool(name=name, arguments=arguments)
            return result
        except Exception as e:
            logger.error("Upstream tool execution failed for '%s': %s", name, e)
            await self.stop()
            raise UpstreamConnectionError(
                f"Upstream execution of tool '{name}' failed: {e}"
            ) from e

    async def stop(self) -> None:
        """Cleanly close the upstream session and terminate the subprocess."""
        async with self._lock:
            if self._exit_stack:
                logger.info("Stopping upstream connection to '%s'...", self.server_name)
                try:
                    await self._exit_stack.aclose()
                except Exception as e:
                    logger.warning("Error during upstream shutdown: %s", e)
                finally:
                    self._exit_stack = None
                    self._session = None
                    self._is_running = False


# Default upstream manager instance
upstream_manager = UpstreamManager()
