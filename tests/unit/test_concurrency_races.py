"""Unit tests for concurrency races, timeout boundaries, and at-most-once guarantees."""

import asyncio
from pathlib import Path
from typing import List

import pytest

from app.authorization.manager import AuthorizationManager
from app.models.requests import (
    AlreadyDispatchedError,
    AuthorizationCancelledError,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    AuthorizationTimeoutError,
    DuplicateDecisionError,
    InvalidStateTransitionError,
)
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager


@pytest.fixture
async def audit_repo(tmp_path: Path) -> AuditRepository:
    """Fixture providing an isolated SQLite audit repository."""
    db_file = tmp_path / "test_concurrency.db"
    manager = DatabaseManager(db_file)
    repo = AuditRepository(manager)
    await repo.initialize()
    return repo


@pytest.mark.asyncio
async def test_race_approval_just_before_timeout(audit_repo: AuditRepository) -> None:
    """Race 1: Approval arrives just before the timeout threshold."""
    auth_mgr = AuthorizationManager(timeout_seconds=0.2, audit_repository=audit_repo)

    async def late_approval(req: AuthorizationRequest) -> None:
        # Sleep for 0.05s (before 0.2s timeout)
        await asyncio.sleep(0.05)
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
                reason="Approved just in time",
            )
        )

    auth_mgr.set_notification_hook(late_approval)

    result = await auth_mgr.request_authorization(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )

    assert result.status == AuthorizationStatus.APPROVED
    assert result.reason == "Approved just in time"


@pytest.mark.asyncio
async def test_race_approval_immediately_after_timeout(audit_repo: AuditRepository) -> None:
    """Race 2 & 3: Approval attempts to arrive after timeout has expired."""
    auth_mgr = AuthorizationManager(timeout_seconds=0.05, audit_repository=audit_repo)
    intercepted_req: List[AuthorizationRequest] = []

    async def delayed_hook(req: AuthorizationRequest) -> None:
        intercepted_req.append(req)

    auth_mgr.set_notification_hook(delayed_hook)

    with pytest.raises(AuthorizationTimeoutError):
        await auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="calculate_sum",
            arguments={"a": 1, "b": 2},
        )

    assert len(intercepted_req) == 1
    req = intercepted_req[0]
    assert req.status == AuthorizationStatus.TIMED_OUT

    # Decision submitted after timeout must be rejected
    with pytest.raises(DuplicateDecisionError, match="terminal state TIMED_OUT"):
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
                reason="Late approval after expiration",
            )
        )


@pytest.mark.asyncio
async def test_race_concurrent_approval_and_rejection(audit_repo: AuditRepository) -> None:
    """Race 4: Rejection races with Approval concurrently on the same request."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    task = asyncio.create_task(
        auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="calculate_sum",
            arguments={"a": 1, "b": 2},
        )
    )
    await asyncio.sleep(0.01)

    pending = await auth_mgr.registry.list_pending()
    assert len(pending) == 1
    req_id = pending[0].request_id

    d_approve = AuthorizationDecision(request_id=req_id, decision=AuthorizationStatus.APPROVED)
    d_reject = AuthorizationDecision(request_id=req_id, decision=AuthorizationStatus.REJECTED)

    # Submit both concurrently
    results = await asyncio.gather(
        auth_mgr.submit_decision(d_approve),
        auth_mgr.submit_decision(d_reject),
        return_exceptions=True,
    )

    # Exactly one must succeed, the other must raise DuplicateDecisionError
    successes = [r for r in results if isinstance(r, AuthorizationRequest)]
    failures = [r for r in results if isinstance(r, DuplicateDecisionError)]

    assert len(successes) == 1
    assert len(failures) == 1

    final_req = await task
    assert final_req.status in (AuthorizationStatus.APPROVED, AuthorizationStatus.REJECTED)


@pytest.mark.asyncio
async def test_race_cancellation_with_approval(audit_repo: AuditRepository) -> None:
    """Race 5: Cancellation races with Approval."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    task = asyncio.create_task(
        auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="calculate_sum",
            arguments={"a": 1, "b": 2},
        )
    )
    await asyncio.sleep(0.01)

    pending = await auth_mgr.registry.list_pending()
    assert len(pending) == 1
    req_id = pending[0].request_id

    # Cancel task while submitting approval
    task.cancel()
    try:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req_id,
                decision=AuthorizationStatus.APPROVED,
            )
        )
    except (DuplicateDecisionError, KeyError):
        pass  # Cancellation won

    try:
        await task
    except (AuthorizationCancelledError, asyncio.CancelledError):
        pass  # Cancellation completed


@pytest.mark.asyncio
async def test_at_most_once_dispatch_enforcement(audit_repo: AuditRepository) -> None:
    """At-most-once: AuthorizationRequest cannot be dispatched more than once."""
    req = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={},
    )
    # Cannot dispatch when PENDING
    with pytest.raises(InvalidStateTransitionError):
        req.mark_dispatched()

    # Transition to APPROVED
    req.transition_to(AuthorizationStatus.APPROVED)
    assert not req.dispatched
    req.mark_dispatched()
    assert req.dispatched
    assert req.dispatched_at is not None

    # Second dispatch must be rejected
    with pytest.raises(AlreadyDispatchedError):
        req.mark_dispatched()


@pytest.mark.asyncio
async def test_concurrent_duplicate_decisions_rejected(audit_repo: AuditRepository) -> None:
    """Verify multiple concurrent approvals on the same request ID reject duplicates."""
    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    task = asyncio.create_task(
        auth_mgr.request_authorization(
            server_name="test-server",
            tool_name="calculate_sum",
            arguments={"a": 5, "b": 10},
        )
    )
    await asyncio.sleep(0.01)

    pending = await auth_mgr.registry.list_pending()
    req_id = pending[0].request_id

    decisions = [
        AuthorizationDecision(request_id=req_id, decision=AuthorizationStatus.APPROVED)
        for _ in range(5)
    ]

    results = await asyncio.gather(
        *(auth_mgr.submit_decision(d) for d in decisions),
        return_exceptions=True,
    )

    success_count = sum(1 for r in results if isinstance(r, AuthorizationRequest))
    dup_count = sum(1 for r in results if isinstance(r, DuplicateDecisionError))

    assert success_count == 1
    assert dup_count == 4
    await task
