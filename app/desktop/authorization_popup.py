"""PySide6 desktop popup dialog for explicit MCP tool call authorization."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.desktop.request_presenter import RequestPresenter
from app.desktop.theme import (
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    FONT_FAMILY_MONO,
    POPUP_STYLESHEET,
)
from app.models.requests import AuthorizationRequest, AuthorizationStatus

logger = logging.getLogger(__name__)


class AuthorizationPopup(QDialog):
    """Always-on-top desktop dialog requesting explicit user authorization for an MCP tool call."""

    # Signal emitted when a decision is made: (request_id, decision_status, reason)
    decision_made = Signal(str, object, str)

    def __init__(
        self,
        request: AuthorizationRequest,
        timeout_seconds: float = 30.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.request = request
        self.request_id = request.request_id
        self.timeout_seconds = timeout_seconds
        self._is_resolved = False

        self._setup_window_properties()
        self._setup_ui()
        self._setup_shortcuts()
        self._setup_timer()

    def _setup_window_properties(self) -> None:
        """Configure dialog window behavior, modality, and always-on-top flags."""
        self.setWindowTitle(f"Walled AI – Authorize Tool: {self.request.tool_name}")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.resize(600, 560)

        # Ensure dialog stays on top of all windows (e.g. Claude Desktop)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(POPUP_STYLESHEET)

    def _setup_ui(self) -> None:
        """Build the visual hierarchy and layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 1. Header Card
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(6)

        header_top = QHBoxLayout()
        brand_label = QLabel("WALLED AI SECURITY GATEWAY")
        brand_label.setObjectName("brandTitle")
        header_top.addWidget(brand_label)

        header_top.addStretch()

        sec_badge = QLabel("FAIL-CLOSED INTERCEPTION")
        sec_badge.setObjectName("securityBadge")
        header_top.addWidget(sec_badge)
        header_layout.addLayout(header_top)

        subtitle = QLabel(
            "An MCP client requested execution of an upstream tool. Approval required."
        )
        subtitle.setObjectName("subtitle")
        header_layout.addWidget(subtitle)
        layout.addWidget(header_card)

        # 2. Metadata Grid Card
        details_card = QFrame()
        details_card.setObjectName("detailsCard")
        details_layout = QVBoxLayout(details_card)
        details_layout.setContentsMargins(16, 12, 16, 12)
        details_layout.setSpacing(8)

        # Row 1: Server and Tool
        row1 = QHBoxLayout()
        lbl_server_title = QLabel("Upstream Server:")
        lbl_server_title.setObjectName("metaLabel")
        lbl_server_title.setFixedWidth(110)
        self.lbl_server_val = QLabel(self.request.server_name)
        self.lbl_server_val.setObjectName("metaValue")
        row1.addWidget(lbl_server_title)
        row1.addWidget(self.lbl_server_val)
        row1.addStretch()
        details_layout.addLayout(row1)

        row2 = QHBoxLayout()
        lbl_tool_title = QLabel("Tool Name:")
        lbl_tool_title.setObjectName("metaLabel")
        lbl_tool_title.setFixedWidth(110)
        self.lbl_tool_val = QLabel(self.request.tool_name)
        self.lbl_tool_val.setObjectName("metaValueHighlight")
        row2.addWidget(lbl_tool_title)
        row2.addWidget(self.lbl_tool_val)
        row2.addStretch()
        details_layout.addLayout(row2)

        # Row 3: Request ID and Timestamp
        row3 = QHBoxLayout()
        lbl_id_title = QLabel("Request ID:")
        lbl_id_title.setObjectName("metaLabel")
        lbl_id_title.setFixedWidth(110)
        self.lbl_id_val = QLabel(self.request.request_id)
        self.lbl_id_val.setObjectName("metaValue")
        font_mono = QFont(FONT_FAMILY_MONO, 9)
        self.lbl_id_val.setFont(font_mono)
        row3.addWidget(lbl_id_title)
        row3.addWidget(self.lbl_id_val)
        row3.addStretch()
        details_layout.addLayout(row3)

        row4 = QHBoxLayout()
        lbl_time_title = QLabel("Timestamp:")
        lbl_time_title.setObjectName("metaLabel")
        lbl_time_title.setFixedWidth(110)
        self.lbl_time_val = QLabel(
            RequestPresenter.get_formatted_timestamp(self.request.created_at)
        )
        self.lbl_time_val.setObjectName("metaValue")
        row4.addWidget(lbl_time_title)
        row4.addWidget(self.lbl_time_val)
        row4.addStretch()
        details_layout.addLayout(row4)

        layout.addWidget(details_card)

        # 3. Arguments Section with Redacted JSON Viewer
        args_header_layout = QHBoxLayout()
        lbl_args = QLabel("Tool Arguments (Redacted Preview):")
        lbl_args.setStyleSheet(f"font-weight: 600; color: {COLOR_TEXT_PRIMARY}; font-size: 13px;")
        args_header_layout.addWidget(lbl_args)
        args_header_layout.addStretch()

        lbl_redact_notice = QLabel("Sensitive parameters redacted")
        lbl_redact_notice.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")
        args_header_layout.addWidget(lbl_redact_notice)
        layout.addLayout(args_header_layout)

        self.json_viewer = QPlainTextEdit()
        self.json_viewer.setObjectName("jsonViewer")
        self.json_viewer.setReadOnly(True)
        # Populate with safe redacted JSON string
        self.json_viewer.setPlainText(RequestPresenter.get_formatted_json(self.request))
        layout.addWidget(self.json_viewer, stretch=1)

        # 4. Timeout Progress Section
        timeout_layout = QVBoxLayout()
        timeout_layout.setSpacing(4)
        progress_info = QHBoxLayout()
        self.lbl_status = QLabel("Awaiting authorization decision...")
        self.lbl_status.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; font-weight: 500;"
        )
        progress_info.addWidget(self.lbl_status)
        progress_info.addStretch()

        self.lbl_countdown = QLabel(f"Timeout: {int(self.timeout_seconds)}s")
        self.lbl_countdown.setStyleSheet("font-weight: 600; font-size: 11px; color: #D97706;")
        progress_info.addWidget(self.lbl_countdown)
        timeout_layout.addLayout(progress_info)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("timeoutBar")
        self.progress_bar.setRange(0, int(self.timeout_seconds * 10))
        self.progress_bar.setValue(int(self.timeout_seconds * 10))
        self.progress_bar.setTextVisible(False)
        timeout_layout.addWidget(self.progress_bar)
        layout.addLayout(timeout_layout)

        # 5. Action Buttons (Reject / Approve)
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)

        self.btn_reject = QPushButton("Reject Request")
        self.btn_reject.setObjectName("rejectButton")
        self.btn_reject.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reject.clicked.connect(self._on_reject_clicked)
        buttons_layout.addWidget(self.btn_reject)

        self.btn_approve = QPushButton("Approve & Execute")
        self.btn_approve.setObjectName("approveButton")
        self.btn_approve.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_approve.setDefault(True)
        self.btn_approve.clicked.connect(self._on_approve_clicked)
        buttons_layout.addWidget(self.btn_approve)

        layout.addLayout(buttons_layout)

    def _setup_shortcuts(self) -> None:
        """Register keyboard shortcuts for fast, accessible decision making."""
        # Enter -> Approve
        self.shortcut_approve = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        self.shortcut_approve.activated.connect(self._on_approve_clicked)

        # Esc -> Reject
        self.shortcut_reject = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.shortcut_reject.activated.connect(self._on_reject_clicked)

    def _setup_timer(self) -> None:
        """Start precision timer for countdown animation and automatic timeout handling."""
        self.timer = QTimer(self)
        self.timer.setInterval(100)  # 100ms ticks
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start()

    def _on_timer_tick(self) -> None:
        """Update progress bar and countdown label; disable buttons on expiration."""
        if self._is_resolved:
            self.timer.stop()
            return

        remaining = RequestPresenter.calculate_remaining_seconds(
            self.request,
            self.timeout_seconds,
        )
        self.lbl_countdown.setText(f"Timeout: {remaining:.1f}s")
        self.progress_bar.setValue(int(remaining * 10))

        if remaining <= 0:
            self.timer.stop()
            self._handle_timeout()

    def _disable_action_buttons(self) -> None:
        """Atomically disable decision buttons to prevent double-clicks."""
        self.btn_approve.setEnabled(False)
        self.btn_reject.setEnabled(False)
        self.shortcut_approve.setEnabled(False)
        self.shortcut_reject.setEnabled(False)

    def _on_approve_clicked(self) -> None:
        """Handle explicit approval button click."""
        if self._is_resolved:
            return
        self._is_resolved = True
        self._disable_action_buttons()
        self.lbl_status.setText("Decision submitted: APPROVED")
        self.lbl_status.setStyleSheet(f"color: {COLOR_SUCCESS}; font-weight: 700;")
        self.timer.stop()

        logger.info("User clicked APPROVE for request %s", self.request_id)
        self.decision_made.emit(
            self.request_id,
            AuthorizationStatus.APPROVED,
            "Approved by user in desktop popup",
        )
        self.accept()

    def _on_reject_clicked(self) -> None:
        """Handle explicit rejection button click."""
        if self._is_resolved:
            return
        self._is_resolved = True
        self._disable_action_buttons()
        self.lbl_status.setText("Decision submitted: REJECTED")
        self.lbl_status.setStyleSheet(f"color: {COLOR_DANGER}; font-weight: 700;")
        self.timer.stop()

        logger.info("User clicked REJECT for request %s", self.request_id)
        self.decision_made.emit(
            self.request_id,
            AuthorizationStatus.REJECTED,
            "Blocked by user in desktop popup",
        )
        self.reject()

    def _handle_timeout(self) -> None:
        """Handle countdown reaching zero."""
        if self._is_resolved:
            return
        self._is_resolved = True
        self._disable_action_buttons()
        self.lbl_status.setText("Authorization timed out. Request blocked (fail-closed).")
        self.lbl_status.setStyleSheet(f"color: {COLOR_DANGER}; font-weight: 700;")
        self.lbl_countdown.setText("EXPIRED")

        logger.info("Popup timeout expired for request %s", self.request_id)
        # Note: AuthorizationManager will enforce timeout server-side;
        # this notification ensures the UI rejects gracefully
        self.reject()

    def mark_external_resolution(self, status: AuthorizationStatus, reason: str = "") -> None:
        """Called if the request reached a terminal state outside this popup dialog."""
        if self._is_resolved:
            return
        self._is_resolved = True
        self.timer.stop()
        self._disable_action_buttons()

        status_text = f"Request finalized externally: {status.value}"
        if reason:
            status_text += f" ({reason})"
        self.lbl_status.setText(status_text)
        self.lbl_status.setStyleSheet(
            f"color: {COLOR_SUCCESS if status == AuthorizationStatus.APPROVED else COLOR_DANGER}; "
            f"font-weight: 600;"
        )
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle dialog close [X]. Closing popup without explicit approval fails closed."""
        if not self._is_resolved:
            self._is_resolved = True
            self.timer.stop()
            self._disable_action_buttons()
            logger.info(
                "User closed popup for %s without decision. Failing closed.",
                self.request_id,
            )
            self.decision_made.emit(
                self.request_id,
                AuthorizationStatus.REJECTED,
                "Popup closed by user without explicit approval",
            )
        super().closeEvent(event)
