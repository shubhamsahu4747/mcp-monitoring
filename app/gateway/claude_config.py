"""Utility to configure Claude Desktop with Walled AI MCP Gateway.

Provides safe configuration installation, timestamped backups, rollback,
and strict validation for %APPDATA%\\Claude\\claude_desktop_config.json.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def get_claude_config_path() -> Path:
    """Return the platform-specific path to claude_desktop_config.json."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata)
        else:
            base = Path.home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    else:
        return Path.home() / ".config" / "claude" / "claude_desktop_config.json"


def generate_gateway_config(
    server_name: str = "Safe Mock MCP Server",
    upstream_cmd: Optional[str] = None,
    upstream_args: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate the MCP server entry for Claude Desktop using this environment.

    Uses absolute paths and unbuffered output mode so Claude Desktop stdio
    transport operates cleanly without pipe buffer stalls.
    """
    python_exe = str(Path(sys.executable).resolve())
    project_root = str(Path.cwd().resolve())
    mock_server_script = str(Path("examples/mock_mcp_server.py").resolve())

    actual_upstream_cmd = upstream_cmd or python_exe
    actual_upstream_args = upstream_args or f'"{mock_server_script}"'

    return {
        "command": python_exe,
        "args": [
            "-u",
            "-m",
            "app.main",
            "local-proxy",
            "--upstream-cmd",
            actual_upstream_cmd,
            "--upstream-args",
            actual_upstream_args,
            "--server-name",
            server_name,
        ],
        "env": {
            "PYTHONPATH": project_root,
            "PYTHONUNBUFFERED": "1",
        },
    }


def backup_claude_config(config_path: Optional[Path] = None) -> Optional[Path]:
    """Create timestamped and standard .bak backups of Claude Desktop config.

    Args:
        config_path: Path to claude_desktop_config.json, or default if None.

    Returns:
        Path to the timestamped backup file if a backup was made, or None.
    """
    target = config_path or get_claude_config_path()
    if not target.exists():
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    timestamped_bak = target.parent / f"{target.name}.bak.{timestamp}"
    standard_bak = target.with_suffix(".json.bak")

    shutil.copy2(target, timestamped_bak)
    shutil.copy2(target, standard_bak)
    logger.info("Created configuration backup at: %s", timestamped_bak)
    return timestamped_bak


def install_claude_config(
    server_key: str = "walled-ai-gateway",
    config_path: Optional[Path] = None,
    server_name: str = "Safe Mock MCP Server",
    upstream_cmd: Optional[str] = None,
    upstream_args: Optional[str] = None,
) -> Tuple[Path, str]:
    """Install or update Walled AI gateway entry in Claude Desktop config safely.

    Preserves existing unrelated MCP servers, creates a timestamped backup first,
    and writes out the updated JSON configuration.

    Args:
        server_key: Key name under mcpServers dictionary.
        config_path: Optional custom config path (for testing or non-standard paths).
        server_name: Display name for upstream server.
        upstream_cmd: Upstream command executable.
        upstream_args: Upstream command arguments string.

    Returns:
        Tuple of (config_path, generated_json_string).
    """
    target_path = config_path or get_claude_config_path()
    entry = generate_gateway_config(
        server_name=server_name,
        upstream_cmd=upstream_cmd,
        upstream_args=upstream_args,
    )
    json_snippet = json.dumps({"mcpServers": {server_key: entry}}, indent=2)

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        config_data: Dict[str, Any] = {}
        if target_path.exists():
            # Create safety backup before modification
            backup_claude_config(target_path)
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception as e:
                logger.warning("Could not parse existing %s, backing up: %s", target_path, e)
                ts = int(datetime.now().timestamp())
                corrupt_backup = target_path.parent / f"{target_path.name}.corrupt.{ts}"
                shutil.copy2(target_path, corrupt_backup)
                config_data = {}

        if "mcpServers" not in config_data or not isinstance(config_data["mcpServers"], dict):
            config_data["mcpServers"] = {}

        # Preserve existing entries and update Walled AI gateway
        config_data["mcpServers"][server_key] = entry

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        logger.info("Successfully updated Claude Desktop configuration at %s", target_path)
    except PermissionError:
        logger.warning(
            "Permission denied writing to %s. Outputting snippet for manual setup.",
            target_path,
        )

    return target_path, json_snippet


def validate_claude_config(
    config_path: Optional[Path] = None,
    server_key: str = "walled-ai-gateway",
) -> Tuple[bool, List[str]]:
    """Validate Claude Desktop configuration file and Walled AI entry.

    Performs structural, path, and environment validation.

    Args:
        config_path: Path to claude_desktop_config.json, or default if None.
        server_key: Expected MCP server key name.

    Returns:
        Tuple of (is_valid, list_of_status_messages).
    """
    target = config_path or get_claude_config_path()
    messages: List[str] = []

    if not target.exists():
        messages.append(f"FAIL: Configuration file does not exist at '{target}'")
        return False, messages

    messages.append(f"PASS: Configuration file exists at '{target}'")

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        messages.append(f"FAIL: Configuration is not valid JSON: {e}")
        return False, messages

    messages.append("PASS: Configuration is valid JSON")

    if not isinstance(data, dict):
        messages.append("FAIL: Root configuration must be a JSON object")
        return False, messages

    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        messages.append("FAIL: 'mcpServers' object is missing or not a dictionary")
        return False, messages

    messages.append(f"PASS: Found 'mcpServers' object with {len(mcp_servers)} server(s)")

    if server_key not in mcp_servers:
        messages.append(f"FAIL: Server '{server_key}' not found in 'mcpServers'")
        return False, messages

    server_entry = mcp_servers[server_key]
    if not isinstance(server_entry, dict):
        messages.append(f"FAIL: Entry for '{server_key}' must be a dictionary")
        return False, messages

    messages.append(f"PASS: Found server entry for '{server_key}'")

    # 1. Validate Command Executable
    command = server_entry.get("command")
    if not command:
        messages.append("FAIL: 'command' field is missing or empty")
        return False, messages

    cmd_path = Path(command)
    if not cmd_path.exists():
        messages.append(f"FAIL: Command executable not found on disk: '{command}'")
        return False, messages

    messages.append(f"PASS: Command executable verified: '{command}'")

    # 2. Validate Python version of the command executable
    try:
        result = subprocess.run(
            [str(cmd_path), "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        version_str = (result.stdout or result.stderr).strip()
        messages.append(f"PASS: Python interpreter verified: {version_str}")
        if "3.12" not in version_str:
            messages.append(f"WARN: Interpreter is '{version_str}', recommended is Python 3.12")
    except Exception as e:
        messages.append(f"WARN: Could not execute command to verify version: {e}")

    # 3. Validate Arguments
    args = server_entry.get("args", [])
    if not isinstance(args, list):
        messages.append("FAIL: 'args' field must be a list")
        return False, messages

    if "local-proxy" not in args:
        messages.append("FAIL: 'args' must include 'local-proxy' command")
        return False, messages

    messages.append("PASS: Proxy command arguments verified ('local-proxy' present)")

    # 4. Validate Environment Variables
    env_vars = server_entry.get("env", {})
    if isinstance(env_vars, dict) and "PYTHONPATH" in env_vars:
        pypath = Path(env_vars["PYTHONPATH"])
        if pypath.exists():
            messages.append(f"PASS: PYTHONPATH directory verified on disk: '{pypath}'")
        else:
            messages.append(f"WARN: PYTHONPATH directory does not exist: '{pypath}'")

    # 5. Check other servers are preserved
    other_servers = [k for k in mcp_servers.keys() if k != server_key]
    if other_servers:
        messages.append(f"PASS: Other MCP server(s) preserved: {', '.join(other_servers)}")
    else:
        messages.append("INFO: Walled AI is the only configured MCP server")

    all_passed = not any(m.startswith("FAIL:") for m in messages)
    return all_passed, messages


def rollback_claude_config(config_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Roll back Claude Desktop configuration to the latest available backup.

    Args:
        config_path: Path to claude_desktop_config.json, or default if None.

    Returns:
        Tuple of (success, message).
    """
    target = config_path or get_claude_config_path()
    if not target.parent.exists():
        return False, f"Directory does not exist: '{target.parent}'"

    # Search for all backup files
    backups = list(target.parent.glob(f"{target.name}.bak*"))
    standard_bak = target.with_suffix(".json.bak")
    if standard_bak.exists() and standard_bak not in backups:
        backups.append(standard_bak)

    if not backups:
        return False, f"No backup configuration files found in '{target.parent}'"

    # Sort by modification time (most recent first)
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_backup = backups[0]

    try:
        shutil.copy2(latest_backup, target)
        msg = (
            f"Successfully rolled back Claude Desktop configuration "
            f"from '{latest_backup.name}' to '{target.name}'"
        )
        logger.info(msg)
        return True, msg
    except Exception as e:
        err = f"Failed to restore configuration from '{latest_backup}': {e}"
        logger.error(err)
        return False, err
