"""Request Interceptor enforcing authorization before upstream MCP execution."""

from __future__ import annotations

import logging
from typing import Any, Dict

import mcp.types as types

from app.authorization.manager import AuthorizationManager, authorization_manager
from app.gateway.upstream_manager import UpstreamConnectionError, UpstreamManager, upstream_manager
from app.interception.request_parser import RequestParser
from app.models.requests import (
    AuditRecord,
    AuthorizationCancelledError,
    AuthorizationStatus,
    AuthorizationTimeoutError,
)
from app.storage.audit_repository import AuditRepository, audit_repo

logger = logging.getLogger(__name__)


class RequestInterceptor:
    """Intercepts MCP tool invocations, gates execution behind authorization,
    and audits outcomes.
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

            # Record audit entry for rejection
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome="BLOCKED",
                failure_reason=reason,
            )
            await self.audit_repo.record_audit(audit_record)

            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Tool execution for '{clean_name}' was blocked by user: {reason}",
                    )
                ],
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
            await self.audit_repo.record_audit(audit_record)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Tool execution blocked: status {auth_request.status.value}",
                    )
                ],
                is_error=True,
            )

        # 3. Approved: Forward to upstream MCP server preserving original arguments
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
            await self.audit_repo.record_audit(audit_record)
            return upstream_result

        except UpstreamConnectionError as e:
            logger.error("Upstream execution failed for '%s': %s", clean_name, e)
            audit_record = AuditRecord.from_request(
                auth_request,
                execution_outcome="UPSTREAM_FAILURE",
                failure_reason=str(e),
            )
            await self.audit_repo.record_audit(audit_record)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Upstream MCP server error executing '{clean_name}': {e}",
                    )
                ],
                is_error=True,
            )


# Default singleton instance
request_interceptor = RequestInterceptor()
