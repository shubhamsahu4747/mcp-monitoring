"""Desktop notification utilities for Walled AI."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtWidgets import QSystemTrayIcon

from app.desktop.request_presenter import RequestPresenter
from app.models.requests import AuthorizationRequest

logger = logging.getLogger(__name__)


class NotificationService:
    """Manages secure desktop notifications without exposing sensitive argument data."""

    def __init__(self, tray_icon: Optional[QSystemTrayIcon] = None) -> None:
        self.tray_icon = tray_icon

    def set_tray_icon(self, tray_icon: QSystemTrayIcon) -> None:
        """Register the system tray icon for displaying OS notifications."""
        self.tray_icon = tray_icon

    def notify_new_request(self, request: AuthorizationRequest) -> None:
        """Display a safe notification for an incoming authorization request.

        Guarantees that sensitive secrets (passwords, tokens) are never included
        in the notification title or message body.
        """
        title = f"Walled AI: Tool Approval Required ({request.tool_name})"
        message = RequestPresenter.get_safe_notification_summary(request)

        logger.info(
            "Security notification triggered for request %s (tool: %s, server: %s)",
            request.request_id,
            request.tool_name,
            request.server_name,
        )

        if self.tray_icon and self.tray_icon.isSystemTrayAvailable():
            try:
                self.tray_icon.showMessage(
                    title,
                    message,
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,  # 5 seconds duration
                )
            except Exception as e:
                logger.warning("Failed to display system tray notification: %s", e)
