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
    QLineEdit,
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
    trigger_demo_requested = Signal()       # user clicked demo trigger button

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        on_exit_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.on_exit_callback = on_exit_callback
        self._pending_requests: Dict[str, AuthorizationRequest] = {}
        self._audit_records: List[AuditRecord] = []

        self.setWindowTitle("Walled AI – MCP Security Gateway")
        self.setMinimumSize(920, 640)
        self.resize(1020, 720)
        self.setStyleSheet(MAIN_WINDOW_STYLESHEET)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Construct the main window layout with KPI cards, tabs, and tables."""
        central_widget = QWidget()
        central_widget.setObjectName("centralWidget")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # 1. Header Banner
        header = QFrame()
        header.setStyleSheet(
            f"background-color: {COLOR_BG_DARK}; border-radius: 8px; padding: 12px 18px;"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title_lbl = QLabel("🛡️ WALLED AI MCP SECURITY GATEWAY")
        title_lbl.setStyleSheet(
            f"color: {COLOR_TEXT_LIGHT}; font-weight: 700; font-size: 15px; letter-spacing: 0.5px;"
        )
        subtitle_lbl = QLabel("Active Protocol Interception & Explicit User Authorization")
        subtitle_lbl.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        title_layout.addWidget(title_lbl)
        title_layout.addWidget(subtitle_lbl)
        header_layout.addLayout(title_layout)

        header_layout.addStretch()

        # Status Badges
        badge_layout = QHBoxLayout()
        badge_layout.setSpacing(8)

        upstream_badge = QLabel("🔌 Upstream: Safe Mock (stdio)")
        upstream_badge.setStyleSheet(
            "background-color: #1E293B; color: #94A3B8; font-size: 11px; "
            "font-weight: 500; padding: 4px 10px; border-radius: 12px; border: 1px solid #334155;"
        )
        badge_layout.addWidget(upstream_badge)

        status_pill = QLabel("● GATEWAY ACTIVE")
        status_pill.setStyleSheet(
            f"background-color: {COLOR_BADGE_SECURITY}; color: white; "
            f"font-weight: 700; font-size: 11px; padding: 4px 12px; border-radius: 12px;"
        )
        badge_layout.addWidget(status_pill)
        header_layout.addLayout(badge_layout)

        main_layout.addWidget(header)

        # 2. KPI / Metrics Summary Bar (4 Cards)
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(12)

        # Card 1: Active Pending
        card_pending = QFrame()
        card_pending.setObjectName("statCard")
        cp_layout = QVBoxLayout(card_pending)
        cp_layout.setContentsMargins(0, 0, 0, 0)
        cp_layout.setSpacing(2)
        lbl_cp_title = QLabel("Pending Approvals")
        lbl_cp_title.setObjectName("statTitle")
        self.lbl_stat_pending = QLabel("0")
        self.lbl_stat_pending.setObjectName("statValue")
        self.lbl_stat_pending.setStyleSheet(f"color: {COLOR_BRAND_PRIMARY};")
        lbl_cp_sub = QLabel("Awaiting user decision")
        lbl_cp_sub.setObjectName("statSub")
        cp_layout.addWidget(lbl_cp_title)
        cp_layout.addWidget(self.lbl_stat_pending)
        cp_layout.addWidget(lbl_cp_sub)
        kpi_layout.addWidget(card_pending)

        # Card 2: Audited Invocations
        card_audit = QFrame()
        card_audit.setObjectName("statCard")
        ca_layout = QVBoxLayout(card_audit)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        ca_layout.setSpacing(2)
        lbl_ca_title = QLabel("Audited Invocations")
        lbl_ca_title.setObjectName("statTitle")
        self.lbl_stat_audit = QLabel("0")
        self.lbl_stat_audit.setObjectName("statValue")
        lbl_ca_sub = QLabel("Persisted to SQLite")
        lbl_ca_sub.setObjectName("statSub")
        ca_layout.addWidget(lbl_ca_title)
        ca_layout.addWidget(self.lbl_stat_audit)
        ca_layout.addWidget(lbl_ca_sub)
        kpi_layout.addWidget(card_audit)

        # Card 3: Security Policy
        card_policy = QFrame()
        card_policy.setObjectName("statCard")
        cpol_layout = QVBoxLayout(card_policy)
        cpol_layout.setContentsMargins(0, 0, 0, 0)
        cpol_layout.setSpacing(2)
        lbl_cpol_title = QLabel("Enforcement Mode")
        lbl_cpol_title.setObjectName("statTitle")
        lbl_cpol_val = QLabel("Fail-Closed")
        lbl_cpol_val.setObjectName("statValue")
        lbl_cpol_val.setStyleSheet("color: #0D9488;")
        lbl_cpol_sub = QLabel("Default block on timeout")
        lbl_cpol_sub.setObjectName("statSub")
        cpol_layout.addWidget(lbl_cpol_title)
        cpol_layout.addWidget(lbl_cpol_val)
        cpol_layout.addWidget(lbl_cpol_sub)
        kpi_layout.addWidget(card_policy)

        # Card 4: Timeout Threshold
        card_timeout = QFrame()
        card_timeout.setObjectName("statCard")
        ct_layout = QVBoxLayout(card_timeout)
        ct_layout.setContentsMargins(0, 0, 0, 0)
        ct_layout.setSpacing(2)
        lbl_ct_title = QLabel("Authorization Timeout")
        lbl_ct_title.setObjectName("statTitle")
        self.lbl_stat_timeout = QLabel("30.0s")
        self.lbl_stat_timeout.setObjectName("statValue")
        self.lbl_stat_timeout.setStyleSheet("color: #D97706;")
        lbl_ct_sub = QLabel("Per-request countdown")
        lbl_ct_sub.setObjectName("statSub")
        ct_layout.addWidget(lbl_ct_title)
        ct_layout.addWidget(self.lbl_stat_timeout)
        ct_layout.addWidget(lbl_ct_sub)
        kpi_layout.addWidget(card_timeout)

        main_layout.addLayout(kpi_layout)

        # 3. Main Tab View
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabWidget")

        # Tab 1: Pending Queue
        self.pending_tab = QWidget()
        self.pending_tab.setObjectName("pendingTab")
        pending_layout = QVBoxLayout(self.pending_tab)
        pending_layout.setContentsMargins(14, 14, 14, 14)
        pending_layout.setSpacing(12)

        pending_header = QHBoxLayout()
        self.lbl_queue_count = QLabel("Active Authorizations Queue (0)")
        self.lbl_queue_count.setObjectName("sectionHeaderLabel")
        pending_header.addWidget(self.lbl_queue_count)
        pending_header.addStretch()

        btn_demo_trigger = QPushButton("⚡ Test Authorization Popup")
        btn_demo_trigger.setObjectName("primaryButton")
        btn_demo_trigger.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_demo_trigger.clicked.connect(lambda: self.trigger_demo_requested.emit())
        pending_header.addWidget(btn_demo_trigger)
        pending_layout.addLayout(pending_header)

        # Empty State Placeholder Frame
        self.empty_state_frame = QFrame()
        self.empty_state_frame.setObjectName("emptyStateFrame")
        empty_layout = QVBoxLayout(self.empty_state_frame)
        empty_layout.setContentsMargins(30, 40, 30, 40)
        empty_layout.setSpacing(12)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_icon = QLabel("🛡️")
        empty_icon.setStyleSheet(
            "font-size: 32px; background-color: #CCFBF1; border-radius: 28px; padding: 12px;"
        )
        empty_icon.setFixedSize(56, 56)
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        empty_title = QLabel("Security Gateway Active & Monitoring")
        empty_title.setObjectName("emptyStateTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_subtitle = QLabel(
            "No tool authorization requests are currently pending.\n"
            "When Claude Desktop or an MCP client executes an upstream tool,\n"
            "an always-on-top authorization dialog will pop up immediately."
        )
        empty_subtitle.setObjectName("emptyStateSubtitle")
        empty_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_subtitle)

        btn_empty_demo = QPushButton("⚡ Trigger Demo Tool Request")
        btn_empty_demo.setObjectName("secondaryButton")
        btn_empty_demo.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_empty_demo.clicked.connect(lambda: self.trigger_demo_requested.emit())
        empty_layout.addWidget(btn_empty_demo, alignment=Qt.AlignmentFlag.AlignCenter)

        pending_layout.addWidget(self.empty_state_frame)

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
        self.table_pending.hide()  # Initially hidden until requests arrive
        pending_layout.addWidget(self.table_pending)

        self.tabs.addTab(self.pending_tab, "⚡ Active Requests (0)")

        # Tab 2: Audit History
        self.audit_tab = QWidget()
        self.audit_tab.setObjectName("auditTab")
        audit_layout = QVBoxLayout(self.audit_tab)
        audit_layout.setContentsMargins(14, 14, 14, 14)
        audit_layout.setSpacing(12)

        audit_header = QHBoxLayout()
        lbl_audit_title = QLabel("Security Audit History (SQLite)")
        lbl_audit_title.setObjectName("sectionHeaderLabel")
        audit_header.addWidget(lbl_audit_title)
        audit_header.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Filter audit logs...")
        self.search_input.setFixedWidth(200)
        self.search_input.textChanged.connect(self._on_audit_search_changed)
        audit_header.addWidget(self.search_input)

        btn_refresh = QPushButton("↻ Refresh Log")
        btn_refresh.setObjectName("secondaryButton")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(lambda: self.refresh_audit_requested.emit())
        audit_header.addWidget(btn_refresh)
        audit_layout.addLayout(audit_header)

        # Empty State Placeholder Frame for Audit Tab
        self.audit_empty_frame = QFrame()
        self.audit_empty_frame.setObjectName("emptyStateFrame")
        audit_empty_layout = QVBoxLayout(self.audit_empty_frame)
        audit_empty_layout.setContentsMargins(30, 40, 30, 40)
        audit_empty_layout.setSpacing(12)
        audit_empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        audit_empty_icon = QLabel("📋")
        audit_empty_icon.setStyleSheet(
            "font-size: 32px; background-color: #F1F5F9; border-radius: 28px; padding: 12px;"
        )
        audit_empty_icon.setFixedSize(56, 56)
        audit_empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audit_empty_layout.addWidget(audit_empty_icon, alignment=Qt.AlignmentFlag.AlignCenter)

        audit_empty_title = QLabel("No Audit Records Found")
        audit_empty_title.setObjectName("emptyStateTitle")
        audit_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audit_empty_layout.addWidget(audit_empty_title)

        audit_empty_subtitle = QLabel(
            "Every MCP tool call intercepted and decided (Approved, Rejected, or Timed Out)\n"
            "will be durably persisted to SQLite and displayed here."
        )
        audit_empty_subtitle.setObjectName("emptyStateSubtitle")
        audit_empty_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audit_empty_layout.addWidget(audit_empty_subtitle)

        audit_layout.addWidget(self.audit_empty_frame)

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
        self.table_audit.hide()  # Initially hidden until records arrive
        audit_layout.addWidget(self.table_audit)

        self.tabs.addTab(self.audit_tab, "📋 Audit History")
        main_layout.addWidget(self.tabs)

        # 4. Status Bar
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
        """Re-render the active pending table and update KPI metrics."""
        self.table_pending.setRowCount(0)
        num_pending = len(self._pending_requests)
        self.lbl_queue_count.setText(f"Active Authorizations Queue ({num_pending})")
        self.tabs.setTabText(0, f"⚡ Active Requests ({num_pending})")
        self.lbl_stat_pending.setText(str(num_pending))

        if num_pending == 0:
            self.empty_state_frame.show()
            self.table_pending.hide()
            return

        self.empty_state_frame.hide()
        self.table_pending.show()

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
            btn_inspect.setObjectName("primaryButton")
            btn_inspect.setStyleSheet(
                f"padding: 2px 10px; font-size: 11px; background-color: {COLOR_BRAND_PRIMARY}; "
                f"color: white; border-radius: 4px;"
            )
            btn_inspect.setCursor(Qt.CursorShape.PointingHandCursor)
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
    # Audit Table Population & Filtering
    # -------------------------------------------------------------------------

    def update_audit_records(self, records: List[AuditRecord]) -> None:
        """Render recent audit records into the audit log table."""
        self._audit_records = records
        self.lbl_stat_audit.setText(str(len(records)))
        self._render_audit_table(records)

    def _on_audit_search_changed(self, search_text: str) -> None:
        """Filter audit table by search query in server, tool, or ID."""
        query = search_text.strip().lower()
        if not query:
            self._render_audit_table(self._audit_records)
            return

        filtered = [
            r for r in self._audit_records
            if query in r.server_name.lower()
            or query in r.tool_name.lower()
            or query in r.request_id.lower()
            or query in r.decision.value.lower()
        ]
        self._render_audit_table(filtered)

    def _render_audit_table(self, records: List[AuditRecord]) -> None:
        """Render given list of records to the audit table."""
        self.table_audit.setRowCount(0)

        if not records:
            self.audit_empty_frame.show()
            self.table_audit.hide()
            self.status_bar.showMessage("Audit log ready. Total records loaded: 0")
            return

        self.audit_empty_frame.hide()
        self.table_audit.show()

        for row, rec in enumerate(records):
            self.table_audit.insertRow(row)

            # Timestamp
            ts_str = RequestPresenter.get_formatted_timestamp(rec.timestamp)
            self.table_audit.setItem(row, 0, QTableWidgetItem(ts_str))

            # Server & Tool
            self.table_audit.setItem(row, 1, QTableWidgetItem(rec.server_name))
            self.table_audit.setItem(row, 2, QTableWidgetItem(rec.tool_name))

            # Decision Badge
            decision_item = QTableWidgetItem(f" {rec.decision.value} ")
            if rec.decision == AuthorizationStatus.APPROVED:
                decision_item.setForeground(Qt.GlobalColor.darkGreen)
            elif rec.decision == AuthorizationStatus.REJECTED:
                decision_item.setForeground(Qt.GlobalColor.red)
            elif rec.decision == AuthorizationStatus.TIMED_OUT:
                decision_item.setForeground(Qt.GlobalColor.darkYellow)
            else:
                decision_item.setForeground(Qt.GlobalColor.gray)
            self.table_audit.setItem(row, 3, decision_item)

            # Execution Outcome
            outcome_text = rec.execution_outcome or ""
            if rec.failure_reason:
                outcome_text = f"{outcome_text}: {rec.failure_reason}"
            self.table_audit.setItem(row, 4, QTableWidgetItem(outcome_text))

            # Request ID
            self.table_audit.setItem(row, 5, QTableWidgetItem(rec.request_id[:8] + "..."))

        self.status_bar.showMessage(
            f"Audit log updated. Total records loaded: {len(self._audit_records)}"
        )

    # -------------------------------------------------------------------------
    # Window Close Override (Minimize to Tray)
    # -------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing main window hides to system tray rather than killing the security gateway."""
        self.hide()
        event.ignore()
        logger.info("Main window minimized to system tray.")
