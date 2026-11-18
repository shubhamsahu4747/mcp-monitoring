"""Request Interceptor enforcing authorization and at-most-once upstream execution."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

import mcp.types as types

from app.authorization.manager import AuthorizationManager, authorization_manager
from app.gateway.upstream_manager import UpstreamConnectionError, UpstreamManager, upstream_manager
from app.interception.request_parser import RequestParser
from app.models.requests import (
    AlreadyDispatchedError,
    AuditOutcome,
    AuditRecord,
    AuthorizationCancelledError,
    AuthorizationStatus,
    AuthorizationTimeoutError,
)
from app.storage.audit_repository import AuditRepository, audit_repo

logger = logging.getLogger(__name__)


class RequestInterceptor:
    """Intercepts MCP tool invocations, gates execution behind authorization,
    enforces at-most-once dispatch, and audits outcomes.
    """

    def __init__(
        self,
        auth_mgr: AuthorizationManager | None = None,
        upstream_mgr: UpstreamManager | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self.auth_mgr = auth_mgr or authorization_manager
        self.upstream_mgr = upstream_mgr or upstream_manager
        self.audit_repo = audit_repository or audit_repo

    async def _safe_audit(self, record: AuditRecord) -> None:
        """Safely attempt to persist an audit record without breaking fail-closed flow."""
        try:
            await self.audit_repo.record_audit(record)
        except Exception as e:
            logger.error(
                "CRITICAL: Failed to write audit record %s to database: %s",
                record.request_id,
                e,
            )

    async def intercept_call_tool(
        self,
        name: str,
        arguments: Dict[str, Any] | None,
    ) -> types.CallToolResult:
        """Process an intercepted tools/call request.

        Args:
            name: Requested tool name.
            arguments: Original tool arguments dictionary.

        Returns:
            CallToolResult containing either upstream execution output or a valid MCP error.
        """
        try:
            clean_name, clean_args = RequestParser.parse_tool_call(name, arguments)
        except ValueError as err:
            logger.warning("Rejected invalid tool call request: %s", err)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Invalid request: {err}")],
                is_error=True,
            )

        # 1. Request authorization
        try:
            auth_request = await self.auth_mgr.request_authorization(
                server_name=self.upstream_mgr.server_name,
                tool_name=clean_name,
                arguments=clean_args,
            )
        except AuthorizationTimeoutError:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Authorization timed out for tool '{clean_name}'. Request blocked.",
                    )
                ],
                is_error=True,
            )
        except AuthorizationCancelledError:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Authorization cancelled for tool '{clean_name}'. Request blocked.",
                    )
                ],
                is_error=True,
            )

        # 2. Check Decision
        if auth_request.status == AuthorizationStatus.REJECTED:
            reason = auth_request.reason or "Explicitly rejected by user"
            logger.info("Blocked tool execution for %s: %s", clean_name, reason)

            reason_lower = reason.lower()
            if (
                "agent is not running" in reason_lower
                or "unreachable" in reason_lower
                or "all connection attempts failed" in reason_lower
            ):
                outcome = AuditOutcome.BLOCKED_AGENT_UNAVAILABLE
                error_msg = (
                    f"Tool execution for '{clean_name}' was blocked: "
                    f"Walled AI Desktop Agent is unavailable ({reason})"
                )
            elif (
                "agent authentication failed" in reason_lower
                or "http 401" in reason_lower
                or "invalid agent token" in reason_lower
                or "internal authorization error" in reason_lower
            ):
                outcome = AuditOutcome.BLOCKED_AUTHORIZATION_ERROR
                error_msg = (
                    f"Tool execution for '{clean_name}' was blocked due to authorization error: "
                    f"{reason}"
                )
            else:
                outcome = AuditOutcome.USER_REJECTED
                error_msg = f"Tool execution for '{clean_name}' was blocked by user: {reason}"

            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome=outcome,
                failure_reason=reason,
            )
            await self._safe_audit(audit_record)

            return types.CallToolResult(
                content=[types.TextContent(type="text", text=error_msg)],
                is_error=True,
            )

        elif auth_request.status != AuthorizationStatus.APPROVED:
            # Any non-approved status must never execute (Fail-closed)
            logger.warning(
                "Tool execution blocked: request in non-approved status %s",
                auth_request.status.value,
            )
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome="BLOCKED",
                failure_reason=f"Status: {auth_request.status.value}",
            )
            await self._safe_audit(audit_record)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Tool execution blocked: status {auth_request.status.value}",
                    )
                ],
                is_error=True,
            )

        # 3. Approved: Enforce at-most-once dispatch check
        try:
            auth_request.mark_dispatched()
        except AlreadyDispatchedError as e:
            logger.error("Blocked duplicate dispatch attempt for %s: %s", clean_name, e)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Duplicate execution blocked: request was already dispatched ({e})",
                    )
                ],
                is_error=True,
            )

        # 4. Forward to upstream MCP server preserving original arguments
        logger.info(
            "Forwarding approved tool call '%s' (req_id=%s) upstream...",
            clean_name,
            auth_request.request_id,
        )
        try:
            upstream_result = await self.upstream_mgr.call_tool(
                name=clean_name,
                arguments=clean_args,
            )
            execution_outcome = "TOOL_ERROR" if upstream_result.is_error else "SUCCESS"
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome=execution_outcome,
                failure_reason=None if execution_outcome == "SUCCESS" else "Upstream tool error",
            )
            await self._safe_audit(audit_record)
            return upstream_result

        except asyncio.CancelledError:
            logger.warning("Upstream call for '%s' was cancelled in flight", clean_name)
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome="EXECUTION_UNKNOWN",
                failure_reason="Client cancelled or gateway shut down during upstream execution",
            )
            await self._safe_audit(audit_record)
            raise

        except UpstreamConnectionError as e:
            logger.error("Upstream execution failed for '%s': %s", clean_name, e)
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome="UPSTREAM_FAILURE",
                failure_reason=str(e),
            )
            await self._safe_audit(audit_record)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Upstream MCP server error executing '{clean_name}': {e}",
                    )
                ],
                is_error=True,
            )
        except Exception as e:
            logger.error(
                "Unexpected exception during upstream execution of '%s': %s",
                clean_name,
                e,
            )
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome="EXECUTION_UNKNOWN",
                failure_reason=f"Unexpected error: {e}",
            )
            await self._safe_audit(audit_record)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Unexpected execution error for '{clean_name}': {e}",
                    )
                ],
                is_error=True,
            )


# Default singleton instance
request_interceptor = RequestInterceptor()
