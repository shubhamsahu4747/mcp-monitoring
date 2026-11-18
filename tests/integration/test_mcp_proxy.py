"""Integration tests for Walled AI MCP Security Proxy with Safe Mock MCP Server.

Verifies end-to-end:
- Test 1: Tool discovery (tools/list)
- Test 2: Approved request executes upstream exactly once
- Test 3: Rejected request never reaches upstream
- Test 4: Authorization timeout fails closed
- Test 5: Concurrent requests maintain independent authorization states
- Test 6: Duplicate decisions are rejected
- Test 7: Upstream process failure is handled gracefully
- Test 8: Argument integrity (original sent upstream, redacted in audit)
- Test 9: Proxy shutdown cleanly terminates pending requests and subprocesses
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import mcp.types as types
import pytest

from app.authorization.manager import AuthorizationManager
from app.gateway.local_proxy import LocalMCPProxy
from app.gateway.upstream_manager import UpstreamManager
from app.interception.request_interceptor import RequestInterceptor
from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    DuplicateDecisionError,
)
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager


@pytest.fixture
async def integration_environment(
    tmp_path: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Fixture initializing an isolated database, upstream manager, and proxy."""
    db_file = tmp_path / "integration_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    # Spawn actual safe mock server as upstream subprocess
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

    proxy = LocalMCPProxy(
        upstream_mgr=upstream_mgr,
        interceptor=interceptor,
        server_name="Walled AI Gateway Integration Proxy",
    )

    yield {
        "proxy": proxy,
        "auth_mgr": auth_mgr,
        "upstream_mgr": upstream_mgr,
        "audit_repo": audit_repo,
    }

    # Teardown
    await proxy.stop()


@pytest.mark.asyncio
async def test_1_tool_discovery(integration_environment: Dict[str, Any]) -> None:
    """Test 1: Connect proxy to safe mock MCP server, request tools/list, verify tools."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    result = await proxy._handle_list_tools(ctx=None)

    assert result is not None
    assert isinstance(result.tools, list)
    assert len(result.tools) >= 4

    tool_names = [tool.name for tool in result.tools]
    assert "get_system_info" in tool_names
    assert "calculate_sum" in tool_names
    assert "read_demo_file" in tool_names
    assert "create_demo_file" in tool_names

    # Verify input schemas are preserved
    calc_tool = next(t for t in result.tools if t.name == "calculate_sum")
    assert calc_tool.description is not None
    assert "sum" in calc_tool.description.lower()


@pytest.mark.asyncio
async def test_2_approved_request_executes_upstream(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 2: Call safe mock tool, approve request, verify upstream execution and client result."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]
    audit_repo: AuditRepository = integration_environment["audit_repo"]

    async def approve_hook(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
                reason="User approved addition",
            )
        )

    auth_mgr.set_notification_hook(approve_hook)

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 18.5, "b": 23.5},
    )

    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert not result.is_error
    assert len(result.content) == 1
    # 18.5 + 23.5 == 42.0
    text_content = result.content[0]
    assert isinstance(text_content, types.TextContent)
    assert "42" in text_content.text

    # Verify audit record
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.APPROVED
    assert records[0].execution_outcome == "SUCCESS"


@pytest.mark.asyncio
async def test_3_rejected_request_never_executes_upstream(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 3: Call safe mock tool, reject request, verify upstream tool NEVER executes."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]
    audit_repo: AuditRepository = integration_environment["audit_repo"]

    target_file = "should_not_exist.txt"
    demo_path = Path("data/demo_sandbox") / target_file
    if demo_path.exists():
        demo_path.unlink()

    async def reject_hook(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.REJECTED,
                reason="Unauthorized file write attempt",
            )
        )

    auth_mgr.set_notification_hook(reject_hook)

    params = types.CallToolRequestParams(
        name="create_demo_file",
        arguments={"filename": target_file, "content": "Sensitive Payload"},
    )

    result = await proxy._handle_call_tool(ctx=None, params=params)

    # 1. Error returned to client
    assert result.is_error
    assert "was blocked by user" in result.content[0].text  # type: ignore

    # 2. File was NEVER created upstream
    assert not demo_path.exists(), "Upstream file was created despite rejection!"

    # 3. Audit record shows REJECTED / BLOCKED
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].execution_outcome in ("BLOCKED", "USER_REJECTED")


@pytest.mark.asyncio
async def test_4_authorization_timeout_fails_closed(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 4: Unanswered request times out, fails closed, and never executes upstream."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]
    audit_repo: AuditRepository = integration_environment["audit_repo"]

    # Shorten timeout for test
    auth_mgr.timeout_seconds = 0.1
    auth_mgr.set_notification_hook(None)  # Do not respond

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 100, "b": 200},
    )

    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "Authorization timed out" in result.content[0].text  # type: ignore

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.TIMED_OUT
    assert records[0].execution_outcome == "TIMED_OUT"


@pytest.mark.asyncio
async def test_5_concurrent_requests_state_isolation(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 5: Submit 10 concurrent calls with mixed decisions and verify isolated results."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]

    async def mixed_responder(req: AuthorizationRequest) -> None:
        await asyncio.sleep(0.02)
        idx = int(req.arguments["a"])
        # Even indices approved, odd indices rejected
        status = AuthorizationStatus.APPROVED if idx % 2 == 0 else AuthorizationStatus.REJECTED
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=status,
                reason=f"Decision for idx {idx}",
            )
        )

    auth_mgr.set_notification_hook(mixed_responder)

    num_calls = 10
    tasks = [
        proxy._handle_call_tool(
            ctx=None,
            params=types.CallToolRequestParams(
                name="calculate_sum",
                arguments={"a": i, "b": 10.0},
            ),
        )
        for i in range(num_calls)
    ]

    results: List[types.CallToolResult] = await asyncio.gather(*tasks)

    for i, res in enumerate(results):
        if i % 2 == 0:
            # Approved -> 10.0 + i
            assert not res.is_error
            expected_sum = float(i + 10)
            assert str(expected_sum) in res.content[0].text  # type: ignore
        else:
            # Rejected
            assert res.is_error
            assert "was blocked by user" in res.content[0].text  # type: ignore


@pytest.mark.asyncio
async def test_6_duplicate_decisions_rejected(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 6: Attempting to approve a request more than once must be rejected."""
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]

    # Start pending request
    task = asyncio.create_task(
        auth_mgr.request_authorization(
            server_name="Safe Mock MCP Server",
            tool_name="calculate_sum",
            arguments={"a": 1, "b": 2},
        )
    )
    await asyncio.sleep(0.02)

    pending = await auth_mgr.registry.list_pending()
    assert len(pending) == 1
    req_id = pending[0].request_id

    decision = AuthorizationDecision(
        request_id=req_id,
        decision=AuthorizationStatus.APPROVED,
        reason="First approval",
    )

    # First decision succeeds
    await auth_mgr.submit_decision(decision)
    await task

    # Second decision attempt on same request must fail
    with pytest.raises((DuplicateDecisionError, KeyError)):
        await auth_mgr.submit_decision(decision)


@pytest.mark.asyncio
async def test_7_upstream_server_failure_handling(tmp_path: Path) -> None:
    """Test 7: Unavailable or crashed upstream server returns controlled error and cleans up."""
    db_file = tmp_path / "upstream_crash.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    async def approve_hook(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
            )
        )

    auth_mgr.set_notification_hook(approve_hook)

    # Configure invalid non-existent executable to simulate crash/unavailability
    bad_upstream = UpstreamManager(
        command="non_existent_binary_xyz_123.exe",
        args=[],
        server_name="Crashed Server",
    )

    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=bad_upstream,
        audit_repository=audit_repo,
    )

    proxy = LocalMCPProxy(
        upstream_mgr=bad_upstream,
        interceptor=interceptor,
        server_name="Crash Test Proxy",
    )

    params = types.CallToolRequestParams(
        name="any_tool",
        arguments={},
    )

    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "Upstream MCP server error" in result.content[0].text  # type: ignore

    # Verify audit record marked failure
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].execution_outcome == "UPSTREAM_FAILURE"

    await proxy.stop()


@pytest.mark.asyncio
async def test_8_argument_integrity_and_redacted_audit(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 8: Upstream receives untouched arguments, but audit record redacts sensitive keys."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]
    audit_repo: AuditRepository = integration_environment["audit_repo"]

    async def approve_hook(req: AuthorizationRequest) -> None:
        await auth_mgr.submit_decision(
            AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.APPROVED,
            )
        )

    auth_mgr.set_notification_hook(approve_hook)

    test_filename = "integrity_test.txt"
    demo_path = Path("data/demo_sandbox") / test_filename
    if demo_path.exists():
        demo_path.unlink()

    sensitive_content = "SuperSecretTokenValue_98765"

    params = types.CallToolRequestParams(
        name="create_demo_file",
        arguments={
            "filename": test_filename,
            "content": sensitive_content,
            "secret_key": "raw_sensitive_key_abcdef",
            "password": "cleartext_password_xyz",
        },
    )

    result = await proxy._handle_call_tool(ctx=None, params=params)

    # 1. Execution succeeded
    assert not result.is_error

    # 2. Upstream created file with UNTOUCHED sensitive content
    assert demo_path.exists()
    assert demo_path.read_text(encoding="utf-8") == sensitive_content

    # 3. Audit record contains REDACTED values for sensitive keys
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    audit = records[0]
    assert audit.decision == AuthorizationStatus.APPROVED
    assert audit.execution_outcome == "SUCCESS"
    assert audit.arguments_redacted["secret_key"] == "[REDACTED]"
    assert audit.arguments_redacted["password"] == "[REDACTED]"
    assert audit.arguments_redacted["filename"] == test_filename

    # Clean up file
    demo_path.unlink()


@pytest.mark.asyncio
async def test_9_proxy_shutdown_cleans_pending_and_upstream(
    integration_environment: Dict[str, Any],
) -> None:
    """Test 9: Shutting down proxy cancels pending requests safely and stops upstream process."""
    proxy: LocalMCPProxy = integration_environment["proxy"]
    auth_mgr: AuthorizationManager = integration_environment["auth_mgr"]
    upstream_mgr: UpstreamManager = integration_environment["upstream_mgr"]

    # Ensure upstream is connected first
    await upstream_mgr.ensure_connected()
    assert upstream_mgr.is_connected

    # Start pending request without resolving it
    task = asyncio.create_task(
        proxy._handle_call_tool(
            ctx=None,
            params=types.CallToolRequestParams(name="calculate_sum", arguments={"a": 1, "b": 2}),
        )
    )
    await asyncio.sleep(0.02)
    assert auth_mgr.registry.count == 1

    # Stop proxy
    await proxy.stop()

    result = await task
    assert result.is_error
    assert "Authorization cancelled" in result.content[0].text  # type: ignore
    assert auth_mgr.registry.count == 0
    assert not upstream_mgr.is_connected
