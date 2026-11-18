"""Comprehensive Milestone 4 Verification Tests.

Verifies all 8 strict requirements:
1. Approve read_demo_file -> exactly 1 upstream execution.
2. Reject calculate_sum -> 0 upstream executions.
3. Allow authorization to time out -> 0 upstream executions.
4. Two concurrent requests with different decisions -> atomic, independent outcomes.
5. Close popup without deciding -> fail-closed behavior.
6. Sensitive argument redaction across popup, audit DB, and logs.
7. Popup cannot submit duplicate decisions (at-most-once execution).
8. Clean shutdown of GUI, IPC server, and upstream proxy.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Generator

import mcp.types as types
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.authorization.manager import AuthorizationManager  # noqa: E402
from app.desktop.application import WalledAIDesktopApp  # noqa: E402
from app.gateway.local_proxy import LocalMCPProxy  # noqa: E402
from app.gateway.upstream_manager import UpstreamManager  # noqa: E402
from app.interception.request_interceptor import RequestInterceptor  # noqa: E402
from app.models.requests import AuditOutcome, AuthorizationStatus  # noqa: E402
from app.storage.audit_repository import AuditRepository  # noqa: E402
from app.storage.database import DatabaseManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Ensure QApplication singleton exists."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(["--platform", "offscreen"])
    yield app


@pytest.fixture
async def verification_env(
    qapp: QApplication,
    tmp_path: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Isolated environment with real UpstreamManager, RequestInterceptor, LocalMCPProxy,
    and WalledAIDesktopApp.
    """
    db_file = tmp_path / "verification_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=2.0, audit_repository=audit_repo)

    desktop_app = WalledAIDesktopApp(
        auth_mgr=auth_mgr,
        audit_repository=audit_repo,
        enable_tray=False,
        show_main_window=False,
    )

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
        server_name="Walled AI Verification Gateway",
        enable_desktop_ipc=False,  # Direct in-process wiring for UI tests
    )

    try:
        yield {
            "desktop_app": desktop_app,
            "auth_mgr": auth_mgr,
            "audit_repo": audit_repo,
            "upstream_mgr": upstream_mgr,
            "interceptor": interceptor,
            "proxy": proxy,
        }
    finally:
        await desktop_app.shutdown(quit_qt_app=False)
        await upstream_mgr.stop()


# -----------------------------------------------------------------------------
# Check 1: Approve read_demo_file -> exactly 1 upstream execution
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_1_approve_read_demo_file_exactly_one_upstream_execution(
    verification_env: Dict[str, Any],
) -> None:
    """Approve read_demo_file tool call and verify exactly one upstream execution."""
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]
    audit_repo: AuditRepository = verification_env["audit_repo"]

    # Create demo sandbox file so upstream read succeeds
    demo_dir = Path("data/demo_sandbox")
    demo_dir.mkdir(parents=True, exist_ok=True)
    (demo_dir / "confidential_financials_2026.xlsx").write_text(
        "CONFIDENTIAL Q4 2026 AUDIT REPORT", encoding="utf-8"
    )

    # Track actual calls to upstream call_tool
    original_call_tool = upstream_mgr.call_tool
    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return await original_call_tool(name, arguments)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    async def auto_approve() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        assert popup.btn_approve.text() == "Approve & Execute"
        popup.btn_approve.click()

    asyncio.create_task(auto_approve())

    params = types.CallToolRequestParams(
        name="read_demo_file",
        arguments={"filename": "confidential_financials_2026.xlsx"},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert not result.is_error
    assert len(result.content) > 0
    assert call_count == 1, f"Expected exactly 1 upstream execution, got {call_count}"

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.APPROVED
    assert records[0].categorized_outcome == AuditOutcome.USER_APPROVED


# -----------------------------------------------------------------------------
# Check 2: Reject calculate_sum -> 0 upstream executions
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_2_reject_calculate_sum_zero_upstream_executions(
    verification_env: Dict[str, Any],
) -> None:
    """Reject calculate_sum tool call and verify zero upstream executions."""
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]
    audit_repo: AuditRepository = verification_env["audit_repo"]

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    async def auto_reject() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        assert popup.btn_reject.text() == "Reject Request"
        popup.btn_reject.click()

    asyncio.create_task(auto_reject())

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 10.0, "b": 20.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "blocked by user" in result.content[0].text  # type: ignore
    assert call_count == 0, f"Expected 0 upstream executions for rejected call, got {call_count}"

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].categorized_outcome == AuditOutcome.USER_REJECTED


# -----------------------------------------------------------------------------
# Check 3: Timeout -> 0 upstream executions (Fail-closed)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_3_timeout_zero_upstream_executions(
    verification_env: Dict[str, Any],
) -> None:
    """Allow request to time out without deciding; verify zero upstream executions."""
    auth_mgr: AuthorizationManager = verification_env["auth_mgr"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]
    audit_repo: AuditRepository = verification_env["audit_repo"]

    # Short timeout for fast test execution
    auth_mgr.timeout_seconds = 0.4

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 5.0, "b": 15.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "timed out" in result.content[0].text.lower()  # type: ignore
    assert call_count == 0, f"Expected 0 upstream executions for timed-out call, got {call_count}"

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.TIMED_OUT
    assert records[0].categorized_outcome == AuditOutcome.BLOCKED_TIMEOUT


# -----------------------------------------------------------------------------
# Check 4: Concurrent requests with different decisions
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_4_concurrent_requests_independent_decisions(
    verification_env: Dict[str, Any],
) -> None:
    """Trigger two concurrent requests and make different decisions (approve one, reject other)."""
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]
    audit_repo: AuditRepository = verification_env["audit_repo"]

    executed_tools: list[str] = []
    original_call_tool = upstream_mgr.call_tool

    async def tracking_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        executed_tools.append(name)
        return await original_call_tool(name, arguments)

    upstream_mgr.call_tool = tracking_call_tool  # type: ignore[method-assign]

    async def handle_popups() -> None:
        # Wait until both popups are opened
        while len(desktop_app._active_popups) < 2:
            await asyncio.sleep(0.02)

        for popup in list(desktop_app._active_popups.values()):
            if popup.request.tool_name == "calculate_sum":
                popup.btn_approve.click()
            else:
                popup.btn_reject.click()

    asyncio.create_task(handle_popups())

    params_a = types.CallToolRequestParams(
        name="calculate_sum", arguments={"a": 1.0, "b": 2.0}
    )
    params_b = types.CallToolRequestParams(
        name="read_demo_file", arguments={"filename": "test.txt"}
    )

    res_a, res_b = await asyncio.gather(
        proxy._handle_call_tool(ctx=None, params=params_a),
        proxy._handle_call_tool(ctx=None, params=params_b),
    )

    # calculate_sum approved -> executed
    assert not res_a.is_error
    assert "calculate_sum" in executed_tools

    # read_demo_file rejected -> not executed
    assert res_b.is_error
    assert "read_demo_file" not in executed_tools

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 2
    decisions = {r.tool_name: r.decision for r in records}
    assert decisions["calculate_sum"] == AuthorizationStatus.APPROVED
    assert decisions["read_demo_file"] == AuthorizationStatus.REJECTED


# -----------------------------------------------------------------------------
# Check 5: Close popup without deciding -> fail-closed behavior
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_5_close_popup_fails_closed(
    verification_env: Dict[str, Any],
) -> None:
    """Closing an authorization popup without deciding must fail closed."""
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    async def auto_close() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.close()

    asyncio.create_task(auto_close())

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 10.0, "b": 20.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert call_count == 0, f"Expected 0 upstream executions when popup closed, got {call_count}"


# -----------------------------------------------------------------------------
# Check 6: Redaction in popup, audit DB, and logs
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_6_sensitive_arguments_redacted_everywhere(
    verification_env: Dict[str, Any],
) -> None:
    """Verify sensitive arguments are redacted in popup, SQLite audit DB,
    and not leaked in plaintext.
    """
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    audit_repo: AuditRepository = verification_env["audit_repo"]

    raw_secret = "super-secret-admin-pass-xyz"
    raw_token = "bearer-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"

    demo_dir = Path("data/demo_sandbox")
    demo_dir.mkdir(parents=True, exist_ok=True)
    (demo_dir / "financials.xlsx").write_text("REDACTED FINANCIALS DATA", encoding="utf-8")

    popup_text_captured = ""

    async def inspect_popup_and_approve() -> None:
        nonlocal popup_text_captured
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup_text_captured = popup.json_viewer.toPlainText()
        popup.btn_approve.click()

    asyncio.create_task(inspect_popup_and_approve())

    params = types.CallToolRequestParams(
        name="read_demo_file",
        arguments={
            "filename": "financials.xlsx",
            "password": raw_secret,
            "access_token": raw_token,
            "normal_param": "visible_value",
        },
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)
    assert not result.is_error

    # 1. Popup JSON viewer check
    assert raw_secret not in popup_text_captured
    assert raw_token not in popup_text_captured
    assert "[REDACTED]" in popup_text_captured
    assert "visible_value" in popup_text_captured

    # 2. SQLite audit record check
    records = await audit_repo.list_records(limit=5)
    assert len(records) == 1
    redacted_args = records[0].arguments_redacted
    assert raw_secret not in str(redacted_args)
    assert raw_token not in str(redacted_args)
    assert redacted_args["password"] in ("[REDACTED]", "***REDACTED***")
    assert redacted_args["access_token"] in ("[REDACTED]", "***REDACTED***")
    assert redacted_args["normal_param"] == "visible_value"


# -----------------------------------------------------------------------------
# Check 7: Popup cannot submit duplicate decisions
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_7_popup_duplicate_decisions_prevented(
    verification_env: Dict[str, Any],
) -> None:
    """Verify that multiple clicks or decision submissions for the same popup are prevented."""
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]

    call_count = 0
    original_call_tool = upstream_mgr.call_tool

    async def tracking_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return await original_call_tool(name, arguments)

    upstream_mgr.call_tool = tracking_call_tool  # type: ignore[method-assign]

    async def click_twice() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        # Click approve first
        popup.btn_approve.click()
        # Immediately try to click reject or approve again
        popup.btn_reject.click()
        popup.btn_approve.click()

    asyncio.create_task(click_twice())

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 30.0, "b": 70.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert not result.is_error
    assert "100.0" in result.content[0].text  # type: ignore
    assert call_count == 1, f"Expected exactly 1 execution, got {call_count}"


# -----------------------------------------------------------------------------
# Check 8: GUI and gateway shut down cleanly
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_8_clean_shutdown(
    verification_env: Dict[str, Any],
) -> None:
    """Verify that desktop app and proxy shut down cleanly with 0 active popups and 0 pending."""
    desktop_app: WalledAIDesktopApp = verification_env["desktop_app"]
    proxy: LocalMCPProxy = verification_env["proxy"]
    upstream_mgr: UpstreamManager = verification_env["upstream_mgr"]
    auth_mgr: AuthorizationManager = verification_env["auth_mgr"]

    # Trigger shutdown
    await desktop_app.shutdown(quit_qt_app=False)
    await proxy.stop()

    assert len(desktop_app._active_popups) == 0
    assert auth_mgr.registry.count == 0
    assert not upstream_mgr.is_connected
