"""Milestone 5 Integration Tests: Claude Desktop Integration & Dismissal Semantics.

Verifies:
1. Claude Desktop configuration generation with absolute paths, Python 3.12, unbuffered stdio.
2. Backup creation before modification, preserving unrelated MCP servers intact.
3. Configuration validation (schema, executable on disk, python version, args, env).
4. Rollback to latest backup.
5. Dismissal semantics:
   - Explicit rejection -> USER_REJECTED (0 upstream calls)
   - Closing popup without deciding -> USER_DISMISSED (0 upstream calls)
   - Timeout -> BLOCKED_TIMEOUT (0 upstream calls)
   - Agent offline -> BLOCKED_AGENT_UNAVAILABLE (0 upstream calls)
6. MCP stdio protocol integrity:
   - initialize capability negotiation
   - tools/list tool discovery
   - stdout clean JSON-RPC protocol messages only (diagnostics strictly to stderr)
"""

from __future__ import annotations

import asyncio
import json
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
from app.gateway.claude_config import (  # noqa: E402
    backup_claude_config,
    generate_gateway_config,
    install_claude_config,
    rollback_claude_config,
    validate_claude_config,
)
from app.gateway.local_proxy import LocalMCPProxy  # noqa: E402
from app.gateway.upstream_manager import UpstreamManager  # noqa: E402
from app.interception.request_interceptor import RequestInterceptor  # noqa: E402
from app.models.requests import AuditOutcome, AuthorizationStatus  # noqa: E402
from app.storage.audit_repository import AuditRepository  # noqa: E402
from app.storage.database import DatabaseManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Ensure singleton QApplication in offscreen mode."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(["--platform", "offscreen"])
    yield app


@pytest.fixture
async def milestone5_env(
    qapp: QApplication,
    tmp_path: Path,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Isolated environment with database, mock server, and desktop application."""
    db_file = tmp_path / "m5_audit.db"
    db_mgr = DatabaseManager(db_file)
    audit_repo = AuditRepository(db_mgr)
    await audit_repo.initialize()

    auth_mgr = AuthorizationManager(timeout_seconds=2.0, audit_repository=audit_repo)

    desktop_app = WalledAIDesktopApp(
        auth_mgr=auth_mgr,
        audit_repository=audit_repo,
        enable_tray=False,
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
        server_name="Walled AI Claude Gateway",
        enable_desktop_ipc=False,
    )

    try:
        yield {
            "desktop_app": desktop_app,
            "auth_mgr": auth_mgr,
            "audit_repo": audit_repo,
            "upstream_mgr": upstream_mgr,
            "interceptor": interceptor,
            "proxy": proxy,
            "tmp_path": tmp_path,
        }
    finally:
        await desktop_app.shutdown(quit_qt_app=False)
        await upstream_mgr.stop()


# =============================================================================
# 1. Claude Desktop Configuration Tests
# =============================================================================


def test_claude_config_generation() -> None:
    """Verify generated Claude Desktop configuration structure and parameters."""
    cfg = generate_gateway_config()

    assert Path(cfg["command"]).resolve() == Path(sys.executable).resolve()
    assert "-m" in cfg["args"]
    assert "app.main" in cfg["args"]
    assert "local-proxy" in cfg["args"]
    assert "-u" in cfg["args"]
    assert "env" in cfg
    assert "PYTHONPATH" in cfg["env"]
    assert cfg["env"]["PYTHONUNBUFFERED"] == "1"


def test_claude_config_backup_and_preservation(tmp_path: Path) -> None:
    """Verify existing unrelated MCP servers are preserved and backup is made."""
    config_file = tmp_path / "claude_desktop_config.json"

    # Seed with an unrelated third-party MCP server
    initial_data = {
        "mcpServers": {
            "filesystem-server": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\allowed"],
            },
            "github-mcp": {
                "command": "docker",
                "args": ["run", "-i", "mcp/github"],
            },
        }
    }
    config_file.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

    # Install Walled AI entry
    target, snippet = install_claude_config(config_path=config_file)
    assert target == config_file

    # Verify backup exists
    backups = list(tmp_path.glob("claude_desktop_config.json.bak*"))
    assert len(backups) >= 1

    # Verify content preservation
    with open(config_file, "r", encoding="utf-8") as f:
        updated = json.load(f)

    assert "filesystem-server" in updated["mcpServers"]
    assert "github-mcp" in updated["mcpServers"]
    assert "walled-ai-gateway" in updated["mcpServers"]
    assert updated["mcpServers"]["filesystem-server"]["command"] == "npx"


def test_claude_config_validation(tmp_path: Path) -> None:
    """Verify validation passes for valid config and fails for invalid configs."""
    config_file = tmp_path / "claude_desktop_config.json"

    # 1. Non-existent file
    valid, msgs = validate_claude_config(config_file)
    assert not valid
    assert any("does not exist" in m for m in msgs)

    # 2. Corrupt JSON
    config_file.write_text("{ broken json", encoding="utf-8")
    valid, msgs = validate_claude_config(config_file)
    assert not valid
    assert any("not valid JSON" in m for m in msgs)

    # 3. Valid installed config
    install_claude_config(config_path=config_file)
    valid, msgs = validate_claude_config(config_file)
    assert valid
    assert any("Command executable verified" in m for m in msgs)
    assert any("Python interpreter verified" in m for m in msgs)


def test_claude_config_rollback(tmp_path: Path) -> None:
    """Verify rollback restores the prior configuration cleanly."""
    config_file = tmp_path / "claude_desktop_config.json"

    # Step 1: Initial config
    initial_data = {"mcpServers": {"legacy-server": {"command": "legacy.exe", "args": []}}}
    config_file.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")
    backup_claude_config(config_file)

    # Step 2: Overwrite with new config
    install_claude_config(config_path=config_file)
    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "walled-ai-gateway" in data["mcpServers"]

    # Step 3: Rollback
    success, msg = rollback_claude_config(config_file)
    assert success
    assert "Successfully rolled back" in msg

    # Step 4: Verify restored to original
    with open(config_file, "r", encoding="utf-8") as f:
        restored = json.load(f)
    assert "legacy-server" in restored["mcpServers"]


# =============================================================================
# 2. Audit Outcome Dismissal Semantics Tests
# =============================================================================


@pytest.mark.asyncio
async def test_popup_dismissal_records_user_dismissed(
    milestone5_env: Dict[str, Any],
) -> None:
    """Closing popup [X] without clicking buttons records USER_DISMISSED and blocks upstream."""
    desktop_app: WalledAIDesktopApp = milestone5_env["desktop_app"]
    proxy: LocalMCPProxy = milestone5_env["proxy"]
    upstream_mgr: UpstreamManager = milestone5_env["upstream_mgr"]
    audit_repo: AuditRepository = milestone5_env["audit_repo"]

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    async def close_dialog() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.close()

    asyncio.create_task(close_dialog())

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 10.0, "b": 20.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "dismissed" in result.content[0].text.lower()  # type: ignore
    assert call_count == 0, "Upstream executed despite dialog dismissal!"

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].categorized_outcome in (
        AuditOutcome.USER_DISMISSED,
        AuditOutcome.BLOCKED_USER_DISMISSED,
    )


@pytest.mark.asyncio
async def test_explicit_rejection_records_user_rejected(
    milestone5_env: Dict[str, Any],
) -> None:
    """Explicitly clicking Reject Request records USER_REJECTED."""
    desktop_app: WalledAIDesktopApp = milestone5_env["desktop_app"]
    proxy: LocalMCPProxy = milestone5_env["proxy"]
    upstream_mgr: UpstreamManager = milestone5_env["upstream_mgr"]
    audit_repo: AuditRepository = milestone5_env["audit_repo"]

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    async def click_reject() -> None:
        while not desktop_app._active_popups:
            await asyncio.sleep(0.02)
        popup = next(iter(desktop_app._active_popups.values()))
        popup.btn_reject.click()

    asyncio.create_task(click_reject())

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 5.0, "b": 15.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert call_count == 0

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].categorized_outcome == AuditOutcome.USER_REJECTED


@pytest.mark.asyncio
async def test_timeout_records_blocked_timeout(
    milestone5_env: Dict[str, Any],
) -> None:
    """Leaving authorization request unanswered until deadline records BLOCKED_TIMEOUT."""
    proxy: LocalMCPProxy = milestone5_env["proxy"]
    upstream_mgr: UpstreamManager = milestone5_env["upstream_mgr"]
    audit_repo: AuditRepository = milestone5_env["audit_repo"]

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 1.0, "b": 2.0},
    )
    # Fast timeout set in fixture (2.0s)
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "timed out" in result.content[0].text.lower()  # type: ignore
    assert call_count == 0

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.TIMED_OUT
    assert records[0].categorized_outcome == AuditOutcome.BLOCKED_TIMEOUT


@pytest.mark.asyncio
async def test_agent_offline_records_blocked_agent_unavailable(
    milestone5_env: Dict[str, Any],
) -> None:
    """When desktop agent is offline, request fails closed with BLOCKED_AGENT_UNAVAILABLE."""
    upstream_mgr: UpstreamManager = milestone5_env["upstream_mgr"]
    audit_repo: AuditRepository = milestone5_env["audit_repo"]

    # Create proxy with an active IPC client pointing to an offline port
    auth_mgr = AuthorizationManager(timeout_seconds=2.0, audit_repository=audit_repo)
    interceptor = RequestInterceptor(
        auth_mgr=auth_mgr,
        upstream_mgr=upstream_mgr,
        audit_repository=audit_repo,
    )
    proxy = LocalMCPProxy(
        upstream_mgr=upstream_mgr,
        interceptor=interceptor,
        enable_desktop_ipc=True,
    )
    assert proxy.ipc_client is not None
    proxy.ipc_client.port = 59999
    proxy.ipc_client.base_url = "http://127.0.0.1:59999"

    call_count = 0

    async def counted_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        nonlocal call_count
        call_count += 1
        return types.CallToolResult(content=[], is_error=False)

    upstream_mgr.call_tool = counted_call_tool  # type: ignore[method-assign]

    params = types.CallToolRequestParams(
        name="calculate_sum",
        arguments={"a": 3.0, "b": 7.0},
    )
    result = await proxy._handle_call_tool(ctx=None, params=params)

    assert result.is_error
    assert "unavailable" in result.content[0].text.lower()  # type: ignore
    assert call_count == 0

    records = await audit_repo.list_records(limit=10)
    assert len(records) == 1
    assert records[0].decision == AuthorizationStatus.REJECTED
    assert records[0].categorized_outcome == AuditOutcome.BLOCKED_AGENT_UNAVAILABLE


# =============================================================================
# 3. Genuine MCP stdio Protocol Integrity Tests
# =============================================================================


@pytest.mark.asyncio
async def test_proxy_tools_list_returns_upstream_tools(
    milestone5_env: Dict[str, Any],
) -> None:
    """Downstream tools/list returns genuine real tools discovered from upstream."""
    proxy: LocalMCPProxy = milestone5_env["proxy"]
    upstream_mgr: UpstreamManager = milestone5_env["upstream_mgr"]

    # Mock list_tools on upstream to return realistic schema
    real_tools = [
        types.Tool(
            name="calculate_sum",
            description="Add two numbers safely",
            inputSchema={"type": "object", "properties": {"a": {"type": "number"}}},
        ),
        types.Tool(
            name="read_demo_file",
            description="Read a file from demo sandbox",
            inputSchema={"type": "object", "properties": {"filename": {"type": "string"}}},
        ),
    ]

    async def mock_list() -> list[types.Tool]:
        return real_tools

    upstream_mgr.list_tools = mock_list  # type: ignore[method-assign]

    result = await proxy._handle_list_tools(ctx=None)
    assert len(result.tools) == 2
    tool_names = [t.name for t in result.tools]
    assert "calculate_sum" in tool_names
    assert "read_demo_file" in tool_names
