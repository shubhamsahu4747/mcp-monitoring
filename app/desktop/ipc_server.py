"""FastAPI-based local IPC server hosted by WalledAIDesktopApp.

Allows local MCP proxies (such as child processes spawned by Claude Desktop)
to communicate authorization requests to the running desktop UI, triggering
always-on-top popups, updating the dashboard queue, and returning user decisions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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

    def _setup_routes(self) -> None:
        """Register API routes."""

        @self.app.get("/health")
        @self.app.get("/api/status")
        async def status() -> Dict[str, Any]:
            """Return desktop agent status and pending request count."""
            return {
                "status": "ok",
                "service": "walled-ai-desktop-agent",
                "pending_count": self.desktop_app.auth_mgr.registry.count,
                "timeout_seconds": self.desktop_app.auth_mgr.timeout_seconds,
            }

        @self.app.post("/api/authorize", response_model=AuthorizeResponsePayload)
        async def authorize(payload: AuthorizeRequestPayload) -> AuthorizeResponsePayload:
            """Process an authorization request via the Desktop Agent GUI."""
            logger.info(
                "IPC received authorization request: server='%s', tool='%s'",
                payload.server_name,
                payload.tool_name,
            )
            try:
                # Use desktop application to present popup and wait for user decision
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

    async def start(self) -> None:
        """Start Uvicorn server in background task."""
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
