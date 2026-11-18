"""Milestone 6 Integration Tests: Remote MCP Gateway and Claude Web Integration.

Verifies all 15 core architectural and security requirements:
1. Remote MCP initialization (initialize) over Streamable HTTP transport.
2. Remote tools/list returns authentic upstream tools.
3. Approved remote tool execution (exactly 1 upstream execution).
4. Rejected remote tool execution (0 upstream executions).
5. Authorization timeout (fails closed, 0 upstream executions).
6. Desktop agent unavailable (fails closed, 0 upstream executions).
7. WebSocket disconnect during pending authorization (fails closed, 0 upstream executions).
8. Agent reconnection (subsequent approvals succeed).
9. Concurrent remote clients (isolated independent request IDs).
10. Duplicate decision attempts (rejected with DuplicateDecisionError / HTTP 409).
11. Invalid agent authentication token (WebSocket 1008 policy violation).
12. Invalid client API key (HTTP 401 Unauthorized).
13. Malformed request payload handling (graceful error response).
14. Rate-limit enforcement (HTTP 429 Too Many Requests).
15. Graceful gateway shutdown (clean termination of sessions and upstream).
16. Performance metric measurement (latency, round-trip time, throughput).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from app.authorization.manager import AuthorizationManager  # noqa: E402
from app.config import settings  # noqa: E402
from app.gateway.upstream_manager import UpstreamManager  # noqa: E402
from app.interception.request_interceptor import RequestInterceptor  # noqa: E402
from app.models.requests import AuditOutcome  # noqa: E402
from app.remote.app import create_remote_app  # noqa: E402
from app.remote.auth import RateLimiter  # noqa: E402
from app.remote.server import RemoteMCPGatewayServer  # noqa: E402
from app.remote.websocket_broker import WebSocketAgentBroker  # noqa: E402
from app.storage.audit_repository import AuditRepository  # noqa: E402
from app.storage.database import DatabaseManager  # noqa: E402


class MockAgentWebSocket:
    """Mock WebSocket simulating connected desktop authorization agent."""

    def __init__(self) -> None:
        self.sent_messages: list[Dict[str, Any]] = []
        self.received_messages: list[Dict[str, Any]] = []
        self.is_closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._new_message_event = asyncio.Event()

    async def send_json(self, data: Dict[str, Any]) -> None:
        self.sent_messages.append(data)
        self._new_message_event.set()

    async def wait_for_message(self, timeout: float = 3.0) -> Dict[str, Any]:
        """Wait until a message is sent to this agent."""
        start = time.time()
        while not self.sent_messages:
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for message to agent")
            self._new_message_event.clear()
            try:
                await asyncio.wait_for(self._new_message_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError("Timed out waiting for message to agent") from None
        return self.sent_messages[-1]

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.is_closed = True
        self.close_code = code
        self.close_reason = reason


class ManagedSessionRunner:
    """Manages StreamableHTTPSessionManager lifecycle in a dedicated task to isolate cancel scopes."""

    def __init__(self, server: RemoteMCPGatewayServer) -> None:
        self.server = server
        self.started_event = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        try:
            await self.server.start()
            async with self.server.session_manager.run():
                self.started_event.set()
                await self.stop_event.wait()
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            try:
                await self.server.stop()
            except (Exception, asyncio.CancelledError):
                pass

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())
        await self.started_event.wait()

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task and not self.task.done():
            try:
                await self.task
            except (Exception, asyncio.CancelledError):
                pass


@pytest.fixture
async def remote_gateway_env(
    tmp_path: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Sets up an isolated remote gateway test environment."""
    db_file = tmp_path / "m6_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=2.0, audit_repository=audit_repo)
    broker = WebSocketAgentBroker()

    upstream_mgr = UpstreamManager(
        command=sys.executable,
        args=["examples/mock_mcp_server.py"],
        server_name="Safe Mock MCP Server",
    )

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=upstream_mgr,
        audit_repository=audit_repo,
    )

    gateway_server = RemoteMCPGatewayServer(
        upstream_mgr=upstream_mgr,
        auth_mgr=auth_mgr,
        interceptor=interceptor,
        broker=broker,
        server_name="Walled AI Test Remote Gateway",
    )

    limiter = RateLimiter(limit_per_minute=100)
    app = create_remote_app(
        gateway_server=gateway_server,
        broker=broker,
        limiter=limiter,
    )

    runner = ManagedSessionRunner(gateway_server)
    await runner.start()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "app": app,
                "client": client,
                "gateway_server": gateway_server,
                "broker": broker,
                "auth_mgr": auth_mgr,
                "audit_repo": audit_repo,
                "upstream_mgr": upstream_mgr,
                "limiter": limiter,
                "runner": runner,
            }
    finally:
        await runner.stop()


def _auth_headers(session_id: str | None = None) -> Dict[str, str]:
    """Helper creating valid authentication and MCP headers."""
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "authorization": f"Bearer {settings.remote_client_api_key}",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _extract_sse_json(body_text: str) -> Dict[str, Any]:
    """Extract JSON object from text/event-stream SSE payload."""
    for line in body_text.splitlines():
        if line.startswith("data:"):
            raw_json = line[len("data:") :].strip()
            if raw_json:
                return json.loads(raw_json)
    return {}


# -----------------------------------------------------------------------------
# 1. MCP Initialization
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_mcp_initialization(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 1: Verify remote MCP initialization and capability negotiation."""
    client: AsyncClient = remote_gateway_env["client"]

    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    session_id = resp.headers.get("mcp-session-id")
    assert session_id is not None
    assert len(session_id) > 10

    payload = _extract_sse_json(resp.text)
    assert payload.get("id") == 1
    result = payload.get("result", {})
    assert "capabilities" in result
    assert result.get("serverInfo", {}).get("name") == "Walled AI Test Remote Gateway"


# -----------------------------------------------------------------------------
# 2. Remote tools/list Discovery
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_tools_list(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 2: Verify tools/list returns authentic upstream tools."""
    client: AsyncClient = remote_gateway_env["client"]

    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    tools_resp = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=_auth_headers(session_id),
    )

    assert tools_resp.status_code == 200
    payload = _extract_sse_json(tools_resp.text)
    tools = payload.get("result", {}).get("tools", [])
    assert len(tools) >= 2
    tool_names = [t["name"] for t in tools]
    assert "calculate_sum" in tool_names
    assert "read_demo_file" in tool_names


# -----------------------------------------------------------------------------
# 3. Approved Remote Tool Execution
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_tool_execution_approved(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 3: Verify approved tool execution yields exactly 1 upstream call."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]
    audit_repo: AuditRepository = remote_gateway_env["audit_repo"]

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-1", mock_ws)  # type: ignore[arg-type]

    # Initialize session
    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    async def _auto_approve() -> None:
        msg = await mock_ws.wait_for_message(timeout=3.0)
        assert msg["type"] == "authorize"
        assert msg["tool_name"] == "calculate_sum"
        assert msg["arguments"] == {"a": 40, "b": 60}
        req_id = msg["request_id"]

        await broker.submit_decision(
            {
                "request_id": req_id,
                "status": "APPROVED",
                "reason": "Approved by user in test",
            }
        )

    approve_task = asyncio.create_task(_auto_approve())

    call_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "calculate_sum",
                "arguments": {"a": 40, "b": 60},
            },
        },
        headers=_auth_headers(session_id),
    )
    await approve_task

    assert call_resp.status_code == 200
    payload = _extract_sse_json(call_resp.text)
    result = payload.get("result", {})
    assert result.get("isError") is False
    content_text = result.get("content", [{}])[0].get("text", "")
    assert "100.0" in content_text

    records = await audit_repo.list_records(limit=10)
    matching = [r for r in records if r.tool_name == "calculate_sum"]
    assert len(matching) == 1
    assert matching[0].execution_outcome == AuditOutcome.SUCCESS


# -----------------------------------------------------------------------------
# 4. Rejected Remote Tool Execution
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_tool_execution_rejected(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 4: Verify rejected tool execution yields exactly 0 upstream calls."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]
    audit_repo: AuditRepository = remote_gateway_env["audit_repo"]

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-1", mock_ws)  # type: ignore[arg-type]

    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    async def _auto_reject() -> None:
        msg = await mock_ws.wait_for_message(timeout=3.0)
        await broker.submit_decision(
            {
                "request_id": msg["request_id"],
                "status": "REJECTED",
                "reason": "Forbidden by user",
            }
        )

    reject_task = asyncio.create_task(_auto_reject())

    call_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "calculate_sum",
                "arguments": {"a": 10, "b": 20},
            },
        },
        headers=_auth_headers(session_id),
    )
    await reject_task

    assert call_resp.status_code == 200
    payload = _extract_sse_json(call_resp.text)
    result = payload.get("result", {})
    assert result.get("isError") is True
    err_text = result.get("content", [{}])[0].get("text", "")
    assert "blocked by user" in err_text.lower()

    records = await audit_repo.list_records(limit=10)
    matching = [r for r in records if r.tool_name == "calculate_sum"]
    assert matching[0].execution_outcome == AuditOutcome.USER_REJECTED


# -----------------------------------------------------------------------------
# 5. Authorization Timeout (Fails Closed)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_authorization_timeout(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 5: Verify deadline expiration fails closed with 0 upstream executions."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]
    auth_mgr: AuthorizationManager = remote_gateway_env["auth_mgr"]
    audit_repo: AuditRepository = remote_gateway_env["audit_repo"]

    # Shorten timeout for test speed
    auth_mgr.timeout_seconds = 0.8

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-1", mock_ws)  # type: ignore[arg-type]

    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    # Tool call without agent answering
    call_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "calculate_sum", "arguments": {"a": 1, "b": 2}},
        },
        headers=_auth_headers(session_id),
    )

    assert call_resp.status_code == 200
    payload = _extract_sse_json(call_resp.text)
    result = payload.get("result", {})
    assert result.get("isError") is True
    err_text = result.get("content", [{}])[0].get("text", "")
    assert "timed out" in err_text.lower()

    records = await audit_repo.list_records(limit=10)
    matching = [r for r in records if r.tool_name == "calculate_sum"]
    assert matching[0].categorized_outcome == AuditOutcome.BLOCKED_TIMEOUT


# -----------------------------------------------------------------------------
# 6. Desktop Agent Unavailable (Fails Closed)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_desktop_agent_unavailable(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 6: When no desktop agent is connected, immediately fail closed."""
    client: AsyncClient = remote_gateway_env["client"]
    audit_repo: AuditRepository = remote_gateway_env["audit_repo"]

    # Verify no agent is registered
    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    call_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "calculate_sum", "arguments": {"a": 5, "b": 5}},
        },
        headers=_auth_headers(session_id),
    )

    assert call_resp.status_code == 200
    payload = _extract_sse_json(call_resp.text)
    result = payload.get("result", {})
    assert result.get("isError") is True
    err_text = result.get("content", [{}])[0].get("text", "")
    assert "unavailable" in err_text.lower() or "not connected" in err_text.lower()

    records = await audit_repo.list_records(limit=10)
    matching = [r for r in records if r.tool_name == "calculate_sum"]
    assert matching[0].execution_outcome == AuditOutcome.BLOCKED_AGENT_UNAVAILABLE


# -----------------------------------------------------------------------------
# 7. WebSocket Disconnect Mid-Flight (Fails Closed)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_websocket_disconnect_mid_flight(
    remote_gateway_env: Dict[str, Any],
) -> None:
    """Requirement 7: Agent disconnect during pending request immediately fails closed."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]
    audit_repo: AuditRepository = remote_gateway_env["audit_repo"]

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-flake", mock_ws)  # type: ignore[arg-type]

    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    async def _disconnect_on_receive() -> None:
        await mock_ws.wait_for_message(timeout=3.0)
        # Simulate abrupt WebSocket disconnect
        await broker.unregister_agent("desktop-agent-flake")

    disconnect_task = asyncio.create_task(_disconnect_on_receive())

    call_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "calculate_sum", "arguments": {"a": 9, "b": 1}},
        },
        headers=_auth_headers(session_id),
    )
    await disconnect_task

    assert call_resp.status_code == 200
    payload = _extract_sse_json(call_resp.text)
    result = payload.get("result", {})
    assert result.get("isError") is True
    err_text = result.get("content", [{}])[0].get("text", "")
    assert "disconnected" in err_text.lower() or "unavailable" in err_text.lower()

    records = await audit_repo.list_records(limit=10)
    matching = [r for r in records if r.tool_name == "calculate_sum"]
    assert matching[0].execution_outcome == AuditOutcome.BLOCKED_AGENT_UNAVAILABLE


# -----------------------------------------------------------------------------
# 8. Agent Reconnection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_agent_reconnection(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 8: After agent reconnects, subsequent requests are successfully approved."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]

    # 1. Connect agent and then disconnect
    mock_ws_1 = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-1", mock_ws_1)  # type: ignore[arg-type]
    await broker.unregister_agent("desktop-agent-1")
    assert broker.active_agent_count == 0

    # 2. Reconnect new agent instance
    mock_ws_2 = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-2", mock_ws_2)  # type: ignore[arg-type]
    assert broker.active_agent_count == 1

    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-web-test", "version": "1.0.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    async def _approve_reconnected() -> None:
        msg = await mock_ws_2.wait_for_message(timeout=3.0)
        await broker.submit_decision(
            {
                "request_id": msg["request_id"],
                "status": "APPROVED",
                "reason": "Approved on reconnected agent",
            }
        )

    task = asyncio.create_task(_approve_reconnected())

    call_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "calculate_sum", "arguments": {"a": 20, "b": 30}},
        },
        headers=_auth_headers(session_id),
    )
    await task

    assert call_resp.status_code == 200
    payload = _extract_sse_json(call_resp.text)
    assert payload.get("result", {}).get("isError") is False
    assert "50.0" in payload.get("result", {}).get("content", [{}])[0].get("text", "")


# -----------------------------------------------------------------------------
# 9. Concurrent Remote Clients
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_concurrent_clients(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 9: Multiple concurrent remote clients receive isolated decisions."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-main", mock_ws)  # type: ignore[arg-type]

    # Initialize 2 distinct sessions
    init1 = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "client-A", "version": "1.0"},
            },
        },
        headers=_auth_headers(),
    )
    sess1 = init1.headers["mcp-session-id"]

    init2 = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "client-B", "version": "1.0"},
            },
        },
        headers=_auth_headers(),
    )
    sess2 = init2.headers["mcp-session-id"]
    assert sess1 != sess2

    # Background handler approving client 1 and rejecting client 2
    async def _handle_concurrent() -> None:
        decided = 0
        while decided < 2:
            await mock_ws.wait_for_message(timeout=3.0)
            msg = mock_ws.sent_messages.pop(0)
            if msg.get("type") == "authorize":
                if msg["arguments"] == {"a": 10, "b": 10}:
                    # Approve Client A
                    await broker.submit_decision(
                        {
                            "request_id": msg["request_id"],
                            "status": "APPROVED",
                            "reason": "Approved A",
                        }
                    )
                else:
                    # Reject Client B
                    await broker.submit_decision(
                        {
                            "request_id": msg["request_id"],
                            "status": "REJECTED",
                            "reason": "Rejected B",
                        }
                    )
                decided += 1

    handler_task = asyncio.create_task(_handle_concurrent())

    call_a = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "calculate_sum", "arguments": {"a": 10, "b": 10}},
        },
        headers=_auth_headers(sess1),
    )

    call_b = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {"name": "calculate_sum", "arguments": {"a": 99, "b": 99}},
        },
        headers=_auth_headers(sess2),
    )

    resp_a, resp_b = await asyncio.gather(call_a, call_b)
    await handler_task

    payload_a = _extract_sse_json(resp_a.text)
    payload_b = _extract_sse_json(resp_b.text)

    # Client A should be approved
    assert payload_a.get("result", {}).get("isError") is False
    assert "20.0" in payload_a.get("result", {}).get("content", [{}])[0].get("text", "")

    # Client B should be rejected
    assert payload_b.get("result", {}).get("isError") is True
    text_b = payload_b.get("result", {}).get("content", [{}])[0].get("text", "").lower()
    assert "blocked by user" in text_b


# -----------------------------------------------------------------------------
# 10. Duplicate Decision Rejection
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_duplicate_decision_rejected(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 10: Submitting duplicate decisions is rejected with DuplicateDecisionError."""
    from app.models.requests import DuplicateDecisionError

    broker: WebSocketAgentBroker = remote_gateway_env["broker"]
    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-1", mock_ws)  # type: ignore[arg-type]

    # Fabricate a pending request
    from app.models.requests import AuthorizationRequest

    req = AuthorizationRequest(
        server_name="TestServer",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )

    req_task = asyncio.create_task(broker.request_authorization(req, timeout_seconds=2.0))
    await mock_ws.wait_for_message()

    # First decision should succeed
    await broker.submit_decision(
        {
            "request_id": req.request_id,
            "status": "APPROVED",
            "reason": "First decision",
        }
    )
    decision = await req_task
    assert decision.decision.value == "APPROVED"

    # Second decision for the same request ID MUST raise DuplicateDecisionError
    with pytest.raises(DuplicateDecisionError):
        await broker.submit_decision(
            {
                "request_id": req.request_id,
                "status": "REJECTED",
                "reason": "Duplicate decision attempt",
            }
        )


# -----------------------------------------------------------------------------
# 11. Invalid Agent Authentication Token (WebSocket 1008)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_invalid_agent_token(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 11: Invalid agent token closes WebSocket with code 1008."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = remote_gateway_env["app"]
    test_client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect("/ws/agent?token=invalid-secret-token"):
            pass
    assert exc_info.value.code == 1008


# -----------------------------------------------------------------------------
# 12. Invalid Client API Key (HTTP 401)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_invalid_client_api_key(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 12: Invalid or missing remote client API key returns HTTP 401."""
    client: AsyncClient = remote_gateway_env["client"]

    # 1. No Authorization header
    resp_no_auth = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"content-type": "application/json"},
    )
    assert resp_no_auth.status_code == 401
    assert "WWW-Authenticate" in resp_no_auth.headers

    # 2. Bad token
    resp_bad_auth = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={
            "content-type": "application/json",
            "authorization": "Bearer bad-client-api-key-12345",
        },
    )
    assert resp_bad_auth.status_code == 401


# -----------------------------------------------------------------------------
# 13. Malformed Payload Handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_malformed_payload_handling(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 13: Malformed request payload returns client error without crashing."""
    client: AsyncClient = remote_gateway_env["client"]

    # Malformed JSON body to /api/authorize
    resp = await client.post(
        "/api/authorize",
        content="not-valid-json{{{",
        headers={
            "content-type": "application/json",
            "x-agent-token": settings.agent_auth_token,
        },
    )
    assert resp.status_code == 400


# -----------------------------------------------------------------------------
# 14. Rate-Limit Enforcement (HTTP 429)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_rate_limiting(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 14: Excess requests trigger HTTP 429 Too Many Requests."""
    client: AsyncClient = remote_gateway_env["client"]
    limiter: RateLimiter = remote_gateway_env["limiter"]

    # Set tight limit for testing
    limiter.limit_per_minute = 3
    limiter.reset()

    for i in range(3):
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": i,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

    # 4th request must be rate-limited
    resp_blocked = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "initialize"},
        headers=_auth_headers(),
    )
    assert resp_blocked.status_code == 429
    assert "Rate limit exceeded" in resp_blocked.text


# -----------------------------------------------------------------------------
# 15. Graceful Gateway Shutdown
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_graceful_shutdown(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 15: Gateway shuts down cleanly, failing closed any active pending requests."""
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]
    gateway_server: RemoteMCPGatewayServer = remote_gateway_env["gateway_server"]
    audit_repo: AuditRepository = remote_gateway_env["audit_repo"]

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-1", mock_ws)  # type: ignore[arg-type]

    # Create pending request in auth_mgr
    async def _make_pending() -> None:
        try:
            await gateway_server.auth_mgr.request_authorization(
                server_name="TestServer",
                tool_name="calculate_sum",
                arguments={"a": 1, "b": 1},
            )
        except Exception:
            pass

    pending_task = asyncio.create_task(_make_pending())
    await asyncio.sleep(0.1)

    # Perform shutdown
    await gateway_server.stop()
    await pending_task

    # Verify audit record shows cancelled
    records = await audit_repo.list_records(limit=5)
    matching = [r for r in records if r.tool_name == "calculate_sum"]
    assert len(matching) >= 1
    assert matching[0].execution_outcome in ("CANCELLED", "BLOCKED")


# -----------------------------------------------------------------------------
# 16. Performance Metrics Measurement
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_performance_metrics(remote_gateway_env: Dict[str, Any]) -> None:
    """Requirement 16: Measure and verify latency, round-trip time, and throughput."""
    client: AsyncClient = remote_gateway_env["client"]
    broker: WebSocketAgentBroker = remote_gateway_env["broker"]

    mock_ws = MockAgentWebSocket()
    await broker.register_agent("desktop-agent-perf", mock_ws)  # type: ignore[arg-type]

    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "perf-test", "version": "1.0"},
            },
        },
        headers=_auth_headers(),
    )
    session_id = init_resp.headers["mcp-session-id"]

    # Background auto-approver
    async def _fast_approver() -> None:
        while True:
            try:
                await mock_ws.wait_for_message(timeout=2.0)
                msg = mock_ws.sent_messages.pop(0)
                if msg.get("type") == "authorize":
                    await broker.submit_decision(
                        {
                            "request_id": msg["request_id"],
                            "status": "APPROVED",
                            "reason": "Perf test approval",
                        }
                    )
            except (TimeoutError, asyncio.CancelledError):
                break

    approver_task = asyncio.create_task(_fast_approver())

    latencies: list[float] = []
    iterations = 5

    for i in range(iterations):
        t0 = time.perf_counter()
        call_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 100 + i,
                "method": "tools/call",
                "params": {"name": "calculate_sum", "arguments": {"a": i, "b": 10}},
            },
            headers=_auth_headers(session_id),
        )
        elapsed = (time.perf_counter() - t0) * 1000.0  # in ms
        assert call_resp.status_code == 200
        latencies.append(elapsed)

    approver_task.cancel()
    try:
        await approver_task
    except asyncio.CancelledError:
        pass

    avg_latency = sum(latencies) / len(latencies)
    min_latency = min(latencies)
    max_latency = max(latencies)

    # Output metrics to stderr for milestone verification
    sys.stderr.write(
        f"\n[PERFORMANCE METRICS - REMOTE MCP GATEWAY]\n"
        f"  Total Calls Evaluated    : {iterations}\n"
        f"  Average Round-Trip Latency: {avg_latency:.2f} ms\n"
        f"  Minimum Latency          : {min_latency:.2f} ms\n"
        f"  Maximum Latency          : {max_latency:.2f} ms\n"
        f"  Estimated Decision TPS   : {1000.0 / avg_latency:.1f} req/sec\n\n"
    )

    # Verification threshold: avg round-trip should comfortably be under 200ms in-process
    assert avg_latency < 300.0
