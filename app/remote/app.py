"""FastAPI application factory for Walled AI Remote MCP Gateway.

Configures:
1. Lifespan managing the Streamable HTTP Session Manager and upstream lifecycle.
2. Authenticated and rate-limited MCP Streamable HTTP transport at `/mcp`.
3. Authenticated WebSocket endpoint at `/ws/agent` for Desktop Agent(s).
4. Fallback HTTP authorization endpoint at `/api/authorize`.
5. Health and diagnostic monitoring endpoints.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import urllib.parse
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import Receive, Scope, Send

from app.config import settings
from app.models.requests import DuplicateDecisionError
from app.remote.auth import RateLimiter, rate_limiter, verify_agent_token
from app.remote.server import RemoteMCPGatewayServer
from app.remote.websocket_broker import WebSocketAgentBroker

logger = logging.getLogger(__name__)


class AuthenticatedStreamableHTTPApp:
    """ASGI application wrapping MCP Streamable HTTP with rate-limiting and authentication."""

    def __init__(
        self,
        server: RemoteMCPGatewayServer,
        limiter: RateLimiter,
    ) -> None:
        self.server = server
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.server.session_manager.asgi_app(scope, receive, send)
            return

        # 1. Rate Limiting by client IP
        client_info = scope.get("client")
        client_ip = client_info[0] if client_info else "unknown-remote"
        if not self.limiter.is_allowed(client_ip):
            logger.warning("Rate limit exceeded for remote client: %s", client_ip)
            resp = Response(
                content=json.dumps({"detail": "Rate limit exceeded. Please wait before retrying."}),
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
            )
            await resp(scope, receive, send)
            return

        # 2. Remote Client Authentication Check
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        x_api_key = headers.get(b"x-api-key", b"").decode("latin-1")
        query_string = scope.get("query_string", b"").decode("latin-1")
        params = urllib.parse.parse_qs(query_string)

        token: Optional[str] = None
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif x_api_key:
            token = x_api_key.strip()
        elif "token" in params and params["token"]:
            token = params["token"][0].strip()

        expected = settings.remote_client_api_key
        if not token or not secrets.compare_digest(token, expected):
            logger.warning("Unauthorized remote MCP client access attempt from %s", client_ip)
            resp = Response(
                content=json.dumps(
                    {"detail": "Invalid or missing remote client API key / Bearer token"}
                ),
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )
            await resp(scope, receive, send)
            return

        # 3. Authenticated: Forward to official Streamable HTTP handler
        await self.server.session_manager.asgi_app(scope, receive, send)


class RewriteMCPPathMiddleware:
    """Ensures `/mcp` without trailing slash is routed directly without 307 redirect."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope["path"] = "/mcp/"
        await self.app(scope, receive, send)


def create_remote_app(
    gateway_server: Optional[RemoteMCPGatewayServer] = None,
    broker: Optional[WebSocketAgentBroker] = None,
    limiter: Optional[RateLimiter] = None,
) -> FastAPI:
    """Build and configure the Remote MCP Gateway FastAPI application."""
    srv = gateway_server or RemoteMCPGatewayServer()
    brk = broker or srv.broker
    lmt = limiter or rate_limiter

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Initializing Remote MCP Gateway application lifespan...")
        await srv.start()
        async with srv.session_manager.run():
            logger.info("Remote MCP Gateway is active and accepting requests.")
            yield
        logger.info("Shutting down Remote MCP Gateway application lifespan...")
        await srv.stop()

    app = FastAPI(
        title="Walled AI Remote MCP Gateway",
        description="Secure MCP Streamable HTTP Gateway with Desktop Agent Authorization",
        version="1.0.0",
        lifespan=lifespan,
    )

    # 1. Path rewrite middleware
    app.add_middleware(RewriteMCPPathMiddleware)

    # 2. CORS configuration
    origins = [o.strip() for o in settings.remote_cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 3. Mount Authenticated MCP Streamable HTTP App at `/mcp`
    mcp_asgi = AuthenticatedStreamableHTTPApp(server=srv, limiter=lmt)
    app.mount("/mcp", mcp_asgi)

    # 4. WebSocket Endpoint for Desktop Authorization Agent
    @app.websocket("/ws/agent")
    async def ws_agent_endpoint(
        websocket: WebSocket,
        token: Optional[str] = Query(None),
    ) -> None:
        """Authenticated WebSocket connection for the Walled AI Windows Desktop Agent."""
        # Check query param token or authorization header
        agent_tok = token
        if not agent_tok:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                agent_tok = auth_header[7:].strip()
            elif "x-agent-token" in websocket.headers:
                agent_tok = websocket.headers["x-agent-token"].strip()

        if not verify_agent_token(agent_tok):
            logger.warning(
                "Rejected desktop agent WebSocket connection from %s: Invalid token",
                websocket.client.host if websocket.client else "unknown",
            )
            # 1008 indicates policy violation (unauthorized)
            await websocket.close(code=1008, reason="Invalid agent authentication token")
            return

        await websocket.accept()
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        await brk.register_agent(agent_id, websocket)

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                if msg_type == "decision":
                    try:
                        await brk.submit_decision(data)
                    except DuplicateDecisionError as dde:
                        logger.warning("Duplicate decision over WebSocket: %s", dde)
                        await websocket.send_json({"type": "error", "message": str(dde)})
                    except Exception as err:
                        logger.error("Error submitting decision over WebSocket: %s", err)
                        await websocket.send_json({"type": "error", "message": str(err)})

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "pong":
                    # Keepalive acknowledged
                    pass
                else:
                    logger.warning("Unknown message type on agent WebSocket: %s", msg_type)

        except WebSocketDisconnect:
            logger.info("Desktop agent '%s' disconnected from WebSocket", agent_id)
        except Exception as err:
            logger.error("Desktop agent '%s' WebSocket encountered error: %s", agent_id, err)
        finally:
            await brk.unregister_agent(agent_id)

    # 5. Fallback HTTP Authorization Decision Submission
    @app.post("/api/authorize")
    async def post_authorize(request: Request) -> Dict[str, Any]:
        """HTTP fallback endpoint for submitting authorization decisions."""
        auth_header = request.headers.get("authorization", "")
        agent_tok: Optional[str] = None
        if auth_header.lower().startswith("bearer "):
            agent_tok = auth_header[7:].strip()
        elif "x-agent-token" in request.headers:
            agent_tok = request.headers["x-agent-token"].strip()
        elif "token" in request.query_params:
            agent_tok = request.query_params["token"].strip()

        if not verify_agent_token(agent_tok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing agent authentication token",
            )

        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed JSON body: {e}",
            ) from e

        try:
            await brk.submit_decision(body)
            return {"status": "ok", "request_id": body.get("request_id")}
        except DuplicateDecisionError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            ) from e

    # 6. Health and Monitoring Endpoints
    @app.get("/health")
    async def health_check() -> Dict[str, Any]:
        """Basic service health check."""
        return {
            "status": "healthy",
            "service": "walled-ai-remote-gateway",
            "active_agents": brk.active_agent_count,
        }

    @app.get("/api/status")
    async def api_status() -> Dict[str, Any]:
        """Detailed gateway operational status."""
        return {
            "gateway": "running",
            "active_agents": brk.active_agent_count,
            "upstream_server": srv.upstream_mgr.server_name,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
        }

    return app
