"""Fault injection tests: Database failures, Hook crashes, and Fail-closed behavior."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.authorization.manager import AuthorizationManager
from app.gateway.upstream_manager import UpstreamConnectionError, UpstreamManager
from app.interception.request_interceptor import RequestInterceptor
from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    AuthorizationTimeoutError,
)
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager


@pytest.fixture
async def audit_repo(tmp_path: Path) -> AuditRepository:
    """Fixture providing an isolated SQLite audit repository."""
    db_file = tmp_path / "test_fault.db"
    manager = DatabaseManager(db_file)
    repo = AuditRepository(manager)
    await repo.initialize()
    return repo


@pytest.mark.asyncio
async def test_fault_notification_hook_crashes_fails_closed(audit_repo: AuditRepository) -> None:
    """Fault: Hook raises unhandled exception; must NOT cause implicit approval."""
    auth_mgr = AuthorizationManager(timeout_seconds=0.1, audit_repository=audit_repo)

    async def exploding_hook(req: AuthorizationRequest) -> None:
        raise RuntimeError("Notification webhook network connection failed!")

    auth_mgr.set_notification_hook(exploding_hook)

    # Request must still wait and time out (fail closed), not approve
    with pytest.raises(AuthorizationTimeoutError):
        await auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="sensitive_action",
            arguments={},
        )


@pytest.mark.asyncio
async def test_fault_database_unavailable_during_rejection() -> None:
    """Fault: Audit database fails; rejection remains fail-closed."""
    broken_audit_repo = AsyncMock(spec=AuditRepository)
    broken_audit_repo.record_audit.side_effect = RuntimeError("Disk full / DB locked")

    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=broken_audit_repo)

    async def auto_reject(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.REJECTED,
                reason="Denied by user",
            )
        )

    auth_mgr.set_notification_hook(auto_reject)

    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Server"

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=broken_audit_repo,
    )

    # Calling tool must still return blocked tool error
    result = await interceptor.intercept_call_tool(name="delete_database", arguments={})

    assert result.is_error
    assert "was blocked by user" in result.content[0].text  # type: ignore
    mock_upstream.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_fault_database_unavailable_during_timeout() -> None:
    """Fault: Audit database fails during timeout; timeout remains fail-closed."""
    broken_audit_repo = AsyncMock(spec=AuditRepository)
    broken_audit_repo.record_audit.side_effect = RuntimeError("Database I/O error")

    auth_mgr = AuthorizationManager(timeout_seconds=0.05, audit_repository=broken_audit_repo)
    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Server"

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=broken_audit_repo,
    )

    result = await interceptor.intercept_call_tool(name="calculate_sum", arguments={})
    assert result.is_error
    assert "Authorization timed out" in result.content[0].text  # type: ignore
    mock_upstream.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_fault_upstream_crash_during_execution(audit_repo: AuditRepository) -> None:
    """Fault: Upstream process crashes while executing tool."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    async def auto_approve(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
            )
        )

    auth_mgr.set_notification_hook(auto_approve)

    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Crash Server"
    mock_upstream.call_tool.side_effect = UpstreamConnectionError(
        "Broken pipe: process exited with code -9"
    )

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=audit_repo,
    )

    result = await interceptor.intercept_call_tool(name="crash_test", arguments={})
    assert result.is_error
    assert "Upstream MCP server error" in result.content[0].text  # type: ignore

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].execution_outcome == "UPSTREAM_FAILURE"


@pytest.mark.asyncio
async def test_fault_high_concurrency_timeouts_fail_closed(audit_repo: AuditRepository) -> None:
    """Fault: High concurrency workload (20 requests) all left unanswered; all fail closed."""
    auth_mgr = AuthorizationManager(timeout_seconds=0.05, audit_repository=audit_repo)
    mock_upstream = AsyncMock(spec=UpstreamManager)
    mock_upstream.server_name = "Mock Server"

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=mock_upstream,
        audit_repository=audit_repo,
    )

    num_concurrent = 20
    tasks = [
        interceptor.intercept_call_tool(name="sum_tool", arguments={"idx": i})
        for i in range(num_concurrent)
    ]

    results = await asyncio.gather(*tasks)

    # 1. Upstream must NEVER be called
    mock_upstream.call_tool.assert_not_called()

    # 2. Every single result must report timeout error
    for res in results:
        assert res.is_error
        assert "Authorization timed out" in res.content[0].text  # type: ignore

    # 3. Registry must be completely clean (0 leaked requests)
    assert auth_mgr.registry.count == 0

    # 4. All 20 persisted as TIMED_OUT
    records = await audit_repo.list_records(limit=50)
    assert len(records) == num_concurrent
    assert all(r.decision == AuthorizationStatus.TIMED_OUT for r in records)
