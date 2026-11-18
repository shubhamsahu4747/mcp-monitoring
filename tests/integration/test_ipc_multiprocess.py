"""Tests for Desktop IPC server and client communication."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Generator

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.authorization.manager import AuthorizationManager  # noqa: E402
from app.desktop.application import WalledAIDesktopApp  # noqa: E402
from app.gateway.ipc_client import IPCAuthorizationClient  # noqa: E402
from app.models.requests import AuthorizationRequest, AuthorizationStatus  # noqa: E402
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
async def ipc_env(
    qapp: QApplication,
    tmp_path: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Isolated environment with DesktopApp, IPC server on port 8799, and IPCClient."""
    db_file = tmp_path / "test_ipc_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=3.0, audit_repository=audit_repo)

    desktop_app = WalledAIDesktopApp(
        auth_mgr=auth_mgr,
        audit_repository=audit_repo,
        enable_tray=False,
        show_main_window=False,
    )
    # Configure test port
    desktop_app.ipc_server.port = 8799
    desktop_app.ipc_server.host = "127.0.0.1"

    await desktop_app.start()

    client = IPCAuthorizationClient(
        host="127.0.0.1",
        port=8799,
        timeout_seconds=3.0,
    )

    try:
        yield {
            "desktop_app": desktop_app,
            "client": client,
            "auth_mgr": auth_mgr,
        }
    finally:
        await desktop_app.shutdown(quit_qt_app=False)


@pytest.mark.asyncio
async def test_ipc_health_check(ipc_env: Dict[str, Any]) -> None:
    """Verify IPC server responds to health check."""
    client: IPCAuthorizationClient = ipc_env["client"]
    is_healthy = await client.check_health()
    assert is_healthy is True


@pytest.mark.asyncio
async def test_ipc_approval_flow(ipc_env: Dict[str, Any]) -> None:
    """Verify IPC client forwards request, popup opens, approval returns to client."""
    desktop_app: WalledAIDesktopApp = ipc_env["desktop_app"]
    client: IPCAuthorizationClient = ipc_env["client"]

    # Auto-approve when popup appears
    async def auto_approve() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.btn_approve.click()

    asyncio.create_task(auto_approve())

    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        arguments={"a": 10, "b": 20},
    )

    decision = await client.request_authorization(req)
    assert decision.decision == AuthorizationStatus.APPROVED


@pytest.mark.asyncio
async def test_ipc_rejection_flow(ipc_env: Dict[str, Any]) -> None:
    """Verify IPC client forwards request, popup opens, user rejection returns to client."""
    desktop_app: WalledAIDesktopApp = ipc_env["desktop_app"]
    client: IPCAuthorizationClient = ipc_env["client"]

    async def auto_reject() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.btn_reject.click()

    asyncio.create_task(auto_reject())

    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="delete_database",
        arguments={},
    )

    decision = await client.request_authorization(req)
    assert decision.decision == AuthorizationStatus.REJECTED


@pytest.mark.asyncio
async def test_ipc_fails_closed_when_desktop_agent_offline() -> None:
    """Verify client fails closed with informative error when desktop agent is offline."""
    client = IPCAuthorizationClient(
        host="127.0.0.1",
        port=9999,  # Unused port
        timeout_seconds=1.0,
    )
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )

    decision = await client.request_authorization(req)
    assert decision.decision == AuthorizationStatus.REJECTED
    assert "not running" in (decision.reason or "").lower()
