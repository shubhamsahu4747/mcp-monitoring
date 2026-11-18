"""Repository for persisting and querying audit records in SQLite."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

import aiosqlite

from app.models.requests import AuditRecord, AuthorizationStatus
from app.storage.database import DatabaseManager, db_manager

logger = logging.getLogger(__name__)


class AuditRepository:
    """Asynchronous audit log repository backed by SQLite and aiosqlite."""

    def __init__(self, manager: DatabaseManager | None = None) -> None:
        self.manager = manager or db_manager

    async def initialize(self) -> None:
        """Initialize database schema."""
        await self.manager.initialize_schema()

    async def record_audit(self, record: AuditRecord) -> None:
        """Persist an audit record to the database."""
        sql = """
        INSERT INTO audit_logs (
            request_id,
            server_name,
            tool_name,
            timestamp,
            decision,
            decision_timestamp,
            execution_outcome,
            failure_reason,
            arguments_redacted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET
            decision = excluded.decision,
            decision_timestamp = excluded.decision_timestamp,
            execution_outcome = excluded.execution_outcome,
            failure_reason = excluded.failure_reason,
            arguments_redacted = excluded.arguments_redacted;
        """
        params = (
            record.request_id,
            record.server_name,
            record.tool_name,
            record.timestamp.isoformat(),
            record.decision.value,
            record.decision_timestamp.isoformat() if record.decision_timestamp else None,
            record.execution_outcome,
            record.failure_reason,
            json.dumps(record.arguments_redacted),
        )

        async with self.manager.connection() as conn:
            await conn.execute(sql, params)
            await conn.commit()
            logger.debug("Persisted audit record %s", record.request_id)

    async def get_record(self, request_id: str) -> Optional[AuditRecord]:
        """Retrieve an audit record by its request ID."""
        sql = "SELECT * FROM audit_logs WHERE request_id = ?"
        async with self.manager.connection() as conn:
            async with conn.execute(sql, (request_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return self._row_to_record(row)

    async def list_records(self, limit: int = 50, offset: int = 0) -> List[AuditRecord]:
        """Retrieve recent audit records ordered by timestamp descending."""
        sql = "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        async with self.manager.connection() as conn:
            async with conn.execute(sql, (limit, offset)) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> AuditRecord:
        """Convert an aiosqlite Row to a validated AuditRecord model."""
        arguments_data = json.loads(row["arguments_redacted"])
        decision_dt = (
            datetime.fromisoformat(row["decision_timestamp"])
            if row["decision_timestamp"]
            else None
        )
        return AuditRecord(
            request_id=row["request_id"],
            server_name=row["server_name"],
            tool_name=row["tool_name"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            decision=AuthorizationStatus(row["decision"]),
            decision_timestamp=decision_dt,
            execution_outcome=row["execution_outcome"],
            failure_reason=row["failure_reason"],
            arguments_redacted=arguments_data,
        )


# Default audit repository singleton
audit_repo = AuditRepository()
