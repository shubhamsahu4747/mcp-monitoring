"""Authorization Manager coordinating pending requests, timeouts, and decisions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from app.authorization.decisions import DecisionProcessor
from app.authorization.pending_requests import PendingRequestRegistry
from app.config import settings
from app.models.requests import (
    AuditRecord,
    AuthorizationCancelledError,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    AuthorizationTimeoutError,
)
from app.storage.audit_repository import AuditRepository, audit_repo

logger = logging.getLogger(__name__)

NotificationHook = Callable[[AuthorizationRequest], Awaitable[None]]


class AuthorizationManager:
    """Asynchronous authorization service enforcing atomic decisions and timeouts."""

    def __init__(
        self,
        timeout_seconds: float | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.auth_timeout_seconds
        )
        self.audit_repo = audit_repository or audit_repo
        self.registry = PendingRequestRegistry()
        self.decision_processor = DecisionProcessor(self.registry)
        self._notification_hook: Optional[NotificationHook] = None

    def set_notification_hook(self, hook: Optional[NotificationHook]) -> None:
        """Register a callback invoked whenever a new authorization request is intercepted."""
        self._notification_hook = hook

    async def request_authorization(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> AuthorizationRequest:
        """Intercept a tool invocation, request authorization, and await user decision.

        Args:
            server_name: Upstream MCP server hosting the tool.
            tool_name: Name of tool being called.
            arguments: Original tool arguments dictionary.

        Returns:
            The finalized AuthorizationRequest in an APPROVED state.

        Raises:
            AuthorizationTimeoutError: If the user does not respond within timeout.
            AuthorizationCancelledError: If the request is cancelled or aborted.
            AuthorizationError: If rejected by user or an error occurs.
        """
        request = AuthorizationRequest(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AuthorizationDecision] = loop.create_future()

        await self.registry.register(request, future)

        # Notify any registered UI or test hook
        if self._notification_hook:
            try:
                await self._notification_hook(request)
            except Exception as e:
                logger.error("Error invoking authorization notification hook: %s", e)

        try:
            await asyncio.wait_for(future, timeout=self.timeout_seconds)
            # Request status was already transitioned to APPROVED or REJECTED by DecisionProcessor
            return request

        except asyncio.TimeoutError as e:
            logger.warning(
                "Authorization request %s timed out after %.1fs (tool=%s)",
                request.request_id,
                self.timeout_seconds,
                request.tool_name,
            )
            await self.registry.remove(request.request_id)
            if not request.is_terminal():
                request.transition_to(
                    AuthorizationStatus.TIMED_OUT,
                    reason=f"Authorization timed out after {self.timeout_seconds} seconds",
                )
            audit_record = AuditRecord.from_request(
                request,
                execution_outcome="TIMED_OUT",
                failure_reason=request.reason,
            )
            await self.audit_repo.record_audit(audit_record)
            raise AuthorizationTimeoutError(
                f"Authorization timed out for tool '{tool_name}'"
            ) from e

        except asyncio.CancelledError as e:
            logger.warning(
                "Authorization request %s was cancelled (tool=%s)",
                request.request_id,
                request.tool_name,
            )
            await self.registry.remove(request.request_id)
            if not request.is_terminal():
                request.transition_to(
                    AuthorizationStatus.CANCELLED,
                    reason="Authorization request was cancelled during shutdown",
                )
            audit_record = AuditRecord.from_request(
                request,
                execution_outcome="CANCELLED",
                failure_reason=request.reason,
            )
            await self.audit_repo.record_audit(audit_record)
            raise AuthorizationCancelledError(
                f"Authorization request cancelled for tool '{tool_name}'"
            ) from e

    async def submit_decision(self, decision: AuthorizationDecision) -> AuthorizationRequest:
        """Process an authorization decision submitted by the user or test provider."""
        return await self.decision_processor.apply_decision(decision)

    async def shutdown(self) -> None:
        """Cancel all pending requests and clean up registry."""
        logger.info("Shutting down AuthorizationManager, cancelling pending requests...")
        await self.registry.cancel_all(reason="Gateway shutdown")


# Default singleton instance
authorization_manager = AuthorizationManager()
