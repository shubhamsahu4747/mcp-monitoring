"""FastAPI-based local IPC server hosted by WalledAIDesktopApp.

Allows local MCP proxies (such as child processes spawned by Claude Desktop)
to communicate authorization requests to the running desktop UI, triggering
always-on-top popups, updating the dashboard queue, and returning user decisions.
Supports HTTP and WebSocket communication with strict bearer token authentication.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import TYPE_CHECKING, Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.config import settings

if TYPE_CHECKING:
    from app.desktop.application import WalledAIDesktopApp

logger = logging.getLogger(__name__)


class AuthorizeRequestPayload(BaseModel):
    """Payload received from local MCP proxy requesting user authorization."""

    server_name: str = Field(..., description="Name of upstream MCP server")
    tool_name: str = Field(..., description="Name of tool being invoked")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Tool invocation arguments")
    timeout_seconds: Optional[float] = Field(
        default=None, description="Optional custom timeout in seconds"
    )


class AuthorizeResponsePayload(BaseModel):
    """Decision returned to local MCP proxy."""

    request_id: str
    status: str  # "APPROVED", "REJECTED", "TIMED_OUT", "CANCELLED"
    reason: str


class DesktopIPCServer:
    """Embedded FastAPI + Uvicorn server running within the Desktop Qt/asyncio loop."""

    def __init__(
        self,
        desktop_app: WalledAIDesktopApp,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.desktop_app = desktop_app
        self.host = host
        self.port = port
        self.app = FastAPI(title="Walled AI Desktop Agent IPC", version="1.0.0")
        self._server: Optional[uvicorn.Server] = None
        self._serve_task: Optional[asyncio.Task[None]] = None
        self._setup_routes()

    def _verify_token(
        self,
        authorization: Optional[str] = None,
        x_agent_token: Optional[str] = None,
    ) -> None:
        """Verify request bearer token or X-Agent-Token header matches configured token."""
        token = x_agent_token
        if not token and authorization:
            if authorization.lower().startswith("bearer "):
                token = authorization[7:].strip()
            else:
                token = authorization.strip()

        expected = settings.agent_auth_token
        if not token or not secrets.compare_digest(token, expected):
            logger.warning("Rejected unauthorized IPC request: invalid or missing agent token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Invalid or missing agent authentication token",
            )

    def _setup_routes(self) -> None:
        """Register API routes."""

        @self.app.get("/health")
        @self.app.get("/api/status")
        async def status_endpoint() -> Dict[str, Any]:
            """Return desktop agent status and pending request count."""
            return {
                "status": "ok",
                "service": "walled-ai-desktop-agent",
                "host": self.host,
                "port": self.port,
                "pending_count": self.desktop_app.auth_mgr.registry.count,
                "timeout_seconds": self.desktop_app.auth_mgr.timeout_seconds,
            }

        @self.app.post("/api/authorize", response_model=AuthorizeResponsePayload)
        async def authorize(
            payload: AuthorizeRequestPayload,
            authorization: Optional[str] = Header(None),
            x_agent_token: Optional[str] = Header(None),
        ) -> AuthorizeResponsePayload:
            """Process an authorization request via the Desktop Agent GUI."""
            self._verify_token(authorization, x_agent_token)

            # Signal to dashboard that MCP Gateway is active
            if hasattr(self.desktop_app, "main_window"):
                self.desktop_app.main_window.set_gateway_status(
                    True, "Running (Active Interception)"
                )

            logger.info(
                "IPC received authenticated authorization request: server='%s', tool='%s'",
                payload.server_name,
                payload.tool_name,
            )
            try:
                timeout = payload.timeout_seconds or self.desktop_app.auth_mgr.timeout_seconds
                decision = await self.desktop_app.request_authorization_via_gui(
                    server_name=payload.server_name,
                    tool_name=payload.tool_name,
                    arguments=payload.arguments,
                    timeout_seconds=timeout,
                )
                return AuthorizeResponsePayload(
                    request_id=decision.request_id,
                    status=decision.decision.value,
                    reason=decision.reason or "",
                )
            except asyncio.TimeoutError:
                logger.warning("IPC authorization request timed out")
                return AuthorizeResponsePayload(
                    request_id="unknown",
                    status="TIMED_OUT",
                    reason="Authorization timed out waiting for user response",
                )
            except Exception as e:
                logger.error("IPC authorization request failed: %s", e)
                raise HTTPException(status_code=500, detail=str(e)) from e

        @self.app.websocket("/ws/agent")
        async def websocket_agent(websocket: WebSocket) -> None:
            """WebSocket endpoint for duplex streaming communication with gateway."""
            token = websocket.query_params.get("token") or websocket.headers.get("x-agent-token")
            auth_header = websocket.headers.get("authorization")
            if not token and auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()

            expected = settings.agent_auth_token
            if not token or not secrets.compare_digest(token, expected):
                logger.warning("Rejected unauthorized WebSocket connection: invalid token")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

            await websocket.accept()
            if hasattr(self.desktop_app, "main_window"):
                self.desktop_app.main_window.set_gateway_status(
                    True, "Running (WebSocket Connected)"
                )

            try:
                while True:
                    data = await websocket.receive_json()
                    msg_type = data.get("type", "ping")
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong", "status": "ok"})
                    elif msg_type == "authorize":
                        server_name = data.get("server_name", "Unknown")
                        tool_name = data.get("tool_name", "unknown")
                        arguments = data.get("arguments", {})
                        request_id = data.get("request_id")
                        timeout = data.get(
                            "timeout_seconds",
                            self.desktop_app.auth_mgr.timeout_seconds,
                        )
                        decision = await self.desktop_app.request_authorization_via_gui(
                            server_name=server_name,
                            tool_name=tool_name,
                            arguments=arguments,
                            timeout_seconds=timeout,
                            request_id=request_id,
                        )
                        await websocket.send_json({
                            "type": "decision",
                            "request_id": decision.request_id,
                            "status": decision.decision.value,
                            "reason": decision.reason or "",
                        })
            except WebSocketDisconnect:
                logger.info("WebSocket agent client disconnected.")

    async def start(self) -> None:
        """Start Uvicorn server in background task with automatic port collision fallback."""
        import socket

        target_port = self.port
        for p in range(self.port, self.port + 20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((self.host, p))
                    target_port = p
                    break
                except OSError:
                    continue

        if target_port != self.port:
            logger.warning(
                "Port %d occupied; Desktop IPC server binding to available port %d",
                self.port,
                target_port,
            )
        self.port = target_port

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._serve_task = asyncio.create_task(self._server.serve())
        logger.info("Desktop IPC server started at http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stop Uvicorn server."""
        if self._server:
            self._server.should_exit = True
        if self._serve_task:
            try:
                await asyncio.wait_for(self._serve_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._serve_task.cancel()
        logger.info("Desktop IPC server stopped.")
