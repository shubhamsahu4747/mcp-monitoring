"""Unit tests for the Authorization subsystem (registry, decisions, manager, timeouts)."""

import asyncio
from pathlib import Path
from typing import List

import pytest

from app.authorization.decisions import DecisionProcessor
from app.authorization.manager import AuthorizationManager
from app.authorization.pending_requests import PendingRequestRegistry
from app.models.requests import (
    AuthorizationCancelledError,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    AuthorizationTimeoutError,
    DuplicateDecisionError,
)
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager


@pytest.fixture
async def audit_repo(tmp_path: Path) -> AuditRepository:
    """Fixture providing an isolated SQLite audit repository."""
    db_file = tmp_path / "test_auth.db"
    manager = DatabaseManager(db_file)
    repo = AuditRepository(manager)
    await repo.initialize()
    return repo


@pytest.mark.asyncio
async def test_pending_registry_operations() -> None:
    """Verify registration, retrieval, removal, and listing in PendingRequestRegistry."""
    registry = PendingRequestRegistry()
    loop = asyncio.get_running_loop()

    req = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )
    future = loop.create_future()

    assert registry.count == 0
    await registry.register(req, future)
    assert registry.count == 1

    entry = await registry.get(req.request_id)
    assert entry is not None
    assert entry.request.tool_name == "calculate_sum"

    pending_list = await registry.list_pending()
    assert len(pending_list) == 1
    assert pending_list[0].request_id == req.request_id

    removed = await registry.remove(req.request_id)
    assert removed is not None
    assert registry.count == 0
    assert await registry.get(req.request_id) is None


@pytest.mark.asyncio
async def test_decision_processor_atomic_transitions() -> None:
    """Verify that decisions resolve futures and duplicate decisions are rejected."""
    registry = PendingRequestRegistry()
    processor = DecisionProcessor(registry)
    loop = asyncio.get_running_loop()

    req = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )
    future = loop.create_future()
    await registry.register(req, future)

    decision = AuthorizationDecision(
        request_id=req.request_id,
        decision=AuthorizationStatus.APPROVED,
        reason="Approved by user",
    )

    resolved_req = await processor.apply_decision(decision)
    assert resolved_req.status == AuthorizationStatus.APPROVED
    assert future.done()
    assert future.result() == decision
    assert registry.count == 0

    # Test 6: Duplicate decision protection
    with pytest.raises((DuplicateDecisionError, KeyError)):
        # Already removed from registry
        await processor.apply_decision(decision)


@pytest.mark.asyncio
async def test_authorization_manager_approve_flow(audit_repo: AuditRepository) -> None:
    """Test 2: Request authorization, apply approval, verify request returns APPROVED."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    async def auto_approve(request: AuthorizationRequest) -> None:
        await asyncio.sleep(0.01)
        decision = AuthorizationDecision(
            request_id=request.request_id,
            decision=AuthorizationStatus.APPROVED,
            reason="Automated test approval",
        )
        await auth_mgr.submit_decision(decision)

    auth_mgr.set_notification_hook(auto_approve)

    result_req = await auth_mgr.request_authorization(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 10, "b": 20},
    )
    assert result_req.status == AuthorizationStatus.APPROVED
    assert result_req.reason == "Automated test approval"


@pytest.mark.asyncio
async def test_authorization_manager_reject_flow(audit_repo: AuditRepository) -> None:
    """Test 3: Request authorization, apply rejection, verify request returns REJECTED."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    async def auto_reject(request: AuthorizationRequest) -> None:
        await asyncio.sleep(0.01)
        decision = AuthorizationDecision(
            request_id=request.request_id,
            decision=AuthorizationStatus.REJECTED,
            reason="Denied suspicious arguments",
        )
        await auth_mgr.submit_decision(decision)

    auth_mgr.set_notification_hook(auto_reject)

    result_req = await auth_mgr.request_authorization(
        server_name="test-server",
        tool_name="read_demo_file",
        arguments={"filename": "secret.txt"},
    )
    assert result_req.status == AuthorizationStatus.REJECTED
    assert result_req.reason == "Denied suspicious arguments"


@pytest.mark.asyncio
async def test_authorization_manager_timeout(audit_repo: AuditRepository) -> None:
    """Test 4: Allow authorization timeout to expire, verify fail-closed and audit."""
    auth_mgr = AuthorizationManager(timeout_seconds=0.1, audit_repository=audit_repo)

    with pytest.raises(AuthorizationTimeoutError):
        await auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="calculate_sum",
            arguments={"a": 1, "b": 2},
        )

    # Verify audit record persisted with TIMED_OUT status
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.TIMED_OUT
    assert records[0].execution_outcome == "TIMED_OUT"


@pytest.mark.asyncio
async def test_authorization_manager_concurrent_requests(audit_repo: AuditRepository) -> None:
    """Test 5: Submit at least 10 concurrent tool calls and ensure independent state isolation."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    async def dynamic_responder(request: AuthorizationRequest) -> None:
        await asyncio.sleep(0.01)
        # Even numbered IDs get approved, odd get rejected
        index = int(request.arguments.get("index", 0))
        outcome = AuthorizationStatus.APPROVED if index % 2 == 0 else AuthorizationStatus.REJECTED
        decision = AuthorizationDecision(
            request_id=request.request_id,
            decision=outcome,
            reason=f"Decided for index {index}",
        )
        await auth_mgr.submit_decision(decision)

    auth_mgr.set_notification_hook(dynamic_responder)

    num_requests = 12
    tasks = [
        auth_mgr.request_authorization(
            server_name="test-server",
            tool_name=f"tool_{i}",
            arguments={"index": i, "token": f"secret-token-{i}"},
        )
        for i in range(num_requests)
    ]

    results: List[AuthorizationRequest] = await asyncio.gather(*tasks)

    for i, req in enumerate(results):
        expected_status = (
            AuthorizationStatus.APPROVED if i % 2 == 0 else AuthorizationStatus.REJECTED
        )
        assert req.status == expected_status
        assert req.arguments["index"] == i
        # Original arguments remain untouched
        assert req.arguments["token"] == f"secret-token-{i}"


@pytest.mark.asyncio
async def test_authorization_manager_shutdown_cancellation(audit_repo: AuditRepository) -> None:
    """Test 9: Shutdown while requests are pending cancels them safely and fails closed."""
    auth_mgr = AuthorizationManager(timeout_seconds=10.0, audit_repository=audit_repo)

    # Launch pending authorization without approving it
    task = asyncio.create_task(
        auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="long_running_tool",
            arguments={"param": "value"},
        )
    )
    # Wait until it is registered
    await asyncio.sleep(0.02)
    assert auth_mgr.registry.count == 1

    # Trigger shutdown
    await auth_mgr.shutdown()

    with pytest.raises(AuthorizationCancelledError):
        await task

    assert auth_mgr.registry.count == 0

    # Verify audit record persisted as CANCELLED
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.CANCELLED
    assert records[0].execution_outcome == "CANCELLED"
