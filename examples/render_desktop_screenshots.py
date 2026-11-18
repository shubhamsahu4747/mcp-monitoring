"""Renders high-resolution PNG screenshots of Walled AI desktop UI widgets offscreen."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run offscreen
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.desktop.authorization_popup import AuthorizationPopup  # noqa: E402
from app.desktop.main_window import MainWindow  # noqa: E402
from app.models.requests import AuditRecord, AuthorizationRequest, AuthorizationStatus  # noqa: E402


def generate_screenshots(output_dir: Path) -> list[Path]:
    """Capture and save PNG screenshots of desktop components."""
    output_dir.mkdir(parents=True, exist_ok=True)
    created_files: list[Path] = []

    app = QApplication.instance() or QApplication(sys.argv)

    # 1. Screenshot: Authorization Popup (with sensitive argument redaction)
    req = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="read_demo_file",
        arguments={
            "filename": "confidential_financials_2026.xlsx",
            "access_token": "bearer-eyJhbGciOiJIUzI1NiIsInR5cCI...",
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

    popup = AuthorizationPopup(request=req, timeout_seconds=30.0)
    popup.resize(620, 580)
    popup.show()
    app.processEvents()

    pixmap_popup = popup.grab()
    popup_path = output_dir / "screenshot_authorization_popup.png"
    pixmap_popup.save(str(popup_path), "PNG")
    created_files.append(popup_path)
    popup.close()

    # 2. Screenshot: Main Dashboard (with pending queue and audit records)
    main_win = MainWindow()
    main_win.resize(980, 640)

    # Add mock pending requests
    req1 = AuthorizationRequest(
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        arguments={"a": 1250.0, "b": 3750.0},
    )
    req2 = AuthorizationRequest(
        server_name="Cloud Infrastructure MCP",
        tool_name="restart_service",
        arguments={"service": "production-api-gateway", "force": False},
    )
    main_win.add_pending_request(req)
    main_win.add_pending_request(req1)
    main_win.add_pending_request(req2)

    # Add mock audit history
    audit_records = [
        AuditRecord(
            request_id="8f9a2b1c-3d4e-5f6a-7b8c-9d0e1f2a3b4c",
            server_name="Safe Mock MCP Server",
            tool_name="get_system_info",
            timestamp=datetime.now(timezone.utc),
            decision=AuthorizationStatus.APPROVED,
            decision_timestamp=datetime.now(timezone.utc),
            execution_outcome="SUCCESS",
            arguments_redacted={},
        ),
        AuditRecord(
            request_id="1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            server_name="Cloud Infrastructure MCP",
            tool_name="terminate_instance",
            timestamp=datetime.now(timezone.utc),
            decision=AuthorizationStatus.REJECTED,
            decision_timestamp=datetime.now(timezone.utc),
            execution_outcome="BLOCKED",
            failure_reason="Blocked by user in desktop popup",
            arguments_redacted={"instance_id": "i-0987654321"},
        ),
        AuditRecord(
            request_id="9b8a7f6e-5d4c-3b2a-1f0e-9d8c7b6a5f4e",
            server_name="Safe Mock MCP Server",
            tool_name="create_demo_file",
            timestamp=datetime.now(timezone.utc),
            decision=AuthorizationStatus.TIMED_OUT,
            decision_timestamp=datetime.now(timezone.utc),
            execution_outcome="TIMED_OUT",
            failure_reason="Authorization timed out after 30.0 seconds",
            arguments_redacted={"filename": "demo.txt"},
        ),
    ]
    main_win.update_audit_records(audit_records)
    main_win.show()
    app.processEvents()

    pixmap_main = main_win.grab()
    main_path = output_dir / "screenshot_main_dashboard.png"
    pixmap_main.save(str(main_path), "PNG")
    created_files.append(main_path)
    main_win.close()

    print(f"Generated {len(created_files)} desktop screenshots in {output_dir}")
    return created_files


if __name__ == "__main__":
    out = Path("docs/screenshots")
    generate_screenshots(out)
