"""Main dashboard window for Walled AI Security Gateway."""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.desktop.request_presenter import RequestPresenter
from app.desktop.theme import (
    COLOR_BADGE_SECURITY,
    COLOR_BG_DARK,
    COLOR_BRAND_PRIMARY,
    COLOR_TEXT_LIGHT,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    MAIN_WINDOW_STYLESHEET,
)
from app.models.requests import AuditRecord, AuthorizationRequest, AuthorizationStatus

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main desktop dashboard managing the pending authorization queue and audit records."""

    # Signals
    request_inspect_requested = Signal(str)  # request_id
    quick_approve_requested = Signal(str)   # request_id
    quick_reject_requested = Signal(str)    # request_id
    refresh_audit_requested = Signal()      # user clicked refresh audit log

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        on_exit_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.on_exit_callback = on_exit_callback
        self._pending_requests: Dict[str, AuthorizationRequest] = {}

        self.setWindowTitle("Walled AI – MCP Security Gateway")
        self.setMinimumSize(850, 600)
        self.resize(950, 680)
        self.setStyleSheet(MAIN_WINDOW_STYLESHEET)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Construct the main window layout with tabs for Pending Queue and Audit History."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Header Banner
        header = QFrame()
        header.setStyleSheet(
            f"background-color: {COLOR_BG_DARK}; border-radius: 8px; padding: 12px 16px;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)

        title_layout = QVBoxLayout()
        title_lbl = QLabel("🛡️ WALLED AI MCP SECURITY GATEWAY")
        title_lbl.setStyleSheet(
            f"color: {COLOR_TEXT_LIGHT}; font-weight: 700; font-size: 15px; letter-spacing: 0.5px;"
        )
        subtitle_lbl = QLabel("Active Protocol Interception & Explicit Desktop Authorization")
        subtitle_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        title_layout.addWidget(title_lbl)
        title_layout.addWidget(subtitle_lbl)
        header_layout.addLayout(title_layout)

        header_layout.addStretch()

        status_pill = QLabel("● GATEWAY ACTIVE (ENFORCING)")
        status_pill.setStyleSheet(
            f"background-color: {COLOR_BADGE_SECURITY}; color: white; "
            f"font-weight: 700; font-size: 11px; padding: 6px 12px; border-radius: 12px;"
        )
        header_layout.addWidget(status_pill)
        main_layout.addWidget(header)

        # 2. Main Tab View
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            f"QTabBar::tab {{ font-weight: 600; padding: 8px 16px; font-size: 12px; }} "
            f"QTabBar::tab:selected {{ color: {COLOR_BRAND_PRIMARY}; }}"
        )

        # Tab 1: Pending Queue
        self.pending_tab = QWidget()
        pending_layout = QVBoxLayout(self.pending_tab)
        pending_layout.setContentsMargins(8, 12, 8, 8)

        pending_header = QHBoxLayout()
        self.lbl_queue_count = QLabel("Active Pending Authorizations (0)")
        self.lbl_queue_count.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {COLOR_TEXT_PRIMARY};"
        )
        pending_header.addWidget(self.lbl_queue_count)
        pending_header.addStretch()
        pending_layout.addLayout(pending_header)

        self.table_pending = QTableWidget(0, 6)
        self.table_pending.setHorizontalHeaderLabels([
            "Request ID",
            "Timestamp",
            "Upstream Server",
            "Tool Name",
            "Arguments Preview",
            "Actions",
        ])
        self.table_pending.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_pending.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_pending.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_pending.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_pending.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.table_pending.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_pending.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_pending.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_pending.cellDoubleClicked.connect(self._on_table_row_double_clicked)
        pending_layout.addWidget(self.table_pending)

        self.tabs.addTab(self.pending_tab, "Active Requests (Queue)")

        # Tab 2: Audit History
        self.audit_tab = QWidget()
        audit_layout = QVBoxLayout(self.audit_tab)
        audit_layout.setContentsMargins(8, 12, 8, 8)

        audit_header = QHBoxLayout()
        lbl_audit_title = QLabel("Security Audit Log (SQLite)")
        lbl_audit_title.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {COLOR_TEXT_PRIMARY};"
        )
        audit_header.addWidget(lbl_audit_title)
        audit_header.addStretch()

        btn_refresh = QPushButton("↻ Refresh Log")
        btn_refresh.setStyleSheet(
            "padding: 4px 12px; font-weight: 600; font-size: 11px; "
            "background-color: white; border: 1px solid #CBD5E1; border-radius: 4px;"
        )
        btn_refresh.clicked.connect(lambda: self.refresh_audit_requested.emit())
        audit_header.addWidget(btn_refresh)
        audit_layout.addLayout(audit_header)

        self.table_audit = QTableWidget(0, 6)
        self.table_audit.setHorizontalHeaderLabels([
            "Timestamp", "Server", "Tool", "Decision", "Outcome", "Request ID"
        ])
        self.table_audit.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_audit.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_audit.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_audit.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_audit.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.table_audit.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_audit.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_audit.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        audit_layout.addWidget(self.table_audit)

        self.tabs.addTab(self.audit_tab, "Audit History")
        main_layout.addWidget(self.tabs)

        # 3. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "Walled AI Gateway ready. Enforcing explicit user authorization."
        )

    # -------------------------------------------------------------------------
    # Pending Queue Management
    # -------------------------------------------------------------------------

    def add_pending_request(self, request: AuthorizationRequest) -> None:
        """Insert or update a pending request in the queue table."""
        self._pending_requests[request.request_id] = request
        self._refresh_pending_table()

    def remove_pending_request(self, request_id: str) -> None:
        """Remove a finalized or expired request from the queue table."""
        if request_id in self._pending_requests:
            del self._pending_requests[request_id]
            self._refresh_pending_table()

    def _refresh_pending_table(self) -> None:
        """Re-render the active pending table."""
        self.table_pending.setRowCount(0)
        self.lbl_queue_count.setText(
            f"Active Pending Authorizations ({len(self._pending_requests)})"
        )
        self.tabs.setTabText(0, f"Active Requests ({len(self._pending_requests)})")

        for row, req in enumerate(self._pending_requests.values()):
            self.table_pending.insertRow(row)

            # Request ID (truncated)
            id_item = QTableWidgetItem(req.request_id[:8] + "...")
            id_item.setData(Qt.ItemDataRole.UserRole, req.request_id)
            self.table_pending.setItem(row, 0, id_item)

            # Timestamp
            ts_str = RequestPresenter.get_formatted_timestamp(req.created_at)
            self.table_pending.setItem(row, 1, QTableWidgetItem(ts_str))

            # Server
            self.table_pending.setItem(row, 2, QTableWidgetItem(req.server_name))

            # Tool
            tool_item = QTableWidgetItem(req.tool_name)
            tool_item.setForeground(Qt.GlobalColor.darkBlue)
            self.table_pending.setItem(row, 3, tool_item)

            # Redacted Preview
            args_summary = str(RequestPresenter.get_redacted_arguments(req))
            if len(args_summary) > 60:
                args_summary = args_summary[:57] + "..."
            self.table_pending.setItem(row, 4, QTableWidgetItem(args_summary))

            # Action Buttons widget
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)

            btn_inspect = QPushButton("Inspect")
            btn_inspect.setStyleSheet(
                f"padding: 2px 8px; font-size: 11px; background-color: {COLOR_BRAND_PRIMARY}; "
                f"color: white; border-radius: 3px;"
            )
            btn_inspect.clicked.connect(
                lambda _, rid=req.request_id: self.request_inspect_requested.emit(rid)
            )
            actions_layout.addWidget(btn_inspect)

            self.table_pending.setCellWidget(row, 5, actions_widget)

    def _on_table_row_double_clicked(self, row: int, _col: int) -> None:
        """Double clicking a pending row opens the full authorization popup."""
        item = self.table_pending.item(row, 0)
        if item:
            request_id = item.data(Qt.ItemDataRole.UserRole)
            if request_id:
                self.request_inspect_requested.emit(request_id)

    # -------------------------------------------------------------------------
    # Audit Table Population
    # -------------------------------------------------------------------------

    def update_audit_records(self, records: List[AuditRecord]) -> None:
        """Render recent audit records into the audit log table."""
        self.table_audit.setRowCount(0)
        for row, rec in enumerate(records):
            self.table_audit.insertRow(row)

            # Timestamp
            ts_str = RequestPresenter.get_formatted_timestamp(rec.timestamp)
            self.table_audit.setItem(row, 0, QTableWidgetItem(ts_str))

            # Server & Tool
            self.table_audit.setItem(row, 1, QTableWidgetItem(rec.server_name))
            self.table_audit.setItem(row, 2, QTableWidgetItem(rec.tool_name))

            # Decision Badge
            decision_item = QTableWidgetItem(rec.decision.value)
            if rec.decision == AuthorizationStatus.APPROVED:
                decision_item.setForeground(Qt.GlobalColor.darkGreen)
            elif rec.decision == AuthorizationStatus.REJECTED:
                decision_item.setForeground(Qt.GlobalColor.red)
            else:
                decision_item.setForeground(Qt.GlobalColor.darkYellow)
            self.table_audit.setItem(row, 3, decision_item)

            # Execution Outcome
            outcome_text = rec.execution_outcome or ""
            if rec.failure_reason:
                outcome_text = f"{outcome_text}: {rec.failure_reason}"
            self.table_audit.setItem(row, 4, QTableWidgetItem(outcome_text))

            # Request ID
            self.table_audit.setItem(row, 5, QTableWidgetItem(rec.request_id[:8] + "..."))

        self.status_bar.showMessage(
            f"Audit log updated. Total records loaded: {len(records)}"
        )

    # -------------------------------------------------------------------------
    # Window Close Override (Minimize to Tray)
    # -------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing main window hides to system tray rather than killing the security gateway."""
        # Hide window to tray
        self.hide()
        event.ignore()
        logger.info("Main window minimized to system tray.")
