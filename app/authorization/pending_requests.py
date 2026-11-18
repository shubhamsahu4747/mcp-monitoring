"""Concurrency-safe registry managing pending authorization requests and atomic transitions."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    DuplicateDecisionError,
)

logger = logging.getLogger(__name__)


@dataclass
class PendingEntry:
    """Encapsulates an active pending request and its awaiting concurrency future."""

    request: AuthorizationRequest
    future: asyncio.Future[AuthorizationDecision]


class PendingRequestRegistry:
    """Thread/coroutine-safe registry enforcing atomic transitions for authorization requests."""

    def __init__(self, finalized_cache_size: int = 5000) -> None:
        self._lock = asyncio.Lock()
        self._pending: Dict[str, PendingEntry] = {}
        # Bounded LRU-style cache of recent finalized requests for duplicate decision detection
        self._finalized: OrderedDict[str, AuthorizationRequest] = OrderedDict()
        self._finalized_cache_size = finalized_cache_size

    def _record_finalized(self, request: AuthorizationRequest) -> None:
        """Cache finalized request to detect duplicate decisions even after removal."""
        self._finalized[request.request_id] = request
        if len(self._finalized) > self._finalized_cache_size:
            self._finalized.popitem(last=False)

    async def register(
        self,
        request: AuthorizationRequest,
        future: asyncio.Future[AuthorizationDecision],
    ) -> None:
        """Register a new pending authorization request."""
        async with self._lock:
            self._pending[request.request_id] = PendingEntry(
                request=request,
                future=future,
            )
            logger.debug(
                "Registered pending authorization request: %s (tool=%s)",
                request.request_id,
                request.tool_name,
            )

    async def get(self, request_id: str) -> Optional[PendingEntry]:
        """Retrieve a pending entry by request ID."""
        async with self._lock:
            return self._pending.get(request_id)

    async def transition_and_resolve(
        self,
        decision: AuthorizationDecision,
    ) -> AuthorizationRequest:
        """Atomically transition a request to APPROVED or REJECTED and resolve its future.

        Guarantees that:
        - Exactly one terminal transition occurs.
        - Concurrent decisions for the same request ID are rejected.
        - Decisions for expired, cancelled, or unknown request IDs are rejected.

        Raises:
            DuplicateDecisionError: If the request is already in a terminal state.
            KeyError: If the request ID is completely unknown.
        """
        async with self._lock:
            # 1. Check if already finalized (e.g. duplicate approval or timeout race)
            if decision.request_id in self._finalized:
                prev = self._finalized[decision.request_id]
                raise DuplicateDecisionError(
                    f"Duplicate decision rejected: Request '{decision.request_id}' "
                    f"has already reached terminal state {prev.status.value}"
                )

            # 2. Check if currently active in pending
            entry = self._pending.get(decision.request_id)
            if entry is None:
                raise KeyError(
                    f"No pending authorization request found with ID: '{decision.request_id}' "
                    f"(it may have timed out, been cancelled, or never existed)"
                )

            request = entry.request

            # 3. Guard against duplicate resolution
            if request.is_terminal() or (entry.future.done() and not entry.future.cancelled()):
                self._record_finalized(request)
                raise DuplicateDecisionError(
                    f"Duplicate decision rejected: Request '{decision.request_id}' "
                    f"is already finalized in state {request.status.value}"
                )

            # 4. Atomic domain transition
            request.transition_to(
                new_status=decision.decision,
                reason=decision.reason,
                timestamp=decision.timestamp,
            )

            # 5. Resolve awaiting future
            if not entry.future.done():
                entry.future.set_result(decision)

            # 6. Move from pending to finalized cache
            del self._pending[decision.request_id]
            self._record_finalized(request)

            logger.info(
                "Atomic authorization transition: %s -> %s (tool=%s)",
                request.request_id,
                request.status.value,
                request.tool_name,
            )
            return request

    async def claim_timeout(
        self,
        request_id: str,
        reason: str,
    ) -> Optional[AuthorizationRequest]:
        """Atomically claim a timeout for a request.

        If a decision won the race and resolved the future before this method runs,
        returns None to allow the approved request to proceed.

        Returns:
            The timed-out AuthorizationRequest if timeout was claimed, or None if decision won.
        """
        async with self._lock:
            entry = self._pending.get(request_id)
            if entry is None:
                # Decision or cancellation already finalized and removed this request
                return None

            request = entry.request
            # If a decision already finalized the request, the decision won the race!
            if request.is_terminal():
                return None
            if entry.future.done() and not entry.future.cancelled():
                return None

            # Timeout claims the terminal state
            request.transition_to(
                new_status=AuthorizationStatus.TIMED_OUT,
                reason=reason,
            )

            del self._pending[request_id]
            self._record_finalized(request)

            logger.warning(
                "Atomic timeout claimed for request %s: %s",
                request_id,
                reason,
            )
            return request

    async def claim_cancellation(
        self,
        request_id: str,
        reason: str = "Client disconnected or request cancelled",
    ) -> Optional[AuthorizationRequest]:
        """Atomically claim cancellation for a request.

        Returns:
            The cancelled AuthorizationRequest if cancellation was claimed, or None if resolved.
        """
        async with self._lock:
            entry = self._pending.get(request_id)
            if entry is None:
                return None

            request = entry.request
            if request.is_terminal():
                return None
            if entry.future.done() and not entry.future.cancelled():
                return None

            request.transition_to(
                new_status=AuthorizationStatus.CANCELLED,
                reason=reason,
            )

            if not entry.future.done():
                entry.future.cancel()

            del self._pending[request_id]
            self._record_finalized(request)

            logger.info(
                "Atomic cancellation claimed for request %s: %s",
                request_id,
                reason,
            )
            return request

    async def remove(self, request_id: str) -> Optional[PendingEntry]:
        """Remove and return a pending entry by request ID."""
        async with self._lock:
            return self._pending.pop(request_id, None)

    async def list_pending(self) -> List[AuthorizationRequest]:
        """Return a snapshot of all currently pending authorization requests."""
        async with self._lock:
            return [entry.request for entry in self._pending.values()]

    async def cancel_all(self, reason: str = "Gateway shutting down") -> List[AuthorizationRequest]:
        """Cancel all pending requests safely and atomically, ensuring fail-closed behavior."""
        cancelled_requests: List[AuthorizationRequest] = []
        async with self._lock:
            for req_id, entry in list(self._pending.items()):
                request = entry.request
                if not request.is_terminal():
                    request.transition_to(
                        new_status=AuthorizationStatus.CANCELLED,
                        reason=reason,
                    )
                    cancelled_requests.append(request)
                    self._record_finalized(request)

                if not entry.future.done():
                    entry.future.cancel()

                logger.info("Cancelled pending authorization request %s: %s", req_id, reason)

            self._pending.clear()
        return cancelled_requests

    @property
    def count(self) -> int:
        """Return the count of currently pending requests."""
        return len(self._pending)
