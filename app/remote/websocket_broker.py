"""WebSocket Agent Broker coordinating Remote MCP Gateway with Desktop Agent(s).

Manages active WebSocket connections, request routing, decision correlation,
heartbeats, disconnect detection, and fail-closed security.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import WebSocket

from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    DuplicateDecisionError,
)

logger = logging.getLogger(__name__)


class BoundedSet:
    """Thread/coroutine-safe bounded FIFO set with O(1) membership check."""

    def __init__(self, maxsize: int = 10000) -> None:
        self._maxsize = maxsize
        self._items: OrderedDict[str, None] = OrderedDict()

    def add(self, item: str) -> None:
        self._items[item] = None
        if len(self._items) > self._maxsize:
            self._items.popitem(last=False)

    def __contains__(self, item: object) -> bool:
        return item in self._items


class WebSocketAgentBroker:
    """Brokers authorization requests and decisions between Remote Gateway and Desktop Agents."""

    def __init__(self) -> None:
        # Active connected agent WebSockets keyed by unique agent connection ID
        self._active_connections: Dict[str, WebSocket] = {}
        # Per-agent lock serializing outbound WebSocket frame transmission
        self._send_locks: Dict[str, asyncio.Lock] = {}
        # Futures awaiting user decision keyed by request_id
        self._pending_decisions: Dict[str, asyncio.Future[AuthorizationDecision]] = {}
        # Map of request_id -> agent_id handling that request
        self._request_agent_mapping: Dict[str, str] = {}
        # Lock for atomic connection and pending request manipulation
        self._lock = asyncio.Lock()
        # Track resolved requests with bounded memory to prevent duplicate decision submissions
        self._resolved_requests = BoundedSet(maxsize=10000)

    @property
    def active_agent_count(self) -> int:
        """Return the number of connected desktop agents."""
        return len(self._active_connections)

    def has_active_agent(self) -> bool:
        """Check if at least one authenticated desktop agent is connected."""
        return len(self._active_connections) > 0

    async def register_agent(self, agent_id: str, websocket: WebSocket) -> None:
        """Register an authenticated desktop agent WebSocket connection."""
        async with self._lock:
            self._active_connections[agent_id] = websocket
            self._send_locks[agent_id] = asyncio.Lock()
            logger.info(
                "Registered desktop agent '%s'. Total active agents: %d",
                agent_id,
                len(self._active_connections),
            )

    async def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent on disconnect and fail closed all pending requests owned by it."""
        async with self._lock:
            self._active_connections.pop(agent_id, None)
            self._send_locks.pop(agent_id, None)
            logger.warning(
                "Unregistered desktop agent '%s'. Remaining active agents: %d",
                agent_id,
                len(self._active_connections),
            )

            # Find all pending requests assigned to this disconnected agent
            orphaned = [
                req_id
                for req_id, assigned_agent in self._request_agent_mapping.items()
                if assigned_agent == agent_id
            ]

            for req_id in orphaned:
                future = self._pending_decisions.pop(req_id, None)
                self._request_agent_mapping.pop(req_id, None)
                self._resolved_requests.add(req_id)
                if future and not future.done():
                    logger.warning(
                        "Failing closed pending request %s: Desktop agent %s disconnected",
                        req_id,
                        agent_id,
                    )
                    decision = AuthorizationDecision(
                        request_id=req_id,
                        decision=AuthorizationStatus.REJECTED,
                        reason="Desktop Agent disconnected while authorization request was pending",
                        timestamp=datetime.now(timezone.utc),
                    )
                    future.set_result(decision)

    async def request_authorization(
        self,
        request: AuthorizationRequest,
        timeout_seconds: float = 30.0,
    ) -> AuthorizationDecision:
        """Route an intercepted tool call to an active desktop agent and await human decision.

        Enforces strict fail-closed security if no agent is connected, if the agent
        disconnects in flight, or if the authorization deadline expires.
        """
        async with self._lock:
            if not self._active_connections:
                logger.warning(
                    "Rejected tool '%s': No Walled AI Desktop Agent connected to remote gateway",
                    request.tool_name,
                )
                return AuthorizationDecision(
                    request_id=request.request_id,
                    decision=AuthorizationStatus.REJECTED,
                    reason="Walled AI Desktop Agent is not connected to the remote gateway",
                    timestamp=datetime.now(timezone.utc),
                )

            # Select the primary connected desktop agent
            agent_id, ws = next(iter(self._active_connections.items()))

            loop = asyncio.get_running_loop()
            future: asyncio.Future[AuthorizationDecision] = loop.create_future()
            self._pending_decisions[request.request_id] = future
            self._request_agent_mapping[request.request_id] = agent_id

        # Send authorization request to desktop agent over WebSocket
        payload = {
            "type": "authorize",
            "request_id": request.request_id,
            "server_name": request.server_name,
            "tool_name": request.tool_name,
            "arguments": request.arguments,
            "timeout_seconds": timeout_seconds,
            "client_id": request.client_id,
            "session_id": request.session_id,
        }

        send_lock = self._send_locks.get(agent_id)
        try:
            logger.info(
                "Forwarding remote request %s ('%s') to desktop agent '%s'...",
                request.request_id,
                request.tool_name,
                agent_id,
            )
            if send_lock:
                async with send_lock:
                    await ws.send_json(payload)
            else:
                await ws.send_json(payload)
        except Exception as e:
            logger.error("Failed to send authorization request to agent %s: %s", agent_id, e)
            async with self._lock:
                self._pending_decisions.pop(request.request_id, None)
                self._request_agent_mapping.pop(request.request_id, None)
                self._resolved_requests.add(request.request_id)
            return AuthorizationDecision(
                request_id=request.request_id,
                decision=AuthorizationStatus.REJECTED,
                reason=f"Failed to communicate with desktop agent: {e}",
                timestamp=datetime.now(timezone.utc),
            )

        # Await decision with deadline
        try:
            decision = await asyncio.wait_for(future, timeout=timeout_seconds)
            return decision
        except asyncio.TimeoutError:
            logger.warning("Remote authorization request %s timed out", request.request_id)
            async with self._lock:
                self._pending_decisions.pop(request.request_id, None)
                self._request_agent_mapping.pop(request.request_id, None)
                self._resolved_requests.add(request.request_id)
            return AuthorizationDecision(
                request_id=request.request_id,
                decision=AuthorizationStatus.TIMED_OUT,
                reason=f"Authorization timed out after {timeout_seconds:.1f}s",
                timestamp=datetime.now(timezone.utc),
            )

    async def submit_decision(self, data: Dict[str, Any]) -> None:
        """Process incoming decision payload from an authorized desktop agent."""
        request_id = data.get("request_id")
        if not request_id:
            logger.warning("Ignored malformed decision payload: missing request_id")
            return

        status_str = data.get("status", "REJECTED")
        reason = data.get("reason", "Decided by user in desktop agent")

        decision_enum = (
            AuthorizationStatus.APPROVED
            if status_str == "APPROVED"
            else AuthorizationStatus.REJECTED
        )

        decision = AuthorizationDecision(
            request_id=request_id,
            decision=decision_enum,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
        )

        async with self._lock:
            if request_id in self._resolved_requests:
                logger.warning("Duplicate decision rejected for request %s", request_id)
                raise DuplicateDecisionError(
                    f"Decision for request {request_id} was already finalized"
                )

            future = self._pending_decisions.pop(request_id, None)
            self._request_agent_mapping.pop(request_id, None)
            self._resolved_requests.add(request_id)

            if future and not future.done():
                future.set_result(decision)
                logger.info(
                    "Successfully resolved remote request %s -> %s",
                    request_id,
                    decision.decision.value,
                )
            else:
                logger.warning("Received decision for unknown or expired request: %s", request_id)
                raise DuplicateDecisionError(
                    f"Request {request_id} has expired or was already resolved"
                )


# Singleton broker instance
agent_broker = WebSocketAgentBroker()
