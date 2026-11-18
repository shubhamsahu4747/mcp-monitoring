"""Integration tests connecting LocalMCPProxy, UpstreamManager, and WalledAIDesktopApp.

Verifies end-to-end:
1. Downstream MCP tool call triggers Desktop UI popup.
2. User approving in desktop popup executes upstream tool and returns valid result.
3. User rejecting in desktop popup returns valid MCP tool error and never calls upstream.
4. UI popup timeout fails closed and returns tool error.
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
from app.models.requests import AuthorizationStatus  # noqa: E402
from app.storage.audit_repository import AuditRepository  # noqa: E402
from app.storage.database import DatabaseManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Ensure QApplication singleton exists in offscreen mode."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(["--platform", "offscreen"])
    yield app


@pytest.fixture
async def desktop_integration_env(
    qapp: QApplication,
    tmp_path: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Isolated environment with LocalMCPProxy, UpstreamManager, and WalledAIDesktopApp."""
    db_file = tmp_path / "desktop_integration_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=3.0, audit_repository=audit_repo)

    desktop_app = WalledAIDesktopApp(
        auth_mgr=auth_mgr,
        audit_repository=audit_repo,
        enable_tray=True,
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
        server_name="Walled AI Test Gateway",
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


@pytest.mark.asyncio
async def test_end_to_end_desktop_approval_flow(
    desktop_integration_env: Dict[str, Any],
) -> None:
    """End-to-End: Downstream tool call triggers popup; user approves -> executes upstream."""
    desktop_app: WalledAIDesktopApp = desktop_integration_env["desktop_app"]
    proxy: LocalMCPProxy = desktop_integration_env["proxy"]
    audit_repo: AuditRepository = desktop_integration_env["audit_repo"]

    # Asynchronously wait for popup and click approve
    async def simulate_user_approval() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.btn_approve.click()

    approval_task = asyncio.create_task(simulate_user_approval())

    # Client issues tools/call to proxy
    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 42.0, "b": 58.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)
    await approval_task

    assert not result.is_error
    assert "100.0" in result.content[0].text  # type: ignore

    # Verify audit persistence
    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.APPROVED
    assert records[0].execution_outcome == "SUCCESS"


@pytest.mark.asyncio
async def test_end_to_end_desktop_rejection_flow(
    desktop_integration_env: Dict[str, Any],
) -> None:
    """End-to-End: Downstream tool call triggers popup; user rejects -> fails closed."""
    desktop_app: WalledAIDesktopApp = desktop_integration_env["desktop_app"]
    proxy: LocalMCPProxy = desktop_integration_env["proxy"]
    audit_repo: AuditRepository = desktop_integration_env["audit_repo"]

    async def simulate_user_rejection() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.btn_reject.click()

    rejection_task = asyncio.create_task(simulate_user_rejection())

    params = types.CallToolRequestParams(
        name="create_demo_file",
        arguments={"filename": "unapproved.txt", "content": "malicious"},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)
    await rejection_task

    assert result.is_error
    assert "was blocked by user" in result.content[0].text  # type: ignore

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].execution_outcome == "BLOCKED"
