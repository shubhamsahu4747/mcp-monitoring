"""Decision processing and state transitions for authorization requests."""

from __future__ import annotations

import logging
from typing import Optional

from app.authorization.pending_requests import PendingEntry, PendingRequestRegistry
from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    DuplicateDecisionError,
    InvalidStateTransitionError,
)

logger = logging.getLogger(__name__)


class DecisionProcessor:
    """Handles validation, atomic state transition, and future resolution for decisions."""

    def __init__(self, registry: PendingRequestRegistry) -> None:
        self.registry = registry

    async def apply_decision(
        self,
        decision: AuthorizationDecision,
    ) -> AuthorizationRequest:
        """Atomically transition a pending request and resolve its waiting future.

        Args:
            decision: Validated AuthorizationDecision (APPROVED or REJECTED).

        Returns:
            The transitioned AuthorizationRequest domain model.

        Raises:
            DuplicateDecisionError: If the request is already resolved or terminal.
            KeyError: If the request ID is not found in pending requests.
        """
        entry: Optional[PendingEntry] = await self.registry.get(decision.request_id)
        if entry is None:
            raise KeyError(
                f"No pending authorization request found with ID: {decision.request_id}"
            )

        request = entry.request

        # Prevent duplicate decisions on an already resolved future/request
        if request.is_terminal() or entry.future.done():
            raise DuplicateDecisionError(
                f"Cannot apply decision to request {decision.request_id}: "
                f"request already finalized in state {request.status.value}"
            )

        try:
            request.transition_to(
                new_status=decision.decision,
                reason=decision.reason,
                timestamp=decision.timestamp,
            )
        except InvalidStateTransitionError as e:
            raise DuplicateDecisionError(str(e)) from e

        # Resolve the concurrency future
        if not entry.future.done():
            entry.future.set_result(decision)

        # Remove from pending registry now that it reached a terminal state
        await self.registry.remove(decision.request_id)

        logger.info(
            "Authorization decision recorded: %s -> %s (tool=%s)",
            request.request_id,
            request.status.value,
            request.tool_name,
        )
        return request
