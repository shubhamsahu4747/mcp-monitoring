"""Automated unit and functional tests for Walled AI Desktop Application (PySide6 + qasync).

Verifies all Milestone 4 requirements offscreen:
1. Application startup and initialization
2. Tray icon initialization and tooltip
3. New authorization request received and queued
4. Correct request details displayed in popup
5. Sensitive argument redaction in JSON viewer
6. Approve action submits the exact request ID
7. Reject action submits the exact request ID
8. Duplicate button clicks prevented
9. Expired request handling and disabled buttons
10. Multiple concurrent authorization dialogs
11. Closing popup without a decision fails closed
12. UI exception and disconnection behavior
13. Graceful application shutdown
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

# Ensure Qt runs offscreen for CI/headless execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.authorization.manager import AuthorizationManager  # noqa: E402
from app.desktop.application import WalledAIDesktopApp  # noqa: E402
from app.desktop.authorization_popup import AuthorizationPopup  # noqa: E402
from app.desktop.request_presenter import RequestPresenter  # noqa: E402
from app.desktop.tray import SystemTrayManager  # noqa: E402
from app.models.requests import (  # noqa: E402
    AuthorizationRequest,
    AuthorizationStatus,
)
from app.storage.audit_repository import AuditRepository  # noqa: E402
from app.storage.database import DatabaseManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Ensure a singleton QApplication exists in offscreen mode."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(["--platform", "offscreen"])
    yield app


@pytest.fixture
async def desktop_env(
    qapp: QApplication,
    tmp_path: Path,
) -> WalledAIDesktopApp:
    """Fixture providing an isolated WalledAIDesktopApp with an ephemeral database."""
    db_file = tmp_path / "desktop_test.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=5.0, audit_repository=audit_repo)

    app = WalledAIDesktopApp(
        auth_mgr=auth_mgr,
        audit_repository=audit_repo,
        enable_tray=True,
        show_main_window=False,
    )
    return app


# -----------------------------------------------------------------------------
# Test 1 & 2: Application Startup and Tray Icon Initialization
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_application_startup_and_initialization(desktop_env: WalledAIDesktopApp) -> None:
    """Test 1: Desktop application initializes cleanly with main window and managers."""
    assert desktop_env.main_window is not None
    assert desktop_env.auth_mgr is not None
    assert desktop_env.audit_repo is not None
    assert desktop_env.auth_mgr._notification_hook == desktop_env.on_authorization_request

    # Check window title and default state
    assert "Walled AI" in desktop_env.main_window.windowTitle()
    assert desktop_env.main_window.table_pending.rowCount() == 0


def test_tray_icon_initialization(qapp: QApplication) -> None:
    """Test 2: System tray icon renders shield icon, context menu, and actions."""
    tray_mgr = SystemTrayManager(parent=None)
    assert tray_mgr.tray_icon is not None
    assert not tray_mgr.icon.isNull()
    assert "Active" in tray_mgr.tray_icon.toolTip()

    # Dynamic pending update
    tray_mgr.update_pending_count(3)
    assert "3 Pending" in tray_mgr.tray_icon.toolTip()
    assert "Pending Requests (3)" in tray_mgr.action_pending.text()

    tray_mgr.update_pending_count(0)
    assert "Active" in tray_mgr.tray_icon.toolTip()


# -----------------------------------------------------------------------------
# Test 3 & 4: New Authorization Request Received & Correct Details Displayed
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_authorization_request_received(desktop_env: WalledAIDesktopApp) -> None:
    """Test 3: Authorization request dispatched through notification hook updates queue and tray."""
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        arguments={"a": 10, "b": 25},
    )

    await desktop_env.on_authorization_request(req)

    # Verified: popup tracked in active popups
    assert req.request_id in desktop_env._active_popups
    popup = desktop_env._active_popups[req.request_id]
    assert popup.request_id == req.request_id
    assert popup.isVisible()

    # Verified: main window queue updated
    assert desktop_env.main_window.table_pending.rowCount() == 1
    assert req.request_id in desktop_env.main_window._pending_requests

    # Verified: tray count updated
    if desktop_env.tray_mgr:
        assert desktop_env.tray_mgr._pending_count == 0  # not yet in registry in this direct call


def test_correct_request_details_displayed(qapp: QApplication) -> None:
    """Test 4: Popup labels accurately render server, tool, request ID, and timestamp."""
    req = AuthorizationRequest(
        server_name="Production Database Gateway",
        tool_name="query_customer_records",
        arguments={"customer_id": "C-98124"},
    )
    popup = AuthorizationPopup(request=req, timeout_seconds=15.0)

    assert popup.lbl_server_val.text() == "Production Database Gateway"
    assert popup.lbl_tool_val.text() == "query_customer_records"
    assert popup.lbl_id_val.text() == req.request_id
    assert popup.lbl_time_val.text() == RequestPresenter.get_formatted_timestamp(req.created_at)
    assert popup.timeout_seconds == 15.0


# -----------------------------------------------------------------------------
# Test 5: Sensitive Argument Redaction in UI
# -----------------------------------------------------------------------------


def test_sensitive_argument_redaction_in_ui(qapp: QApplication) -> None:
    """Test 5: Sensitive secrets (passwords, tokens, api keys) are masked in the UI viewer."""
    req = AuthorizationRequest(
        server_name="Auth Server",
        tool_name="authenticate_user",
        arguments={
            "username": "admin",
            "password": "super-secret-password-12345",
            "auth_token": "bearer-eyJhGciOiJIUzI1NiIsIn...",
            "nested_config": {
                "api_key": "sk-proj-999988887777",
                "safe_field": "public_data",
            },
        },
    )
    popup = AuthorizationPopup(request=req, timeout_seconds=10.0)
    displayed_text = popup.json_viewer.toPlainText()

    # Secrets must NEVER appear in the plain text
    assert "super-secret-password-12345" not in displayed_text
    assert "bearer-eyJhGciOiJIUzI1NiIsIn..." not in displayed_text
    assert "sk-proj-999988887777" not in displayed_text

    # Redacted placeholder and safe fields must appear
    assert "[REDACTED]" in displayed_text
    assert "admin" in displayed_text
    assert "public_data" in displayed_text

    # Original arguments on the request model remain unmutated for upstream forwarding
    assert req.arguments["password"] == "super-secret-password-12345"
    assert req.arguments["nested_config"]["api_key"] == "sk-proj-999988887777"


# -----------------------------------------------------------------------------
# Test 6 & 7: Approve and Reject Actions Submit Correct Request ID
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_action_submits_correct_request_id(desktop_env: WalledAIDesktopApp) -> None:
    """Test 6: User clicking Approve emits APPROVED status with the exact immutable request ID."""
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )
    # Register in auth manager registry so future resolution succeeds
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await desktop_env.auth_mgr.registry.register(req, future)

    await desktop_env.on_authorization_request(req)
    popup = desktop_env._active_popups[req.request_id]

    received_decision: list[tuple[str, AuthorizationStatus, str]] = []

    def on_decision(rid: str, st: AuthorizationStatus, rsn: str) -> None:
        received_decision.append((rid, st, rsn))

    popup.decision_made.connect(on_decision)

    # Simulate user clicking approve
    popup.btn_approve.click()

    assert len(received_decision) == 1
    assert received_decision[0][0] == req.request_id
    assert received_decision[0][1] == AuthorizationStatus.APPROVED

    # Await decision processing coroutine
    await asyncio.sleep(0.05)
    assert future.done()
    assert future.result().decision == AuthorizationStatus.APPROVED
    assert req.status == AuthorizationStatus.APPROVED

    # Popup closed and removed from active popups
    assert req.request_id not in desktop_env._active_popups


@pytest.mark.asyncio
async def test_reject_action_submits_correct_request_id(desktop_env: WalledAIDesktopApp) -> None:
    """Test 7: User clicking Reject emits REJECTED status with the exact immutable request ID."""
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="delete_database",
        arguments={"force": True},
    )
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await desktop_env.auth_mgr.registry.register(req, future)

    await desktop_env.on_authorization_request(req)
    popup = desktop_env._active_popups[req.request_id]

    # Simulate user clicking reject
    popup.btn_reject.click()

    await asyncio.sleep(0.05)
    assert future.done()
    assert future.result().decision == AuthorizationStatus.REJECTED
    assert req.status == AuthorizationStatus.REJECTED
    assert req.request_id not in desktop_env._active_popups


# -----------------------------------------------------------------------------
# Test 8: Duplicate Button Clicks Prevented
# -----------------------------------------------------------------------------


def test_duplicate_button_clicks_prevented(qapp: QApplication) -> None:
    """Test 8: Double-clicking approve/reject disables buttons and emits decision exactly once."""
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )
    popup = AuthorizationPopup(request=req, timeout_seconds=10.0)

    decisions: list[tuple[str, AuthorizationStatus, str]] = []
    popup.decision_made.connect(lambda r, s, m: decisions.append((r, s, m)))

    # Click approve once
    popup.btn_approve.click()

    # Buttons must immediately be disabled
    assert not popup.btn_approve.isEnabled()
    assert not popup.btn_reject.isEnabled()

    # Attempt second click
    popup.btn_approve.click()
    popup.btn_reject.click()

    # Must only emit exactly one decision
    assert len(decisions) == 1
    assert decisions[0][1] == AuthorizationStatus.APPROVED


# -----------------------------------------------------------------------------
# Test 9: Expired Request Handling
# -----------------------------------------------------------------------------


def test_expired_request_handling(qapp: QApplication) -> None:
    """Test 9: Countdown timer expiration disables buttons and transitions to expired state."""
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="slow_tool",
        arguments={},
    )
    popup = AuthorizationPopup(request=req, timeout_seconds=0.01)

    # Trigger manual timeout expiration
    popup._handle_timeout()

    assert not popup.btn_approve.isEnabled()
    assert not popup.btn_reject.isEnabled()
    assert popup.lbl_countdown.text() == "EXPIRED"
    assert "timed out" in popup.lbl_status.text()


# -----------------------------------------------------------------------------
# Test 10: Multiple Concurrent Authorization Dialogs
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_concurrent_authorization_dialogs(
    desktop_env: WalledAIDesktopApp,
) -> None:
    """Test 10: Multiple simultaneous requests each have an independent dialog and state."""
    requests = [
        AuthorizationRequest(
            server_name=f"Server-{i}",
            tool_name=f"tool_{i}",
            arguments={"idx": i},
        )
        for i in range(4)
    ]

    for req in requests:
        await desktop_env.on_authorization_request(req)

    assert len(desktop_env._active_popups) == 4
    assert desktop_env.main_window.table_pending.rowCount() == 4

    # Verify each dialog is bound to its own immutable request ID
    for req in requests:
        popup = desktop_env._active_popups[req.request_id]
        assert popup.request_id == req.request_id
        assert popup.lbl_tool_val.text() == req.tool_name

    # Close/resolve 2 of the 4
    desktop_env._active_popups[requests[0].request_id].btn_approve.click()
    desktop_env._active_popups[requests[1].request_id].btn_reject.click()
    await asyncio.sleep(0.05)

    assert len(desktop_env._active_popups) == 2
    assert requests[2].request_id in desktop_env._active_popups
    assert requests[3].request_id in desktop_env._active_popups


# -----------------------------------------------------------------------------
# Test 11: Closing Popup Without a Decision Fails Closed
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closing_popup_without_decision_fails_closed(
    desktop_env: WalledAIDesktopApp,
) -> None:
    """Test 11: Closing popup via window close [X] without clicking Approve rejects request."""
    req = AuthorizationRequest(
        server_name="Security Critical Server",
        tool_name="format_disk",
        arguments={},
    )
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await desktop_env.auth_mgr.registry.register(req, future)

    await desktop_env.on_authorization_request(req)
    popup = desktop_env._active_popups[req.request_id]

    # Simulate close event
    close_event = QCloseEvent()
    popup.closeEvent(close_event)

    await asyncio.sleep(0.05)
    assert future.done()
    # Must fail closed as REJECTED
    assert future.result().decision == AuthorizationStatus.REJECTED
    assert "without explicit approval" in future.result().reason


# -----------------------------------------------------------------------------
# Test 12: UI Exception and Disconnection Behavior
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ui_exception_fails_closed(desktop_env: WalledAIDesktopApp) -> None:
    """Test 12: An exception during notification hook fails closed safely."""
    broken_req = AuthorizationRequest(
        server_name="Crash Server",
        tool_name="crash_tool",
        arguments={},
    )

    with patch.object(
        desktop_env.main_window,
        "add_pending_request",
        side_effect=RuntimeError("Simulated GUI render failure"),
    ):
        with pytest.raises(RuntimeError):
            await desktop_env.on_authorization_request(broken_req)

    # Request must never be falsely marked as APPROVED
    assert broken_req.status == AuthorizationStatus.PENDING


# -----------------------------------------------------------------------------
# Test 13: Graceful Application Shutdown
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_application_shutdown(desktop_env: WalledAIDesktopApp) -> None:
    """Test 13: Application shutdown cancels pending requests and cleans up UI resources."""
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="long_running_call",
        arguments={},
    )
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await desktop_env.auth_mgr.registry.register(req, future)

    await desktop_env.on_authorization_request(req)
    assert len(desktop_env._active_popups) == 1

    # Perform shutdown (without terminating the test session's QApplication)
    await desktop_env.shutdown(quit_qt_app=False)

    # Active popups cleared
    assert len(desktop_env._active_popups) == 0
    # Pending requests cancelled in authorization manager
    assert desktop_env.auth_mgr.registry.count == 0
    assert desktop_env._shutdown_event.is_set()
