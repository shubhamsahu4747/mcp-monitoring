"""SQLite database management using aiosqlite.

Manages connection lifecycle, schema initialization, and directory provisioning.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_logs (
    request_id TEXT PRIMARY KEY,
    server_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_timestamp TEXT,
    execution_outcome TEXT,
    failure_reason TEXT,
    arguments_redacted TEXT NOT NULL,
    client_id TEXT,
    session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_server_tool ON audit_logs (server_name, tool_name);
CREATE INDEX IF NOT EXISTS idx_audit_decision ON audit_logs (decision);
CREATE INDEX IF NOT EXISTS idx_audit_client_session ON audit_logs (client_id, session_id);
"""


class DatabaseManager:
    """Manages asynchronous SQLite connections and schema initialization."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.db_path

    def ensure_directory_exists(self) -> None:
        """Create parent directory for the database file if it does not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Provide a safe async context manager for an aiosqlite connection."""
        self.ensure_directory_exists()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def initialize_schema(self) -> None:
        """Initialize the SQLite schema (tables and indices)."""
        async with self.connection() as conn:
            await conn.executescript(SCHEMA_SQL)
            await conn.commit()
            logger.debug("Database schema initialized at %s", self.db_path)


# Default database manager singleton
db_manager = DatabaseManager()
