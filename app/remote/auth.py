"""Authentication, authorization, and rate-limiting for the Remote MCP Gateway."""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import Header, HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self, limit_per_minute: int = 60) -> None:
        self.limit_per_minute = limit_per_minute
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        """Check if request from client_id is within rate limits."""
        now = time.time()
        window_start = now - 60.0

        timestamps = self._requests.get(client_id, [])
        valid_timestamps = [t for t in timestamps if t > window_start]

        if len(valid_timestamps) >= self.limit_per_minute:
            self._requests[client_id] = valid_timestamps
            logger.warning(
                "Rate limit exceeded for client '%s' (%d req/min)",
                client_id,
                len(valid_timestamps),
            )
            return False

        valid_timestamps.append(now)
        self._requests[client_id] = valid_timestamps

        # Periodic memory cleanup when many client IPs are tracked
        if len(self._requests) > 1000:
            stale = [
                k for k, ts in self._requests.items()
                if not any(t > window_start for t in ts)
            ]
            for k in stale:
                self._requests.pop(k, None)

        return True

    def reset(self) -> None:
        """Clear all tracked request counts."""
        self._requests.clear()


# Default singleton rate limiter
rate_limiter = RateLimiter(limit_per_minute=settings.rate_limit_per_minute)


def verify_agent_token(token: Optional[str]) -> bool:
    """Validate desktop agent WebSocket token using constant-time comparison."""
    if not token:
        return False
    return secrets.compare_digest(token, settings.agent_auth_token)


async def verify_remote_client_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
) -> str:
    """FastAPI dependency verifying remote client credentials (API key or Bearer token).

    Returns:
        Client identifier string for logging and rate-limiting.

    Raises:
        HTTPException(401): If credentials are missing or invalid.
        HTTPException(429): If client exceeded rate limit.
    """
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    elif "token" in request.query_params:
        token = request.query_params["token"].strip()

    expected = settings.remote_client_api_key
    if not token or not secrets.compare_digest(token, expected):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Unauthorized remote MCP client access attempt from %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing remote client API key / Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Rate limiting
    client_id = request.client.host if request.client else "remote-client"
    if not rate_limiter.is_allowed(client_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before retrying.",
        )

    return client_id
