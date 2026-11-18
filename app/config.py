"""Configuration management for the Walled AI MCP Security Gateway.

All settings are validated via Pydantic Settings and can be overridden
via environment variables or an optional .env file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration schema."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Authorization Engine
    auth_timeout_seconds: float = Field(
        default=30.0,
        description="Timeout in seconds before an unanswered authorization request fails closed.",
    )
    redact_keys: str = Field(
        default="password,secret,token,api_key,authorization,private_key,access_token,credential,secret_key",
        description="Comma-separated sensitive parameter keys to redact in UI and audit logs.",
    )

    # SQLite Database
    database_path: str = Field(
        default="data/audit.db",
        description="Path to SQLite audit log database.",
    )

    # Upstream MCP Server
    default_upstream_command: str = Field(
        default="python",
        description="Command to launch default upstream MCP server.",
    )
    default_upstream_args: str = Field(
        default="examples/mock_mcp_server.py",
        description="Arguments passed to upstream MCP server command.",
    )
    upstream_server_name: str = Field(
        default="Safe Mock MCP Server",
        description="Display name of the upstream MCP server.",
    )

    # Remote Gateway Settings
    remote_host: str = Field(
        default="127.0.0.1",
        description="Host for the remote MCP gateway.",
    )
    remote_port: int = Field(
        default=8765,
        description="Port for the remote MCP gateway.",
    )
    agent_auth_token: str = Field(
        default="change-this-secure-desktop-token-in-production",
        description="Authentication bearer token required for desktop agent WebSocket connections.",
    )

    # Diagnostics
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )

    @property
    def sensitive_keys_list(self) -> List[str]:
        """Return list of lowercase sensitive parameter keys."""
        return [k.strip().lower() for k in self.redact_keys.split(",") if k.strip()]

    @property
    def db_path(self) -> Path:
        """Resolved Path object for database file."""
        return Path(self.database_path).resolve()


def configure_logging(level: str = "INFO") -> None:
    """Configure logging strictly to stderr to keep stdout clean for MCP stdio.

    Args:
        level: Log level name (e.g. INFO, DEBUG).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)


# Global settings singleton
settings = Settings()
