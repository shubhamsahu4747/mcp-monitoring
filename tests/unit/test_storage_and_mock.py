"""Unit tests for SQLite audit storage and Safe Mock MCP Server."""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.requests import AuditRecord, AuthorizationStatus
from app.storage.audit_repository import AuditRepository
from app.storage.database import DatabaseManager
from examples.mock_mcp_server import (
    calculate_sum,
    create_demo_file,
    get_system_info,
    read_demo_file,
)
from examples.mock_mcp_server import (
    server as mock_server,
)


@pytest.mark.asyncio
async def test_database_manager_missing_directory(tmp_path: Path) -> None:
    """Check 10: Verify application handles missing database directories automatically."""
    deep_path = tmp_path / "deep" / "nested" / "dir" / "audit.db"
    assert not deep_path.parent.exists()

    manager = DatabaseManager(deep_path)
    await manager.initialize_schema()
    assert deep_path.parent.exists()
    assert deep_path.exists()


@pytest.mark.asyncio
async def test_audit_repository_crud(tmp_path: Path) -> None:
    """Check 3: Verify SQLite database initialization, schema creation, and CRUD operations."""
    db_file = tmp_path / "test_audit.db"
    manager = DatabaseManager(db_file)
    repo = AuditRepository(manager)
    await repo.initialize()

    record1 = AuditRecord(
        request_id="audit-101",
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        timestamp=datetime.now(timezone.utc),
        decision=AuthorizationStatus.APPROVED,
        decision_timestamp=datetime.now(timezone.utc),
        execution_outcome="SUCCESS",
        failure_reason=None,
        arguments_redacted={"a": 5, "b": 10},
    )

    record2 = AuditRecord(
        request_id="audit-102",
        server_name="Safe Mock MCP Server",
        tool_name="read_demo_file",
        timestamp=datetime.now(timezone.utc),
        decision=AuthorizationStatus.REJECTED,
        decision_timestamp=datetime.now(timezone.utc),
        execution_outcome="BLOCKED",
        failure_reason="User denied authorization",
        arguments_redacted={"filename": "confidential.txt"},
    )

    # Insert records
    await repo.record_audit(record1)
    await repo.record_audit(record2)

    # Read by ID
    retrieved1 = await repo.get_record("audit-101")
    assert retrieved1 is not None
    assert retrieved1.request_id == "audit-101"
    assert retrieved1.decision == AuthorizationStatus.APPROVED
    assert retrieved1.arguments_redacted == {"a": 5, "b": 10}

    retrieved2 = await repo.get_record("audit-102")
    assert retrieved2 is not None
    assert retrieved2.decision == AuthorizationStatus.REJECTED
    assert retrieved2.execution_outcome == "BLOCKED"

    # Non-existent ID returns None
    assert await repo.get_record("nonexistent") is None

    # List records
    all_records = await repo.list_records(limit=10)
    assert len(all_records) == 2

    # Update existing record (UPSERT)
    record1_updated = AuditRecord(
        request_id="audit-101",
        server_name="Safe Mock MCP Server",
        tool_name="calculate_sum",
        timestamp=record1.timestamp,
        decision=AuthorizationStatus.APPROVED,
        decision_timestamp=datetime.now(timezone.utc),
        execution_outcome="SUCCESS",
        failure_reason="Updated note",
        arguments_redacted={"a": 5, "b": 10},
    )
    await repo.record_audit(record1_updated)
    retrieved1_updated = await repo.get_record("audit-101")
    assert retrieved1_updated is not None
    assert retrieved1_updated.failure_reason == "Updated note"


def test_mock_mcp_server_tools_registered() -> None:
    """Check 8: Verify mock MCP server tools are registered and functional."""
    # Ensure server has tools registered
    assert mock_server.name == "Safe Mock MCP Server"

    # Test get_system_info
    sys_info = get_system_info()
    assert "os" in sys_info
    assert "python_version" in sys_info
    assert "architecture" in sys_info

    # Test calculate_sum
    result = calculate_sum(12.5, 7.5)
    assert result == 20.0

    # Test create_demo_file & read_demo_file in sandbox
    create_msg = create_demo_file("test_audit.txt", "Audit Test Content")
    assert "Successfully created" in create_msg

    content = read_demo_file("test_audit.txt")
    assert content == "Audit Test Content"

    # Missing file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        read_demo_file("missing_file_xyz.txt")

    # Path traversal must be blocked
    with pytest.raises(ValueError, match="Path traversal detected"):
        read_demo_file("../../windows/system32/cmd.exe")

    with pytest.raises(ValueError, match="Path traversal detected"):
        create_demo_file("../outside.txt", "malicious")


def test_mock_mcp_server_process_lifecycle() -> None:
    """Check 8: Verify mock MCP server process starts and shuts down cleanly on EOF."""
    proc = subprocess.Popen(
        [sys.executable, "examples/mock_mcp_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Close stdin to signal EOF and trigger graceful shutdown
    stdout, stderr = proc.communicate(timeout=5.0)
    assert proc.returncode == 0 or proc.returncode is None
    # Verify stderr received startup log
    assert "Starting Safe Mock MCP Server on stdio transport" in stderr
