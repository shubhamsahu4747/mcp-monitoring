"""System tray integration and tray context menu for Walled AI Desktop."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

logger = logging.getLogger(__name__)


def create_shield_icon(size: int = 64, active: bool = True) -> QIcon:
    """Dynamically render an enterprise security shield icon using QPainter."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Base shield background color
    base_color = QColor("#0284C7") if active else QColor("#64748B")  # Sky-600 or Slate-500
    painter.setBrush(base_color)
    painter.setPen(Qt.PenStyle.NoPen)

    # Draw rounded shield-like polygon
    w, h = float(size), float(size)
    points = [
        QPoint(int(w * 0.15), int(h * 0.15)),
        QPoint(int(w * 0.85), int(h * 0.15)),
        QPoint(int(w * 0.85), int(h * 0.55)),
        QPoint(int(w * 0.50), int(h * 0.90)),
        QPoint(int(w * 0.15), int(h * 0.55)),
    ]
    painter.drawPolygon(points)

    # Draw inner security lock / check mark
    painter.setPen(QColor("#FFFFFF"))
    font = QFont("Segoe UI", int(size * 0.38), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "W")

    painter.end()
    return QIcon(pixmap)


class SystemTrayManager:
    """Manages system tray icon, notifications, and context menu actions."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        on_open_dashboard: Optional[Callable[[], None]] = None,
        on_show_pending: Optional[Callable[[], None]] = None,
        on_exit_app: Optional[Callable[[], None]] = None,
    ) -> None:
        self.parent = parent
        self.on_open_dashboard = on_open_dashboard
        self.on_show_pending = on_show_pending
        self.on_exit_app = on_exit_app

        self.tray_icon = QSystemTrayIcon(parent)
        self.icon = create_shield_icon()
        self.tray_icon.setIcon(self.icon)
        self.tray_icon.setToolTip("Walled AI – MCP Security Gateway (Active)")

        self._pending_count = 0
        self._setup_menu()
        self._setup_events()

    def _setup_menu(self) -> None:
        """Construct the tray popup context menu."""
        self.menu = QMenu()

        # Branding / Status Title Header
        self.action_title = QAction("🛡️ Walled AI Security Gateway", self.menu)
        self.action_title.setEnabled(False)
        self.menu.addAction(self.action_title)

        self.action_status = QAction("Status: Active (Enforcing)", self.menu)
        self.action_status.setEnabled(False)
        self.menu.addAction(self.action_status)

        self.menu.addSeparator()

        # Dashboard Action
        self.action_open = QAction("Open Dashboard", self.menu)
        if self.on_open_dashboard:
            self.action_open.triggered.connect(self.on_open_dashboard)
        self.menu.addAction(self.action_open)

        # Pending Requests Action
        self.action_pending = QAction("Pending Requests (0)", self.menu)
        if self.on_show_pending:
            self.action_pending.triggered.connect(self.on_show_pending)
        self.menu.addAction(self.action_pending)

        self.menu.addSeparator()

        # Exit Action
        self.action_exit = QAction("Exit Walled AI", self.menu)
        if self.on_exit_app:
            self.action_exit.triggered.connect(self.on_exit_app)
        self.menu.addAction(self.action_exit)

        self.tray_icon.setContextMenu(self.menu)

    def _setup_events(self) -> None:
        """Handle tray icon activations (single/double clicks)."""
        self.tray_icon.activated.connect(self._on_tray_activated)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Restore window when clicked or double clicked."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.on_open_dashboard:
                self.on_open_dashboard()

    def update_pending_count(self, count: int) -> None:
        """Update the pending count label in tray menu and tooltip."""
        self._pending_count = count
        self.action_pending.setText(f"Pending Requests ({count})")
        if count > 0:
            self.tray_icon.setToolTip(f"Walled AI – {count} Pending Authorization Request(s)")
        else:
            self.tray_icon.setToolTip("Walled AI – MCP Security Gateway (Active)")

    def show(self) -> None:
        """Make the tray icon visible in the notification area."""
        self.tray_icon.show()

    def hide(self) -> None:
        """Hide the tray icon."""
        self.tray_icon.hide()
