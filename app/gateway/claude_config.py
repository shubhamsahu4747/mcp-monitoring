"""Utility to configure Claude Desktop with Walled AI MCP Gateway."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

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


def generate_gateway_config() -> Dict[str, Any]:
    """Generate the MCP server entry for Claude Desktop using this environment."""
    python_exe = str(Path(sys.executable).resolve())
    project_root = str(Path.cwd().resolve())
    mock_server_script = str(Path("examples/mock_mcp_server.py").resolve())

    return {
        "command": python_exe,
        "args": [
            "-m",
            "app.main",
            "local-proxy",
            "--upstream-cmd",
            python_exe,
            "--upstream-args",
            f'"{mock_server_script}"',
            "--server-name",
            "Safe Mock MCP Server",
        ],
        "env": {
            "PYTHONPATH": project_root,
        },
    }


def install_claude_config(server_key: str = "walled-ai-gateway") -> tuple[Path, str]:
    """Install or update Walled AI gateway entry in Claude Desktop config.

    Args:
        server_key: Key name under mcpServers dictionary.

    Returns:
        Tuple of (config_path, generated_json_string).
    """
    config_path = get_claude_config_path()
    entry = generate_gateway_config()
    json_snippet = json.dumps({"mcpServers": {server_key: entry}}, indent=2)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config_data: Dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
            except Exception as e:
                logger.warning("Could not parse existing %s, backing up: %s", config_path, e)
                backup_path = config_path.with_suffix(".json.bak")
                config_path.rename(backup_path)
                config_data = {}

        if "mcpServers" not in config_data or not isinstance(config_data["mcpServers"], dict):
            config_data["mcpServers"] = {}

        config_data["mcpServers"][server_key] = entry

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)

        logger.info("Successfully updated Claude Desktop configuration at %s", config_path)
    except PermissionError:
        logger.warning(
            "Permission denied writing to %s. Outputting snippet for manual setup.",
            config_path,
        )

    return config_path, json_snippet
