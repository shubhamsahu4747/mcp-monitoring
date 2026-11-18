"""Domain models and state machine for authorization requests and audit records.

Implements strict state transition enforcement, Pydantic validation,
and sensitive data redaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Dict, Optional, Sequence, cast

from pydantic import BaseModel, Field, field_validator


class AuthorizationStatus(StrEnum):
    """Lifecycle states for an MCP authorization request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


# Terminal states from which no further transition is permitted
TERMINAL_STATUSES = frozenset(
    {
        AuthorizationStatus.APPROVED,
        AuthorizationStatus.REJECTED,
        AuthorizationStatus.TIMED_OUT,
        AuthorizationStatus.CANCELLED,
    }
)

# Valid transitions map: from_state -> set of allowed to_states
VALID_TRANSITIONS: dict[AuthorizationStatus, set[AuthorizationStatus]] = {
    AuthorizationStatus.PENDING: {
        AuthorizationStatus.APPROVED,
        AuthorizationStatus.REJECTED,
        AuthorizationStatus.TIMED_OUT,
        AuthorizationStatus.CANCELLED,
    },
    AuthorizationStatus.APPROVED: set(),
    AuthorizationStatus.REJECTED: set(),
    AuthorizationStatus.TIMED_OUT: set(),
    AuthorizationStatus.CANCELLED: set(),
}


class AuthorizationError(Exception):
    """Base exception for authorization-related errors."""


class InvalidStateTransitionError(AuthorizationError):
    """Raised when attempting an invalid state transition (e.g. from terminal states)."""


class DuplicateDecisionError(AuthorizationError):
    """Raised when attempting to submit a second decision for a resolved request."""


class AuthorizationTimeoutError(AuthorizationError):
    """Raised when an authorization request exceeds its time limit."""


class AuthorizationCancelledError(AuthorizationError):
    """Raised when an authorization request is cancelled before completion."""


def redact_sensitive_data(
    data: Any,
    sensitive_keys: Sequence[str] = (
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "private_key",
        "access_token",
        "credential",
        "secret_key",
    ),
) -> Any:
    """Recursively redacts sensitive parameter values from dictionaries and lists.

    Args:
        data: The arbitrary Python structure (dict, list, primitive) to redact.
        sensitive_keys: Case-insensitive substrings/keys to treat as sensitive.

    Returns:
        A deep copy of data with sensitive values masked by "[REDACTED]".
    """
    if isinstance(data, dict):
        redacted_dict: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s.lower() in k_lower for s in sensitive_keys):
                redacted_dict[k] = "[REDACTED]"
            else:
                redacted_dict[k] = redact_sensitive_data(v, sensitive_keys)
        return redacted_dict
    elif isinstance(data, list):
        return [redact_sensitive_data(item, sensitive_keys) for item in data]
    return data


class AuthorizationDecision(BaseModel):
    """A user or policy decision for a pending authorization request."""

    request_id: str = Field(description="UUID of the authorization request.")
    decision: AuthorizationStatus = Field(
        description="Outcome decision: APPROVED or REJECTED."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the decision.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional justification or failure explanation.",
    )

    @field_validator("decision")
    @classmethod
    def validate_terminal_decision(cls, v: AuthorizationStatus) -> AuthorizationStatus:
        """Ensure decision submitted by user is explicitly APPROVED or REJECTED."""
        if v not in (AuthorizationStatus.APPROVED, AuthorizationStatus.REJECTED):
            raise ValueError(
                f"User decision must be APPROVED or REJECTED, received {v}"
            )
        return v


class AuthorizationRequest(BaseModel):
    """Represents an intercepted MCP tool invocation awaiting user authorization."""

    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique identifier for the request.",
    )
    server_name: str = Field(
        description="Name of the MCP server hosting the intercepted tool."
    )
    tool_name: str = Field(
        description="Name of the tool requested by the LLM client."
    )
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Exact tool arguments passed in the MCP tools/call request.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the request was intercepted.",
    )
    status: AuthorizationStatus = Field(
        default=AuthorizationStatus.PENDING,
        description="Current lifecycle state of the request.",
    )
    decision_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the decision was finalized.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Explanation or error details associated with the final status.",
    )

    def is_terminal(self) -> bool:
        """Check if request has reached a terminal state."""
        return self.status in TERMINAL_STATUSES

    def can_transition_to(self, new_status: AuthorizationStatus) -> bool:
        """Check if transitioning to new_status is permitted by state machine."""
        allowed = VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def transition_to(
        self,
        new_status: AuthorizationStatus,
        reason: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Safely transition request state.

        Raises:
            InvalidStateTransitionError: If the transition is invalid or already terminal.
        """
        if not self.can_transition_to(new_status):
            raise InvalidStateTransitionError(
                f"Invalid state transition for request {self.request_id}: "
                f"cannot transition from {self.status.value} to {new_status.value}."
            )
        self.status = new_status
        self.decision_at = timestamp or datetime.now(timezone.utc)
        if reason:
            self.reason = reason

    def get_redacted_arguments(
        self, sensitive_keys: Optional[Sequence[str]] = None
    ) -> Dict[str, Any]:
        """Return a copy of the arguments with sensitive information masked."""
        from app.config import settings

        keys = sensitive_keys if sensitive_keys is not None else settings.sensitive_keys_list
        result = redact_sensitive_data(self.arguments, keys)
        return cast(Dict[str, Any], result if isinstance(result, dict) else {})


class AuditRecord(BaseModel):
    """Immutable audit entry persisted to the SQLite database."""

    request_id: str
    server_name: str
    tool_name: str
    timestamp: datetime
    decision: AuthorizationStatus
    decision_timestamp: Optional[datetime] = None
    execution_outcome: Optional[str] = None
    failure_reason: Optional[str] = None
    arguments_redacted: Dict[str, Any] = Field(default_factory=dict)
