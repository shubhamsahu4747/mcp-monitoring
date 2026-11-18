"""Remote MCP Gateway Server using MCP Streamable HTTP transport.

Bridges remote MCP clients (e.g. Claude Web) with upstream MCP servers,
routing all tool invocations through the Walled AI authorization engine
and Desktop Agent WebSocket broker.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.authorization.manager import AuthorizationManager, authorization_manager
from app.gateway.upstream_manager import UpstreamManager, upstream_manager
from app.interception.request_interceptor import RequestInterceptor, request_interceptor
from app.models.requests import AuthorizationRequest
from app.remote.websocket_broker import WebSocketAgentBroker, agent_broker

logger = logging.getLogger(__name__)


class RemoteMCPGatewayServer:
    """Streamable HTTP MCP Server enforcing Desktop Agent human authorization."""

    def __init__(
        self,
        upstream_mgr: Optional[UpstreamManager] = None,
        auth_mgr: Optional[AuthorizationManager] = None,
        interceptor: Optional[RequestInterceptor] = None,
        broker: Optional[WebSocketAgentBroker] = None,
        server_name: str = "Walled AI Remote MCP Gateway",
    ) -> None:
        self.upstream_mgr = upstream_mgr or upstream_manager
        self.auth_mgr = auth_mgr or authorization_manager
        self.interceptor = interceptor or request_interceptor
        self.broker = broker or agent_broker
        self.server_name = server_name

        # Connect authorization notifications to the WebSocket agent broker
        self.auth_mgr.set_notification_hook(self._broker_notification_hook)

        # Official MCP lowlevel Server
        self.server = Server(
            name=self.server_name,
            version="1.0.0",
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
        )

        # Official Streamable HTTP Session Manager
        self.session_manager = StreamableHTTPSessionManager(
            app=self.server,
            session_idle_timeout=1800,
            max_sessions=1000,
        )

    async def _broker_notification_hook(self, request: AuthorizationRequest) -> None:
        """Route intercepted tool requests to the connected Desktop Agent via WebSocket."""

        async def _forward_and_resolve() -> None:
            try:
                decision = await self.broker.request_authorization(
                    request=request,
                    timeout_seconds=self.auth_mgr.timeout_seconds,
                )
                await self.auth_mgr.submit_decision(decision)
            except Exception as e:
                logger.error(
                    "Error routing authorization request %s through broker: %s",
                    request.request_id,
                    e,
                )

        asyncio.create_task(_forward_and_resolve())

    async def _handle_list_tools(
        self,
        ctx: Any,
        params: types.PaginatedRequestParams | None = None,
    ) -> types.ListToolsResult:
        """Fetch discovered tools from upstream MCP server."""
        logger.debug("Received remote tools/list request")
        try:
            tools = await self.upstream_mgr.list_tools()
            logger.info(
                "Discovered %d tools from upstream '%s' for remote client",
                len(tools),
                self.upstream_mgr.server_name,
            )
            return types.ListToolsResult(tools=tools)
        except Exception as e:
            logger.error("Failed to list tools from upstream: %s", e)
            return types.ListToolsResult(tools=[])

    async def _handle_call_tool(
        self,
        ctx: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """Intercept remote tools/call and route through authorization engine."""
        # Extract remote client and session information if available
        client_id: Optional[str] = None
        session_id: Optional[str] = None

        if hasattr(ctx, "request") and ctx.request is not None:
            client_ip = getattr(getattr(ctx.request, "client", None), "host", None)
            client_id = client_ip or "remote-client"
            headers = getattr(ctx.request, "headers", {})
            session_id = headers.get("mcp-session-id") or headers.get("x-session-id")

        if not session_id and hasattr(ctx, "session") and ctx.session is not None:
            session_id = str(id(ctx.session))

        logger.info(
            "Received remote tools/call for '%s' (client=%s, session=%s)",
            params.name,
            client_id,
            session_id,
        )

        return await self.interceptor.intercept_call_tool(
            name=params.name,
            arguments=params.arguments,
            client_id=client_id,
            session_id=session_id,
        )

    async def start(self) -> None:
        """Initialize upstream connection."""
        logger.info(
            "Starting Remote MCP Gateway Server (Upstream: %s)...",
            self.upstream_mgr.server_name,
        )
        try:
            await self.upstream_mgr.ensure_connected()
        except Exception as e:
            logger.warning("Upstream MCP server connection deferred: %s", e)

    async def stop(self) -> None:
        """Shut down server and close active upstream connections."""
        logger.info("Stopping Remote MCP Gateway Server...")
        try:
            await self.upstream_mgr.stop()
        except Exception as e:
            logger.warning("Error stopping upstream manager: %s", e)
        try:
            await self.auth_mgr.shutdown()
        except Exception as e:
            logger.warning("Error shutting down authorization manager: %s", e)
