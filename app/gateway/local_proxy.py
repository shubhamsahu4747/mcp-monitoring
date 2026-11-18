"""Local stdio MCP proxy server.

Acts as a genuine MCP server to downstream clients (e.g. Claude Desktop)
over standard I/O streams while forwarding approved tool calls to the
configured upstream MCP server.
Stdout is strictly preserved for JSON-RPC MCP messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from app.config import configure_logging, settings
from app.gateway.ipc_client import IPCAuthorizationClient
from app.gateway.upstream_manager import UpstreamManager, upstream_manager
from app.interception.request_interceptor import RequestInterceptor, request_interceptor
from app.models.requests import AuthorizationRequest

logger = logging.getLogger(__name__)


class LocalMCPProxy:
    """Stdio MCP Security Proxy intercepting downstream requests."""

    def __init__(
        self,
        upstream_mgr: UpstreamManager | None = None,
        interceptor: RequestInterceptor | None = None,
        server_name: str = "Walled AI MCP Security Gateway",
        enable_desktop_ipc: bool = True,
    ) -> None:
        self.upstream_mgr = upstream_mgr or upstream_manager
        self.interceptor = interceptor or request_interceptor
        self.server_name = server_name
        self.enable_desktop_ipc = enable_desktop_ipc

        # IPC client communicating with running WalledAIDesktopApp
        self.ipc_client: IPCAuthorizationClient | None = None
        if self.enable_desktop_ipc and self.interceptor.auth_mgr._notification_hook is None:
            self.ipc_client = IPCAuthorizationClient(
                host=settings.remote_host,
                port=settings.remote_port,
                timeout_seconds=self.interceptor.auth_mgr.timeout_seconds,
            )
            self.interceptor.auth_mgr.set_notification_hook(self._ipc_notification_hook)

        # Create low-level official MCP server
        self.server = Server(
            name=self.server_name,
            version="1.0.0",
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
        )

    async def _ipc_notification_hook(self, request: AuthorizationRequest) -> None:
        """Forward intercepted tool call to running Desktop Agent over local IPC."""
        if not self.ipc_client:
            return

        async def _forward_and_resolve() -> None:
            try:
                assert self.ipc_client is not None
                decision = await self.ipc_client.request_authorization(request)
                await self.interceptor.auth_mgr.submit_decision(decision)
            except Exception as e:
                logger.error("IPC forward error for request %s: %s", request.request_id, e)

        asyncio.create_task(_forward_and_resolve())

    async def _handle_list_tools(
        self,
        ctx: Any,
        params: types.PaginatedRequestParams | None = None,
    ) -> types.ListToolsResult:
        """Forward tools/list request to upstream and return discovered tools."""
        logger.debug("Received downstream tools/list request")
        try:
            tools = await self.upstream_mgr.list_tools()
            logger.info(
                "Discovered %d tools from upstream '%s'",
                len(tools),
                self.upstream_mgr.server_name,
            )
            return types.ListToolsResult(tools=tools)
        except Exception as e:
            logger.error("Failed to fetch tools from upstream: %s", e)
            return types.ListToolsResult(tools=[])

    async def _handle_call_tool(
        self,
        ctx: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """Intercept downstream tools/call and route through authorization engine."""
        logger.info("Received downstream tools/call request for tool: %s", params.name)
        return await self.interceptor.intercept_call_tool(
            name=params.name,
            arguments=params.arguments,
        )

    async def run(self) -> None:
        """Run the stdio MCP proxy loop on stdin/stdout streams."""
        # Ensure diagnostics write exclusively to stderr
        configure_logging(settings.log_level)
        logger.info(
            "Starting Walled AI MCP Stdio Proxy (Upstream: %s)...",
            self.upstream_mgr.server_name,
        )

        try:
            # Connect upstream in root proxy task to preserve anyio task-scope invariants
            try:
                await self.upstream_mgr.ensure_connected()
            except Exception as e:
                logger.warning("Upstream pre-connection deferred: %s", e)

            async with stdio_server() as (read_stream, write_stream):
                init_options = self.server.create_initialization_options()
                await self.server.run(
                    read_stream=read_stream,
                    write_stream=write_stream,
                    initialization_options=init_options,
                    raise_exceptions=False,
                )
        except Exception as e:
            logger.error("Stdio proxy encountered unexpected error: %s", e)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Clean up upstream connection and active authorization states."""
        logger.info("Stopping Walled AI Local MCP Proxy...")
        try:
            await self.upstream_mgr.stop()
        except Exception as e:
            logger.warning("Error stopping upstream manager: %s", e)

        try:
            await self.interceptor.auth_mgr.shutdown()
        except Exception as e:
            logger.warning("Error shutting down authorization manager: %s", e)


# Default proxy instance
local_proxy = LocalMCPProxy()
