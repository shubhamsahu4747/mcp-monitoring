"""Decision processing and atomic state transitions for authorization requests."""

from __future__ import annotations

import logging

from app.authorization.pending_requests import PendingRequestRegistry
from app.models.requests import AuthorizationDecision, AuthorizationRequest

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
        return await self.registry.transition_and_resolve(decision)
