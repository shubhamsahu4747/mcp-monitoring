"""IPC Authorization Client connecting LocalMCPProxy to WalledAIDesktopApp.

When Claude Desktop invokes tools via local-proxy stdio, this client forwards
the authorization request to the running desktop UI over local loopback HTTP (127.0.0.1:8765)
with bearer token authentication.
If the desktop agent is not running, it enforces fail-closed security.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
)

logger = logging.getLogger(__name__)


class DesktopAgentNotRunningError(Exception):
    """Raised when the Walled AI Desktop Agent is not reachable."""


class IPCAuthorizationClient:
    """Client communicating with Walled AI Desktop Agent IPC endpoint."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        auth_token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        allow_inprocess_fallback: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.auth_token = auth_token or settings.agent_auth_token
        self.timeout_seconds = timeout_seconds or settings.auth_timeout_seconds
        self.allow_inprocess_fallback = allow_inprocess_fallback
        self.base_url = f"http://{self.host}:{self.port}"

    def _get_headers(self) -> Dict[str, str]:
        """Return authentication headers including bearer token."""
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "X-Agent-Token": self.auth_token,
        }

    async def check_health(self) -> bool:
        """Check if desktop agent IPC server is responsive."""
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(
                    f"{self.base_url}/health",
                    headers=self._get_headers(),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def request_authorization(
        self,
        request: AuthorizationRequest,
    ) -> AuthorizationDecision:
        """Forward an authorization request to the running desktop agent.

        Args:
            request: The intercepted AuthorizationRequest.

        Returns:
            AuthorizationDecision with user decision status and reason.
        """
        payload = {
            "server_name": request.server_name,
            "tool_name": request.tool_name,
            "arguments": request.arguments,
            "timeout_seconds": self.timeout_seconds,
        }

        # Client timeout gives a buffer beyond auth timeout;
        # connect timeout is bounded to fail-closed quickly if agent is offline
        connect_timeout = min(1.0, max(0.2, self.timeout_seconds / 2.0))
        timeout_config = httpx.Timeout(
            timeout=self.timeout_seconds + 5.0,
            connect=connect_timeout,
        )

        try:
            logger.info(
                "Forwarding authorization request '%s' (%s) to Desktop Agent at %s...",
                request.request_id,
                request.tool_name,
                self.base_url,
            )
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.post(
                    f"{self.base_url}/api/authorize",
                    json=payload,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                data: Dict[str, Any] = response.json()

            status_str = data.get("status", "REJECTED")
            reason_str = data.get("reason", "Desktop authorization completed")

            # Map status string to domain enum
            status_map = {
                "APPROVED": AuthorizationStatus.APPROVED,
                "REJECTED": AuthorizationStatus.REJECTED,
                "TIMED_OUT": AuthorizationStatus.TIMED_OUT,
                "CANCELLED": AuthorizationStatus.CANCELLED,
            }
            status = status_map.get(status_str, AuthorizationStatus.REJECTED)

            return AuthorizationDecision(
                request_id=request.request_id,
                decision=status,
                reason=reason_str,
            )

        except (httpx.ConnectError, httpx.ConnectTimeout) as err:
            logger.warning(
                "Desktop Agent not reachable at %s: %s",
                self.base_url,
                err,
            )
            # Fail closed: reject unapproved tool execution
            return AuthorizationDecision(
                request_id=request.request_id,
                decision=AuthorizationStatus.REJECTED,
                reason=(
                    f"Walled AI Desktop Agent is not running on {self.base_url}. "
                    "Start the desktop authorization agent ('python -m app.main desktop') "
                    "to review and approve tool calls."
                ),
            )
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                logger.error("Desktop Agent rejected IPC connection: Unauthorized token mismatch")
                return AuthorizationDecision(
                    request_id=request.request_id,
                    decision=AuthorizationStatus.REJECTED,
                    reason=(
                        "Agent authentication failed: Invalid or missing agent bearer token "
                        "(HTTP 401 Unauthorized)"
                    ),
                )
            logger.error("Desktop Agent returned HTTP error %d: %s", err.response.status_code, err)
            return AuthorizationDecision(
                request_id=request.request_id,
                decision=AuthorizationStatus.REJECTED,
                reason=f"Desktop Agent returned HTTP error {err.response.status_code}",
            )
        except httpx.TimeoutException:
            logger.warning("Desktop Agent authorization request timed out.")
            return AuthorizationDecision(
                request_id=request.request_id,
                decision=AuthorizationStatus.TIMED_OUT,
                reason=f"Authorization timed out after {self.timeout_seconds} seconds.",
            )
        except Exception as e:
            logger.error("Unexpected error in IPC authorization client: %s", e)
            return AuthorizationDecision(
                request_id=request.request_id,
                decision=AuthorizationStatus.REJECTED,
                reason=f"Authorization failed due to internal error: {e}",
            )
