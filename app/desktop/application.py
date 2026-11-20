"""Desktop application lifecycle, qasync coordination, and authorization gateway integration."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any, Dict, Optional

import qasync  # type: ignore[import-untyped]
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.authorization.manager import AuthorizationManager, authorization_manager
from app.config import settings
from app.desktop.authorization_popup import AuthorizationPopup
from app.desktop.ipc_server import DesktopIPCServer
from app.desktop.main_window import MainWindow
from app.desktop.notifications import NotificationService
from app.desktop.remote_client import RemoteAgentWebSocketClient
from app.desktop.tray import SystemTrayManager
from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    DuplicateDecisionError,
)
from app.storage.audit_repository import AuditRepository, audit_repo

logger = logging.getLogger(__name__)


class WalledAIDesktopApp:
    """Manages the PySide6 desktop GUI integrated with asyncio via qasync."""

    def __init__(
        self,
        auth_mgr: Optional[AuthorizationManager] = None,
        audit_repository: Optional[AuditRepository] = None,
        enable_tray: bool = True,
        show_main_window: bool = True,
        remote_url: Optional[str] = None,
    ) -> None:
        self.auth_mgr = auth_mgr or authorization_manager
        self.audit_repo = audit_repository or audit_repo
        self.enable_tray = enable_tray
        self.should_show_main_window = show_main_window
        self.remote_url = remote_url

        # Ensure a single QApplication instance exists
        existing_app = QApplication.instance()
        if isinstance(existing_app, QApplication):
            self.qt_app: QApplication = existing_app
        else:
            self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName("Walled AI")
        self.qt_app.setQuitOnLastWindowClosed(False)

        # Active popups keyed by immutable request ID
        self._active_popups: Dict[str, AuthorizationPopup] = {}
        self._shutdown_event = asyncio.Event()

        # Instantiate UI components
        self.main_window = MainWindow(on_exit_callback=self._schedule_exit)
        self.notification_service = NotificationService()

        # System tray setup
        self.tray_mgr: Optional[SystemTrayManager] = None
        if self.enable_tray:
            self.tray_mgr = SystemTrayManager(
                parent=self.main_window,
                on_open_dashboard=self.show_main_window,
                on_show_pending=self.show_pending_from_tray,
                on_exit_app=self._schedule_exit,
            )
            self.notification_service.set_tray_icon(self.tray_mgr.tray_icon)

        # Wire main window signals
        self.main_window.request_inspect_requested.connect(self._on_inspect_requested)
        self.main_window.refresh_audit_requested.connect(
            lambda: asyncio.create_task(self.refresh_audit_log())
        )
        self.main_window.trigger_demo_requested.connect(
            lambda: asyncio.create_task(self._trigger_demo_request())
        )

        # Hook authorization manager to desktop UI
        self.auth_mgr.set_notification_hook(self.on_authorization_request)

        # Embedded IPC server for local MCP proxies (Claude Desktop integration)
        self.ipc_server = DesktopIPCServer(
            desktop_app=self,
            host=settings.remote_host,
            port=settings.remote_port,
        )

        # Outbound WebSocket client for Remote MCP Gateway (Milestone 6)
        self.remote_client: Optional[RemoteAgentWebSocketClient] = None
        if self.remote_url:
            self.remote_client = RemoteAgentWebSocketClient(
                remote_url=self.remote_url,
                desktop_app=self,
            )

        # Periodic wake-up timer ensuring Python interpreter evaluates OS signals (Ctrl+C)
        self._sigint_timer = QTimer(self.main_window)
        self._sigint_timer.setInterval(200)
        self._sigint_timer.timeout.connect(lambda: None)
        self._sigint_timer.start()

    async def request_authorization_via_gui(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout_seconds: float,
        client_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> AuthorizationDecision:
        """Present authorization request in desktop UI and wait for user decision."""
        kwargs: Dict[str, Any] = {
            "server_name": server_name,
            "tool_name": tool_name,
            "arguments": arguments,
            "client_id": client_id,
            "session_id": session_id,
        }
        if request_id:
            kwargs["request_id"] = request_id
        req = AuthorizationRequest(**kwargs)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AuthorizationDecision] = loop.create_future()
        await self.auth_mgr.registry.register(req, future)
        await self.on_authorization_request(req)

        try:
            decision = await asyncio.wait_for(future, timeout=timeout_seconds)
            return decision
        except (asyncio.TimeoutError, TimeoutError):
            await self.auth_mgr.registry.claim_timeout(
                req.request_id,
                reason=f"Authorization timed out after {timeout_seconds}s",
            )
            popup = self._active_popups.pop(req.request_id, None)
            if popup:
                popup.close()
            self.main_window.remove_pending_request(req.request_id)
            if self.tray_mgr:
                self.tray_mgr.update_pending_count(self.auth_mgr.registry.count)
            asyncio.create_task(self.refresh_audit_log())
            return AuthorizationDecision(
                request_id=req.request_id,
                decision=AuthorizationStatus.TIMED_OUT,
                reason=f"Authorization timed out after {timeout_seconds} seconds",
            )

    async def _trigger_demo_request(self) -> None:
        """Trigger a safe demo tool authorization request for live UI testing."""
        logger.info("Triggering demo tool authorization request from UI button...")
        demo_req = AuthorizationRequest(
            server_name="Safe Mock MCP Server",
            tool_name="read_demo_file",
            arguments={
                "filename": "confidential_financials_2026.xlsx",
                "access_token": "bearer-eyJhbGciOiJIUzI1NiIsIn...",
                "user_credentials": {
                    "admin_password": "super-secret-password-xyz",
                    "role": "auditor",
                },
                "parameters": {
                    "format": "csv",
                    "max_rows": 500,
                },
            },
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AuthorizationDecision] = loop.create_future()
        await self.auth_mgr.registry.register(demo_req, future)
        await self.on_authorization_request(demo_req)

    # -------------------------------------------------------------------------
    # UI Presentation & Actions
    # -------------------------------------------------------------------------

    def show_main_window(self) -> None:
        """Display and bring main dashboard window to the foreground."""
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def show_pending_from_tray(self) -> None:
        """Open or focus the first pending authorization popup or main window queue."""
        if self._active_popups:
            first_popup = next(iter(self._active_popups.values()))
            first_popup.show()
            first_popup.raise_()
            first_popup.activateWindow()
        else:
            self.show_main_window()

    # -------------------------------------------------------------------------
    # Authorization Hook & Popup Dispatch
    # -------------------------------------------------------------------------

    async def on_authorization_request(self, request: AuthorizationRequest) -> None:
        """Callback invoked by AuthorizationManager when an MCP tool call is intercepted."""
        logger.info(
            "Desktop UI received pending authorization request: %s (tool: %s)",
            request.request_id,
            request.tool_name,
        )

        # 1. Update Main Window Queue
        self.main_window.add_pending_request(request)

        # 2. Update Tray Count
        if self.tray_mgr:
            self.tray_mgr.update_pending_count(self.auth_mgr.registry.count)

        # 3. Trigger OS Notification
        self.notification_service.notify_new_request(request)

        # 4. Construct and display always-on-top popup dialog
        popup = AuthorizationPopup(
            request=request,
            timeout_seconds=self.auth_mgr.timeout_seconds,
            parent=None,
        )
        self._active_popups[request.request_id] = popup

        # Offset popup slightly if multiple are already open
        if len(self._active_popups) > 1:
            offset = (len(self._active_popups) - 1) * 30
            popup.move(popup.x() + offset, popup.y() + offset)

        popup.decision_made.connect(
            lambda rid, st, rsn: asyncio.create_task(self.submit_decision(rid, st, rsn))
        )
        popup.show()
        popup.raise_()
        popup.activateWindow()

    async def submit_decision(
        self,
        request_id: str,
        status: AuthorizationStatus,
        reason: str,
    ) -> None:
        """Submit user authorization decision to the AuthorizationManager."""
        logger.info(
            "Submitting authorization decision from UI: %s -> %s (%s)",
            request_id,
            status.value,
            reason,
        )
        decision = AuthorizationDecision(
            request_id=request_id,
            decision=status,
            reason=reason,
        )

        try:
            await self.auth_mgr.submit_decision(decision)
        except DuplicateDecisionError:
            logger.warning("Duplicate decision ignored for request %s", request_id)
        except Exception as e:
            logger.error("Failed to submit decision for request %s: %s", request_id, e)

        # Cleanup active popup
        popup = self._active_popups.pop(request_id, None)
        if popup:
            popup.close()

        # Update Main Window & Tray
        self.main_window.remove_pending_request(request_id)
        if self.tray_mgr:
            self.tray_mgr.update_pending_count(self.auth_mgr.registry.count)

        # Refresh audit logs in background
        asyncio.create_task(self.refresh_audit_log())

    def _on_inspect_requested(self, request_id: str) -> None:
        """Focus the corresponding authorization popup."""
        popup = self._active_popups.get(request_id)
        if popup:
            popup.show()
            popup.raise_()
            popup.activateWindow()

    # -------------------------------------------------------------------------
    # Audit Log Refresh
    # -------------------------------------------------------------------------

    async def refresh_audit_log(self, limit: int = 50) -> None:
        """Fetch recent audit records and update dashboard table."""
        try:
            records = await self.audit_repo.list_records(limit=limit)
            self.main_window.update_audit_records(records)
        except Exception as e:
            logger.error("Error refreshing audit logs: %s", e)

    # -------------------------------------------------------------------------
    # Lifecycle & Shutdown
    # -------------------------------------------------------------------------

    def _schedule_exit(self) -> None:
        """Trigger async shutdown from synchronous UI event."""
        asyncio.create_task(self.shutdown())

    async def start(self) -> None:
        """Initialize and display the desktop application."""
        await self.audit_repo.initialize()
        await self.refresh_audit_log()

        if self.tray_mgr:
            self.tray_mgr.show()

        if self.should_show_main_window:
            self.show_main_window()

        # Start IPC server for local MCP proxies (Claude Desktop integration)
        await self.ipc_server.start()

        # Start Remote WebSocket client if remote URL configured (Milestone 6)
        if self.remote_client:
            await self.remote_client.start()

        # Update UI Component Status Indicators
        self.main_window.set_agent_status(
            True, f"Desktop Agent: Connected (Port {self.ipc_server.port})"
        )
        self.main_window.set_upstream_status(
            True, f"Upstream: Connected ({settings.upstream_server_name})"
        )
        if self.remote_url:
            self.main_window.set_gateway_status(
                True, f"Remote Gateway: Connecting ({self.remote_url})"
            )
        else:
            self.main_window.set_gateway_status(True, "MCP Gateway: Standby (Ready)")
        logger.info("Walled AI Desktop Application started successfully.")

    async def shutdown(self, quit_qt_app: bool = True) -> None:
        """Gracefully shut down application, cancel pending requests, and close UI."""
        logger.info("Initiating Walled AI Desktop Application shutdown...")

        # Update status indicators to offline
        self.main_window.set_agent_status(False, "Desktop Agent: Disconnected")
        self.main_window.set_gateway_status(False, "MCP Gateway: Stopped")

        # 0a. Stop remote WebSocket client
        if self.remote_client:
            await self.remote_client.stop()

        # 0b. Stop IPC server
        await self.ipc_server.stop()

        # 1. Close all active popups
        for popup in list(self._active_popups.values()):
            popup.close()
        self._active_popups.clear()

        # 2. Cancel pending authorization requests in manager (fail-closed)
        await self.auth_mgr.shutdown()

        # 3. Hide tray icon
        if self.tray_mgr:
            self.tray_mgr.hide()

        # 4. Close main window
        self.main_window.hide()

        # 5. Signal completion and quit Qt event loop if requested
        self._shutdown_event.set()
        if quit_qt_app:
            self.qt_app.quit()
        logger.info("Walled AI Desktop Application shutdown complete.")

    async def wait_until_closed(self) -> None:
        """Await application shutdown completion."""
        await self._shutdown_event.wait()


def run_desktop(remote_url: Optional[str] = None) -> int:
    """Launch PySide6 Windows desktop application using qasync with Ctrl+C support."""
    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    desktop_app = WalledAIDesktopApp(remote_url=remote_url)

    # 1. Python signal handler for Ctrl+C (SIGINT) and termination (SIGTERM)
    def _sigint_handler(signum: int, frame: Any) -> None:
        logger.info(
            "Received termination signal (%d). Shutting down desktop application...",
            signum,
        )
        if not desktop_app._shutdown_event.is_set():
            loop.create_task(desktop_app.shutdown())

    signal.signal(signal.SIGINT, _sigint_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sigint_handler)

    # 2. Windows-native Console Ctrl Handler (catches Ctrl+C in Windows Terminal/PowerShell)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _win_console_ctrl_handler(ctrl_type: int) -> bool:
            if ctrl_type in (0, 1, 2):  # CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT
                logger.info(
                    "Windows console control event (%d) received. Shutting down...",
                    ctrl_type,
                )
                if not desktop_app._shutdown_event.is_set():
                    loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(desktop_app.shutdown())
                    )
                return True
            return False

        _console_handler_ref = HandlerRoutine(_win_console_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, True)

    async def _async_main() -> None:
        await desktop_app.start()
        await desktop_app.wait_until_closed()

    try:
        with loop:
            loop.run_until_complete(_async_main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Desktop application received keyboard interrupt, completing shutdown...")
        if not desktop_app._shutdown_event.is_set():
            loop.run_until_complete(desktop_app.shutdown())

    return 0
