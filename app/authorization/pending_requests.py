"""Registry for tracking and managing pending authorization requests in memory."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.models.requests import AuthorizationDecision, AuthorizationRequest

logger = logging.getLogger(__name__)


@dataclass
class PendingEntry:
    """Encapsulates a pending request and its awaiting concurrency future."""

    request: AuthorizationRequest
    future: asyncio.Future[AuthorizationDecision]


class PendingRequestRegistry:
    """Concurrency-safe in-memory registry of active pending authorization requests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: Dict[str, PendingEntry] = {}

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

    async def remove(self, request_id: str) -> Optional[PendingEntry]:
        """Remove and return a pending entry by request ID."""
        async with self._lock:
            return self._pending.pop(request_id, None)

    async def list_pending(self) -> List[AuthorizationRequest]:
        """Return a snapshot of all currently pending authorization requests."""
        async with self._lock:
            return [entry.request for entry in self._pending.values()]

    async def cancel_all(self, reason: str = "Gateway shutting down") -> None:
        """Cancel all pending requests safely, ensuring fail-closed behavior."""
        async with self._lock:
            for req_id, entry in list(self._pending.items()):
                if not entry.future.done():
                    entry.future.cancel()
                logger.info(
                    "Cancelled pending authorization request %s: %s",
                    req_id,
                    reason,
                )
            self._pending.clear()

    @property
    def count(self) -> int:
        """Return the count of currently pending requests."""
        return len(self._pending)
