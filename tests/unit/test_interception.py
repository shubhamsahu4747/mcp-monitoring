"""Unit tests for the MCP Request Interceptor and Parser."""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock

import mcp.types as types
import pytest

from app.authorization.manager import AuthorizationManager
from app.gateway.upstream_manager import UpstreamConnectionError, UpstreamManager
from app.interception.request_interceptor import RequestInterceptor
from app.interception.request_parser import RequestParser
from app.models.requests import AuthorizationDecision, AuthorizationRequest, AuthorizationStatus
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager


@pytest.fixture
async def audit_repo(tmp_path: Path) -> AuditRepository:
    """Fixture providing an isolated SQLite audit repository."""
    db_file = tmp_path / "test_interception.db"
    manager = DatabaseManager(db_file)
    repo = AuditRepository(manager)
    await repo.initialize()
    return repo


def test_request_parser_validation() -> None:
    """Verify tool name and argument parsing."""
    name, args = RequestParser.parse_tool_call("calculate_sum", {"a": 1, "b": 2})
    assert name == "calculate_sum"
    assert args == {"a": 1, "b": 2}

    # None arguments becomes empty dict
    name2, args2 = RequestParser.parse_tool_call("get_system_info", None)
    assert name2 == "get_system_info"
    assert args2 == {}

    # Empty name raises ValueError
    with pytest.raises(ValueError):
        RequestParser.parse_tool_call("", {"a": 1})

    with pytest.raises(ValueError):
        RequestParser.parse_tool_call("   ", {})


@pytest.mark.asyncio
async def test_interceptor_approved_execution_flow(audit_repo: AuditRepository) -> None:
    """Test 2 & 8: Approved request forwards original arguments upstream and redacts audit log."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    # Auto-approve hook
    async def approve_hook(req: AuthorizationRequest) -> None:
        decision = AuthorizationDecision(
            request_id=req.request_id,
            decision=AuthorizationStatus.APPROVED,
            reason="User confirmed",
        )
        await auth_mgr.submit_decision(decision)

    auth_mgr.set_notification_hook(approve_hook)

    # Mock Upstream Manager
    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Safe Server"
    mock_upstream.call_tool.return_value = types.CallToolResult(
        content=[types.TextContent(type="text", text="Calculation result: 42.0")],
        is_error=False,
    )

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=audit_repo,
    )

    sensitive_payload: Dict[str, Any] = {
        "query": "SELECT * FROM users",
        "password": "secret_database_password_123",
        "api_key": "sk-secret-token",
    }

    result = await interceptor.intercept_call_tool(
        name="run_database_query",
        arguments=sensitive_payload,
    )

    # 1. Assert result is forwarded from upstream
    assert not result.is_error
    assert len(result.content) == 1
    assert "42.0" in result.content[0].text  # type: ignore

    # 2. Assert upstream received the UNTOUCHED, original arguments
    mock_upstream.call_tool.assert_awaited_once_with(
        name="run_database_query",
        arguments=sensitive_payload,
    )
    forwarded_args = mock_upstream.call_tool.await_args.kwargs["arguments"]
    assert forwarded_args["password"] == "secret_database_password_123"

    # 3. Assert persisted audit record contains REDACTED arguments
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.APPROVED
    assert records[0].execution_outcome == "SUCCESS"
    assert records[0].arguments_redacted["password"] == "[REDACTED]"
    assert records[0].arguments_redacted["api_key"] == "[REDACTED]"
    assert records[0].arguments_redacted["query"] == "SELECT * FROM users"


@pytest.mark.asyncio
async def test_interceptor_rejected_execution_never_reaches_upstream(
    audit_repo: AuditRepository,
) -> None:
    """Test 3: Rejected request must NEVER reach the upstream server."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    # Auto-reject hook
    async def reject_hook(req: AuthorizationRequest) -> None:
        decision = AuthorizationDecision(
            request_id=req.request_id,
            decision=AuthorizationStatus.REJECTED,
            reason="Blocked suspicious command",
        )
        await auth_mgr.submit_decision(decision)

    auth_mgr.set_notification_hook(reject_hook)

    # Mock Upstream Manager
    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Safe Server"

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=audit_repo,
    )

    result = await interceptor.intercept_call_tool(
        name="dangerous_format_disk",
        arguments={"drive": "C:"},
    )

    # 1. Upstream must NEVER be called
    mock_upstream.call_tool.assert_not_called()

    # 2. Result must indicate error and explain block
    assert result.is_error
    assert "was blocked by user" in result.content[0].text  # type: ignore

    # 3. Audit log must record USER_REJECTED / BLOCKED
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].execution_outcome in ("BLOCKED", "USER_REJECTED")


@pytest.mark.asyncio
async def test_interceptor_timeout_never_reaches_upstream(audit_repo: AuditRepository) -> None:
    """Test 4: Timed-out request must NEVER reach the upstream server and fail closed."""
    auth_mgr = AuthorizationManager(timeout_seconds=0.1, audit_repository=audit_repo)
    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Safe Server"

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=audit_repo,
    )

    result = await interceptor.intercept_call_tool(
        name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )

    # Upstream must never be called
    mock_upstream.call_tool.assert_not_called()
    assert result.is_error
    assert "Authorization timed out" in result.content[0].text  # type: ignore


@pytest.mark.asyncio
async def test_interceptor_upstream_crash_handled_gracefully(audit_repo: AuditRepository) -> None:
    """Test 7: Upstream process crash/failure returns controlled error and audits failure."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    async def approve_hook(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
            )
        )

    auth_mgr.set_notification_hook(approve_hook)

    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Safe Server"
    mock_upstream.call_tool.side_effect = UpstreamConnectionError(
        "Subprocess closed pipe unexpectedly"
    )

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=audit_repo,
    )

    result = await interceptor.intercept_call_tool(
        name="crash_tool",
        arguments={},
    )

    assert result.is_error
    assert "Upstream MCP server error" in result.content[0].text  # type: ignore

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].execution_outcome == "UPSTREAM_FAILURE"
